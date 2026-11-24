"""Gateway orqali STT va TTS (streaming PCM 24 kHz)."""

from __future__ import annotations

import io
import re
import wave
from typing import AsyncIterator

from ..config import Settings
from ..gateway import make_http

TTS_RATE = 24000
_MD = re.compile(r"[*_`#>]+")


def pcm_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def clean_for_tts(text: str) -> str:
    t = _MD.sub("", text)
    t = re.sub(r"https?://\S+", "havola", t)
    return " ".join(t.split())


_ACRONYM = re.compile(r"\b([A-ZА-Я])([A-ZА-Я]{1,5})\b")


def soften_acronyms(text: str) -> str:
    """'GCC' -> 'Gcc' (TTS normalizatori ba'zi bosh harfli qisqartmalarda yiqiladi)."""
    return _ACRONYM.sub(lambda m: m.group(1) + m.group(2).lower(), text)


class GatewaySpeech:
    def __init__(self, settings: Settings, voice: str | None = None, language: str | None = None,
                 speed: float = 1.0) -> None:
        self.base = settings.speech_base
        self.h = {"Authorization": f"Bearer {settings.speech_key}"}
        self.stt_path, self.tts_path = settings.stt_path, settings.tts_path
        self.http = make_http(settings, timeout=20.0)
        self.voice, self.language, self.speed = voice, language or settings.speech_language, speed
        self.normalize = getattr(settings, "speech_normalize", True)
        self.seed = int(getattr(settings, "speech_seed", 0) or 0)

    async def transcribe(self, pcm16k: bytes) -> dict:
        r = await self.http.post(f"{self.base}{self.stt_path}", headers=self.h, timeout=20.0,
                                 files={"file": ("speech.wav", pcm_to_wav(pcm16k), "audio/wav")})
        r.raise_for_status()
        return r.json()

    async def tts_stream(self, text: str) -> AsyncIterator[bytes]:
        t = clean_for_tts(text)
        produced = False
        async for c in self._tts_once(t):
            produced = True
            yield c
        if not produced and t:
            # Gateway normalizatori ba'zi qisqartmalarda ("GCC", "GC", "CC") jimgina bo'sh audio qaytaradi.
            # "Gcc" ko'rinishida esa o'qiydi — shuning uchun bosh harfli so'zlarni yumshatib qayta urinamiz.
            soft = soften_acronyms(t)
            if soft != t:
                async for c in self._tts_once(soft):
                    yield c

    async def _tts_once(self, text: str) -> AsyncIterator[bytes]:
        body = {"text": text, "language": self.language, "normalize": self.normalize, "playback_speed": self.speed}
        if self.voice:
            body["voice"] = self.voice
        if self.seed:
            body["seed"] = self.seed
        async with self.http.stream("POST", f"{self.base}{self.tts_path}", headers=self.h, json=body) as r:
            if r.status_code >= 400:
                await r.aread()
                r.raise_for_status()
            carry = b""
            async for chunk in r.aiter_bytes():
                data = carry + chunk
                cut = len(data) - (len(data) % 2)  # int16 chegarasi
                carry = data[cut:]
                if cut:
                    yield data[:cut]

    async def voices(self) -> list[dict]:
        r = await self.http.get(f"{self.base}/v1/tts/voices", headers=self.h)
        r.raise_for_status()
        v = r.json()
        return v if isinstance(v, list) else v.get("voices", [])

    async def aclose(self) -> None:
        await self.http.aclose()
