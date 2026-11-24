"""Mikrofon (16 kHz), karnay (24 kHz streaming, to'xtatiladigan), signal tovushlari, VAD va wake word."""

from __future__ import annotations

import asyncio
import collections
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator

import numpy as np
import sounddevice as sd

MIC_RATE = 16000
FRAME = 512            # 32 ms — Silero VAD kadri
FRAME_BYTES = FRAME * 2


# --------------------------------------------------------------------------- microphone

class Mic:
    """Mikrofon kadrlarini aniq 512 namunadan (int16) asyncio navbatiga uzatadi.

    Oqim to'xtab qolsa (qurilma o'zgardi, drayver xatosi) `restart()` qayta ochadi — buni assistent
    watchdog'i kadrlar kelmay qolganda chaqiradi."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device: int | str | None = None) -> None:
        self.loop = loop
        self.device = device
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
        self._carry = bytearray()
        self.overflows = 0
        self.dropped = 0
        self.restarts = 0
        self.stream: sd.RawInputStream | None = None
        # Ixtiyoriy ishlov (echo cancellation): PCM -> PCM, VAD/wake/STT'dan oldin
        self.processor = None
        self.processor_errors = 0

    def _open(self) -> None:
        self.stream = sd.RawInputStream(samplerate=MIC_RATE, blocksize=FRAME, channels=1, dtype="int16",
                                        device=self.device, callback=self._cb)
        self.stream.start()

    def _cb(self, indata, frames, t, status) -> None:  # audio oqimi (PortAudio thread)
        if status and status.input_overflow:
            self.overflows += 1
        try:
            self.loop.call_soon_threadsafe(self._put, bytes(indata))
        except RuntimeError:  # loop yopilgan
            pass

    def _put(self, data: bytes) -> None:
        if self.processor is not None:
            try:
                data = self.processor(data)
            except Exception:  # noqa: BLE001 — ishlov xatosi mikrofonni to'xtatmasin
                self.processor_errors += 1
        self._carry.extend(data)
        while len(self._carry) >= FRAME_BYTES:  # VAD aniq 1024 bayt talab qiladi
            frame = bytes(self._carry[:FRAME_BYTES])
            del self._carry[:FRAME_BYTES]
            if self.queue.full():
                self.dropped += 1
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self.queue.put_nowait(frame)

    def start(self) -> None:
        self._open()

    def restart(self) -> None:
        self.restarts += 1
        self._close()
        self._carry.clear()
        self._open()

    def _close(self) -> None:
        s, self.stream = self.stream, None
        if s is not None:
            for fn in (s.abort, s.close):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass

    def stop(self) -> None:
        self._close()


# --------------------------------------------------------------------------- speaker

class PlaybackError(Exception):
    pass


class Player:
    """Doimiy ochiq chiqish oqimi (ochilish kechikishi yo'q). Buferda narsa bo'lmasa jimlik chiqaradi.
    play() PCM oqimini darhol ijro etadi; stop() istalgan payt to'xtatadi (barge-in).

    Hech qachon cheksiz kutmaydi: TTS bo'lagi `chunk_timeout` ichida kelmasa yoki qurilma buferni
    kutilgan vaqtda bo'shatmasa (oqim to'xtab qolgan) — oqim qayta ochiladi va PlaybackError ko'tariladi."""

    def __init__(self, rate: int = 24000, device: int | str | None = None) -> None:
        self.rate = rate
        self.device = device
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._gen = 0              # har play() yangi avlod; stop() eskisini bekor qiladi
        self._last_cb = 0.0
        self.playing = False
        self.restarts = 0
        self.stream: sd.RawOutputStream | None = None
        self._open()

    def _open(self) -> None:
        self.stream = sd.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16", blocksize=480,
                                         latency="low", device=self.device, callback=self._cb)
        self._last_cb = time.monotonic()
        self.stream.start()

    def _cb(self, outdata, frames, t, status) -> None:
        self._last_cb = time.monotonic()
        need = frames * 2
        try:
            with self._lock:
                n = min(need, len(self._buf))
                outdata[:n] = self._buf[:n]
                del self._buf[:n]
            if n < need:
                outdata[n:] = b"\x00" * (need - n)
        except Exception:  # noqa: BLE001  callback'da exception oqimni o'ldiradi
            outdata[:] = b"\x00" * len(outdata)

    def healthy(self) -> bool:
        s = self.stream
        return bool(s is not None and s.active and time.monotonic() - self._last_cb < 1.0)

    def restart(self) -> None:
        self.restarts += 1
        s, self.stream = self.stream, None
        if s is not None:
            for fn in (s.abort, s.close):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
        self._open()

    def _pending(self) -> int:
        with self._lock:
            return len(self._buf)

    async def play(self, chunks: AsyncIterator[bytes], prebuffer_ms: int = 100,
                   chunk_timeout: float = 15.0) -> bool:
        """True - oxirigacha ijro etildi, False - to'xtatildi (stop/barge-in)."""
        if not self.healthy():
            self.restart()
        self._gen += 1
        gen = self._gen
        pre = int(self.rate * 2 * prebuffer_ms / 1000)
        staged: bytearray | None = bytearray()
        self.playing = True
        it = chunks.__aiter__()
        try:
            while True:
                try:
                    c = await asyncio.wait_for(it.__anext__(), chunk_timeout)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise PlaybackError(f"TTS: {chunk_timeout:.0f} s ichida audio kelmadi") from None
                if gen != self._gen:
                    return False
                if staged is not None:
                    staged.extend(c)
                    if len(staged) < pre:
                        continue
                    c, staged = bytes(staged), None
                with self._lock:
                    self._buf.extend(c)
            if gen != self._gen:
                return False
            if staged:
                with self._lock:
                    self._buf.extend(staged)
            # Bufer bo'shashini kutish — lekin cheksiz emas
            deadline = time.monotonic() + self._pending() / (self.rate * 2) + 2.0
            while self._pending() and gen == self._gen:
                if time.monotonic() > deadline or not self.healthy():
                    self.stop()
                    self.restart()
                    raise PlaybackError("Karnay oqimi to'xtab qolgan edi, qayta ochildi")
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.03)  # qurilma buferidagi oxirgi bo'lak
            return gen == self._gen
        finally:
            if gen == self._gen:
                self.playing = False
            try:
                await it.aclose()  # HTTP oqimini ham yopadi
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._gen += 1
        self.playing = False
        with self._lock:
            self._buf.clear()

    def close(self) -> None:
        self.stop()
        s, self.stream = self.stream, None
        if s is not None:
            for fn in (s.abort, s.close):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass


