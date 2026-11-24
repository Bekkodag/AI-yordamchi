"""O'rganish xotirasi: muvaffaqiyatli bajarilgan vazifalardan qayta ishlatiladigan "retsept" va faktlar.

Masalan, "telegramni och, Ali Valiyevni top" bajarilgach, bu yerda saqlanadi:
  retsept: Telegram'da kontakt topish -> open_app telegram; get_ui_elements; set_element_text(Search) ...
  fakt:    "Ali Valiyev" = Telegram'dagi "Acme Ali Valiyev" chati
Keyingi safar "chromedagi linkni telegramdan Ali Valiyevga yubor" deyilsa, mos retseptlar va faktlar
system promptga qo'shiladi va agent ularni birlashtirib bajaradi.

Retsept fon rejimida (javobdan keyin) LLM yordamida yoziladi — foydalanuvchi kutmaydi.
Keyingi gap "yo'q, bo'lmadi, qolib ketdi" kabi bo'lsa, oxirgi o'rganilgan narsa o'chiriladi.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .tools._fuzzy import norm

PATH = ROOT / "data" / "procedures.json"
MAX_PROCEDURES = 150
PROMPT_CHAR_LIMIT = 1800

NEGATIVE = re.compile(
    r"^\s*(yo'?q|yoq|нет|no)\b|bo'?lmadi|ishlamadi|noto'?g'?ri|qolib ketdi|xato qilding|unaqa emas|"
    r"topmading|yubormading|ochmading|yopmading|qilmading|yangilamading", re.IGNORECASE)

_STOP = {"va", "uni", "bu", "shu", "deb", "ham", "men", "menga", "qil", "the", "and", "for", "with", "ni", "ga",
         "dan", "da", "bilan", "keyin", "uchun", "och", "ochib"}


def stems(text: str) -> set[str]:
    """O'zbekcha qo'shimchalarga chidamli kalitlar: 'telegramdan' ~ 'telegram' (5 harfli boshlanma)."""
    out = set()
    for w in norm(text).split():
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w[:5])
    return out


@dataclass
class Procedure:
    task: str
    app: str
    steps: list[str]
    keywords: list[str]
    id: str = field(default_factory=lambda: f"p{int(time.time() * 1000)}")
    uses: int = 0
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_used: str = ""
    kind: str = "task"   # "task" — vazifa retsepti; "quirk" — ilovaning o'ziga xosligi (har ochilganda kerak)

    def key(self) -> set[str]:
        return stems(" ".join([self.task, self.app, *self.keywords]))

    def line(self) -> str:
        if self.kind == "quirk":
            return f"- {self.app}: {self.task}"
        if self.kind == "taught":
            return f"- TAUGHT BY USER: '{self.task}' ({self.app}) -> call run_skill(name='{self.task}')"
        return f"- {self.task} ({self.app}): " + "; ".join(self.steps)


class Procedures:
    def __init__(self, path: Path = PATH) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[Procedure]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        out = []
        for d in data if isinstance(data, list) else []:
            try:
                out.append(Procedure(**d))
            except TypeError:
                continue
        return out

    def _save(self, items: list[Procedure]) -> None:
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps([p.__dict__ for p in items], ensure_ascii=False, indent=1),
                             encoding="utf-8")

    def add(self, p: Procedure) -> Procedure:
        """O'xshash retsept bo'lsa (bir xil ilova, kalitlar ko'p mos) — yangisi bilan almashtiradi."""
        with self._lock:
            items = self.load()
            k = p.key()
            for old in items:
                ok = old.key()
                if old.kind != p.kind:
                    continue
                if old.app.lower() == p.app.lower() and k and ok and len(k & ok) / len(k | ok) >= 0.6:
                    p.uses = old.uses
                    items.remove(old)
                    break
            items.append(p)
            if len(items) > MAX_PROCEDURES:  # kam ishlatilgan eskilarini tashlaymiz
                items.sort(key=lambda x: (x.uses, x.last_used or x.created))
                items = items[len(items) - MAX_PROCEDURES:]
            self._save(items)
        return p

    def remove(self, pid: str) -> bool:
        with self._lock:
            items = self.load()
            keep = [p for p in items if p.id != pid]
            self._save(keep)
            return len(keep) != len(items)

    def forget(self, text: str) -> int:
        q = norm(text)
        with self._lock:
            items = self.load()
            keep = [p for p in items if q not in norm(p.task + " " + p.app + " " + " ".join(p.keywords))]
            self._save(keep)
            return len(items) - len(keep)

    def relevant(self, text: str, k: int = 3) -> list[Procedure]:
        """Eng mos k ta retsept + so'rovda tilga olingan ilovalarning BARCHA o'ziga xosliklari (quirk)."""
        q = stems(text)
        if not q:
            return []
        scored, quirks = [], []
        for p in self.load():
            if p.kind == "quirk":
                if q & stems(" ".join([p.app, *p.keywords])):
                    quirks.append(p)
                continue
            hit = len(q & p.key())
            if hit:
                scored.append((hit + (2 if p.kind == "taught" and hit >= 2 else 0), p.uses, p))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return quirks[:4] + [p for hit, _, p in scored[:k] if hit >= 1]

    def summary(self) -> dict:
        items = self.load()
        tasks = [p for p in items if p.kind == "task"]
        quirks = [p for p in items if p.kind == "quirk"]
        taught = [p for p in items if p.kind == "taught"]
        by_app: dict[str, int] = {}
        for p in tasks:
            by_app[p.app or "?"] = by_app.get(p.app or "?", 0) + 1
        return {"recipes": len(tasks), "app_quirks": len(quirks), "taught_skills": [p.task for p in taught],
                "recipes_by_app": by_app,
                "quirks": [f"{p.app}: {p.task}" for p in quirks]}

    def mark_used(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            items = self.load()
            now = datetime.now().isoformat(timespec="seconds")
            for p in items:
                if p.id in ids:
                    p.uses += 1
                    p.last_used = now
            self._save(items)


def prompt_section(procs: list[Procedure]) -> str:
    if not procs:
        return ""
    quirks = [p.line() for p in procs if p.kind == "quirk"]
    lines, size = [], 0
    for p in procs:
        if p.kind == "quirk":
            continue
        ln = p.line()
        if size + len(ln) > PROMPT_CHAR_LIMIT:
            break
        lines.append(ln)
        size += len(ln)
    out = ""
    if quirks:
        out += ("\n\nKnown app quirks - handle them automatically every time, without the user asking:\n"
                + "\n".join(quirks))
    if lines:
        out += ("\n\nHow you successfully did similar tasks before (reuse and combine these steps when they fit; "
                "still check results):\n" + "\n".join(lines))
    return out


# --------------------------------------------------------------------------- extraction (LLM)

_SCHEMA = {
    "type": "object",
    "properties": {
        "useful": {"type": "boolean"},
        "task": {"type": "string"},
        "app": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "quirks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["useful", "task", "app", "steps", "facts", "keywords", "quirks"],
}

_INSTRUCTIONS = """You write reusable know-how for a Windows voice assistant agent.
Below is the user's latest request (with a little earlier conversation for context), the tool calls the agent
made for it (in order, with short results) and its final answer.

Return JSON:
- useful: false if the task was trivial (one simple tool), failed, the answer admits it could not do it,
  or nothing would help next time. Otherwise true.
- task: one generic line in English with placeholders, e.g. "Find a contact in Telegram and open the chat
  with <name>", "Send the current Chrome link to <contact> in Telegram".
- app: main application (e.g. "Telegram", "Chrome", "Word").
- steps: 3-8 short imperative steps that WORKED, naming the tools and the exact UI field / element names seen
  in results (e.g. "set_element_text on the 'Search' field with <name>, press_enter=true"). Skip failed
  attempts and dead ends. Use placeholders, not the concrete values.
- facts: concrete user-specific facts worth remembering for later, e.g.
  'Contact "Ali Valiyev" = Telegram chat "Acme Ali Valiyev" (search shows 2 results, the first one)'.
  Only facts confirmed by tool results. Empty list if none.
- keywords: 5-10 lowercase words a future request about this would contain, in Uzbek, Russian and English
  (e.g. "telegram", "yubor", "send", "chat", "qidir", "link", "havola").
- quirks: obstacles of an APP that will happen again every time and that the agent had to handle (or the
  user had to tell it to handle), written as "<App>: <what happens> -> <what to do>", e.g.
  "Word: on start shows the 'Сбой активации продукта' dialog -> close it with its 'Закрыть' button, then work".
  Only things confirmed in the tool results. Empty list if none."""


def _short(result: dict) -> str:
    keep = {k: result[k] for k in ("ok", "error", "clicked", "field", "verified", "title", "url", "opened",
                                   "app", "closed", "sound_verified") if k in result}
    w = result.get("window") or result.get("active_window")
    if isinstance(w, dict) and w.get("title"):
        keep["window"] = w["title"]
    return json.dumps(keep, ensure_ascii=False)[:220]


def trace_lines(trace: list[tuple[str, dict, dict]]) -> list[str]:
    return [f"{i}. {name}({json.dumps(args, ensure_ascii=False)[:160]}) -> {_short(res)}"
            for i, (name, args, res) in enumerate(trace, 1)]


async def extract(llm, model: str, convo: list[str], request: str, trace: list[tuple[str, dict, dict]],
                  reply: str, timeout: float = 30.0) -> dict | None:
    content = (
        "Earlier conversation:\n" + ("\n".join(convo[-4:]) or "(none)")
        + f"\n\nLatest request: {request}\n\nTool calls:\n" + "\n".join(trace_lines(trace))
        + f"\n\nFinal answer: {reply}")
    resp = await asyncio.wait_for(llm.chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": _INSTRUCTIONS}, {"role": "user", "content": content}],
        response_format={"type": "json_schema", "json_schema": {"name": "procedure", "schema": _SCHEMA}},
    ), timeout)
    try:
        return json.loads(resp.choices[0].message.content or "")
    except (json.JSONDecodeError, TypeError):
        return None


def worth_learning(trace: list[tuple[str, dict, dict]]) -> bool:
    """Kamida 2 ta amal va oxirgi amal muvaffaqiyatli bo'lsa. Faqat ko'rish yoki bitta amal — o'rganilmaydi."""
    if len(trace) < 2:
        return False
    return bool(trace[-1][2].get("ok"))
