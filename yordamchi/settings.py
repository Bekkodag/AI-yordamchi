"""Sozlamalar reyestri: yadro va har bir plugin o'z sozlamalarini shu yerda e'lon qiladi.

Qiymat manbalari (ustunlik tartibida):
    1. muhit o'zgaruvchisi / .env  (masalan LLM_BASE_URL) — Web UI'da "env orqali berilgan" deb ko'rsatiladi
    2. data/config.json            — Web UI'dan saqlangan qiymatlar
    3. standart qiymat

Maxfiy qiymatlar (API kalitlar, tokenlar) config.json'ga YOZILMAYDI: operatsion tizim saqlagichiga
(Windows Credential Manager / macOS Keychain / Secret Service) yoziladi. Saqlagich bo'lmasa (masalan
grafik muhitsiz Linux) — data/secrets.json (faqat egasi o'qiy oladi) va ogohlantirish.

Web UI va API maxfiy qiymatni hech qachon qaytarmaydi — faqat "o'rnatilgan / o'rnatilmagan".
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import ROOT

DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
SECRETS_FALLBACK = DATA_DIR / "secrets.json"
KEYRING_SERVICE = "ai-yordamchi"

TYPES = ("str", "int", "float", "bool", "choice", "secret", "text")


@dataclass(frozen=True)
class Setting:
    key: str                       # "llm.base_url", plugin uchun "plugin.<id>.<nom>"
    label: str
    type: str = "str"
    default: Any = None
    env: tuple[str, ...] = ()      # muhit o'zgaruvchilari (birinchisi asosiy, qolganlari eski nomlar)
    help: str = ""
    group: str = "core"
    section: str = ""
    choices: tuple[str, ...] = ()
    restart: bool = True           # o'zgarsa qayta ishga tushirish kerakmi
    required: bool = False

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"{self.key}: unknown type {self.type}")


def _coerce(spec: Setting, raw: Any) -> Any:
    if raw is None:
        return None
    t = spec.type
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on", "ha")
    if t == "int":
        return int(float(raw))
    if t == "float":
        return float(raw)
    if t == "choice":
        v = str(raw)
        if spec.choices and v not in spec.choices:
            raise ValueError(f"{spec.key}: must be one of {spec.choices}")
        return v
    return str(raw)


class SecretStore:
    """OS saqlagichi (keyring), bo'lmasa fayl."""

    def __init__(self, fallback: Path = SECRETS_FALLBACK) -> None:
        self.fallback = fallback
        self._kr = None
        self.backend = "file"
        try:
            import keyring
            from keyring.backends import fail

            kr = keyring.get_keyring()
            if not isinstance(kr, fail.Keyring):
                self._kr, self.backend = keyring, type(kr).__name__
        except Exception:  # noqa: BLE001
            self._kr = None

    def _file(self) -> dict:
        try:
            return json.loads(self.fallback.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, key: str) -> str | None:
        if self._kr:
            try:
                return self._kr.get_password(KEYRING_SERVICE, key)
            except Exception:  # noqa: BLE001
                pass
        return self._file().get(key)

    def set(self, key: str, value: str | None) -> None:
        if self._kr:
            try:
                if value:
                    self._kr.set_password(KEYRING_SERVICE, key, value)
                else:
                    try:
                        self._kr.delete_password(KEYRING_SERVICE, key)
                    except Exception:  # noqa: BLE001 — yo'q edi
                        pass
                return
            except Exception:  # noqa: BLE001
                pass
        data = self._file()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        self.fallback.parent.mkdir(parents=True, exist_ok=True)
        self.fallback.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            os.chmod(self.fallback, 0o600)
        except OSError:
            pass


