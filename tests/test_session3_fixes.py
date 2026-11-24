"""22.11 23:31 sinovi va 23.11 kunduzgi logdan chiqqan muammolar uchun regressiya testlari."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from yordamchi.agent import Agent, ToolFinished
from yordamchi.agent import Reply as AgentReply
from yordamchi.config import Settings
from yordamchi.tools import load_all
from yordamchi.tools.base import ToolRegistry
from yordamchi.tools.browser import clean_tab_title, tab_matches
from yordamchi.voice.assistant import State, VoiceAssistant, VoiceConfig, parse_yes
from yordamchi.voice.speech import soften_acronyms


# --------------------------------------------------------------------------- agent loop guard

def _call(i, name, args):
    return SimpleNamespace(id=i, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ScriptLLM:
    def __init__(self, script):
        self.script = list(script)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        content, calls = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


async def _no(_):
    return False


async def test_observing_again_after_an_action_is_not_a_loop():
    """Logda: Telegram'da 3 ta click orasidagi get_ui_elements bloklangan, vazifa yarimda qolgan."""
    reg = ToolRegistry()

    @reg.tool()
    def get_ui_elements(window: str = "") -> dict:
        return {"elements": []}

    @reg.tool()
    def click_element(n: int) -> dict:
        return {"clicked": n}

    steps = []
    for i in range(5):
        steps += [(None, [_call(f"g{i}", "get_ui_elements", {"window": "1"})]),
                  (None, [_call(f"c{i}", "click_element", {"n": i})])]
    llm = ScriptLLM(steps + [("tayyor", None)])
    ag = Agent(llm=llm, tools=reg, executor=ThreadPoolExecutor(1), confirm=_no, max_steps=20)
    evs = [e async for e in ag.run("faylni och")]
    assert all(e.result["ok"] for e in evs if isinstance(e, ToolFinished))
    assert isinstance(evs[-1], AgentReply)


async def test_observing_without_action_is_still_limited():
    reg = ToolRegistry()

    @reg.tool()
    def get_ui_elements(window: str = "") -> dict:
        return {"elements": []}

    llm = ScriptLLM([(None, [_call(f"g{i}", "get_ui_elements", {"window": "1"})]) for i in range(5)]
                    + [("topolmadim", None)])
    ag = Agent(llm=llm, tools=reg, executor=ThreadPoolExecutor(1), confirm=_no, max_steps=20)
    evs = [e async for e in ag.run("x")]
    assert [e.result["ok"] for e in evs if isinstance(e, ToolFinished)] == [True, True, True, False, False]


# --------------------------------------------------------------------------- tools

def test_unknown_tool_argument_is_reported():
    r = load_all().get("type_text").invoke({"text": "x", "press_keys": "ctrl+a"})
    assert not r["ok"] and "press_keys" in r["error"]


@pytest.mark.parametrize("raw,want", [
    ('Вкладка "радиодеталь - YouTube" неактивна\xa0– освобождено 263 МБ', "радиодеталь - YouTube"),
    ("Xurshid Rasulov - Yoshlik - YouTube - Воспроизведение аудио", "Xurshid Rasulov - Yoshlik - YouTube"),
    ("Claude", "Claude"),
])
def test_clean_tab_title(raw, want):
    assert clean_tab_title(raw) == want


def test_tab_matches():
    assert tab_matches("Редкие радиодетали СССР - YouTube", "youtube")
    assert tab_matches("(869) XAYR IYUL | SAHAR GURUHI - YouTube", "sahar guruhi")
    assert not tab_matches("ChatGPT", "youtube")
    assert not tab_matches("ChatGPT", "")


def test_new_tools_registered():
    reg = load_all()
    for name in ("list_browser_tabs", "close_browser_tabs", "switch_browser_tab", "youtube_search"):
        assert reg.get(name)
    assert "position" in reg.get("click_element").parameters["properties"]


def test_youtube_search_opens_results(monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda u: opened.append(u))
    r = load_all().get("youtube_search").invoke({"query": "g'iyosbek narzullayev"})
    assert r["ok"] and opened[0].startswith("https://www.youtube.com/results?search_query=g%27iyosbek")


# --------------------------------------------------------------------------- voice

def test_soften_acronyms():
    """Gateway TTS 'GCC', 'GC', 'CC' uchun bo'sh audio qaytaradi — javob umuman eshitilmagan."""
    assert soften_acronyms("Kompyuterda GCC yo'q, USB bor, C tili") == "Kompyuterda Gcc yo'q, Usb bor, C tili"


