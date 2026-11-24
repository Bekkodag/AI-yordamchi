"""Echo cancellation: PC karnayidan chiqayotgan ovozni (qo'shiq, video, JARVIS'ning o'z ovozi) mikrofondan ayiradi.

    karnayga ketayotgan ovoz ──(WASAPI loopback)──► reference ─┐
    mikrofon (odam + xonadagi qo'shiq) ──────────► WebRTC AEC3 ─┴─► faqat odam ovozi ──► VAD / wake / STT

Loopback qurilmasi — chiqish qurilmasining "nusxasi": Windows aynan karnayga yuborayotgan audio.
AEC3 (Google Meet/Chrome'dagi algoritm) shu namunani mikrofon signalidan adaptiv ayiradi va kechikishni
o'zi topadi. Hech narsa ijro etilmayotganda WASAPI loopback paket bermaydi — o'shanda reference = jimlik.
"""

from __future__ import annotations

import threading
import time

import numpy as np

RATE = 16000
CHUNK = RATE // 100          # AEC 10 ms bloklar bilan ishlaydi
CHUNK_BYTES = CHUNK * 2
MAX_BACKLOG_S = 0.25         # reference navbati bundan oshsa eskisini tashlaymiz (drift / to'xtalish)


class Loopback:
    """Chiqish qurilmasining WASAPI loopback oqimi -> 16 kHz mono int16 bufer."""

    def __init__(self, output_name: str | None = None) -> None:
        self.output_name = output_name
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._pa = None
        self._stream = None
        self._resampler = None
        self.device_name = ""
        self.last_data = 0.0
        self.level = 0.0          # oxirgi paketning RMS (0..1)
        self.dropped_bytes = 0
        self.underruns = 0

    # ------------------------------------------------------------------ device
    def _device(self, pa):
        import pyaudiowpatch as paw

        wasapi = pa.get_host_api_info_by_type(paw.paWASAPI)
        want = self.output_name
        if not want:
            want = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
        loops = list(pa.get_loopback_device_info_generator())
        for d in loops:
            if d["name"].startswith(want) or want in d["name"]:
                return d
        if loops:
            return loops[0]
        raise RuntimeError("WASAPI loopback device not found")

    def start(self) -> None:
        import pyaudiowpatch as paw
        from livekit import rtc

        self._pa = paw.PyAudio()
        d = self._device(self._pa)
        self.device_name = d["name"]
        rate, ch = int(d["defaultSampleRate"]), max(1, int(d["maxInputChannels"]))
        self._ch, self._rate = ch, rate
        self._resampler = rtc.AudioResampler(rate, RATE, num_channels=1,
                                             quality=rtc.AudioResamplerQuality.MEDIUM) if rate != RATE else None
        self._stream = self._pa.open(format=paw.paInt16, channels=ch, rate=rate, input=True, frames_per_buffer=rate // 100,
                                     input_device_index=d["index"], stream_callback=self._cb)
        self._stream.start_stream()

    def _cb(self, data, frames, info, status):  # PortAudio thread
        import pyaudiowpatch as paw

        try:
            x = np.frombuffer(data, dtype=np.int16)
            if self._ch > 1:
                x = x.reshape(-1, self._ch).mean(axis=1).astype(np.int16)
            self.level = float(np.sqrt(np.mean((x.astype(np.float32) / 32768) ** 2))) if len(x) else 0.0
            pcm = x.tobytes()
            if self._resampler is not None:
                from livekit import rtc

                out = self._resampler.push(rtc.AudioFrame(pcm, self._rate, 1, len(x)))
                pcm = b"".join(bytes(f.data.cast("B")) for f in out)
            with self._lock:
                self._buf.extend(pcm)
                over = len(self._buf) - int(MAX_BACKLOG_S * RATE) * 2
                if over > 0:
                    del self._buf[:over]
                    self.dropped_bytes += over
            self.last_data = time.monotonic()
        except Exception:  # noqa: BLE001 — audio oqimini hech qachon o'ldirmaymiz
            pass
        return (None, paw.paContinue)

    # ------------------------------------------------------------------ API
    def take(self, nbytes: int) -> bytes | None:
        """10 ms reference. Bo'sh bo'lsa None (hozir hech narsa ijro etilmayapti)."""
        with self._lock:
            if len(self._buf) < nbytes:
                if self._buf:
                    self.underruns += 1
                return None
            out = bytes(self._buf[:nbytes])
            del self._buf[:nbytes]
            return out

    def healthy(self) -> bool:
        try:
            return bool(self._stream and self._stream.is_active())
        except Exception:  # noqa: BLE001
            return False

    def playing(self, within_s: float = 0.5) -> bool:
        return time.monotonic() - self.last_data < within_s and self.level > 0.002

    def stop(self) -> None:
        s, self._stream = self._stream, None
        for fn in ((s.stop_stream, s.close) if s else ()):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._pa = None