@dataclass
class SettingsStore:
    config_path: Path = CONFIG_PATH
    specs: dict[str, Setting] = field(default_factory=dict)
    secrets: SecretStore = field(default_factory=SecretStore)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cache: dict | None = field(default=None, repr=False)

    # ---------------------------------------------------------------- schema
    def register(self, *specs: Setting) -> None:
        for s in specs:
            self.specs[s.key] = s

    def unregister_group(self, group: str) -> None:
        for k in [k for k, s in self.specs.items() if s.group == group]:
            del self.specs[k]

    # ---------------------------------------------------------------- storage
    def _config(self) -> dict:
        if self._cache is None:
            try:
                self._cache = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._config(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.config_path)

    def _env(self, spec: Setting) -> tuple[str, str] | None:
        for name in spec.env:
            v = os.environ.get(name)
            if v is not None and v.strip() != "":
                return name, v
        return None

    # ---------------------------------------------------------------- API
    def source(self, key: str) -> str:
        spec = self.specs[key]
        if self._env(spec):
            return "env"
        if spec.type == "secret":
            return "keyring" if self.secrets.get(key) else "default"
        return "config" if key in self._config() else "default"

    def get(self, key: str, default: Any = None) -> Any:
        spec = self.specs.get(key)
        if spec is None:
            return default
        env = self._env(spec)
        try:
            if env:
                return _coerce(spec, env[1])
            if spec.type == "secret":
                return self.secrets.get(key) or spec.default
            cfg = self._config()
            if key in cfg:
                return _coerce(spec, cfg[key])
        except (TypeError, ValueError):
            pass
        return spec.default

    def set(self, key: str, value: Any) -> None:
        spec = self.specs.get(key)
        if spec is None:
            raise KeyError(key)
        if self._env(spec):
            raise PermissionError(f"{key} is set by environment variable {self._env(spec)[0]}; change it there")
        with self._lock:
            if spec.type == "secret":
                self.secrets.set(key, str(value) if value else None)
                return
            cfg = self._config()
            if value is None or value == "":
                cfg.pop(key, None)
            else:
                cfg[key] = _coerce(spec, value)
            self._save()

    def describe(self, group: str | None = None) -> list[dict]:
        """Web UI uchun: sxema + joriy qiymat. Maxfiy qiymat qaytarilmaydi."""
        out = []
        for spec in self.specs.values():
            if group and spec.group != group:
                continue
            d = asdict(spec)
            src = self.source(spec.key)
            d["source"] = src
            d["env_name"] = (self._env(spec) or (None,))[0]
            if spec.type == "secret":
                d["value"] = None
                d["is_set"] = bool(self.get(spec.key))
                d["default"] = None
            else:
                d["value"] = self.get(spec.key)
            out.append(d)
        return out

    def missing_required(self) -> list[str]:
        return [k for k, s in self.specs.items() if s.required and not self.get(k)]


STORE = SettingsStore()


# --------------------------------------------------------------------------- core schema

# Tayyor provayderlar: panelda tanlanganda manzil va model avtomatik to'ldiriladi. Hammasi OpenAI-mos
# (chat/completions + tool calling) API orqali ishlaydi; ro'yxatda yo'q provayder — "custom".
PROVIDERS: dict[str, dict] = {
    "custom": {"label": "O'z serverim / boshqa OpenAI-mos API", "base_url": "", "model": "",
               "key_url": "", "note": "Istalgan OpenAI-mos manzil: vLLM, LiteLLM, korporativ gateway..."},
    "anthropic": {"label": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1",
                  "model": "claude-sonnet-5-5", "key_url": "https://console.anthropic.com/settings/keys",
                  "note": "Claude'ning OpenAI-mos API'si orqali"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "key_url": "https://platform.openai.com/api-keys", "note": ""},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.5-flash", "key_url": "https://aistudio.google.com/apikey", "note": ""},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
                 "key_url": "https://platform.deepseek.com/api_keys", "note": ""},
    "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "model": "mistral-large-latest",
                "key_url": "https://console.mistral.ai/api-keys", "note": ""},
    "xai": {"label": "xAI Grok", "base_url": "https://api.x.ai/v1", "model": "",
            "key_url": "https://console.x.ai", "note": "Modelni \"Ulanishni tekshirish\" ro'yxatidan tanlang"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
             "key_url": "https://console.groq.com/keys", "note": ""},
    "openrouter": {"label": "OpenRouter (yuzlab modellar bitta kalit bilan)", "base_url": "https://openrouter.ai/api/v1",
                   "model": "", "key_url": "https://openrouter.ai/keys",
                   "note": "Claude, GPT, Gemini, DeepSeek, Qwen, Llama... bitta kalit bilan"},
    "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "model": "",
                 "key_url": "https://api.together.ai/settings/api-keys", "note": ""},
    "ollama": {"label": "Ollama (kompyuterda, bepul)", "base_url": "http://localhost:11434/v1", "model": "",
               "key_url": "", "note": "Kalit kerak emas — istalgan so'zni yozing (masalan: ollama)"},
    "lmstudio": {"label": "LM Studio (kompyuterda, bepul)", "base_url": "http://localhost:1234/v1", "model": "",
                 "key_url": "", "note": "Kalit kerak emas — istalgan so'zni yozing"},
}


