"""Suhbat tarixi: panel/ovoz manbasi logda, panel suhbat sahifasi, qayta ishga tushganda tiklash."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from yordamchi.agent import Agent
from yordamchi.runtime import Runtime, TurnLog, read_conversation


def _write(log_dir, *records):
    path = log_dir / f"{datetime.now():%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ts(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")


def test_read_conversation_maps_kinds_and_sources(tmp_path):
    _write(tmp_path,
           {"ts": _ts(5), "kind": "user", "text": "soat necha?", "source": "panel"},
           {"ts": _ts(5), "kind": "tool", "name": "x"},
           {"ts": _ts(5), "kind": "reply", "text": "12:00", "source": "panel"},
           {"ts": _ts(4), "kind": "user", "text": "ovozni pasaytir"},          # eski log: manbasiz = ovoz
           {"ts": _ts(4), "kind": "cancelled", "ms": 10},
           {"ts": _ts(3), "kind": "error", "error": "timeout", "source": "voice"})
    conv = read_conversation(tmp_path)
    assert [(c["who"], c["text"], c["source"]) for c in conv] == [
        ("user", "soat necha?", "panel"), ("bot", "12:00", "panel"),
        ("user", "ovozni pasaytir", "voice"), ("sys", "to'xtatildi", "voice"), ("sys", "xato: timeout", "voice")]


def test_restore_history_only_recent_answered_turns(tmp_path, monkeypatch):
    _write(tmp_path,
           {"ts": _ts(90), "kind": "user", "text": "eski savol"},
           {"ts": _ts(90), "kind": "reply", "text": "eski javob"},
           {"ts": _ts(10), "kind": "user", "text": "Alining raqamini top", "source": "voice"},
           {"ts": _ts(10), "kind": "reply", "text": "Topdim: 90 123", "source": "voice"},
           {"ts": _ts(5), "kind": "user", "text": "uni telegramdan yubor", "source": "panel"},
           {"ts": _ts(5), "kind": "cancelled", "source": "panel"})
    rt = Runtime.__new__(Runtime)
    rt.log = TurnLog.__new__(TurnLog)
    rt.log.dir = tmp_path
    rt.log.write = lambda *a, **k: None
    rt.agent = Agent(llm=None, tools=None, executor=None, confirm=None, history_turns=8)
    assert rt.restore_history(minutes=30) == 2
    assert rt.agent.history == [{"role": "user", "content": "Alining raqamini top"},
                                {"role": "assistant", "content": "Topdim: 90 123"}]
    rt.agent.history.clear()
    assert rt.restore_history(minutes=0) == 0 and rt.agent.history == []


def test_chat_history_api_and_turn_source(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from yordamchi.api import server

    monkeypatch.setattr(server, "TOKEN_PATH", tmp_path / "api_token")
    _write(tmp_path, {"ts": _ts(1), "kind": "user", "text": "salom", "source": "panel"},
           {"ts": _ts(1), "kind": "reply", "text": "Va alaykum", "source": "panel"})

    class FakeRuntime:
        settings = type("S", (), {"log_dir": tmp_path})()

    monkeypatch.setitem(server.STATE, "runtime", FakeRuntime())
    c = TestClient(server.create_app())
    c.headers["Authorization"] = "Bearer " + server.api_token()
    items = c.get("/api/chat/history").json()["items"]
    assert [(i["who"], i["text"], i["source"]) for i in items] == [("user", "salom", "panel"),
                                                                    ("bot", "Va alaykum", "panel")]
    assert "ts_epoch" not in items[0]


def test_assets_are_not_cached_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from yordamchi.api import server

    monkeypatch.setattr(server, "TOKEN_PATH", tmp_path / "api_token")
    r = TestClient(server.create_app()).get("/assets/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_author_is_shown_everywhere(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import yordamchi
    from yordamchi.api import server

    name = "Bek-dag Farhod Eliboyev"
    root = Path(yordamchi.__file__).resolve().parent.parent
    assert yordamchi.__author__ == name
    assert name in (root / "NOTICE").read_text(encoding="utf-8")
    assert name in (root / "README.md").read_text(encoding="utf-8")
    monkeypatch.setattr(server, "TOKEN_PATH", tmp_path / "api_token")
    c = TestClient(server.create_app())
    assert name in c.get("/login").text
    c.headers["Authorization"] = "Bearer " + server.api_token()
    assert c.get("/api/status").json()["author"] == name
    assert name in c.get("/").text
