"""22:16-22:41 sinov logidan chiqqan muammolar uchun regressiya testlari."""

from __future__ import annotations

import pytest

from yordamchi.tools import _input, load_all
from yordamchi.tools import memory as mem
from yordamchi.voice.assistant import fix_mixed_script, has_wake, spoken_part, strip_wake


@pytest.mark.parametrize("heard", ["asalim", "assalim", "aslim", "osalim", "ey asalim"])
def test_misheard_asalim_wakes(heard):
    assert has_wake(heard)


@pytest.mark.parametrize("heard", ["zamiz", "jofi", "galaish kerak", "jalil keldi", "darvoza och", "chorshanba"])
def test_non_wake_words_do_not_wake(heard):
    assert not has_wake(heard)


def test_strip_trailing_and_leading_wake():
    assert strip_wake("aslim meni sardor deb nomla") == "meni sardor deb nomla"
    assert strip_wake("bosilmadi assalim") == "bosilmadi"


def test_mixed_script_from_log():
    assert fix_mixed_script("yangi hujjatga кalkulyяtor ekranidagi sonni yoz") == \
        "yangi hujjatga kalkulyator ekranidagi sonni yoz"
    assert fix_mixed_script("ushbu вкладкаni yop") == "ushbu vkladkani yop"
    assert fix_mixed_script("буфер обмена") == "буфер обмена"  # toza ruscha o'zgarmaydi


def test_long_reply_spoken_part():
    long = "D diskda 25 ta papka bor. " + "Ulardan biri papka, " * 30 + "oxiri. Yana savol bormi?"
    s = spoken_part(long, 260)
    assert len(s) <= 260 and s.startswith("D diskda 25 ta papka bor.")
    assert spoken_part("Qisqa javob.", 260) == "Qisqa javob."


def test_type_text_rejects_shortcuts():
    reg = load_all()
    for combo in ("ctrl+n", "Ctrl + Shift + T", "f5", "enter"):
        r = reg.get("type_text").invoke({"text": combo})
        assert r["ok"] is False and "press_keys" in r["error"], combo


def test_backtick_and_punctuation_keys():
    assert _input.vk_for("`") == 0xC0 or _input.vk_for("`") > 0
    assert _input.vk_for("backtick") == _input.vk_for("`")


def test_memory_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "PATH", tmp_path / "memory.json")
    mem.remember("Foydalanuvchini Sardor deb chaqir")
    mem.remember("Foydalanuvchini Sardor deb chaqir")  # dublikat
    mem.remember("Sevimli brauzer: Chrome")
    assert mem.load() == ["Foydalanuvchini Sardor deb chaqir", "Sevimli brauzer: Chrome"]
    assert mem.forget("sardor")["removed"] == 1
    assert mem.load() == ["Sevimli brauzer: Chrome"]


def test_prompt_contains_memory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from concurrent.futures import ThreadPoolExecutor

    from yordamchi.agent import Agent
    from yordamchi.tools.base import ToolRegistry

    monkeypatch.setattr(mem, "PATH", tmp_path / "memory.json")
    mem.remember("Address the user as Aziz aka")

    async def no(_):
        return False

    ag = Agent(llm=SimpleNamespace(), tools=ToolRegistry(), executor=ThreadPoolExecutor(1), confirm=no)
    assert "Aziz aka" in ag._system()["content"]
