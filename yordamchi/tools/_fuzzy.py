"""Nomlarni noaniq moslash (STT/LLM bergan nom bilan haqiqiy dastur/oyna nomini solishtirish)."""

from __future__ import annotations

import difflib
import re
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

_APOS = re.compile(r"[ʻʼ’‘`']")
_NON = re.compile(r"[^\w\s]+", re.UNICODE)


def norm(s: str) -> str:
    s = _APOS.sub("", s.lower())
    s = _NON.sub(" ", s)
    return " ".join(s.split())


def score(query: str, candidate: str) -> float:
    if query.strip().lower() == candidate.strip().lower():
        return 1.0
    q, c = norm(query), norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:  # faqat belgilar farq qiladi: "notepad" vs "Notepad++"
        return 0.97
    words = c.split()
    if q in words or c.startswith(q + " "):
        return 0.92
    if c.startswith(q):
        return 0.88
    if q in c:
        return 0.8
    qt = q.split()
    if len(qt) > 1 and all(t in c for t in qt):
        return 0.78
    best_word = max((difflib.SequenceMatcher(None, q, w).ratio() for w in words), default=0.0)
    whole = difflib.SequenceMatcher(None, q, c).ratio()
    return max(best_word, whole) * 0.75


def rank(query: str, items: Iterable[T], keys: Callable[[T], Iterable[str]],
         threshold: float = 0.55) -> list[tuple[float, T]]:
    out = []
    for it in items:
        s = max((score(query, k) for k in keys(it) if k), default=0.0)
        if s >= threshold:
            out.append((s, it))
    out.sort(key=lambda x: x[0], reverse=True)
    return out
