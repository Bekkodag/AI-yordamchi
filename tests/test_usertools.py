"""Foydalanuvchi yaratadigan vositalar: API (HTTP), skript, parametrli skill va panel API'si."""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from yordamchi import teach, usertools
from yordamchi.settings import SecretStore, SettingsStore
from yordamchi.tools import load_all
from yordamchi.tools.base import Risk, ToolError, registry


@pytest.fixture
def ut(tmp_path, monkeypatch):
    store = SettingsStore(config_path=tmp_path / "config.json", secrets=SecretStore(tmp_path / "secrets.json"))
    store.secrets._kr = None
    monkeypatch.setattr(usertools, "STORE", store)
    monkeypatch.setattr(usertools, "TOOLS_DIR", tmp_path / "tools")
    monkeypatch.setattr(usertools, "STATE", {"loaded": False, "errors": {}})
    load_all()
    yield store
    registry.remove_source(usertools.SOURCE)


def fake_http(monkeypatch, handler):
    real = httpx.Client
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(wrapped), **kw))
    return seen


WEATHER = {"name": "ob_havo", "kind": "http", "description": "Shahardagi hozirgi ob-havoni aytadi",
           "params": [{"name": "shahar", "type": "string", "description": "Shahar nomi"}],
           "method": "GET", "url": "https://api.example.com/w?city={shahar}&key={secret:OBHAVO}",
           "headers": {"Authorization": "Bearer {secret:OBHAVO}"}, "result_path": "current"}


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("patch,msg", [
    ({"url": "https://{host}/x"}, "host"),
    ({"url": "https://api.example.com{path}"}, "host"),
    ({"url": "https://{secret:OBHAVO}@evil.com/"}, "host"),
    ({"url": "ftp://x.com/"}, "http"),
    ({"url": "https://x.com/{nomalum}"}, "E'lon qilinmagan"),
    ({"name": "Bad Name"}, "Nom"),
    ({"description": "x"}, "Tavsif"),
    ({"headers": {"Bad Header": "x"}}, "Sarlavha"),
    ({"method": "POST", "body": "{not json"}, "JSON"),
])
def test_validate_rejects(patch, msg):
    with pytest.raises(ToolError, match=msg):
        usertools.validate({**WEATHER, **patch})


def test_script_is_always_confirm_and_get_defaults_safe():
    s = usertools.validate({"name": "salom", "kind": "script", "description": "Salom deydi skript",
                            "shell": "python", "script": "print(1)", "risk": "safe"})
    assert s["risk"] == "confirm"
    assert usertools.validate({**WEATHER})["risk"] == "safe"
    post = {k: v for k, v in WEATHER.items() if k != "risk"}
    assert usertools.validate({**post, "method": "POST"})["risk"] == "confirm"


# --------------------------------------------------------------------------- http

def test_http_tool_registers_renders_and_redacts(ut, monkeypatch):
    usertools.save_spec(WEATHER)
    assert usertools.secret_key("OBHAVO") in ut.specs  # panel Sozlamalar sahifasida chiqadi
    tool = registry.get("ob_havo")
    assert tool and tool.source == "user" and tool.parameters["required"] == ["shahar"]

    assert tool.invoke({"shahar": "Toshkent"})["ok"] is False  # kalit kiritilmagan
    ut.set(usertools.secret_key("OBHAVO"), "s3cr3t-value")
    seen = fake_http(monkeypatch, lambda r: httpx.Response(
        200, json={"current": {"temp": 21, "echo": r.headers["authorization"]}}))
    res = tool.invoke({"shahar": "Toshkent & Co/1"})
    assert res["ok"] and res["result"]["temp"] == 21
    assert "s3cr3t" not in json.dumps(res)                       # javobga qaytgan kalit ham yashiriladi
    url = str(seen[0].url)
    assert "city=Toshkent%20%26%20Co%2F1" in url and seen[0].url.host == "api.example.com"
    assert seen[0].headers["authorization"] == "Bearer s3cr3t-value"


