"""Doimiy xotira: foydalanuvchi afzalliklari (qanday murojaat qilish, ismlar, odatlar) — qayta ishga
tushirishdan keyin ham saqlanadi va har navbatda system promptga qo'shiladi."""

from __future__ import annotations

import json
import threading
from typing import Annotated

from ..config import ROOT
from ._fuzzy import norm
from .base import ToolError, registry

PATH = ROOT / "data" / "memory.json"
_LOCK = threading.Lock()
MAX_FACTS = 60


def load() -> list[str]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(facts: list[str]) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")


@registry.tool()
def remember(fact: Annotated[str, "Short fact in English or Uzbek, e.g. 'Address the user as Aziz aka'"]) -> dict:
    """Save a lasting fact/preference about the user (how to address them, favourite apps, names).
    Use when the user asks to remember something or states a preference. Replaces a contradicting older fact."""
    f = " ".join(fact.split())
    if not f:
        raise ToolError("Empty fact")
    with _LOCK:
        facts = [x for x in load() if norm(x) != norm(f)]
        facts.append(f)
        _save(facts[-MAX_FACTS:])
    return {"saved": f, "total": len(facts)}


@registry.tool()
def forget(text: Annotated[str, "Words identifying the fact(s) to remove"]) -> dict:
    """Remove remembered facts that contain the given words."""
    q = norm(text)
    with _LOCK:
        facts = load()
        keep = [x for x in facts if q not in norm(x)]
        _save(keep)
    from ..learning import Procedures

    procs = Procedures().forget(text)  # o'rganilgan retseptlar ham
    return {"removed": len(facts) - len(keep), "removed_procedures": procs}


def add_learned(fact: str) -> bool:
    """Vazifalardan avtomatik o'rganilgan fakt (masalan kontakt nomi). O'xshash eski fakt yangisi bilan
    almashtiriladi, shuning uchun bir kontakt haqida takroriy yozuvlar to'planmaydi."""
    from ..learning import stems

    f = " ".join(fact.split())
    if len(f) < 8:
        return False
    k = stems(f)
    with _LOCK:
        facts = []
        for x in load():
            xk = stems(x)
            if norm(x) == norm(f) or (k and xk and len(k & xk) / len(k | xk) >= 0.6):
                continue
            facts.append(x)
        facts.append(f)
        _save(facts[-MAX_FACTS:])
    return True


@registry.tool()
def skills_overview() -> dict:
    """What JARVIS knows: number of built-in tools, learned task recipes, app quirks it handles automatically,
    remembered facts. Use when the user asks how many skills you have or what you have learned."""
    from ..learning import Procedures

    return {"built_in_tools": len(registry._tools), **Procedures().summary(), "remembered_facts": len(load())}
