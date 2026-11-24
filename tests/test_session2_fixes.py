"""23:04-23:13 sinov logidan chiqqan muammolar uchun regressiya testlari."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from yordamchi.agent import MAX_SAME_CALL, Agent, Reply, ToolFinished
from yordamchi.tools import _input, load_all
from yordamchi.tools import windows as w
from yordamchi.tools.base import ToolRegistry
from yordamchi.voice.gate import is_dismiss, looks_like_noise


# --------------------------------------------------------------------------- gate heuristics

@pytest.mark.parametrize("t", ["i ua oi ooi qo'ydi", "a aiaa ish o", "ai o e", ""])
def test_obvious_noise(t):
    assert looks_like_noise(t)


@pytest.mark.parametrize("t", ["ha", "yo'q", "och", "word dasturini och", "qo'shiqni to'xtat",
                               "kompyuterni blokla men uxlamoqchiman"])
def test_real_speech_is_not_noise(t):
    assert not looks_like_noise(t)


@pytest.mark.parametrize("t,want", [
    ("yo'q senga hech narsa demadim kutish rejimiga ketishing mumkin", True),
    ("bo'ldi", True), ("Rahmat, bo'ldi.", True), ("seni chaqirmadim", True), ("kerak emas", True),
    ("bo'ldi hujjat kerakmas uni yop", False),  # bu buyruq!
    ("telegramni yop", False), ("word dasturini och", False),
])
def test_dismiss(t, want):
    assert is_dismiss(t) is want


# --------------------------------------------------------------------------- agent: context, loops, end flag

def _call(i, name, args):
    return SimpleNamespace(id=i, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ScriptLLM:
    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.sent.append(kw["messages"])
        content, calls = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


async def _no(_):
    return False


def _agent(llm, reg):
    return Agent(llm=llm, tools=reg, executor=ThreadPoolExecutor(1), confirm=_no, max_steps=20)


async def test_big_tool_results_do_not_overflow_context():
    reg = ToolRegistry()

    @reg.tool()
    def get_ui_elements() -> dict:
        return {"elements": [{"n": i, "name": "x" * 50} for i in range(80)]}  # ~5 KB

    steps = [(None, [_call(f"c{i}", "get_ui_elements", {})]) for i in range(14)] + [("tayyor", None)]
    llm = ScriptLLM(steps)
    ag = _agent(llm, reg)
    [e async for e in ag.run("qidir")]
    biggest = max(sum(len(m.get("content") or "") for m in msgs) for msgs in llm.sent)
    assert biggest < 25_000  # 14 ta to'liq natija ~70 KB bo'lardi


async def test_context_overflow_error_retries_compacted():
    reg = ToolRegistry()
    calls = {"n": 0}

    async def create(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Error code: 400 - This model's maximum context length is 32768 tokens")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    ag = _agent(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), reg)
    evs = [e async for e in ag.run("x")]
    assert isinstance(evs[-1], Reply) and calls["n"] == 2


async def test_same_call_repeated_is_blocked():
    reg = ToolRegistry()
    pressed = []

    @reg.tool()
    def press_keys(combo: str, window: str = "") -> dict:
        pressed.append(combo)
        return {}

    steps = [(None, [_call(f"c{i}", "press_keys", {"combo": "up", "window": "telegram"})]) for i in range(6)]
    llm = ScriptLLM(steps + [("topolmadim", None)])
    evs = [e async for e in _agent(llm, reg).run("faylni top")]
    assert len(pressed) == MAX_SAME_CALL
    blocked = [e.result for e in evs if isinstance(e, ToolFinished) and not e.result["ok"]]
    assert blocked and "different approach" in blocked[0]["error"]


async def test_end_conversation_flag_reaches_reply():
    reg = load_all()
    llm = ScriptLLM([(None, [_call("a", "end_conversation", {})]), ("Xo'p.", None)])
    evs = [e async for e in _agent(llm, reg).run("bo'ldi rahmat")]
    assert isinstance(evs[-1], Reply) and evs[-1].end_session


# --------------------------------------------------------------------------- tools

@pytest.fixture
def fake_windows(monkeypatch):
    wins = [w.Win(1, "Документ1 - Word (Сбой активации продукта)", "WINWORD.EXE", 1, False),
            w.Win(2, "‎101 - ГРУППА ТЕСТ – (247319)", "Telegram.exe", 2, False),
            w.Win(4, "Xurshid Rasulov - Yoshlik - YouTube - Google Chrome", "chrome.exe", 4, False)]
    monkeypatch.setattr(w, "list_top_windows", lambda: wins)
    monkeypatch.setattr(w.win32gui, "GetForegroundWindow", lambda: 2)
    return wins


@pytest.mark.parametrize("q,want", [("Word 2013", 1), ("Telegram (247291)", 2),
                                    ("101 - ГРУППА ТЕСТ – (247296)", 2), ("YouTube - Google Chrome", 4)])
def test_stale_or_app_name_window_queries(fake_windows, q, want):
    assert w.find_windows(q)[0].hwnd == want


def test_messenger_send_needs_confirmation(fake_windows):
    reg = load_all()
    t, k = reg.get("type_text"), reg.get("press_keys")
    assert t.needs_confirm({"text": "salom", "window": "2", "press_enter": True})
    assert "101 - ГРУППА ТЕСТ" in t.confirm_text({"text": "salom", "window": "2", "press_enter": True})
    assert not t.needs_confirm({"text": "salom", "window": "2"})            # faqat yozish — tasdiqsiz
    assert not t.needs_confirm({"text": "salom", "window": "1", "press_enter": True})  # Word
    assert k.needs_confirm({"combo": "enter", "window": "2"})
    assert not k.needs_confirm({"combo": "ctrl+f", "window": "2"})


def test_big_volume_drop_needs_confirmation():
    t = load_all().get("set_volume")
    assert t.needs_confirm({"percent": 3}) and t.needs_confirm({"change": -60})
    assert not t.needs_confirm({"percent": 50}) and not t.needs_confirm({"change": -20})


def test_page_keys():
    assert _input.vk_for("page_up") == _input.vk_for("pageup") == 0x21
    assert _input.vk_for("Page Down") == 0x22


def test_open_app_website_fallback(monkeypatch):
    from yordamchi.tools import apps
    opened = []
    monkeypatch.setattr(apps.index, "search", lambda q, limit=5: [])
    monkeypatch.setattr("os.startfile", lambda u: opened.append(u))
    r = load_all().get("open_app").invoke({"name": "youtube"})
    assert r["ok"] and opened == ["https://www.youtube.com"]


def test_log_accepts_kind_field(tmp_path):
    """Avval `kind=` uzatilgan hodisalar (gate, turn_done) TypeError bilan jimgina yo'qolardi."""
    from types import SimpleNamespace

    from yordamchi.runtime import TurnLog
    log = TurnLog(SimpleNamespace(log_dir=tmp_path))
    log.write("voice", ev="gate", kind="noise", verdict="ignore")
    rec = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert rec["kind"] == "voice" and rec["kind_"] == "noise" and rec["ev"] == "gate"