def test_http_json_body_escapes_values_and_no_redirects(ut, monkeypatch):
    spec = {"name": "xabar_yubor", "kind": "http", "description": "Webhook orqali xabar yuboradi",
            "params": [{"name": "matn", "type": "string"}, {"name": "soni", "type": "integer", "required": False,
                                                            "default": 1}],
            "method": "POST", "url": "https://hooks.example.com/x", "body": '{"text": "{matn}", "n": {soni}}'}
    usertools.save_spec(spec)
    tool = registry.get("xabar_yubor")
    assert tool.risk is Risk.CONFIRM and tool.needs_confirm({})
    seen = fake_http(monkeypatch, lambda r: httpx.Response(302, headers={"location": "https://evil.example/"}))
    res = tool.invoke({"matn": 'u "dedi"\nva'})
    assert json.loads(seen[0].content) == {"text": 'u "dedi"\nva', "n": 1}
    assert len(seen) == 1 and res["ok"] is False and res["redirect"] == "https://evil.example/"


# --------------------------------------------------------------------------- script

def test_script_gets_params_as_env_not_text(ut):
    spec = {"name": "aks_sado", "kind": "script", "description": "Matnni qaytaradi (sinov)", "shell": "python",
            "params": [{"name": "matn", "type": "string"}], "secrets": ["MAXFIY"],
            "script": "import os; print(os.environ['P_MATN']); print(os.environ['SECRET_MAXFIY']); "
                      "print('LLM' if any(k.startswith('LLM_') for k in os.environ) else 'clean')"}
    usertools.save_spec(spec)
    ut.set(usertools.secret_key("MAXFIY"), "topsecret123")
    tool = registry.get("aks_sado")
    assert tool.needs_confirm({"matn": "x"}) and "aks_sado" in tool.confirm_text({"matn": "x"})
    res = tool.invoke({"matn": "$(rm -rf /); `calc`"})
    lines = res["stdout"].splitlines()
    assert res["ok"] and lines[0] == "$(rm -rf /); `calc`"   # matn sifatida keldi, bajarilmadi
    assert lines[1] == "***" and lines[2] == "clean"           # kalit yashirildi, yadro kalitlari berilmadi


@pytest.fixture(autouse=False)
def _llm_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gateway-key")


def test_script_does_not_inherit_core_secrets(ut, _llm_env):
    usertools.save_spec({"name": "env_chek", "kind": "script", "description": "Muhitni tekshiradi",
                         "shell": "python", "script": "import os; print(os.environ.get('LLM_API_KEY', 'none'))"})
    assert registry.get("env_chek").invoke({})["stdout"].strip() == "none"


# --------------------------------------------------------------------------- registry safety

def test_user_tool_cannot_take_core_name(ut, tmp_path):
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "run_powershell.json").write_text(json.dumps(
        {**WEATHER, "name": "run_powershell"}), encoding="utf-8")
    errors = usertools.reload()
    assert "run_powershell" in errors and registry.get("run_powershell").source == "core"


def test_disabled_and_deleted_tools_leave_registry(ut):
    usertools.save_spec({**WEATHER, "enabled": False})
    assert registry.get("ob_havo") is None
    usertools.save_spec({**WEATHER}, "ob_havo")
    assert registry.get("ob_havo") is not None
    usertools.delete_spec("ob_havo")
    assert registry.get("ob_havo") is None


# --------------------------------------------------------------------------- parametrised skills

def test_skill_placeholders_fill_and_missing():
    steps = [{"type": "launch", "process": "telegram.exe"}, {"type": "text", "text": "{kontakt}"},
             {"type": "key", "combo": "enter"}, {"type": "text", "text": "Salom, {kontakt}! {xabar}"},
             {"type": "text", "text": "{parol}", "secret": True}]
    assert teach.placeholders(steps) == ["kontakt", "xabar"]
    out = teach.fill(steps, {"kontakt": "Ali", "xabar": "{x}"})
    assert out[1]["text"] == "Ali" and out[3]["text"] == "Salom, Ali! {x}" and steps[1]["text"] == "{kontakt}"
    with pytest.raises(ValueError, match="xabar"):
        teach.fill(steps, {"kontakt": "Ali"})
    res = teach.run_steps(steps, params={"kontakt": "Ali"})
    assert res["ok"] is False and res["params"] == ["kontakt", "xabar"]