def tone(freqs: tuple[float, ...] = (880.0,), ms: int = 90, rate: int = 24000, volume: float = 0.25) -> bytes:
    parts = []
    for f in freqs:
        t = np.arange(int(rate * ms / 1000)) / rate
        env = np.minimum(1, np.minimum(t, t[::-1]) * 200)  # 5 ms fade
        parts.append((np.sin(2 * np.pi * f * t) * env * volume * 32767).astype(np.int16))
    return np.concatenate(parts).tobytes()


async def _once(b: bytes) -> AsyncIterator[bytes]:
    yield b


EARCON_LISTEN = tone((660.0, 990.0), 70)
EARCON_DONE = tone((990.0, 660.0), 60)
EARCON_ERROR = tone((300.0, 220.0), 120)


async def play_bytes(player: Player, pcm: bytes) -> None:
    await player.play(_once(pcm), prebuffer_ms=0)


# --------------------------------------------------------------------------- VAD endpointer

@dataclass
class EndpointConfig:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    start_frames: int = 3          # ~100 ms nutq -> boshlandi
    silence_ms: int = 800          # shuncha jimlik -> gap tugadi
    preroll_ms: int = 300
    max_ms: int = 15000
    start_timeout_ms: int = 6000   # shu vaqtda gap boshlanmasa -> None


class Endpointer:
    def __init__(self, cfg: EndpointConfig | None = None) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        self.cfg = cfg or EndpointConfig()
        self.vad = SileroVoiceActivityDetector()
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self.pre = collections.deque(maxlen=max(1, self.cfg.preroll_ms // 32))
        self.audio = bytearray()
        self.started = False
        self.speech_run = 0
        self.silence = 0
        self.elapsed = 0

    def feed(self, frame: bytes) -> str | None:
        """None - davom, 'end' - gap tugadi (self.audio tayyor), 'timeout' - gap boshlanmadi."""
        p = self.vad.process_chunk(frame)
        self.elapsed += 32
        if not self.started:
            self.pre.append(frame)
            self.speech_run = self.speech_run + 1 if p >= self.cfg.start_threshold else 0
            if self.speech_run >= self.cfg.start_frames:
                self.started = True
                for f in self.pre:
                    self.audio.extend(f)
            elif self.elapsed >= self.cfg.start_timeout_ms:
                return "timeout"
            return None
        self.audio.extend(frame)
        self.silence = self.silence + 32 if p < self.cfg.end_threshold else 0
        audio_ms = len(self.audio) // 32  # 16 kHz int16: 32 bayt = 1 ms
        if self.silence >= self.cfg.silence_ms or audio_ms >= self.cfg.max_ms:
            return "end"
        return None


# --------------------------------------------------------------------------- wake word

class WakeWord:
    """openWakeWord 'hey_jarvis' (80 ms kadrlar). 'Jarvis' deyish ham ko'pincha yetarli."""

    CHUNK = 1280

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        from openwakeword.model import Model

        self.name = model
        self.threshold = threshold
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self._buf = bytearray()
        self.cooldown = 0
        self.peak = 0.0  # oxirgi o'qishdan beri eng yuqori skor (tashxis uchun)

    def take_peak(self) -> float:
        p, self.peak = self.peak, 0.0
        return p

    def reset(self) -> None:
        self.model.reset()
        self._buf.clear()

    def feed(self, frame: bytes) -> float | None:
        """Aniqlansa skor qaytaradi, aks holda None."""
        self._buf.extend(frame)
        hit = None
        while len(self._buf) >= self.CHUNK * 2:
            chunk = np.frombuffer(bytes(self._buf[: self.CHUNK * 2]), dtype=np.int16)
            del self._buf[: self.CHUNK * 2]
            score = float(next(iter(self.model.predict(chunk).values())))
            self.peak = max(self.peak, score)
            if self.cooldown > 0:
                self.cooldown -= 1
                continue
            if score >= self.threshold:
                hit = score
                self.cooldown = 15  # ~1.2 s qayta ishlamasin
                self.model.reset()
        return hit
