"""Agent sikli: LLM tool chaqiradi -> tool bajariladi (tasdiq bilan) -> natija LLM'ga qaytadi -> javob."""

from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable

from openai import AsyncOpenAI

from .tools.base import ToolRegistry

ConfirmFn = Callable[[str], Awaitable[bool]]

CONTEXT_CHAR_BUDGET = 55_000   # ~18k token: 32k kontekstda tool sxemalari va javob uchun joy qoladi
MAX_SAME_CALL = 3              # bir xil tool+argumentni bitta so'rovda ko'pi bilan shuncha marta
# Faqat o'qiydigan tool'lar: oraliqda biror amal bajarilgan bo'lsa, qayta ko'rish takror hisoblanmaydi
# (logda Telegram'da 3 marta bosishdan keyin get_ui_elements bloklanib, vazifa yarimda qolgan).
OBSERVE_TOOLS = frozenset({
    "get_ui_elements", "read_window_text", "list_windows", "list_browser_tabs", "audio_status", "get_volume",
    "system_status", "top_processes", "list_folder", "find_files", "read_text_file", "get_clipboard",
    "search_apps", "find_youtube_videos"})

_WEEKDAYS_UZ = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]

SYSTEM_PROMPT = """You are {name}, a voice assistant that fully controls the user's Windows 11 computer.
The user speaks Uzbek (sometimes Russian or English); their words come from speech recognition, so expect
lowercase text, missing punctuation and misheard app names (e.g. "xrom" = Chrome, "telegramm" = Telegram).

Now: {now} ({weekday}). User profile folder: {home}.

How to work:
- Act with tools. Never say something is done unless a tool result confirms it (ok=true; check fields like
  verified, window_appeared, still_open). If a result shows failure, try another approach (at most 2 retries),
  then honestly say what went wrong.
- Report only what you observed. When the user asks what an app shows (a result, a message, a status),
  read it with read_window_text AFTER the action and answer from that text - never from your own
  calculation or assumption. Do the action and the check in separate steps, not in the same batch.
  If the screen shows a different value than you expected, say the value on the screen and mention that
  it differs (e.g. a key was pressed twice).
- type_text: do not combine an "=" or Enter inside the text with press_enter=true (that presses it twice).
- Use the most specific tool. Use run_powershell only when no other tool can do the job.
- Multi-step tasks: first look (find_files, list_windows, get_ui_elements), then act, then check.
- For relative dates ("kecha" = yesterday, "bugun" = today, "o'tgan hafta") compute exact ISO dates from Now.
- To work inside an app's interface: get_ui_elements -> click_element / set_element_text -> read_window_text or
  get_ui_elements again to check. Element numbers expire after every UI change.
- To write text in an app: open/focus it, then type_text.
- If the request is ambiguous (several matching apps/windows/files) ask one short question.
  If the recognised text is garbled or makes no sense, ask the user to repeat - do not guess an action.
- "uni", "o'shani", "buni" refer to the most recent thing from the conversation.
- Finish the task inside this turn. Never end with a promise like "harakat qilaman" / "ochib beraman" - either
  do it now with tools or say plainly what you could not do.
- Telegram files: a message item with "Not downloaded" is opened by click_element(n, position="left") on that
  message (first click downloads, the next click opens). Check with list_windows that a new window appeared.
- Describe exactly what you did: minimize = "kichraytirildi", close = "yopildi".
- Permissions: NEVER ask "ruxsat berasizmi?" yourself. Risky tools ask the user automatically; just call the
  tool. If the user declined a confirmation, do not retry that action and do not ask again.
- Music and video: to play a song/video call youtube_play (it opens the video and verifies sound).
  web_search only shows a results list. After media_control check sound_now; use audio_status to verify.
  Never say music is playing unless sound_verified / sound_now / audio_status confirms it.
- Browser tabs: window titles show only the ACTIVE tab. To count or find tabs use list_browser_tabs; to close
  tabs by name ("barcha youtube vkladkalarini yop") use close_browser_tabs and report closed /
  still_open_matching; to go to a tab use switch_browser_tab. ctrl+w closes only the current tab.
  close_window closes the WHOLE browser window - use it only when the user asks to close the browser.
- "this link / bu link / havola" from the browser -> get_browser_url (gives the exact URL of the current tab).
  To send it, type the URL with type_text into the chat's message field.
- Teaching: when the user wants to SHOW you how to do something ("o'rgataman", "o'rgatish rejimi",
  "ko'rsataman"), call start_teaching with a short name for it, then answer very briefly that you are
  watching. "o'rgatish tugadi" / "bo'ldi, o'rgatdim" -> stop_teaching and say how many steps were saved.
  When a TAUGHT BY USER skill below matches the request, call run_skill with its name instead of improvising.
  If run_skill fails at a step, say which step and offer to re-teach it. On paused_for_secret ask the user to
  type the password, then call run_skill again with start_step=resume_from.
- Popups: if open_app / a result shows popups or title_warnings (activation failed, update, "what's new",
  tips), close them yourself right away - do not wait for the user to ask - then do the actual task.
  Use the dialog's Close / Закрыть / X / Later / Не сейчас button. Never press buy, activate, sign-in,
  install or accept buttons; if a dialog asks a real question (save changes? delete?), ask the user.
- Contacts and chats: use the remembered facts (exact chat names) when searching, and follow the learned
  steps below when they match the app.
- YouTube search ("youtubeda X deb qidir") -> youtube_search (opens the results page). Play -> youtube_play.
- To run a program/command and see its output use run_powershell (it returns the output); typing into a
  terminal window does not let you read the result.
- Keyboard shortcuts (ctrl+n, ctrl+s, f5 ...) go to press_keys, never into type_text.
- Your own behaviour is fixed by your program: you cannot change settings like your voice, timing or
  wake word. Built in already: while the user talks to you, other apps' sound is lowered automatically and
  restored when the conversation ends. If asked to change such things, say so honestly - never pretend.
- When the user states a lasting preference (how to address them, their name, favourite apps) or says
  "eslab qol", save it with remember. Remembered facts are listed below; follow them.
- When the user ends the conversation ("bo'ldi", "rahmat, kerak emas", "kutish rejimiga o't", "seni
  chaqirmadim", "dam ol"), call end_conversation and answer with one very short phrase.
- Windows: pass the window id (number) from earlier results when you have it - titles change (unread counters,
  document names). Do not pass an app's marketing name like "Word 2013" as a window title.
- Scrolling or moving through lists: use press_keys with times (e.g. combo "pageup", times 5), not many single
  calls. If several attempts make no progress, stop and tell the user what you see.
- Messages (Telegram etc.): open the right chat first and check its title matches what the user said; the
  system asks the user to confirm before sending. After sending, verify with read_window_text.
- After youtube_play, say the actual title from the result, not what you assumed.
- Recognised text with unclear numbers (e.g. "ikki uch bir foiz"): ask, do not guess a value.

Answer style (it will be spoken aloud by TTS):
- Uzbek in Latin script, at most 2 short sentences (about 200 characters), natural spoken style, polite.
- No markdown, no lists, no emojis, no file paths unless asked; for lists give the count and at most 3 names.
- Simple chat or knowledge questions: just answer, no tools needed.{memory}"""