def test_yes_variants():
    assert parse_yes("yes te") and parse_yes("ha bajar") and not parse_yes("yo'q")


def _va(**cfg) -> VoiceAssistant:
    s = Settings(gateway_url="https://x.invalid", gateway_token="t", tls_name=None, verify=True)
    va = VoiceAssistant(s, VoiceConfig(wake=False, stt_wake=False, duck=False, **cfg), status=lambda m: None)
    va.runtime = None
    va._beep = lambda pcm: None
    return va


async def test_wake_during_confirmation_does_not_cancel_the_task():
    """Logda: tasdiq savoli paytida "Jarvis, yes" -> barge-in buyruqni bekor qilgan, "yes" yangi buyruq bo'lgan."""
    va = _va()
    try:
        va._session = asyncio.create_task(asyncio.sleep(5))
        va._confirming = True
        va.state = State.SPEAKING
        va._on_wake(0.98, "wake_word")
        await asyncio.sleep(0)
        assert not va._session.cancelled() and not va._session.done()
        va._confirming = False
        va._on_wake(0.98, "wake_word")  # odatiy barge-in hali ham ishlaydi
        await asyncio.sleep(0)
        va._session.cancel()
    finally:
        va.player.close()


class _FakeRuntime:
    def __init__(self):
        self.turns = []
        self.agent = SimpleNamespace(llm=None)

    async def turn(self, text, source="text"):
        self.turns.append(text)
        return AgentReply("ok", 1, 1.0)


async def _session(va, heard, verdicts, gate_first):
    rt = _FakeRuntime()
    va.runtime = rt
    va.ev = lambda *a, **k: None
    it, vit = iter(heard), iter(verdicts)

    async def listen(_):
        try:
            return next(it)
        except StopIteration:
            return None

    async def transcribe(audio):
        return audio

    async def gate(_, after_wake=False):
        return next(vit)

    async def say(_):
        return True

    va.listen, va.transcribe, va._gate, va.say = listen, transcribe, gate, say
    va._session = asyncio.current_task()
    await va._run_session(gate_first=gate_first)
    return rt.turns


async def test_false_wake_first_utterance_is_filtered():
    """Bugun 11:36: wake word uydagi gapdan uyg'ondi, birinchi gap to'g'ridan-to'g'ri agentga ketdi."""
    va = _va()
    try:
        turns = await _session(va, ["tanishdi mayli bo'shkei arnashimimon", "assalomu alaykum yaxshimisiz"],
                               ["ignore", "ignore"], gate_first=True)
        assert turns == []
    finally:
        va.player.close()


async def test_real_command_after_wake_word_passes_gate():
    va = _va()
    try:
        turns = await _session(va, ["qo'shiqni to'xtat"], ["ok"], gate_first=True)
        assert turns == ["qo'shiqni to'xtat"]
    finally:
        va.player.close()


# --------------------------------------------------------------------------- learning memory

def test_procedures_store_relevance_and_dedupe(tmp_path):
    from yordamchi.learning import Procedure, Procedures, prompt_section

    P = Procedures(tmp_path / "p.json")
    P.add(Procedure(task="Search for a contact in Telegram and open the chat with <name>", app="Telegram",
                    steps=["open_app telegram", "set_element_text on 'Search'"],
                    keywords=["telegram", "qidir", "search", "chat"]))
    P.add(Procedure(task="Play a song on YouTube", app="Chrome", steps=["youtube_play <query>"],
                    keywords=["youtube", "qo'shiq", "qo'y"]))
    rel = P.relevant("chromedagi bu linkni telegramdan ali valiyevga yubor")
    assert rel and rel[0].app == "Telegram"  # 'telegramdan' ~ 'telegram'
    assert "Search" in prompt_section(rel)
    # o'xshash retsept qayta o'rganilsa — almashtiriladi, ko'paymaydi
    P.add(Procedure(task="Search for a contact in Telegram and open chat <name>", app="Telegram",
                    steps=["new"], keywords=["telegram", "qidir", "search", "chat"]))
    assert len(P.load()) == 2 and any(p.steps == ["new"] for p in P.load())
    assert P.forget("youtube") == 1


def test_learned_fact_replaces_similar(tmp_path, monkeypatch):
    from yordamchi.tools import memory

    monkeypatch.setattr(memory, "PATH", tmp_path / "m.json")
    memory.add_learned("Contact 'ali valiyev' = Telegram chat 'Acme Ali Valiyev'")
    memory.add_learned("Contact 'Ali Valiyev' = Telegram chat 'Acme Ali Valiyev' (first result)")
    assert len(memory.load()) == 1 and "first result" in memory.load()[0]