def _core() -> list[Setting]:
    S = Setting
    return [
        # LLM — istalgan OpenAI-mos provayder: o'z serveringiz, OpenAI, Gemini (OpenAI-mos), Ollama, vLLM...
        S("llm.provider", "Provayder", "choice", default="custom", env=("LLM_PROVIDER",), section="LLM",
          choices=tuple(PROVIDERS), help="Tanlansa manzil va model to'ldiriladi. Ro'yxatda yo'q bo'lsa — custom"),
        S("llm.base_url", "LLM manzili", default="", env=("LLM_BASE_URL", "LLM_GATEWAY_URL"), section="LLM",
          help="OpenAI-mos API manzili. Masalan https://api.deepseek.com/v1 yoki http://localhost:11434/v1",
          required=True),
        S("llm.api_key", "LLM API kaliti", "secret", env=("LLM_API_KEY", "LLM_GATEWAY_TOKEN"), section="LLM",
          required=True),
        S("llm.model", "Model", default="fast", env=("LLM_MODEL", "JARVIS_MODEL"), section="LLM",
          help="Tool calling'ni qo'llaydigan model. Ro'yxat \"Ulanishni tekshirish\"dan keyin chiqadi"),
        S("llm.tls_server_name", "TLS server nomi (ixtiyoriy)", default="",
          env=("LLM_TLS_SERVER_NAME", "LLM_GATEWAY_TLS_NAME"), section="LLM",
          help="Sertifikat boshqa nom uchun berilgan bo'lsa (ichki tarmoq). Tekshiruv o'chirilmaydi"),
        S("llm.verify_tls", "TLS sertifikatini tekshirish", "bool", default=True,
          env=("LLM_VERIFY_TLS", "LLM_GATEWAY_VERIFY"), section="LLM",
          help="O'chirish xavfli: faqat ishonchli ichki tarmoqda. O'z sertifikatingiz bo'lsa, quyida CA faylini bering"),
        S("llm.ca_file", "CA sertifikat fayli (ixtiyoriy)", default="", env=("LLM_CA_FILE",), section="LLM",
          help="Ichki sertifikat markazi fayli (.pem). Berilsa, tekshiruv shu fayl bilan bajariladi"),
        # Nutq (STT/TTS)
        S("speech.base_url", "Nutq serveri manzili", default="", env=("SPEECH_BASE_URL",), section="Nutq",
          help="Bo'sh bo'lsa LLM manzili ishlatiladi"),
        S("speech.api_key", "Nutq API kaliti", "secret", env=("SPEECH_API_KEY",), section="Nutq",
          help="Bo'sh bo'lsa LLM kaliti ishlatiladi"),
        S("speech.stt_path", "STT yo'li", default="/v1/stt/transcribe", env=("SPEECH_STT_PATH",), section="Nutq"),
        S("speech.tts_path", "TTS yo'li", default="/v1/tts/stream", env=("SPEECH_TTS_PATH",), section="Nutq"),
        S("speech.language", "Til", default="uz", env=("SPEECH_LANGUAGE",), section="Nutq"),
        S("speech.voice", "TTS ovozi", default="", env=("TTS_VOICE", "JARVIS_VOICE"), section="Nutq",
          help="Bo'sh = server standarti. Ro'yxat \"Ulanishni tekshirish\"dan keyin chiqadi"),
        S("speech.speed", "TTS tezligi", "float", default=1.0, env=("TTS_SPEED", "JARVIS_TTS_SPEED"), section="Nutq",
          help="0.5 - 2.0"),
        S("speech.normalize", "Matnni normallashtirish", "bool", default=True, env=("TTS_NORMALIZE",), section="Nutq",
          help="Raqam, sana, qisqartmalarni so'z bilan o'qish (12 -> o'n ikki)"),
        S("speech.seed", "Ovoz seed'i", "int", default=0, env=("TTS_SEED",), section="Nutq",
          help="0 = har safar biroz boshqacha. Boshqa son = bir xil matn doim bir xil o'qiladi"),
        # Yordamchi
        S("assistant.name", "Yordamchi nomi", default="Asalim", env=("ASSISTANT_NAME",), section="Yordamchi",
          help="Javoblarda o'zini shunday ataydi"),
        S("agent.max_steps", "Bir buyruqdagi maksimal qadam", "int", default=15, env=("AGENT_MAX_STEPS",
                                                                                    "JARVIS_MAX_STEPS"),
          section="Yordamchi"),
        S("agent.history_turns", "Eslab turiladigan suhbat navbatlari", "int", default=8,
          env=("AGENT_HISTORY_TURNS",), section="Yordamchi"),
        S("agent.restore_minutes", "Qayta ishga tushganda suhbatni tiklash (daqiqa)", "int", default=30,
          env=("AGENT_RESTORE_MINUTES",), section="Yordamchi",
          help="Shu daqiqalar ichidagi savol-javoblar yordamchi xotirasiga qaytadi. 0 = tiklamaslik"),
        S("learning.enabled", "Vazifalardan avtomatik o'rganish", "bool", default=True,
          env=("LEARNING_ENABLED",), section="Yordamchi", restart=False),
        # Ovoz
        S("voice.wake_threshold", "Uyg'otish so'zi sezgirligi", "float", default=0.4,
          env=("WAKE_THRESHOLD", "JARVIS_WAKE_THRESHOLD"), section="Ovoz", help="Kichikroq = sezgirroq (0.3-0.6)"),
        S("voice.wake_word", "Wake word (uyg'otish so'zi)", "str", default="Asalim",
          env=("WAKE_WORD", "ASSISTANT_WAKE_WORD"), section="Ovoz",
          help="Ovozni shu nomingiz bilan uyg'otasiz. Masalan: Asalim, Malik, Komron, Titu"),
        S("voice.stt_wake", "Wake wordni STT orqali tanish", "bool", default=True, env=("STT_WAKE",),
          section="Ovoz", help="Sozlangan nomni nutqdan aniqlaydi"),
        S("voice.follow_up_ms", "Javobdan keyin kutish (ms)", "int", default=10000,
          env=("FOLLOW_UP_MS", "JARVIS_FOLLOW_UP_MS"), section="Ovoz"),
        S("voice.duck", "Suhbatda boshqa ovozlarni pasaytirish", "bool", default=True, env=("DUCK",),
          section="Ovoz"),
        S("voice.duck_level", "Pasaytirish darajasi", "float", default=0.3, env=("DUCK_LEVEL", "JARVIS_DUCK_LEVEL"),
          section="Ovoz", help="0.3 = asl ovozning 30%"),
        S("voice.aec", "Echo cancellation", "bool", default=True, env=("AEC",), section="Ovoz",
          help="Kompyuter karnayidan chiqayotgan ovozni mikrofondan ayirish"),
        S("voice.ptt_key", "Push-to-talk tugmasi (Ctrl+Alt+...)", default="J", env=("PTT_KEY", "JARVIS_PTT_KEY"),
          section="Ovoz"),
        S("voice.input_device", "Mikrofon", default="", env=("INPUT_DEVICE", "JARVIS_INPUT_DEVICE"),
          section="Ovoz", help="Bo'sh = tizimdagi standart"),
        S("voice.output_device", "Karnay", default="", env=("OUTPUT_DEVICE", "JARVIS_OUTPUT_DEVICE"),
          section="Ovoz", help="Bo'sh = tizimdagi standart"),
        # Boshqaruv paneli
        S("api.enabled", "Boshqaruv paneli (Web UI)", "bool", default=True, env=("API_ENABLED",), section="Panel"),
        S("api.host", "Panel manzili", default="127.0.0.1", env=("API_HOST",), section="Panel",
          help="127.0.0.1 = faqat shu kompyuter. 0.0.0.0 = tarmoqdan ham (telefon uchun) — token majburiy"),
        S("api.port", "Panel porti", "int", default=8765, env=("API_PORT",), section="Panel"),
    ]


STORE.register(*_core())
