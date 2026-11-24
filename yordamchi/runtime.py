"""Runtime: fon event loop, agent, tool executor, kill switch va log.

UI (CLI, keyinchalik ovoz va tray) faqat `submit()` / `cancel()` bilan ishlaydi.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from typing import Awaitable, Callable

from .agent import Agent, Event, Failed, Reply, ToolFinished
from .config import Settings
from .gateway import make_llm
from .learning import NEGATIVE, Procedure, Procedures, extract, worth_learning
from .safety import GlobalHotkey
from .tools import load_all


def _com_init() -> None:
    import comtypes

    comtypes.CoInitialize()


def apply_tool_prefs() -> None:
    """Paneldan saqlangan vosita sozlamalari: o'chirilganlar va "har doim tasdiq so'rasin"."""
    from .settings import STORE
    from .tools.base import Risk, registry

    cfg = STORE._config()
    registry.disabled = set(cfg.get("tools.disabled", []))
    registry.risk_override = {n: Risk.CONFIRM for n in cfg.get("tools.always_confirm", [])}


class TurnLog:
    listeners: list = []  # panelning jonli log oqimi (har yozuv JSON qatori bilan chaqiriladi)

    def __init__(self, settings: Settings) -> None:
        settings.log_dir.mkdir(exist_ok=True)
        self.dir = settings.log_dir
        self._lock = threading.Lock()

    def write(self, _kind: str, /, **data) -> None:
        # `_kind` faqat pozitsion: data ichida "kind" kaliti bo'lsa ham to'qnashmaydi (avval jimgina yo'qolardi)
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "kind": _kind}
        rec.update({(f"{k}_" if k in ("ts", "kind") else k): v for k, v in data.items()})
        path = self.dir / f"{datetime.now():%Y-%m-%d}.jsonl"
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock, path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        for cb in list(TurnLog.listeners):
            try:
                cb(line)
            except Exception:  # noqa: BLE001
                pass