def test_skill_tool_passes_params(ut, monkeypatch, tmp_path):
    monkeypatch.setattr(teach, "SKILLS_DIR", tmp_path / "skills")
    teach.save_skill("telegramda yozish", [{"type": "text", "text": "{kontakt}"}])
    got = {}
    monkeypatch.setattr(teach, "run_steps", lambda steps, params=None: got.update(params) or {"ok": True})
    usertools.save_spec({"name": "telegram_yoz", "kind": "skill", "skill": "telegramda yozish",
                         "description": "Telegramda kontaktga yozadi", "params": [{"name": "kontakt"}]})
    assert registry.get("telegram_yoz").invoke({"kontakt": "Ali"})["ok"] and got == {"kontakt": "Ali"}


# --------------------------------------------------------------------------- panel API

@pytest.fixture
def client(tmp_path, monkeypatch, ut):
    from fastapi.testclient import TestClient

    from yordamchi.api import server

    monkeypatch.setattr(server, "TOKEN_PATH", tmp_path / "api_token")
    monkeypatch.setattr(server, "STORE", ut)
    c = TestClient(server.create_app())
    c.headers["Authorization"] = "Bearer " + server.api_token()
    return c


def test_api_crud_secret_and_test(client, ut, monkeypatch):
    assert client.post("/api/usertools", json={**WEATHER, "url": "https://{x}/"}).status_code == 400
    r = client.post("/api/usertools", json=WEATHER)
    assert r.status_code == 200 and r.json()["tool"]["name"] == "ob_havo"
    assert client.post("/api/usertools", json=WEATHER).status_code == 400  # takror nom
    rows = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    assert rows["ob_havo"]["source"] == "user"
    d = client.get("/api/usertools").json()
    assert d["tools"][0]["registered"] and d["secrets"] == [{"name": "OBHAVO", "is_set": False}]

    assert client.put("/api/usertools/secrets/OBHAVO", json={"value": "k-123456"}).json()["is_set"]
    assert "k-123456" not in client.get("/api/usertools").text
    assert "k-123456" not in client.get("/api/settings").text
    assert client.put("/api/usertools/secrets/NOMALUM", json={"value": "x"}).status_code == 404

    fake_http(monkeypatch, lambda r: httpx.Response(200, json={"current": {"temp": 5}}))
    res = client.post("/api/usertools/test", json={"spec": WEATHER, "args": {"shahar": "Xiva"}}).json()
    assert res["ok"] and res["result"] == {"temp": 5}

    script = {"name": "sinov_skript", "kind": "script", "description": "Sinov uchun skript",
              "shell": "python", "script": "print('hi')"}
    assert client.post("/api/usertools/test", json={"spec": script}).status_code == 400  # tasdiqsiz yo'q
    assert client.post("/api/usertools/test", json={"spec": script, "confirm": True}).json()["stdout"].strip() == "hi"

    r = client.put("/api/usertools/ob_havo", json={**WEATHER, "name": "ob_havo2"})
    assert r.status_code == 200 and registry.get("ob_havo") is None and registry.get("ob_havo2")
    assert client.delete("/api/usertools/ob_havo2").status_code == 200
    assert client.delete("/api/usertools/ob_havo2").status_code == 404
    assert client.put("/api/usertools/..%2F..%2Fx", json=WEATHER).status_code in (400, 404)


def test_python_runner_uses_current_interpreter():
    assert usertools._script_cmd({"shell": "python", "script": "x"})[0] == sys.executable
