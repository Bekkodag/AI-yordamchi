"""Ovozli assistent holat mashinasi.

IDLE --(wake word / "Jarvis, ..." / Ctrl+Alt+J)--> LISTENING --(VAD: gap tugadi)--> THINKING
     (STT -> agent) --> SPEAKING (TTS stream) --> LISTENING (follow-up, wake word'siz) --> ... --> IDLE

Ishonchlilik qoidalari (hech narsa cheksiz kutmaydi):
  - mikrofon kadrlari 2 s kelmasa — mikrofon qayta ochiladi;
  - karnay buferni bo'shatmasa — oqim qayta ochiladi (Player);
  - STT 20 s, agent navbati 180 s, TTS bo'lagi 15 s — timeout;
  - watchdog har holatning maksimal davomiyligini kuzatadi va osilgan sessiyani tiklaydi;
  - kadrni qayta ishlashdagi har qanday xato sikldan tashqariga chiqmaydi.
Barcha ovoz hodisalari logs/*.jsonl ga `kind=voice` bilan yoziladi.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..agent import Failed, Reply
from ..config import Settings
from ..runtime import Runtime
from .audio import (EARCON_DONE, EARCON_ERROR, EARCON_LISTEN, EndpointConfig, Endpointer, Mic,
                    PlaybackError, Player, WakeWord, play_bytes, tone)
from .gate import classify, is_dismiss, looks_like_noise
from .speech import TTS_RATE, GatewaySpeech

EARCON_SLEEP = tone((520.0, 390.0), 80, volume=0.15)


def _com_init() -> None:
    import comtypes

    comtypes.CoInitialize()


class State(str, Enum):
    IDLE = "kutish"
    LISTENING = "tinglash"
    THINKING = "o'ylash"
    SPEAKING = "gapirish"


# Har holat uchun maksimal davomiylik (s) — oshsa watchdog sessiyani tiklaydi
_MAX_STATE_S = {State.LISTENING: 25, State.THINKING: 200, State.SPEAKING: 120}


@dataclass
class VoiceConfig:
    wake: bool = True
    wake_threshold: float = 0.4
    wake_threshold_speaking: float = 0.85   # o'z ovozidan uyg'onib ketmasligi uchun
    stt_wake: bool = True                    # kutishda qisqa gaplarni STT qilib "Jarvis..." ni tanish
    follow_up: bool = True                   # javobdan keyin wake word'siz yana tinglash
    follow_up_timeout_ms: int = 10000
    first_timeout_ms: int = 7000
    turn_timeout_s: float = 180.0
    continue_window_s: float = 30.0          # suhbat tugagach "Jarvis"siz buyruq qabul qilinadigan vaqt
    continue_after_media_s: float = 180.0    # qo'shiq/video qo'yilgach: "qo'shiqni o'chir" odatda 1-3 daqiqada
    duck: bool = True                        # suhbat paytida boshqa ilovalar ovozini pasaytirish
    duck_level: float = 0.3                  # 0.3 = asl ovozning 30% i
    max_spoken_chars: int = 260              # undan uzun javobning faqat boshi o'qiladi, to'lig'i ekranda
    voice: str | None = None
    speed: float = 1.0
    input_device: int | str | None = None
    output_device: int | str | None = None
    aec: bool = True                         # karnaydan chiqayotgan ovozni mikrofondan ayirish (echo cancellation)
    # Noutbukning Intel Smart Sound mikrofoni o'zi shovqinni bostiradi; ikkinchi marta bostirish bolalarning
    # past ovozini ham "yeb" qo'yishi mumkin — shuning uchun o'chiq (boshqa mikrofon bo'lsa yoqing)
    aec_noise_suppression: bool = False
    echo_text_guard: bool = True             # AEC'dan o'tib ketgan qo'shiq so'zlarini matn bo'yicha ham tutish


_APOS = re.compile(r"[ʻʼ’‘`']")
_WAKE_PREFIX = re.compile(
    r"^\s*(?:(?:hey|hi|ey|xey|hay|эй|хей|ой)[\s,]+)?"
    r"(?:(?:d?j|dzh|ch)a?r?[vw][iy]?[sz]|д?жа?р?ви?[сз])\w*[\s,.!?-]*", re.IGNORECASE)
_STOP = {"toxta", "toxtat", "toxtating", "bas", "stop", "bekor qil", "bekor", "jim", "jim bol", "yetadi",
         "yetarli", "стоп", "хватит", "отмена"}
_YES = ("ha", "xa", "haa", "albatta", "mayli", "tasdiqlayman", "roziman", "ok", "okey", "bajar", "da", "да",
        "yes", "tasdiq")
_NO = ("yoq", "kerak emas", "bekor", "yo", "нет", "net", "toxta", "no", "shart emas")


def _norm(text: str) -> str:
    t = _APOS.sub("", text.lower())
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


_GREET = {"hey", "hi", "ey", "xey", "hay", "эй", "хей", "ой", "e"}
_CYR = dict(zip("абвгдезийклмнопрстуфхцчшщъыьэюяёўқғҳж",
                ["a", "b", "v", "g", "d", "e", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                 "f", "x", "ts", "ch", "sh", "sh", "'", "i", "", "e", "yu", "ya", "yo", "o'", "q", "g'", "h", "j"]))
_HAS_LAT = re.compile(r"[a-z]", re.IGNORECASE)
_HAS_CYR = re.compile(r"[а-яёўқғҳ]", re.IGNORECASE)


def _translit(word: str) -> str:
    out = []
    for ch in word:
        low = ch.lower()
        rep = _CYR.get(low)
        if rep is None:
            out.append(ch)
            continue
        if out and out[-1].lower() == "y" and rep.startswith("y"):
            rep = rep[1:]  # "kalkuly" + "я" -> "kalkulya", "kalkulyya" emas
        out.append(rep.upper() if ch.isupper() and rep else rep)
    return "".join(out)


def fix_mixed_script(text: str) -> str:
    """STT ba'zan bitta so'zni lotin+kirill aralash yozadi ("кalkulyяtor", "вкладкаni"). Shunday so'zlarni
    lotinga o'giradi; to'liq ruscha so'zlarga tegmaydi."""
    return " ".join(_translit(w) if _HAS_LAT.search(w) and _HAS_CYR.search(w) else w for w in text.split())


