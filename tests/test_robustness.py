"""Qotib qolish holatlariga qarshi regressiya testlari."""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from yordamchi.agent import Agent, ToolFinished
from yordamchi.tools.base import ToolRegistry
from yordamchi.voice.assistant import has_wake, strip_wake
from yordamchi.voice.audio import PlaybackError, Player


def test_uzbek_wake_variants():
    for t in ("ey asalim", "Ey Asalim, chromeni och", "asalim", "assalim", "aslim ovozni oshir"):
        assert has_wake(t), t
    assert strip_wake("ey asalim") == ""
    assert not has_wake("jarvis")
    assert not has_wake("chorshanba kuni") and not has_wake("ovozni oshir")


async def _gen(chunks, delay=0.0):
    for c in chunks:
        if delay:
            await asyncio.sleep(delay)
        yield c


async def test_player_recovers_from_dead_stream():
    p = Player()
    try:
        p.stream.abort()  # qurilma "o'lib qoldi"
        assert not p.healthy()
        ok = await asyncio.wait_for(p.play(_gen([b"\x00\x00" * 2400])), 5)
        assert ok and p.restarts == 1 and p.healthy()
    finally:
        p.close()


async def test_player_stream_dies_mid_playback_raises_not_hangs():
    p = Player()
    try:
        async def chunks():
            yield b"\x00\x00" * 24000 * 3  # 3 s audio
            p.stream.abort()               # ijro paytida oqim to'xtadi

        t = time.monotonic()
        with pytest.raises(PlaybackError):
            await asyncio.wait_for(p.play(chunks()), 10)
        assert time.monotonic() - t < 8
        assert p.healthy()  # qayta ochilgan
    finally:
        p.close()


async def test_player_tts_stall_times_out():
    p = Player()
    try:
        async def stalled():
            yield b"\x00\x00" * 2400
            await asyncio.sleep(30)
            yield b""
        with pytest.raises(PlaybackError):
            await asyncio.wait_for(p.play(stalled(), chunk_timeout=0.5), 5)
    finally:
        p.close()


def _call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        content, calls = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


async def test_hung_tool_does_not_block_next_tools():
    reg = ToolRegistry()

    @reg.tool(timeout=0.3)
    def frozen() -> dict:
        time.sleep(3)
        return {}

    @reg.tool()
    def quick() -> dict:
        return {"fine": True}

    replaced = []

    def new_exec():
        ex = ThreadPoolExecutor(1)
        replaced.append(ex)
        return ex

    async def no(_):
        return False

    llm = FakeLLM([(None, [_call("a", "frozen", {})]), (None, [_call("b", "quick", {})]), ("tayyor", None)])
    ag = Agent(llm=llm, tools=reg, executor=ThreadPoolExecutor(1), confirm=no, on_tool_hang=new_exec)
    t = time.monotonic()
    evs = [e async for e in ag.run("x")]
    results = [e.result for e in evs if isinstance(e, ToolFinished)]
    assert results[0]["ok"] is False and "timed out" in results[0]["error"]
    assert results[1] == {"ok": True, "fine": True}
    assert replaced and time.monotonic() - t < 2  # 'quick' osilgan thread'ni kutmadi


async def test_llm_timeout_becomes_failure_not_hang():
    class SlowLLM:
        chat = SimpleNamespace(completions=SimpleNamespace(create=None))

        def __init__(self):
            async def create(**kw):
                await asyncio.sleep(10)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    async def no(_):
        return False

    ag = Agent(llm=SlowLLM(), tools=ToolRegistry(), executor=ThreadPoolExecutor(1), confirm=no, llm_timeout=0.3)
    evs = await asyncio.wait_for(_collect(ag), 3)
    assert type(evs[-1]).__name__ == "Failed"
    assert [m["role"] for m in ag.history] == ["user", "assistant"]


async def _collect(ag):
    return [e async for e in ag.run("x")]
