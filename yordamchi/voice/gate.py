"""Eshitilgan gap JARVIS'ga qaratilganmi? — follow-up va davom oynasida musiqa, TV, boshqa odamlar bilan
gaplashish va STT shovqinini buyruq deb qabul qilmaslik uchun.

Ikki bosqich:
  1. looks_like_noise — tez evristika (LLM'siz): "i ua oi ooi", "a aiaa ish o" kabi;
  2. classify — gateway `fast` modeli JSON Schema bilan: command / answer / dismiss / not_for_me / noise.
"""

from __future__ import annotations

import asyncio
import json
import re

_APOS = re.compile(r"[ʻʼ’‘`']")
_CONS = re.compile(r"[bcdfghjklmnpqrstvxzбвгджзйклмнпрстфхцчшщқғҳ]")
_VOW = re.compile(r"[aeiouyаеёиоуыэюяў]")
SHORT_OK = {"ha", "xa", "yoq", "yo", "och", "yop", "ok", "da", "net", "нет", "да", "bas", "stop", "jim", "mayli",
            "rahmat", "keyin", "orqaga", "ol", "qo'y", "qoy", "uni", "shu", "bu", "u"}

# Aniq iboralar — gapning istalgan joyida; qisqa so'zlar — faqat 3 so'zgacha bo'lgan gapda
# ("bo'ldi hujjat kerakmas uni yop" — bu buyruq, tugatish emas)
_DISMISS_STRONG = re.compile(
    r"kutish rejimi|senga gapirmadim|seni chaqirmadim|sen bilan gaplashmayapman|hech narsa demadim|"
    r"senga emas|sendan so'ramadim", re.IGNORECASE)
_DISMISS_SHORT = re.compile(r"^(?:yaxshi |xo'p |ok )?(?:bo'?ldi|rahmat|kerak emas|kerakmas|xayr|dam ol|bas)"
                            r"(?: rahmat| bo'?ldi)?$", re.IGNORECASE)


def _norm(text: str) -> str:
    t = _APOS.sub("", text.lower())
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def looks_like_noise(text: str) -> bool:
    words = _norm(text).split()
    if not words:
        return True
    real = [w for w in words if len(w) >= 3 and _CONS.search(w) and _VOW.search(w)]
    if not real:
        return not all(w in SHORT_OK for w in words)
    return len(words) >= 3 and len(real) / len(words) < 0.4


def is_dismiss(text: str) -> bool:
    t = " ".join(re.sub(r"[^\w\s']", " ", _APOS.sub("'", text.lower())).split())
    return bool(_DISMISS_STRONG.search(t) or _DISMISS_SHORT.match(t))


_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["command", "answer", "dismiss", "not_for_me", "noise"]}},
    "required": ["kind"],
}

_PROMPT = """You filter speech captured by a room microphone for JARVIS, a voice assistant that controls the
user's computer. Speech is Uzbek (sometimes Russian/English) from speech recognition: it can contain
recognition errors, song lyrics or TV from speakers, and the user talking to other people in the room.

JARVIS's last reply was: "{last}"

Classify the new utterance:
- command: a request or question for the assistant (open/close apps, play music, type text, volume,
  files, websites, information, writing dictation the user asked for).
- answer: a reply to JARVIS's last question (yes/no, a name, a number, a choice, details).
- dismiss: the user ends the conversation or says it was not meant for JARVIS.
- not_for_me: talk to other people, comments, greetings to others, lyrics, TV.
- noise: garbled or meaningless text.

Examples:
"word dasturini och" -> command
"ha" (after a question) -> answer
"kutish rejimiga ketishing mumkin" -> dismiss
"hozir men eshikni ochaman sekin sekin" -> not_for_me
"ora uxlanglar menga xalaqit qilmanglar" -> not_for_me
"i ua oi ooi qo'ydi" -> noise
"qo'shiqni to'xtat" -> command"""


async def classify(llm, model: str, text: str, last_reply: str | None, timeout: float = 4.0) -> str:
    """Xato yoki timeout bo'lsa 'command' (foydalanuvchining haqiqiy buyrug'ini yo'qotmaslik uchun)."""
    try:
        resp = await asyncio.wait_for(llm.chat.completions.create(
            model=model, temperature=0, max_tokens=20,
            messages=[{"role": "system", "content": _PROMPT.format(last=(last_reply or "-")[:300])},
                      {"role": "user", "content": text[:500]}],
            response_format={"type": "json_schema", "json_schema": {"name": "Gate", "schema": _SCHEMA}},
        ), timeout)
        kind = json.loads(resp.choices[0].message.content or "{}").get("kind", "command")
        return kind if kind in ("command", "answer", "dismiss", "not_for_me", "noise") else "command"
    except Exception:  # noqa: BLE001
        return "command"