class Runtime:
    def __init__(self, settings: Settings, confirm: Callable[[str], Awaitable[bool]],
                 on_event: Callable[[Event], None], kill_hotkey: bool = True) -> None:
        self.settings = settings
        self.on_event = on_event
        self.log = TurnLog(settings)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="yordamchi-loop")
        self._thread.start()
        # Tool'lar bitta COM-initsializatsiya qilingan oqimda ketma-ket bajariladi (UI uchun xavfsiz)
        self.executor = ThreadPoolExecutor(max_workers=1, initializer=_com_init, thread_name_prefix="tools")
        from . import plugins

        plugins.load_all_plugins()
        self._bus = plugins.BUS
        apply_tool_prefs()
        self.agent = Agent(
            llm=make_llm(settings), tools=load_all(), executor=self.executor, confirm=confirm,
            model=settings.model, max_steps=settings.max_steps, history_turns=settings.history_turns,
            on_tool_hang=self._replace_executor, assistant_name=settings.assistant_name,
        )
        self.procedures = Procedures()
        self.agent.procedures = self.procedures
        self.learn_enabled = settings.learning
        self._learn_task: asyncio.Task | None = None
        self._last_learned: tuple[float, str | None, list[str]] | None = None  # (vaqt, retsept id, faktlar)
        self._task: asyncio.Task | None = None
        self.on_cancel: list[Callable[[], None]] = []  # masalan, TTS'ni to'xtatish
        self.hotkey: GlobalHotkey | None = None
        plugins.attach_runtime(self)
        if kill_hotkey:
            self.hotkey = GlobalHotkey(self.cancel)
            self.hotkey.start()
            self.hotkey.registered.wait(2)

    # ------------------------------------------------------------------ API
    def submit(self, text: str) -> Future:
        return asyncio.run_coroutine_threadsafe(self.turn(text), self.loop)

    def cancel(self) -> None:
        """Istalgan oqimdan chaqirsa bo'ladi (kill switch, Ctrl+C, ovozli 'to'xta')."""
        task = self._task
        if task and not task.done():
            self.loop.call_soon_threadsafe(task.cancel)
        for cb in self.on_cancel:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def _replace_executor(self) -> ThreadPoolExecutor:
        old = self.executor
        self.executor = ThreadPoolExecutor(max_workers=1, initializer=_com_init, thread_name_prefix="tools")
        old.shutdown(wait=False, cancel_futures=True)
        self.log.write("executor_replaced")
        return self.executor

    def call(self, coro) -> object:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def shutdown(self) -> None:
        from . import plugins

        plugins.shutdown_plugins()
        if self.hotkey:
            self.hotkey.stop()
        self.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ turn
    async def turn(self, text: str) -> Reply | Failed | None:
        """Bitta foydalanuvchi navbati. Runtime loop'ida alohida task sifatida ishga tushiring."""
        self._task = asyncio.current_task()
        self.log.write("user", text=text)
        self._bus.emit("user_text", text=text)
        self._undo_learning_if_negative(text)
        convo = [f"{m['role']}: {m['content'][:200]}" for m in self.agent.history
                 if m["role"] in ("user", "assistant") and m.get("content")][-4:]
        trace: list[tuple[str, dict, dict]] = []
        final = None
        t0 = time.perf_counter()
        try:
            async for ev in self.agent.run(text):
                if isinstance(ev, ToolFinished):
                    trace.append((ev.name, ev.args, ev.result))
                    self.log.write("tool", name=ev.name, args=ev.args, result=ev.result, ms=round(ev.ms))
                    self._bus.emit("tool_finished", name=ev.name, args=ev.args, result=ev.result)
                elif isinstance(ev, (Reply, Failed)):
                    final = ev
                    self.log.write("reply" if isinstance(ev, Reply) else "error", **asdict(ev))
                    self._bus.emit("reply" if isinstance(ev, Reply) else "error", user_text=text, **asdict(ev))
                self.on_event(ev)
        except asyncio.CancelledError:
            self.log.write("cancelled", ms=round((time.perf_counter() - t0) * 1000))
            return None
        finally:
            self._task = None
        if isinstance(final, Reply) and self.learn_enabled and worth_learning(trace):
            self._learn_task = asyncio.create_task(self._learn(convo, text, trace, final.text))
        return final

    # ------------------------------------------------------------------ learning
    async def _learn(self, convo: list[str], request: str, trace: list, reply: str) -> None:
        """Fon rejimida: bajarilgan vazifadan retsept va faktlar yozadi (javobni kechiktirmaydi)."""
        from .tools import memory

        try:
            data = await extract(self.agent.llm, self.settings.model, convo, request, trace, reply)
        except Exception as e:  # noqa: BLE001
            self.log.write("learn_error", error=f"{type(e).__name__}: {e}")
            return
        if not data or not data.get("useful") or not data.get("steps"):
            self.log.write("learn_skip", request=request[:120])
            return
        steps = [" ".join(str(s).split())[:160] for s in data["steps"][:8] if str(s).strip()]
        p = self.procedures.add(Procedure(
            task=str(data.get("task", ""))[:160], app=str(data.get("app", ""))[:40], steps=steps,
            keywords=[str(k).lower()[:30] for k in data.get("keywords", [])[:12]]))
        facts = [" ".join(str(f).split())[:200] for f in data.get("facts", [])[:4]]
        saved = [f for f in facts if memory.add_learned(f)]
        ids = [p.id]
        quirks = []
        for q in data.get("quirks", [])[:3]:  # "Word: aktivatsiya oynasi chiqadi -> Закрыть" — har safar kerak
            app, sep, note = str(q).partition(":")
            if not sep or not note.strip():
                app, note = p.app, str(q)
            app = app.strip()[:40] or p.app
            qp = self.procedures.add(Procedure(task=" ".join(note.split())[:200], app=app, steps=[], kind="quirk",
                                               keywords=[app.lower(), *[str(k).lower()[:30] for k in
                                                                        data.get("keywords", [])[:6]]]))
            ids.append(qp.id)
            quirks.append(f"{app}: {qp.task}")
        self._last_learned = (time.monotonic(), ids, saved)
        self.log.write("learned", task=p.task, app=p.app, steps=steps, facts=saved, quirks=quirks)

    def _undo_learning_if_negative(self, text: str) -> None:
        """'yo'q, bo'lmadi, qolib ketdi' — oxirgi o'rganilgan narsa noto'g'ri bo'lgan: o'chiramiz."""
        if not NEGATIVE.search(text):
            return
        t = self._learn_task
        if t and not t.done():
            t.cancel()
            self.log.write("learn_undo", reason="negative_feedback", pending=True)
            return
        last = self._last_learned
        if last and time.monotonic() - last[0] < 300:
            from .tools import memory

            _, pids, facts = last
            for pid in (pids if isinstance(pids, list) else [pids]):
                if pid:
                    self.procedures.remove(pid)
            for f in facts:
                memory.forget(f)
            self._last_learned = None
            self.log.write("learn_undo", reason="negative_feedback", procedures=pids, facts=facts)