# --------------------------------------------------------------------------- events

@dataclass
class ToolStarted:
    name: str
    args: dict


@dataclass
class ToolFinished:
    name: str
    args: dict
    result: dict
    ms: float


@dataclass
class Reply:
    text: str
    steps: int
    ms: float
    end_session: bool = False  # ovozli suhbatni shu javobdan keyin tugatish (qo'shiq qo'yildi, blok, "bo'ldi")


@dataclass
class Failed:
    error: str


Event = ToolStarted | ToolFinished | Reply | Failed


@dataclass
class Agent:
    llm: AsyncOpenAI
    tools: ToolRegistry
    executor: Executor
    confirm: ConfirmFn
    model: str = "fast"
    max_steps: int = 15
    history_turns: int = 8
    temperature: float = 0.2
    llm_timeout: float = 90.0
    # Tool timeout bo'lsa oqim band qolishi mumkin; yangi executor beradigan callback
    on_tool_hang: Callable[[], Executor] | None = None
    assistant_name: str = "Jarvis"
    history: list[dict] = field(default_factory=list)
    procedures: object | None = None  # learning.Procedures: o'xshash vazifalarning ishlagan qadamlari
    _procs: list = field(default_factory=list, repr=False)

    def reset(self) -> None:
        self.history.clear()

    def _system(self) -> dict:
        from .tools import memory

        now = datetime.now()
        facts = memory.load()
        mem = ("\n\nRemembered about the user:\n" + "\n".join(f"- {f}" for f in facts)) if facts else ""
        if self._procs:
            from .learning import prompt_section

            mem += prompt_section(self._procs)
        return {"role": "system", "content": SYSTEM_PROMPT.format(name=self.assistant_name,
            now=now.strftime("%Y-%m-%d %H:%M"), weekday=_WEEKDAYS_UZ[now.weekday()], home=os.path.expanduser("~"),
            memory=mem)}

    def _trimmed(self) -> list[dict]:
        users = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(users) > self.history_turns:
            self.history = self.history[users[-self.history_turns]:]
        return self.history

    def _context(self, aggressive: bool = False) -> list[dict]:
        """Modelga yuboriladigan tarix: eski tool natijalari qisqartiriladi, umumiy hajm byudjetda saqlanadi.
        (Gateway modelining konteksti 32k token; UI daraxtlari tez to'ldiradi.)"""
        h = self._trimmed()
        last_user = max((i for i, m in enumerate(h) if m["role"] == "user"), default=0)
        cur_tools = [i for i in range(last_user, len(h)) if h[i]["role"] == "tool"]
        keep_full = set(cur_tools[-(1 if aggressive else 2):])
        cur_lim, old_lim = (300, 200) if aggressive else (800, 300)
        out: list[dict] = []
        for i, m in enumerate(h):
            if m["role"] == "tool" and i not in keep_full:
                lim = cur_lim if i > last_user else old_lim
                c = m.get("content") or ""
                if len(c) > lim:
                    m = {**m, "content": c[:lim] + ' ..."(truncated)"'}
            out.append(m)
        budget = 25_000 if aggressive else CONTEXT_CHAR_BUDGET

        def size(ms):
            return sum(len(x.get("content") or "") + len(json.dumps(x.get("tool_calls", ""))) for x in ms)

        while size(out) > budget:
            users = [i for i, m in enumerate(out) if m["role"] == "user"]
            if len(users) < 2:
                break
            out = out[users[1]:]  # eng eski navbatni tashlaymiz (joriy navbat qoladi)
        return out

    async def _complete(self, step: int):
        for attempt in (0, 1):
            try:
                return await asyncio.wait_for(self.llm.chat.completions.create(
                    model=self.model,
                    messages=[self._system(), *self._context(aggressive=attempt == 1)],
                    tools=self.tools.schemas(),
                    tool_choice="auto",
                    temperature=self.temperature,
                ), self.llm_timeout)
            except Exception as e:  # noqa: BLE001
                if attempt == 0 and "context length" in str(e).lower():
                    continue  # qattiqroq siqib qayta urinamiz
                raise

    async def run(self, user_text: str) -> AsyncIterator[Event]:
        t0 = time.perf_counter()
        start = len(self.history)
        prev_user = next((m["content"] for m in reversed(self.history) if m["role"] == "user"), "")
        self.history.append({"role": "user", "content": user_text})
        self._procs = []
        if self.procedures is not None:
            try:  # "uni telegramdan yubor" — oldingi gap ham kontekst beradi
                self._procs = self.procedures.relevant(f"{user_text} {prev_user}")
            except Exception:  # noqa: BLE001
                self._procs = []
        used_tools = False
        seen: dict[str, int] = {}
        end_session = False
        try:
            for step in range(1, self.max_steps + 1):
                resp = await self._complete(step)
                msg = resp.choices[0].message
                calls = msg.tool_calls or []
                entry: dict = {"role": "assistant", "content": msg.content or ""}
                if calls:
                    entry["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                        for c in calls]
                self.history.append(entry)

                if not calls:
                    if used_tools and self._procs:
                        try:
                            self.procedures.mark_used([p.id for p in self._procs])
                        except Exception:  # noqa: BLE001
                            pass
                    text = (msg.content or "").strip() or "Bajarildi."
                    yield Reply(text, step, (time.perf_counter() - t0) * 1000, end_session=end_session)
                    return

                used_tools = True
                for c in calls:
                    try:
                        args = json.loads(c.function.arguments or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be an object")
                    except (json.JSONDecodeError, ValueError) as e:
                        args, result, ms = {}, {"ok": False, "error": f"Invalid JSON arguments: {e}"}, 0.0
                    else:
                        key = c.function.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                        seen[key] = seen.get(key, 0) + 1
                        yield ToolStarted(c.function.name, args)
                        ts = time.perf_counter()
                        if seen[key] > MAX_SAME_CALL:
                            result = {"ok": False, "error": (
                                f"You already did exactly this {MAX_SAME_CALL} times in this request without "
                                "reaching the goal. Stop repeating it: try a clearly different approach, or tell the "
                                "user what you found and ask how to continue.")}
                        else:
                            result = await self._execute(c.function.name, args)
                        ms = (time.perf_counter() - ts) * 1000
                        if result.get("end_conversation"):
                            end_session = True
                        if c.function.name not in OBSERVE_TOOLS:  # holat o'zgardi: ko'rish hisoblagichlari 0
                            for k in [k for k in seen if k.split("{", 1)[0] in OBSERVE_TOOLS]:
                                del seen[k]
                    yield ToolFinished(c.function.name, args, result, ms)
                    self.history.append({"role": "tool", "tool_call_id": c.id,
                                         "content": json.dumps(result, ensure_ascii=False, default=str)[:5000]})

            yield Reply("Bu vazifa juda ko'p qadam talab qildi, shu yerda to'xtatdim. Qanday davom etay?",
                        self.max_steps, (time.perf_counter() - t0) * 1000)
        except asyncio.CancelledError:
            self._rollback(start, user_text, "[Vazifa foydalanuvchi tomonidan to'xtatildi]")
            raise
        except Exception as e:  # noqa: BLE001
            self._rollback(start, user_text, "[Xatolik tufayli vazifa bajarilmadi]")
            yield Failed(f"{type(e).__name__}: {e}")

    def _rollback(self, start: int, user_text: str, note: str) -> None:
        """Tarixda javobsiz tool_call qolmasligi uchun shu navbatni qisqa yozuv bilan almashtiradi."""
        del self.history[start:]
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": note})

    async def _execute(self, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool '{name}'"}
        if tool.needs_confirm(args):
            if not await self.confirm(tool.confirm_text(args)):
                return {"ok": False, "declined": True,
                        "error": "The user declined this action. Do not retry it; tell the user it was cancelled."}
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self.executor, tool.invoke, args)
        try:
            return await asyncio.wait_for(fut, tool.timeout)
        except TimeoutError:
            # Thread hali ham band — keyingi tool'lar uning orqasida qolmasligi uchun yangi executor
            if self.on_tool_hang:
                self.executor = self.on_tool_hang()
            return {"ok": False, "error": f"Tool timed out after {tool.timeout:.0f} s (the app may be frozen)"}