def default_output_name() -> str:
    """Windows'dagi joriy standart chiqish qurilmasi nomi (COM oqimida chaqiring)."""
    from pycaw.pycaw import AudioUtilities

    try:
        return AudioUtilities.GetSpeakers().FriendlyName or ""
    except Exception:  # noqa: BLE001
        return ""


class EchoCanceller:
    """Mikrofon PCM (16 kHz mono int16, istalgan uzunlikda) -> echo'si olib tashlangan PCM (bir xil uzunlikda).

    Oxirgi HISTORY_S soniyalik reference saqlanadi: STT'dan o'tib ketgan gapni shu paytda karnaydan chiqqan
    ovoz bilan matn bo'yicha solishtirish uchun (ikkinchi himoya)."""

    HISTORY_S = 12

    def __init__(self, loopback: Loopback | None, noise_suppression: bool = True, delay_ms: int | None = None) -> None:
        from livekit import rtc

        self.loopback = loopback
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=noise_suppression,
                                             high_pass_filter=True, auto_gain_control=False)
        if delay_ms is not None:
            self.apm.set_stream_delay_ms(delay_ms)
        self._carry = bytearray()
        self._silence = bytes(CHUNK_BYTES)
        self._hist = bytearray()
        self._hist_max = self.HISTORY_S * RATE * 2
        self.chunks = 0
        self.ref_chunks = 0
        self.errors = 0
        self.reset_stats()

    def reset_stats(self) -> None:
        # Faqat karnay o'ynayotgan bloklarda: mikrofon energiyasi kirish/chiqishda (jonli ERLE bahosi)
        self.e_in = self.e_out = 0.0
        self.n_play = 0

    def erle_db(self) -> float | None:
        if self.n_play < 100 or self.e_out <= 0:
            return None
        return round(10 * float(np.log10(max(self.e_in, 1e-9) / self.e_out)), 1)

    def _ref(self) -> tuple[bytes, bool]:
        r = self.loopback.take(CHUNK_BYTES) if self.loopback else None
        if r is not None:
            self.ref_chunks += 1
            return r, True
        return self._silence, False

    def recent_reference(self, seconds: float) -> bytes:
        """Mikrofonning oxirgi `seconds` soniyasiga mos keladigan reference (karnaydan chiqqan ovoz)."""
        n = int(seconds * RATE) * 2
        return bytes(self._hist[-n:]) if n > 0 else b""

    def process(self, pcm: bytes) -> bytes:
        from livekit import rtc

        self._carry.extend(pcm)
        out = bytearray()
        while len(self._carry) >= CHUNK_BYTES:
            mic = bytes(self._carry[:CHUNK_BYTES])
            del self._carry[:CHUNK_BYTES]
            ref, real = self._ref()
            self._hist.extend(ref)
            if len(self._hist) > self._hist_max:
                del self._hist[:len(self._hist) - self._hist_max]
            try:
                self.apm.process_reverse_stream(rtc.AudioFrame(ref, RATE, 1, CHUNK))
                f = rtc.AudioFrame(mic, RATE, 1, CHUNK)
                self.apm.process_stream(f)
                res = bytes(f.data.cast("B"))
            except Exception:  # noqa: BLE001 — AEC xato qilsa xom ovozni beramiz, mikrofon to'xtamasin
                self.errors += 1
                res = mic
            # Statistika faqat karnaydan haqiqiy ovoz chiqqanda (JARVIS player'i doim jimlik ham "ijro" qiladi)
            if real and np.abs(np.frombuffer(ref, np.int16)).max() > 300:
                x = np.frombuffer(mic, np.int16).astype(np.float32)
                y = np.frombuffer(res, np.int16).astype(np.float32)
                self.e_in += float((x * x).sum())
                self.e_out += float((y * y).sum())
                self.n_play += 1
            out.extend(res)
            self.chunks += 1
        return bytes(out)

    def process_pair(self, mic: bytes, ref: bytes) -> bytes:
        """Offline: oldindan yozilgan mic va reference (bir xil uzunlik) — o'lchash va testlar uchun."""
        from livekit import rtc

        out = bytearray()
        for i in range(0, min(len(mic), len(ref)) - CHUNK_BYTES + 1, CHUNK_BYTES):
            self.apm.process_reverse_stream(rtc.AudioFrame(ref[i:i + CHUNK_BYTES], RATE, 1, CHUNK))
            f = rtc.AudioFrame(mic[i:i + CHUNK_BYTES], RATE, 1, CHUNK)
            self.apm.process_stream(f)
            out.extend(bytes(f.data.cast("B")))
        return bytes(out)