@pytest.mark.parametrize("t,neg", [("yo'q yangilamading qaytadan", True), ("yana bitta qolib ketdi", True),
                                   ("bo'lmadi", True), ("word dasturini och", False), ("yozib qo'y", False)])
def test_negative_feedback(t, neg):
    from yordamchi.learning import NEGATIVE

    assert bool(NEGATIVE.search(t)) is neg


def test_worth_learning():
    from yordamchi.learning import worth_learning

    ok, bad = {"ok": True}, {"ok": False}
    assert worth_learning([("a", {}, ok), ("b", {}, ok)])
    assert not worth_learning([("a", {}, ok)]) and not worth_learning([("a", {}, ok), ("b", {}, bad)])


def test_messenger_message_field_send_needs_confirmation(monkeypatch):
    from yordamchi.tools import windows as w

    tg = w.Win(7, "Acme Ali Valiyev – (247317)", "Telegram.exe", 7, False)
    w._ELEMENTS_WIN.clear()
    w._ELEMENTS_WIN.update(win=tg, names={20: "Search", 40: "Write a message…"})
    t = load_all().get("set_element_text")
    assert t.needs_confirm({"n": 40, "text": "https://kun.uz", "press_enter": True})
    assert "Acme Ali Valiyev" in t.confirm_text({"n": 40, "text": "https://kun.uz", "press_enter": True})
    assert not t.needs_confirm({"n": 20, "text": "ali", "press_enter": True})   # qidiruv — tasdiqsiz
    assert not t.needs_confirm({"n": 40, "text": "salom"})                       # Enter'siz — yuborilmaydi
    w._ELEMENTS_WIN.update(win=w.Win(8, "Doc - Word", "WINWORD.EXE", 8, False))
    assert not t.needs_confirm({"n": 40, "text": "x", "press_enter": True})
    w._ELEMENTS_WIN.clear()


# --------------------------------------------------------------------------- 24.11: bolaning "Darvis"i

@pytest.mark.parametrize("t", ["darvis", "darbis", "darveys", "narvis", "jariiz", "jarviiz", "darves", "Jarvis"])
def test_kid_wake_variants(t):
    from yordamchi.voice.assistant import _jarvis_like

    assert _jarvis_like(t)


@pytest.mark.parametrize("t", ["darvesh", "dervish", "javob", "davlat", "davom", "parvoz", "qo'shiqni"])
def test_not_wake_words(t):
    from yordamchi.voice.assistant import _jarvis_like

    assert not _jarvis_like(t)


def test_wake_in_the_middle_or_end():
    from yordamchi.voice.assistant import find_wake_anywhere

    assert find_wake_anywhere("bo'ldi qo'shiqni o'chir darvis qo'shiqni o'chir bo'ldi") == (
        True, "bo'ldi qo'shiqni o'chir qo'shiqni o'chir bo'ldi")
    assert find_wake_anywhere("yangi yil haqidagi qo'shiqni darves") == (True, "yangi yil haqidagi qo'shiqni")
    assert find_wake_anywhere("darvesh mast aylading")[0] is False  # qo'shiq so'zi
    assert find_wake_anywhere("bugun bozorga bordik")[0] is False


# --------------------------------------------------------------------------- echo cancellation

def test_aec_removes_echo_but_keeps_speech():
    import numpy as np

    from yordamchi.voice.aec import EchoCanceller

    rng = np.random.default_rng(1)
    n = 16000 * 8
    ref = (rng.standard_normal(n) * 3000).astype(np.float32)              # karnaydan chiqqan "qo'shiq"
    ir = np.concatenate([np.zeros(16 * 60), rng.standard_normal(800) * np.exp(-np.arange(800) / 150)])
    ir /= np.abs(ir).sum() / 1.2
    echo = np.convolve(ref, ir)[:n]
    speech = np.zeros(n, np.float32)
    t = np.arange(16000) / 16000
    speech[6 * 16000:7 * 16000] = 2500 * np.sin(2 * np.pi * 220 * t) * np.sin(2 * np.pi * 3 * t)  # "odam"
    mic = (echo + speech).astype(np.int16)
    out = np.frombuffer(EchoCanceller(None, noise_suppression=False).process_pair(
        mic.tobytes(), ref.astype(np.int16).tobytes()), np.int16).astype(np.float32)
    seg = slice(3 * 16000, 6 * 16000)
    erle = 10 * np.log10((mic[seg].astype(np.float32) ** 2).mean() / (out[seg] ** 2).mean())
    assert erle > 10, erle                                                # echo kamida 10 dB pasaydi
    sp = slice(6 * 16000 + 4000, 7 * 16000 - 4000)
    assert np.sqrt((out[sp] ** 2).mean()) > 0.3 * np.sqrt((speech[sp] ** 2).mean())  # odam ovozi qoldi


