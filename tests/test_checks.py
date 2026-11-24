"""Ulanishni tekshirish, TLS sozlamasi va TTS parametrlari."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from yordamchi import checks, config, gateway
from yordamchi.config import Settings
from yordamchi.settings import STORE, SecretStore


def _settings(**kw) -> Settings:
    base = dict(gateway_url="https://llm.example", gateway_token="tok-123456", tls_name=None, verify=True,
                model="fast")
    base.update(kw)
    return Settings(**base)


def _mock(monkeypatch, handler, **kw):
    monkeypatch.setattr(checks, "_settings", lambda: _settings(**kw))
    monkeypatch.setattr(gateway, "make_http", lambda s, timeout=20.0: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(STORE, "config_path", tmp_path / "config.json")
    monkeypatch.setattr(STORE, "_cache", None)
    monkeypatch.setattr(STORE, "secrets", SecretStore(tmp_path / "secrets.json"))
    STORE.secrets._kr = None
    for name in ("LLM_VERIFY_TLS", "LLM_GATEWAY_VERIFY", "LLM_CA_FILE", "TTS_VOICE", "JARVIS_VOICE"):
        monkeypatch.delenv(name, raising=False)
    return STORE


def test_llm_check_lists_models_and_pings(monkeypatch):
    seen = []

    def handler(r: httpx.Request):
        seen.append((r.method, r.url.path))
        if r.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "fast", "kind": "chat", "context_length": 32768},
                                                      {"id": "smart"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    _mock(monkeypatch, handler)
    res = asyncio.run(checks.check_llm())
    assert res["ok"] and res["options"]["llm.model"] == ["fast", "smart"]
    assert "2 ta model" in res["summary"] and ("POST", "/v1/chat/completions") in seen
    assert "32 768" in res["details"][0]["info"]


def test_llm_check_wrong_model_and_bad_key(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": "smart"}]}), model="fast")
    res = asyncio.run(checks.check_llm())
    assert res["ok"] is False and "'fast'" in res["summary"]

    _mock(monkeypatch, lambda r: httpx.Response(401, json={"error": "bad"}))
    out = asyncio.run(checks._run_one("llm", "LLM", checks.check_llm, 5))
    assert out["ok"] is False and "Kalit noto'g'ri" in out["summary"] and "tok-123456" not in json.dumps(out)


def test_speech_check_voices_become_options(monkeypatch, store):
    voices = [{"name": "aziza-uz", "checkpoint": "operator", "languages": ["uz", "ru"], "seconds": 8.3},
              {"name": "kamola", "languages": ["en"], "registered": False}]
    _mock(monkeypatch, lambda r: httpx.Response(200, json=voices))
    res = asyncio.run(checks.check_speech())
    assert res["ok"] and res["options"]["speech.voice"] == ["", "aziza-uz", "kamola"]
    assert res["options"]["speech.language"] == ["en", "ru", "uz"]
    assert "operator" in res["details"][0]["info"] and "ro'yxatdan o'tmagan" in res["details"][1]["info"]
    store.set("speech.voice", "yo'q-ovoz")
    assert asyncio.run(checks.check_speech())["ok"] is False


def test_plugin_check_options_are_namespaced_and_errors_isolated(monkeypatch):
    def good():
        return {"ok": True, "summary": "3 ta model", "options": {"model": ["a", "b"]}}

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(checks, "_core_checks", lambda: [])
    monkeypatch.setattr(checks, "_plugin_checks", lambda: [("plugin:vis", "Vis", good), ("plugin:x", "X", broken)])
    from yordamchi import plugins

    monkeypatch.setattr(plugins, "load_all_plugins", lambda *a, **k: None)
    monkeypatch.setattr(config, "load_env", lambda *a, **k: None)  # haqiqiy .env testlarga aralashmasin
    out = asyncio.run(checks.run_all())
    assert out["options"] == {"plugin.vis.model": ["a", "b"]}
    assert [r["ok"] for r in out["results"]] == [True, False] and "boom" in out["results"][1]["summary"]
    assert checks.LAST["options"] == out["options"]


def test_tls_verify_toggle_ca_file_and_legacy_path(store, monkeypatch, tmp_path):
    assert config._tls_verify() is True
    store.set("llm.verify_tls", False)
    assert config._tls_verify() is False
    store._config()["llm.verify_tls"] = "C:/certs/ca.pem"  # eski uslub: fayl yo'li verify qiymatida
    assert config._tls_verify() == "C:/certs/ca.pem"
    store.set("llm.ca_file", str(tmp_path / "ca.pem"))
    assert config._tls_verify() == str(tmp_path / "ca.pem")
    monkeypatch.setenv("LLM_GATEWAY_VERIFY", "false")
    store.set("llm.ca_file", "")
    assert config._tls_verify() is False


def test_tts_body_has_voice_speed_normalize_seed(monkeypatch):
    from yordamchi.voice import speech

    bodies = []

    def handler(r):
        bodies.append(json.loads(r.content))
        return httpx.Response(200, content=b"\x00\x00" * 10)

    monkeypatch.setattr(speech, "make_http", lambda s, timeout=20.0: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    s = _settings(speech_normalize=False, speech_seed=42)
    sp = speech.GatewaySpeech(s, voice="aziza-uz", speed=1.2)

    async def go():
        return b"".join([c async for c in sp.tts_stream("Salom")])

    assert asyncio.run(go())
    assert bodies[0] == {"text": "Salom", "language": "uz", "normalize": False, "playback_speed": 1.2,
                         "voice": "aziza-uz", "seed": 42}


@pytest.mark.parametrize("raw,host,api", [
    ("https://llm.lan", "https://llm.lan", "https://llm.lan/v1"),
    ("https://llm.lan/v1/", "https://llm.lan", "https://llm.lan/v1"),
    ("https://api.deepseek.com", "https://api.deepseek.com", "https://api.deepseek.com/v1"),
    ("https://generativelanguage.googleapis.com/v1beta/openai",
     "https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    ("", "", ""),
])
def test_split_base_url(raw, host, api):
    assert config.split_base_url(raw) == (host, api)


def test_presets_are_valid_and_cover_main_providers():
    from yordamchi.settings import PROVIDERS

    assert {"anthropic", "openai", "gemini", "deepseek", "mistral", "openrouter", "ollama", "custom"} <= set(PROVIDERS)
    for pid, p in PROVIDERS.items():
        assert p["label"] and (pid == "custom" or p["base_url"].startswith(("https://", "http://localhost")))
        host, api = config.split_base_url(p["base_url"])
        assert api == p["base_url"].rstrip("/") or pid == "custom"


def test_public_provider_does_not_get_speech_or_key():
    s = _settings(provider="deepseek", gateway_url="https://api.deepseek.com", api_base="https://api.deepseek.com/v1")
    assert s.llm_base == "https://api.deepseek.com/v1" and s.speech_base == "" and s.speech_key == ""
    own = _settings()
    assert own.llm_base == "https://llm.example/v1" and own.speech_base == "https://llm.example"


def test_anthropic_models_retry_with_native_key_header(monkeypatch):
    seen = []

    def handler(r: httpx.Request):
        seen.append(dict(r.headers))
        if r.url.path == "/v1/models":
            if "x-api-key" not in r.headers:
                return httpx.Response(401, json={"error": "auth"})
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5-5", "type": "model"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _mock(monkeypatch, handler, provider="anthropic", gateway_url="https://api.anthropic.com/v1",
          api_base="https://api.anthropic.com/v1", model="claude-sonnet-5-5")
    res = asyncio.run(checks.check_llm())
    assert res["ok"] and res["options"]["llm.model"] == ["claude-sonnet-5-5"]
    assert seen[1]["x-api-key"] == "tok-123456" and seen[1]["anthropic-version"]