# Bolalar "Jarvis"ni "Darvis" deydi; STT esa "darbis", "darveys", "narvis", "radvis" deb yozadi (24.11 logi)
_WAKE_TARGETS = ("jarvis", "darvis", "jariz")
# O'xshash, lekin oddiy so'zlar (qo'shiq so'zlari ham): bular hech qachon wake emas
_NOT_WAKE = {"darvesh", "darvish", "dervish", "darveshi", "darveshlar", "darvoza", "javob", "jarayon", "arvoh",
             "davlat", "davom", "darvozani", "zarvaraq", "darsni", "darslar", "parvoz", "javobi", "dars", "davri"}


def _jarvis_like(token: str) -> bool:
    w = _translit(_norm(token)).replace("'", "")
    if not 4 <= len(w) <= 8 or w in _NOT_WAKE:
        return False
    return max(difflib.SequenceMatcher(None, w, t).ratio() for t in _WAKE_TARGETS) >= 0.72


def find_wake_anywhere(text: str) -> tuple[bool, str]:
    """Gapning istalgan joyidagi "Jarvis" ("qo'shiqni o'chir, darvis"). Wake so'zlari olib tashlangan matnni
    qaytaradi. Faqat qisqa gaplar uchun — uzun suhbat ichida tasodifiy o'xshash so'z chiqib qolmasin."""
    toks = (text or "").split()
    if not toks or len(toks) > 14:
        return False, text
    keep = [t for t in toks if not _jarvis_like(t.strip(",.!?"))]
    if len(keep) == len(toks):
        return False, text
    return True, " ".join(keep).strip(" ,.!?")


def _wake_split(text: str) -> tuple[bool, str]:
    """(wake bormi, qolgan matn). Aniq naqsh + STT xatolari uchun noaniq moslash ("jaris", "jalis", "darvis")."""
    text = (text or "").strip()
    m = _WAKE_PREFIX.match(text)
    if m:
        return True, text[m.end():].strip()
    toks = text.split()
    i = 1 if toks and _norm(toks[0]) in _GREET else 0
    if i < len(toks) and _jarvis_like(toks[i]):
        return True, " ".join(toks[i + 1:]).strip(" ,.!?")
    return False, text


def rms16(pcm: bytes) -> float:
    import numpy as np

    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16).astype(np.float32)
    return float(np.sqrt((x * x).mean())) if len(x) else 0.0


def echo_similarity(mic_text: str, ref_text: str) -> float:
    """Mikrofonda eshitilgan so'zlarning qanchasi shu paytda karnaydan chiqqan gapda ham bor (0..1).
    So'z boshlanmalari (5 harf) bo'yicha — STT ikki marta bir xil yozmaydi."""
    a = [w[:5] for w in _norm(mic_text).split() if len(w) >= 2]
    b = {w[:5] for w in _norm(ref_text).split() if len(w) >= 2}
    if not a or not b:
        return 0.0
    return sum(1 for w in a if w in b) / len(a)