def test_aec_process_keeps_length_and_survives_without_reference():
    from yordamchi.voice.aec import EchoCanceller

    a = EchoCanceller(None)
    out = b"".join(a.process(bytes(1024)) for _ in range(10))
    assert abs(len(out) - 10240) < 320 and a.errors == 0


def test_mic_processor_errors_do_not_stop_audio():
    import asyncio as _a

    from yordamchi.voice.audio import FRAME_BYTES, Mic

    m = Mic(_a.new_event_loop())

    def bad(_):
        raise RuntimeError("x")
    m.processor = bad
    m._put(bytes(FRAME_BYTES))
    assert m.queue.qsize() == 1 and m.processor_errors == 1


def test_echo_similarity():
    from yordamchi.voice.assistant import echo_similarity

    lyric = "ko'ylagingni yengi yo'q mast ayladin"
    assert echo_similarity("ko'ylaging yengi yo'q mast aylading", lyric) >= 0.5
    assert echo_similarity("darvis qo'shiqni o'chir", lyric) < 0.5
    assert echo_similarity("", lyric) == 0


def test_loopback_buffer_caps_backlog():
    from yordamchi.voice.aec import CHUNK_BYTES, MAX_BACKLOG_S, RATE, Loopback

    lb = Loopback()
    assert lb.take(CHUNK_BYTES) is None
    with lb._lock:
        lb._buf.extend(bytes(RATE * 2))                    # 1 s
    lb._buf = lb._buf[-int(MAX_BACKLOG_S * RATE) * 2:]    # _cb shunday kesadi
    assert lb.take(CHUNK_BYTES) == bytes(CHUNK_BYTES)


# --------------------------------------------------------------------------- popups & quirks

class _C:
    """uiautomation Control o'rnida: daraxt tuguni."""
    def __init__(self, t, name="", kids=()):
        self.ControlTypeName, self.Name, self.kids, self.parent, self.invoked = t, name, list(kids), None, False
        for k in self.kids:
            k.parent = self

    def GetParentControl(self):
        return self.parent

    def GetInvokePattern(self):
        return self

    def Invoke(self):
        self.invoked = True


def _walk(root, maxDepth=0xFFFF):
    def go(n, d):
        if d > maxDepth:
            return
        yield n, d
        for k in n.kids:
            yield from go(k, d + 1)
    yield from go(root, 0)


def test_activation_panel_closes_panel_not_word(monkeypatch):
    import uiautomation

    from yordamchi.tools import popups

    title_close = _C("ButtonControl", "Закрыть")
    panel_close = _C("ButtonControl", "Закрыть")
    activate = _C("ButtonControl", "Активировать")
    tree = _C("WindowControl", "Word (Сбой активации продукта)", [
        _C("TitleBarControl", "", [_C("ButtonControl", "Свернуть"), title_close]),
        _C("PaneControl", "Сбой активации", [_C("PaneControl", "", [
            _C("TextControl", "Щелкните здесь, чтобы узнать подробнее"),
            _C("HyperlinkControl", "Изменить ключ продукта"), activate, panel_close])]),
    ])
    monkeypatch.setattr(uiautomation, "ControlFromHandle", lambda h: tree)
    monkeypatch.setattr(uiautomation, "WalkControl", _walk)
    assert popups._close_panel(1, popups.RULES[0].markers) is not None
    assert panel_close.invoked and not title_close.invoked and not activate.invoked


def test_title_only_warning_is_not_a_popup(monkeypatch):
    """Sarlavhada doim '(Сбой активации продукта)' turadi — bu to'siq emas, hech narsa bosilmasin."""
    import uiautomation

    from yordamchi.tools import popups

    title_close = _C("ButtonControl", "Закрыть")
    tree = _C("WindowControl", "Документ1 - Word (Сбой активации продукта)",
              [_C("TitleBarControl", "Документ1 - Word (Сбой активации продукта)", [title_close])])
    monkeypatch.setattr(uiautomation, "ControlFromHandle", lambda h: tree)
    monkeypatch.setattr(uiautomation, "WalkControl", _walk)
    assert popups._close_panel(1, popups.RULES[0].markers) is None and not title_close.invoked


