from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal

import pytest

from yordamchi.agent import Agent, Reply, ToolFinished
from yordamchi.tools import files
from yordamchi.tools._fuzzy import rank, score
from yordamchi.tools.base import Risk, ToolRegistry, build_parameters


# --------------------------------------------------------------------------- registry

def test_schema_from_signature():
    def f(a: Annotated[str, "desc a"], n: int, mode: Literal["x", "y"] = "x",
          items: list[str] | None = None, flag: bool = False):
        """Doc."""
    p = build_parameters(f)
    assert p["required"] == ["a", "n"]
    assert p["properties"]["a"] == {"type": "string", "description": "desc a"}
    assert p["properties"]["mode"]["enum"] == ["x", "y"]
    assert p["properties"]["items"]["type"] == "array"
    assert p["properties"]["flag"] == {"type": "boolean", "default": False}


def test_invoke_coerces_and_catches():
    reg = ToolRegistry()

    @reg.tool()
    def add(a: int, b: int = 1) -> dict:
        return {"sum": a + b}

    @reg.tool()
    def boom() -> dict:
        raise RuntimeError("x")

    assert reg.get("add").invoke({"a": "30", "b": 2.0}) == {"ok": True, "sum": 32}
    bad = reg.get("add").invoke({"a": 1, "junk": 1})  # noma'lum argument jimgina tashlanmaydi
    assert bad["ok"] is False and "junk" in bad["error"]
    assert reg.get("add").invoke({})["ok"] is False
    assert reg.get("boom").invoke({}) == {"ok": False, "error": "RuntimeError: x"}


def test_confirm_rules():
    reg = ToolRegistry()

    @reg.tool(risk=Risk.CONFIRM, confirm_if=lambda a: len(a["paths"]) > 2, confirm=lambda a: "move")
    def mv(paths: list[str]) -> dict:
        return {}

    t = reg.get("mv")
    assert not t.needs_confirm({"paths": ["a"]})
    assert t.needs_confirm({"paths": ["a", "b", "c"]})
    assert t.confirm_text({}) == "move"


# --------------------------------------------------------------------------- fuzzy

def test_fuzzy_prefers_exact():
    names = ["Notepad++", "Notepad", "Google Chrome", "Telegram Desktop"]
    assert rank("notepad", names, lambda s: [s])[0][1] == "Notepad"
    assert rank("chrome", names, lambda s: [s])[0][1] == "Google Chrome"
    assert rank("telegram", names, lambda s: [s])[0][1] == "Telegram Desktop"
    assert score("xyz", "Google Chrome") < 0.55


# --------------------------------------------------------------------------- files

def test_find_move_rename(tmp_path: Path):
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "b.PDF").write_text("x")
    (tmp_path / "c.txt").write_text("salom")
    old = tmp_path / "old.pdf"
    old.write_text("x")
    t = time.mktime((2020, 1, 1, 12, 0, 0, 0, 0, -1))
    os.utime(old, (t, t))

    today = time.strftime("%Y-%m-%d")
    r = files.find_files(str(tmp_path), extensions=["pdf"], modified_after=today, modified_before=today)
    assert sorted(f["name"] for f in r["files"]) == ["a.pdf", "b.PDF"]

    dest = tmp_path / "PDFs"
    (tmp_path / "x").mkdir()
    (dest).mkdir()
    (dest / "a.pdf").write_text("existing")
    m = files.move_files([str(tmp_path / "a.pdf")], str(dest))
    assert m["ok"] and m["verified"] and Path(m["results"][0]).name == "a (1).pdf"

    rn = files.rename_path(str(tmp_path / "c.txt"), "d.txt")
    assert rn["verified"]
    assert files.read_text_file(str(tmp_path / "d.txt"))["content"] == "salom"


def test_known_folder_aliases():
    assert files.resolve_path("Downloads") == files.known_folder("downloads")
    assert files.resolve_path("yuklamalar/sub") == files.known_folder("downloads") / "sub"
    assert files.resolve_path("ish stoli") == files.known_folder("desktop")


# --------------------------------------------------------------------------- agent loop (fake LLM)

def _call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.seen.append(kw["messages"])
        content, calls = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def _agent(llm, reg, confirm=None):
    async def no(_):
        return False
    return Agent(llm=llm, tools=reg, executor=ThreadPoolExecutor(1), confirm=confirm or no)


async def _collect(agent, text):
    return [e async for e in agent.run(text)]


async def test_agent_runs_tool_then_replies():
    reg = ToolRegistry()

    @reg.tool()
    def set_volume(percent: int) -> dict:
        return {"volume_percent": percent, "verified": True}

    llm = FakeLLM([(None, [_call("c1", "set_volume", {"percent": 30})]), ("Ovoz 30 foiz.", None)])
    ag = _agent(llm, reg)
    evs = await _collect(ag, "ovozni 30 qil")
    fin = [e for e in evs if isinstance(e, ToolFinished)][0]
    assert fin.result == {"ok": True, "volume_percent": 30, "verified": True}
    assert isinstance(evs[-1], Reply) and evs[-1].text == "Ovoz 30 foiz." and evs[-1].steps == 2
    roles = [m["role"] for m in ag.history]
    assert roles == ["user", "assistant", "tool", "assistant"]


async def test_agent_declined_confirmation():
    reg = ToolRegistry()
    ran = []

    @reg.tool(risk=Risk.CONFIRM)
    def delete_files(paths: list[str]) -> dict:
        ran.append(paths)
        return {}

    llm = FakeLLM([(None, [_call("c1", "delete_files", {"paths": ["a"]})]), ("Bekor qilindi.", None)])
    evs = await _collect(_agent(llm, reg), "o'chir")
    fin = [e for e in evs if isinstance(e, ToolFinished)][0]
    assert fin.result["declined"] and not ran


async def test_agent_cancel_rolls_back_history():
    reg = ToolRegistry()

    @reg.tool()
    def slow() -> dict:
        time.sleep(0.5)
        return {}

    llm = FakeLLM([(None, [_call("c1", "slow", {})]), ("never", None)])
    ag = _agent(llm, reg)
    task = asyncio.create_task(_collect(ag, "sekin"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [m["role"] for m in ag.history] == ["user", "assistant"]
    assert "to'xtatildi" in ag.history[-1]["content"]


async def test_history_trimming_keeps_turn_boundaries():
    reg = ToolRegistry()
    llm = FakeLLM([("ok", None)] * 12)
    ag = _agent(llm, reg)
    ag.history_turns = 3
    for i in range(10):
        await _collect(ag, f"q{i}")
    sent = llm.seen[-1][1:]  # system'siz
    assert sent[0]["role"] == "user" and sum(m["role"] == "user" for m in sent) == 3