def has_wake(text: str) -> bool:
    return _wake_split(text)[0]


def strip_wake(text: str) -> str:
    found, rest = _wake_split(text)
    if not found:
        rest = text.strip()
    toks = rest.split()
    if toks and _jarvis_like(toks[-1].strip(",.!?")):  # "bosilmadi jarvis"
        rest = " ".join(toks[:-1])
    return rest.strip(" ,")


_SENT = re.compile(r"(?<=[.!?])\s+")


def spoken_part(text: str, max_chars: int) -> str:
    """Ovozda o'qiladigan qism: to'liq gaplar bilan `max_chars` gacha. To'liq matn ekranda qoladi."""
    t = " ".join(text.split())
    if len(t) <= max_chars:
        return t
    out = ""
    for s in _SENT.split(t):
        if len(out) + len(s) + 1 > max_chars:
            break
        out = f"{out} {s}".strip()
    if not out:
        out = t[:max_chars].rsplit(" ", 1)[0] + "..."
    return out


def is_stop(text: str) -> bool:
    return _norm(text) in _STOP


def parse_yes(text: str) -> bool:
    padded = f" {_norm(text)} "
    if any(f" {w} " in padded for w in _NO):
        return False
    return any(f" {w} " in padded for w in _YES)


class VoiceAssistant:
    def __init__(self, settings: Settings, cfg: VoiceConfig, status: Callable[[str], None] = print) -> None:
        self.settings = settings
        self.cfg = cfg
        self.status = status
        self.speech = GatewaySpeech(settings, voice=cfg.voice, speed=cfg.speed)
        self.player = Player(rate=TTS_RATE, device=cfg.output_device)
        self.endpointer = Endpointer(EndpointConfig())
        # Kutish rejimida "Jarvis, ..." ni STT orqali tanish uchun alohida endpointer
        self.idle_ep = Endpointer(EndpointConfig(silence_ms=600, max_ms=8000, start_timeout_ms=10**9))
        self.wake = WakeWord(threshold=cfg.wake_threshold) if cfg.wake else None
        self.runtime: Runtime | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mic: Mic | None = None
        self.aec = None          # aec.EchoCanceller
        self._aec_out = ""       # loopback olinayotgan chiqish qurilmasi (o'zgarsa qayta ulanadi)
        self.state = State.IDLE
        self._state_since = time.monotonic()
        self._capture: tuple[Endpointer, asyncio.Future] | None = None
        self._session: asyncio.Task | None = None
        self._idle_stt: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self._closing = False
        self._last_reply: str | None = None
        self._continue_until = 0.0
        self._confirming = False  # tasdiq ("ha"/"yo'q") kutilmoqda: wake word sessiyani bekor qilmasin
        from ..tools.audio_sessions import Ducker

        self.ducker = Ducker()
        self._com = ThreadPoolExecutor(1, initializer=_com_init, thread_name_prefix="duck")

    # ------------------------------------------------------------------ ducking
    async def _com_call(self, fn, *args):
        try:
            return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._com, fn, *args), 3)
        except Exception as e:  # noqa: BLE001
            self.ev("duck_error", error=f"{type(e).__name__}: {e}")
            return None

    async def duck(self) -> None:
        """Idempotent: yangi paydo bo'lgan ilovalarni ham pasaytiradi (masalan, hozirgina qo'yilgan qo'shiq)."""
        if self.cfg.duck:
            n = await self._com_call(self.ducker.duck, self.cfg.duck_level)
            if n:
                self.ev("duck", apps=n, level=self.cfg.duck_level)

    async def unduck(self) -> None:
        if self.cfg.duck and self.ducker.saved:
            n = await self._com_call(self.ducker.restore)
            self.ev("unduck", apps=n)

    # ------------------------------------------------------------------ wiring / logging
    def attach(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.loop = runtime.loop
        runtime.on_cancel.append(self.interrupt_threadsafe)

    def ev(self, name: str, **data) -> None:
        if self.runtime:
            try:
                self.runtime.log.write("voice", ev=name, state=self.state.value, **data)
            except Exception:  # noqa: BLE001
                pass

    def _set(self, st: State) -> None:
        if st != self.state:
            self.ev("state", to=st.value, after_s=round(time.monotonic() - self._state_since, 2))
            self.state = st
            self._state_since = time.monotonic()
            self.status(f"[{st.value}]")
            if st == State.IDLE:
                self.idle_ep.reset()
                if self.wake:
                    self.wake.reset()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def _beep(self, pcm: bytes) -> None:
        if self._closing:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # loop to'xtatilgan (yopilish paytida) — signal shart emas
            return

        async def go():
            try:
                await play_bytes(self.player, pcm)
            except Exception:  # noqa: BLE001
                pass
        self._spawn(go())

    # ------------------------------------------------------------------ public (thread-safe)
    def trigger_threadsafe(self) -> None:
        """Push-to-talk hotkey: tinglashni boshlaydi (yoki joriy ishni bo'lib, qayta tinglaydi)."""
        self.loop.call_soon_threadsafe(self._on_wake, None, "hotkey")

    def interrupt_threadsafe(self) -> None:
        self.loop.call_soon_threadsafe(self._interrupt, "kill_switch")

    def _interrupt(self, reason: str = "") -> None:
        self.player.stop()
        if self._session and not self._session.done():
            self.ev("interrupt", reason=reason)
            self._session.cancel()

    # ------------------------------------------------------------------ main
    def _start_aec(self) -> None:
        from .aec import EchoCanceller, Loopback

        out = None
        if self.cfg.output_device is not None:
            import sounddevice as sd

            out = sd.query_devices(self.cfg.output_device)["name"]
        lb = Loopback(out)
        lb.start()
        self.aec = EchoCanceller(lb, noise_suppression=self.cfg.aec_noise_suppression)
        self._aec_out = lb.device_name
        self.ev("aec_on", device=lb.device_name)

    def _restart_loopback(self, reason: str) -> None:
        lb = self.aec.loopback if self.aec else None
        if not lb:
            return
        self.ev("aec_loopback_restart", reason=reason, old=lb.device_name)
        lb.stop()
        lb.start()
        self._aec_out = lb.device_name

    async def run(self) -> None:
        self.mic = Mic(asyncio.get_running_loop(), device=self.cfg.input_device)
        if self.cfg.aec:
            try:
                self._start_aec()
                self.mic.processor = self.aec.process
            except Exception as e:  # noqa: BLE001 — AEC bo'lmasa ham ovoz rejimi ishlayversin
                self.aec = None
                self.ev("aec_unavailable", error=f"{type(e).__name__}: {e}")
                self.status(f"Echo cancellation yoqilmadi: {e}")
        self.mic.start()
        hint = "'Jarvis' yoki 'Hey Jarvis' deng" if self.wake or self.cfg.stt_wake else "Ctrl+Alt+J bosing"
        self.status(f"Tayyor. {hint}; to'xtatish: Ctrl+Alt+X yoki \"to'xta\".")
        self.ev("start", wake=bool(self.wake), stt_wake=self.cfg.stt_wake, threshold=self.cfg.wake_threshold)
        recovered = await self._com_call(self.ducker.recover_from_crash)
        if recovered:
            self.ev("duck_recovered", apps=recovered)
        watchdog = self._spawn(self._watchdog())
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(self.mic.queue.get(), 2.0)
                except TimeoutError:
                    self._recover_mic("no_frames_2s")
                    continue
                try:
                    self._on_frame(frame)
                except Exception as e:  # noqa: BLE001  — bitta kadr xatosi siklni o'ldirmasin
                    self.ev("frame_error", error=f"{type(e).__name__}: {e}")
        finally:
            self._closing = True
            self.mic.stop()
            if self.aec and self.aec.loopback:
                self.aec.loopback.stop()
            self._interrupt("shutdown")
            pending = [t for t in (self._session, *self._bg) if t and not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2)
            await self._com_call(self.ducker.restore)
            self._com.shutdown(wait=False)
            self.player.close()
            await self.speech.aclose()

    def _recover_mic(self, reason: str) -> None:
        self.ev("mic_restart", reason=reason, overflows=self.mic.overflows, dropped=self.mic.dropped)
        self.status(f"Mikrofon javob bermadi ({reason}) - qayta ulanmoqda...")
        try:
            self.mic.restart()
        except Exception as e:  # noqa: BLE001
            self.ev("mic_restart_failed", error=str(e))
            self.status(f"Mikrofonni ochib bo'lmadi: {e}")

    async def _aec_watch(self) -> None:
        """Loopback oqimi tirikmi, standart chiqish qurilmasi o'zgarmadimi (quloqchin ulandi) + jonli statistika."""
        from .aec import default_output_name

        last_stats = time.monotonic()
        while self.aec:
            await asyncio.sleep(10)
            try:
                lb = self.aec.loopback
                loop = asyncio.get_running_loop()
                if not lb.healthy():
                    await loop.run_in_executor(None, self._restart_loopback, "stream_dead")
                elif self.cfg.output_device is None:
                    name = await self._com_call(default_output_name)
                    if name and not self._aec_out.startswith(name):
                        await loop.run_in_executor(None, self._restart_loopback, f"default_output_changed:{name}")
                if time.monotonic() - last_stats > 60 and self.aec.n_play >= 100:
                    last_stats = time.monotonic()
                    self.ev("aec_stats", erle_db=self.aec.erle_db(), play_s=round(self.aec.n_play / 100, 1),
                            underruns=lb.underruns, dropped=lb.dropped_bytes, errors=self.aec.errors)
                    self.aec.reset_stats()
            except Exception as e:  # noqa: BLE001
                self.ev("aec_watch_error", error=f"{type(e).__name__}: {e}")

    async def _watchdog(self) -> None:
        last_peak_log = time.monotonic()
        if self.aec:
            self._spawn(self._aec_watch())
        while True:
            await asyncio.sleep(1.0)
            try:
                age = time.monotonic() - self._state_since
                limit = _MAX_STATE_S.get(self.state)
                if limit and age > limit:
                    self.ev("watchdog_reset", stuck_in=self.state.value, seconds=round(age, 1))
                    self.status(f"Watchdog: '{self.state.value}' holatida {age:.0f} s qotib qoldi - tiklanmoqda.")
                    self._interrupt("watchdog")
                    self._capture = None
                    self._set(State.IDLE)
                    self._beep(EARCON_ERROR)
                if not self.player.healthy() and not self.player.playing:
                    self.ev("player_restart", reason="unhealthy_idle")
                    self.player.restart()
                if self.wake and self.state == State.IDLE and time.monotonic() - last_peak_log > 3:
                    last_peak_log = time.monotonic()
                    peak = self.wake.take_peak()
                    if 0.15 <= peak < self.wake.threshold:
                        self.ev("wake_near_miss", peak=round(peak, 3), threshold=self.wake.threshold)
            except Exception as e:  # noqa: BLE001
                self.ev("watchdog_error", error=str(e))

    def _on_frame(self, frame: bytes) -> None:
        if self._capture:
            ep, fut = self._capture
            r = ep.feed(frame)
            if r and not fut.done():
                fut.set_result(bytes(ep.audio) if r == "end" else None)
                self._capture = None
            return
        if self.wake:
            # gapirayotganda — o'z ovozidan, ishlayotganda — tasodifiy shovqindan bo'linib qolmaslik uchun yuqoriroq
            self.wake.threshold = {State.SPEAKING: self.cfg.wake_threshold_speaking,
                                   State.THINKING: max(self.cfg.wake_threshold, 0.65)}.get(
                                       self.state, self.cfg.wake_threshold)
            score = self.wake.feed(frame)
            if score is not None:
                self._on_wake(score, "wake_word")
                return
        if self.cfg.stt_wake and self.state == State.IDLE and not (self._idle_stt and not self._idle_stt.done()):
            if self.idle_ep.feed(frame) == "end":
                audio = bytes(self.idle_ep.audio)
                self.idle_ep.reset()
                if 0.3 <= len(audio) / 32000 <= 8:
                    ref = b""
                    if self.aec and self.cfg.echo_text_guard:  # shu gap paytida karnaydan nima chiqqan edi
                        ref = self.aec.recent_reference(len(audio) / 32000 + 0.3)
                    self._idle_stt = self._spawn(self._check_idle_utterance(audio, ref))

    async def _check_idle_utterance(self, audio: bytes, ref: bytes = b"") -> None:
        """Kutishda eshitilgan qisqa gap 'Jarvis ...' bilan boshlansa — uni buyruq sifatida qabul qiladi."""
        loud_ref = bool(ref) and rms16(ref) > 150
        ref_res = None
        try:
            if loud_ref:  # ikkalasini parallel: mikrofon va o'sha paytdagi karnay ovozi
                res, ref_res = await asyncio.wait_for(asyncio.gather(
                    self.speech.transcribe(audio), self.speech.transcribe(ref), return_exceptions=True), 20)
                if isinstance(res, BaseException):
                    raise res
            else:
                res = await asyncio.wait_for(self.speech.transcribe(audio), 20)
        except Exception as e:  # noqa: BLE001
            self.ev("idle_stt_error", error=f"{type(e).__name__}: {e}")
            return
        if isinstance(ref_res, dict):
            mic_text = fix_mixed_script((res.get("text") or "").strip())
            ref_text = fix_mixed_script((ref_res.get("text") or "").strip())
            sim = echo_similarity(mic_text, ref_text)
            if mic_text and sim >= 0.5:  # AEC'dan qoldiq bo'lib o'tib ketgan qo'shiq/video so'zlari
                self.ev("echo_text", text=mic_text[:100], ref=ref_text[:100], sim=round(sim, 2))
                return
        text = fix_mixed_script((res.get("text") or "").replace("�", "").strip())
        if self.state != State.IDLE or (self._session and not self._session.done()):
            return
        if has_wake(text):
            rest = strip_wake(text)
            self.ev("stt_wake", text=text[:120])
            self.status(f"'Jarvis' eshitildi: {text!r}")
            self._session = asyncio.create_task(self._run_session(first_text=rest or None))
            return
        # "qo'shiqni o'chir, darvis" — wake gap o'rtasida/oxirida. Qolgan qism haqiqiy buyruq bo'lsagina qabul
        found, rest = find_wake_anywhere(text)
        if found:
            if not rest or len(rest.split()) <= 1 and looks_like_noise(rest):  # faqat "darvis" / "a jaris"
                self.ev("stt_wake", text=text[:120], where="anywhere")
                self._session = asyncio.create_task(self._run_session())
                return
            if not looks_like_noise(rest):
                kind = await classify(self.runtime.agent.llm, self.settings.model, rest, self._last_reply)
                self.ev("wake_anywhere", text=text[:120], cls=kind)
                if kind in ("command", "answer") and self.state == State.IDLE and not (
                        self._session and not self._session.done()):
                    self.status(f"'Jarvis' eshitildi: {text!r}")
                    self._session = asyncio.create_task(self._run_session(first_text=rest))
                    return
        # Davom oynasi: suhbat yaqinda tugagan bo'lsa, "Jarvis"siz aytilgan aniq buyruqni ham qabul qilamiz
        # (logda: sessiya tugagach 11 s o'tib "youtube dasturini och" deyilgan va e'tiborsiz qolgan)
        if text and time.monotonic() < self._continue_until and not looks_like_noise(text):
            kind = await classify(self.runtime.agent.llm, self.settings.model, text, self._last_reply)
            self.ev("continuation", text=text[:120], cls=kind)
            if kind == "command" and self.state == State.IDLE and not (self._session and not self._session.done()):
                self.status(f"Davom etyapmiz: {text!r}")
                self._session = asyncio.create_task(self._run_session(first_text=text))
                return
        if text:
            self.ev("idle_heard", text=text[:80])

    def _on_wake(self, score: float | None, source: str) -> None:
        if self.state == State.LISTENING:
            return
        if self._confirming:
            # Logda: tasdiq savoli paytida "Jarvis, yes" deyilgan -> barge-in buyruqni bekor qilgan, "yes" esa
            # yangi buyruq bo'lib ketgan. Endi faqat savolni to'xtatamiz, javobni confirm() o'zi tinglaydi.
            self.ev("wake_in_confirm", source=source, score=round(score, 3) if score is not None else None)
            self.player.stop()
            return
        self.ev("wake", source=source, score=round(score, 3) if score is not None else None)
        self.status(f"wake ({source}{f' {score:.2f}' if score is not None else ''})")
        if self._session and not self._session.done():
            self._interrupt("barge_in")
        # Wake word modeli ba'zan uydagi gapdan ham uyg'onadi (bugun 0.97 ball) — birinchi gapni ham filtrlaymiz
        self._session = asyncio.create_task(self._run_session(gate_first=source == "wake_word"))

    # ------------------------------------------------------------------ building blocks
    async def listen(self, start_timeout_ms: int) -> bytes | None:
        self.endpointer.cfg.start_timeout_ms = start_timeout_ms
        self.endpointer.reset()
        fut = asyncio.get_running_loop().create_future()
        self._capture = (self.endpointer, fut)
        self._set(State.LISTENING)
        try:
            audio = await fut
            self.ev("vad", result="speech" if audio else "timeout",
                    audio_s=round(len(audio) / 32000, 2) if audio else 0)
            return audio
        finally:
            self._capture = None
            if self.wake:
                self.wake.reset()

    async def transcribe(self, audio: bytes) -> str:
        t = time.perf_counter()
        res = await asyncio.wait_for(self.speech.transcribe(audio), 20)
        raw = (res.get("text") or "").replace("�", "")
        text = strip_wake(fix_mixed_script(raw))
        ms = (time.perf_counter() - t) * 1000
        self.ev("stt", text=raw[:200], ms=round(ms), audio_s=round(len(audio) / 32000, 2))
        self.status(f"Siz: {text!r}  [STT {ms:.0f} ms, audio {len(audio) / 32000:.1f} s]")
        return text

    async def say(self, text: str) -> bool:
        self._set(State.SPEAKING)
        t = time.perf_counter()
        first: list[float] = []
        spoken = spoken_part(text, self.cfg.max_spoken_chars)

        async def timed():
            async for c in self.speech.tts_stream(spoken):
                if not first:
                    first.append((time.perf_counter() - t) * 1000)
                yield c

        self.status(f"JARVIS: {text}" + ("" if spoken == text else "  [ovozda faqat boshi o'qildi]"))
        try:
            ok = await self.player.play(timed())
            if ok and not first and spoken.strip():
                # Logda: 166 belgili javobga gateway 66 ms ichida bo'sh oqim qaytargan — javob umuman eshitilmagan
                self.ev("tts_empty", chars=len(spoken))
                ok = await self.player.play(timed())
        except PlaybackError as e:
            self.ev("tts_error", error=str(e))
            self.status(f"Ovoz chiqarishda muammo: {e}")
            return False
        total = (time.perf_counter() - t) * 1000
        self.ev("tts", chars=len(text), first_ms=round(first[0]) if first else None, total_ms=round(total),
                completed=ok)
        self.status(f"  [TTS birinchi audio {first[0] if first else 0:.0f} ms, jami {total:.0f} ms]")
        return ok

    async def confirm(self, text: str) -> bool:
        """Agent xavfli amal oldidan chaqiradi: ovoz bilan so'raydi, 'ha'/'yo'q' tinglaydi."""
        self.ev("confirm_ask", text=text[:200])
        result = False
        head, _, details = text.partition("\n")
        if details.strip():
            self.status(f"  Tafsilot:\n{details}")
        self._confirming = True
        try:
            await self.duck()
            await self.say(f"{head.rstrip(':. ')}. Tasdiqlaysizmi?")
            for _ in range(2):
                audio = await self.listen(9000)
                if audio is None:
                    self.status("Javob bo'lmadi - bekor qilindi.")
                    break
                self._set(State.THINKING)
                ans = await self.transcribe(audio)
                n = _norm(ans)
                if parse_yes(ans):
                    result = True
                    break
                if any(f" {w} " in f" {n} " for w in _NO):
                    break
                await self.say("Tushunmadim. Ha yoki yo'q deng.")
            return result
        finally:
            self._confirming = False
            self.ev("confirm_result", approved=result)
            self._set(State.THINKING)

    async def _gate(self, text: str, after_wake: bool = False) -> str:
        """'ok' | 'ignore' | 'dismiss' — follow-up'da eshitilgan gap JARVIS'ga qaratilganmi.
        after_wake: wake word'dan keyingi birinchi gap — "nima gap", "assalomu alaykum" JARVIS'ga salom bo'lishi
        mumkin, shuning uchun faqat aniq shovqin e'tiborsiz qoldiriladi."""
        if is_dismiss(text):
            verdict, kind = "dismiss", "dismiss_rule"
        elif looks_like_noise(text):
            verdict, kind = "ignore", "noise_rule"
        else:
            t = time.perf_counter()
            kind = await classify(self.runtime.agent.llm, self.settings.model, text, self._last_reply)
            verdict = {"dismiss": "dismiss", "not_for_me": "ignore", "noise": "ignore"}.get(kind, "ok")
            if after_wake and kind == "not_for_me":
                verdict = "ok"
            self.ev("gate_ms", ms=round((time.perf_counter() - t) * 1000))
        self.ev("gate", text=text[:120], cls=kind, verdict=verdict)
        if verdict == "ignore":
            self.status(f"  [e'tiborsiz: {kind}] {text!r}")
        return verdict

    def _media_turn(self) -> bool:
        """Oxirgi navbat qo'shiq/video qo'ydimi (bloklash yoki "bo'ldi" emas)."""
        try:
            hist = self.runtime.agent.history
        except AttributeError:
            return False
        for m in reversed(hist):
            if m.get("role") == "user":
                return False
            for c in m.get("tool_calls") or []:
                if c["function"]["name"] in ("youtube_play", "media_control"):
                    return True
        return False

    # ------------------------------------------------------------------ session
    async def _run_session(self, first_text: str | None = None, gate_first: bool = False) -> None:
        me = asyncio.current_task()
        self.ev("session_start", first_text=first_text)
        turn: asyncio.Task | None = None
        end_reason = "?"
        try:
            await self.duck()
            follow = False
            empty = ignored = 0
            pending_text = first_text
            while True:
                if pending_text:
                    text, pending_text = pending_text, None
                    self.status(f"Siz: {text!r}")
                    self._beep(EARCON_DONE)
                else:
                    self._beep(EARCON_LISTEN)
                    timeout = self.cfg.follow_up_timeout_ms if follow else self.cfg.first_timeout_ms
                    audio = await self.listen(timeout)
                    if audio is None:
                        end_reason = "no_speech"
                        break
                    self._beep(EARCON_DONE)
                    self._set(State.THINKING)
                    text = await self.transcribe(audio)
                    if not text or (not follow and looks_like_noise(text)):  # bo'sh / faqat "Jarvis" / shovqin
                        empty += 1
                        if empty >= 3:
                            end_reason = "empty_stt"
                            break
                        follow = True
                        continue
                    if follow or gate_first:  # wake word'siz (yoki ehtimoliy soxta wake'dan keyin) eshitilgan gap
                        verdict = await self._gate(text, after_wake=gate_first and not follow)
                        if verdict == "dismiss":
                            end_reason = "dismiss"
                            await self.say("Xo'p.")
                            break
                        if verdict == "ignore":  # musiqa, TV, boshqalar bilan suhbat — jimgina o'tkazib yuboramiz
                            ignored += 1
                            if ignored >= (2 if gate_first and not follow else 3):
                                end_reason = "ignored_first" if gate_first and not follow else "ignored_3"
                                break
                            continue
                empty = ignored = 0
                gate_first = False
                if is_stop(text):
                    end_reason = "stop_word"
                    self.status("To'xtatildi.")
                    break
                if is_dismiss(text):
                    end_reason = "dismiss"
                    await self.say("Xo'p.")
                    break
                self._set(State.THINKING)
                t0 = time.perf_counter()
                turn = asyncio.create_task(self.runtime.turn(text))
                try:
                    res = await asyncio.wait_for(asyncio.shield(turn), self.cfg.turn_timeout_s)
                except TimeoutError:
                    turn.cancel()
                    self.ev("turn_timeout", seconds=self.cfg.turn_timeout_s)
                    res = Failed("turn timeout")
                turn = None
                if me.cancelling():
                    raise asyncio.CancelledError
                self.ev("turn_done", result=type(res).__name__, ms=round((time.perf_counter() - t0) * 1000))
                await self.duck()  # navbat paytida yangi ovoz manbai paydo bo'lgan bo'lishi mumkin (qo'shiq)
                if isinstance(res, Reply):
                    self._last_reply = res.text
                    await self.say(res.text)
                    if res.end_session:  # qo'shiq qo'yildi, ekran bloklandi, "bo'ldi" — tinglashni to'xtatamiz
                        end_reason = "media_started" if self._media_turn() else "agent_end"
                        break
                elif isinstance(res, Failed):
                    self._beep(EARCON_ERROR)
                    await self.say("Kechirasiz, xatolik yuz berdi. Qaytadan urinib ko'ring.")
                else:  # bekor qilingan
                    end_reason = "turn_cancelled"
                    break
                if not self.cfg.follow_up:
                    end_reason = "no_follow_up"
                    break
                follow = True
        except asyncio.CancelledError:
            end_reason = "cancelled"
            self.player.stop()
            if turn and not turn.done():
                turn.cancel()
            raise
        except Exception as e:  # noqa: BLE001
            end_reason = f"error: {type(e).__name__}: {e}"
            self.status(f"Xato: {type(e).__name__}: {e}")
            self._beep(EARCON_ERROR)
        finally:
            self.ev("session_end", reason=end_reason)
            if self._session is me:  # barge-in bo'lsa yangi sessiya ducking'ni davom ettiradi
                # Tabiiy tugagan suhbatdan keyin 30 s davomida "Jarvis"siz aniq buyruq ham qabul qilinadi
                # agent_end ham: qo'shiq qo'yilgach "qo'shiqni o'chir" deyiladi (24.11: bola 7 marta takrorlagan).
                # Davom oynasida faqat classify=command qabul qilinadi, qo'shiq so'zlari o'tmaydi.
                natural = end_reason in ("no_speech", "ignored_3", "empty_stt", "no_follow_up", "media_started")
                window = self.cfg.continue_after_media_s if end_reason == "media_started" else self.cfg.continue_window_s
                self._continue_until = time.monotonic() + window if natural else 0.0
                await self.unduck()
                self._set(State.IDLE)
                if end_reason not in ("cancelled",):
                    self._beep(EARCON_SLEEP)
                    hint = "'Jarvis' deng" if (self.wake or self.cfg.stt_wake) else "Ctrl+Alt+J bosing"
                    self.status(f"Kutish rejimi - davom etish uchun {hint}.")