def test_quirks_always_included_for_mentioned_app(tmp_path):
    from yordamchi.learning import Procedure, Procedures, prompt_section

    P = Procedures(tmp_path / "p.json")
    P.add(Procedure(kind="quirk", app="Word", task="activation dialog -> close it", steps=[],
                    keywords=["word", "hujjat"]))
    for i in range(5):
        P.add(Procedure(task=f"Word task {i} create document", app=f"Word{i}", steps=["x"],
                        keywords=["word", "yangi", "hujjat", "yarat", f"k{i}"]))
    rel = P.relevant("word dasturini och yangi hujjat tayyorla")
    assert rel[0].kind == "quirk"
    assert "handle them automatically" in prompt_section(rel)
    assert P.summary()["app_quirks"] == 1
    assert not [p for p in P.relevant("telegramni och") if p.kind == "quirk"]


def test_skills_overview_tool():
    r = load_all().get("skills_overview").invoke({})
    assert r["ok"] and r["built_in_tools"] >= 47 and "recipes" in r


# --------------------------------------------------------------------------- teach mode

def test_finalize_merges_text_and_double_clicks():
    from yordamchi.teach import describe, finalize

    raw = [{"type": "click", "x": 10, "y": 10, "t": 1.0, "process": "a.exe", "rel": [0.1, 0.1], "name": "Next",
            "ctrl": "ButtonControl"},
           {"type": "click", "x": 11, "y": 10, "t": 1.3, "process": "a.exe", "rel": [0.1, 0.1], "name": "Next",
            "ctrl": "ButtonControl"},
           {"type": "text", "text": "sal", "secret": False, "t": 2}, {"type": "text", "text": "om", "secret": False, "t": 2},
           {"type": "text", "text": "", "secret": True, "t": 3}, {"type": "key", "combo": "enter", "t": 4}]
    out = finalize(raw)
    assert [s["type"] for s in out] == ["click", "text", "text", "key"]
    assert out[0]["double"] and "x" not in out[0] and out[1]["text"] == "salom" and out[2]["secret"]
    assert "Next" in describe(out[0]) and "parol" in describe(out[2])


def test_recorder_hooks_install_and_stop_cleanly():
    from yordamchi.teach import Recorder

    r = Recorder()
    r.start("sinov")
    assert r.active and r._hook_thread.is_alive()
    assert r.stop() == [] and not r.active
    assert not r._hook_thread.is_alive()


def test_replay_pauses_on_secret_and_never_types_it(monkeypatch):
    from yordamchi import teach
    from yordamchi.tools import _input

    typed = []
    monkeypatch.setattr(_input, "type_text", lambda t, delay=0.0: typed.append(t))
    r = teach.run_steps([{"type": "text", "text": "user", "secret": False},
                         {"type": "text", "text": "", "secret": True},
                         {"type": "text", "text": "after", "secret": False}])
    assert r["paused_for_secret"] and r["resume_from"] == 2 and typed == ["user"]
    assert teach.run_steps([{"type": "text", "text": "after", "secret": False}])["ok"] and typed[-1] == "after"


def test_stop_teaching_saves_and_registers(tmp_path, monkeypatch):
    from yordamchi import learning, teach
    from yordamchi.tools import skills

    monkeypatch.setattr(teach, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(learning, "PATH", tmp_path / "p.json")
    monkeypatch.setattr(learning.Procedures.__init__, "__defaults__", (tmp_path / "p.json",))
    rec = teach.RECORDER
    monkeypatch.setattr(rec, "active", True)
    monkeypatch.setattr(rec, "name", "burp suite ishga tushirish")
    monkeypatch.setattr(rec, "stop", lambda: [
        {"type": "launch", "process": "burpsuitecommunity.exe", "exe": "C:/x/BurpSuiteCommunity.exe",
         "app": "BurpSuiteCommunity", "app_name": "Burp Suite Community Edition", "window_title": "Burp Suite"},
        {"type": "click", "process": "burpsuitecommunity.exe", "window_title": "Burp Suite", "rel": [0.9, 0.95]}])
    r = load_all().get("stop_teaching").invoke({})
    assert r["ok"] and r["steps"] == 2
    assert teach.find_skill("burp suite")["name"] == "burp suite ishga tushirish"
    rel = learning.Procedures(tmp_path / "p.json").relevant("burp suite dasturini och")
    assert rel and rel[0].kind == "taught" and "run_skill" in rel[0].line()
    assert "run_skill" in learning.prompt_section(rel)
