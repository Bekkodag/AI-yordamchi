"""Ishga tushirish konfiguratsiyasi: sozlamalar reyestridan (settings.py) yig'iladigan o'zgarmas obyekt."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Oddiy .env o'quvchi. Muhitda bor o'zgaruvchilar ustun turadi."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _verify(v: str | None) -> bool | str:
    if v is None or v.strip().lower() in ("", "1", "true", "yes"):
        return True
    if v.strip().lower() in ("0", "false", "no"):
        return False
    return v.strip()  # CA bundle fayli


def _tls_verify() -> bool | str:
    """CA fayli > eski uslub (verify qiymatida fayl yo'li) > tugma (true/false)."""
    from .settings import STORE

    ca = (STORE.get("llm.ca_file") or "").strip()
    if ca:
        return ca
    for name in ("LLM_VERIFY_TLS", "LLM_GATEWAY_VERIFY"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return _verify(raw)
    raw = STORE._config().get("llm.verify_tls")
    if isinstance(raw, str) and raw.strip():
        return _verify(raw)
    return bool(STORE.get("llm.verify_tls"))


class NotConfigured(SystemExit):
    """Majburiy sozlama yo'q — CLI xabar chiqaradi, panel esa sozlash sahifasini ochadi."""


@dataclass(frozen=True)
class Settings:
    gateway_url: str                 # LLM API manzili (/v1 siz)
    gateway_token: str
    tls_name: str | None
    verify: bool | str
    model: str = "fast"
    gemini_api_key: str | None = None
    max_steps: int = 15
    history_turns: int = 8
    log_dir: Path = ROOT / "logs"
    speech_url: str = ""             # bo'sh = gateway_url
    speech_token: str = ""           # bo'sh = gateway_token
    stt_path: str = "/v1/stt/transcribe"
    tts_path: str = "/v1/tts/stream"
    speech_language: str = "uz"
    speech_normalize: bool = True
    speech_seed: int = 0
    assistant_name: str = "Jarvis"
    learning: bool = True

    @property
    def speech_base(self) -> str:
        return (self.speech_url or self.gateway_url).rstrip("/")

    @property
    def speech_key(self) -> str:
        return self.speech_token or self.gateway_token

    @classmethod
    def load(cls, require: bool = True) -> "Settings":
        load_env()
        from .settings import STORE

        g = STORE.get
        url = (g("llm.base_url") or "").rstrip("/").removesuffix("/v1")
        token = g("llm.api_key") or ""
        if require and (not url or not token or "XXXX" in token):
            raise NotConfigured("LLM sozlanmagan: llm.base_url va llm.api_key kerak "
                                "(.env, yoki boshqaruv paneli: python -m yordamchi --panel)")
        return cls(
            gateway_url=url,
            gateway_token=token,
            tls_name=(g("llm.tls_server_name") or None) if url.startswith("https://") else None,
            verify=_tls_verify(),
            model=g("llm.model") or "fast",
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            max_steps=int(g("agent.max_steps")),
            history_turns=int(g("agent.history_turns")),
            speech_url=(g("speech.base_url") or "").rstrip("/"),
            speech_token=g("speech.api_key") or "",
            stt_path=g("speech.stt_path") or "/v1/stt/transcribe",
            tts_path=g("speech.tts_path") or "/v1/tts/stream",
            speech_language=g("speech.language") or "uz",
            speech_normalize=bool(g("speech.normalize")),
            speech_seed=int(g("speech.seed") or 0),
            assistant_name=g("assistant.name") or "Jarvis",
            learning=bool(g("learning.enabled")),
        )

    from_env = load  # eski nom
