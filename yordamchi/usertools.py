"""Foydalanuvchi o'zi yaratadigan vositalar (Web UI'dan, kod yozmasdan).

Har bir vosita — `data/tools/<nom>.json`. Turlari:

* ``http``   — API chaqiruvi: URL/sarlavha/tana shablonlari, ``{param}`` va ``{secret:NOM}`` o'rinbosarlari.
* ``script`` — PowerShell / sh / Python skripti. Parametrlar muhit o'zgaruvchisi sifatida beriladi
  (``P_<NOM>``) — matnga qo'shilmaydi, shuning uchun buyruq in'ektsiyasi bo'lmaydi. HAR DOIM tasdiq so'raydi.
* ``skill``  — o'rgatilgan skill'ni parametrlar bilan chaqirish (skill matnidagi ``{kontakt}`` kabi joylar).

Xavfsizlik:
* Kalitlar tizim saqlagichida (``usertool.secret.<NOM>``), natija va xatolarda doim yashiriladi.
* URL'ning sxema+host qismida o'rinbosar taqiqlangan: model kalitni boshqa serverga yubora olmaydi.
* Qayta yo'naltirish (redirect) kuzatilmaydi — kalitli sarlavha boshqa hostga ketmaydi.
* Yadro yoki plugin vositasi nomini egallab bo'lmaydi.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .settings import DATA_DIR, STORE, Setting
from .tools.base import Risk, Tool, ToolError, registry

TOOLS_DIR = DATA_DIR / "tools"
SOURCE = "user"
SECRET_PREFIX = "usertool.secret."

KINDS = ("http", "script", "skill")
PARAM_TYPES = ("string", "integer", "number", "boolean")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_TYPES = ("json", "form", "text")
SHELLS = ("powershell", "sh", "python")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SECRET_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")
PLACEHOLDER = re.compile(r"\{(secret:)?([A-Za-z_][A-Za-z0-9_]*)\}")

MAX_RESULT = 4000
_lock = threading.Lock()
STATE: dict[str, Any] = {"loaded": False, "errors": {}}


# --------------------------------------------------------------------------- storage

def _path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ToolError("Nom: kichik lotin harflari, raqam va _ (3-48 belgi), harf bilan boshlansin")
    return TOOLS_DIR / f"{name}.json"


def load_specs() -> list[dict]:
    out = []
    for p in sorted(TOOLS_DIR.glob("*.json")) if TOOLS_DIR.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out.append({"name": p.stem, "kind": "?", "_error": f"o'qib bo'lmadi: {e}"})
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def get_spec(name: str) -> dict | None:
    p = _path(name)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_spec(spec: dict, old_name: str | None = None) -> dict:
    spec = validate(spec)
    with _lock:
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        if old_name and old_name != spec["name"]:
            if _path(spec["name"]).exists():
                raise ToolError(f"'{spec['name']}' nomli vosita allaqachon bor")
            _path(old_name).unlink(missing_ok=True)
        elif not old_name and _path(spec["name"]).exists():
            raise ToolError(f"'{spec['name']}' nomli vosita allaqachon bor")
        tmp = _path(spec["name"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_path(spec["name"]))
    reload()
    return spec


def delete_spec(name: str) -> bool:
    p = _path(name)
    if not p.exists():
        return False
    p.unlink()
    reload()
    return True


# --------------------------------------------------------------------------- validation

def _placeholders(text: str) -> tuple[set[str], set[str]]:
    params, secrets = set(), set()
    for m in PLACEHOLDER.finditer(text or ""):
        (secrets if m.group(1) else params).add(m.group(2))
    return params, secrets


def referenced_secrets(spec: dict) -> set[str]:
    out: set[str] = set(spec.get("secrets") or [])
    if spec.get("kind") == "http":
        for text in [spec.get("url", ""), spec.get("body", ""), *(spec.get("headers") or {}).values()]:
            out |= _placeholders(str(text))[1]
    return out


def validate(raw: dict) -> dict:
    """Paneldan kelgan ma'lumotni tekshiradi va toza spetsifikatsiya qaytaradi (ToolError — o'zbekcha sabab)."""
    if not isinstance(raw, dict):
        raise ToolError("Noto'g'ri format")
    name = str(raw.get("name", "")).strip()
    _path(name)
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ToolError(f"Turi: {', '.join(KINDS)} dan biri")
    desc = " ".join(str(raw.get("description", "")).split())
    if len(desc) < 8:
        raise ToolError("Tavsif yozing: yordamchi vositani qachon ishlatishni shundan biladi")
    params = []
    seen = set()
    for p in raw.get("params") or []:
        pname = str(p.get("name", "")).strip()
        if not PARAM_RE.match(pname) or pname in seen:
            raise ToolError(f"Parametr nomi noto'g'ri yoki takror: '{pname}'")
        seen.add(pname)
        ptype = p.get("type", "string")
        if ptype not in PARAM_TYPES:
            raise ToolError(f"'{pname}': turi {', '.join(PARAM_TYPES)} dan biri")
        item = {"name": pname, "type": ptype, "description": " ".join(str(p.get("description", "")).split())[:300],
                "required": bool(p.get("required", True))}
        if p.get("default") not in (None, ""):
            item["default"] = p["default"]
        params.append(item)
    spec: dict[str, Any] = {"name": name, "kind": kind, "description": desc[:1000], "params": params,
                            "enabled": bool(raw.get("enabled", True)),
                            "timeout": max(3, min(int(raw.get("timeout") or 30), 300))}
    declared = {p["name"] for p in params}

    if kind == "http":
        method = str(raw.get("method", "GET")).upper()
        if method not in METHODS:
            raise ToolError(f"Metod: {', '.join(METHODS)}")
        url = str(raw.get("url", "")).strip()
        origin = url.split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "{" in origin or "}" in origin:
            raise ToolError("URL'ning host qismida {…} bo'lishi mumkin emas (kalit boshqa serverga ketmasin)")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ToolError("URL http:// yoki https:// bilan boshlansin")
        headers = {}
        for k, v in (raw.get("headers") or {}).items():
            k = str(k).strip()
            if not k:
                continue
            if not re.match(r"^[A-Za-z0-9-]{1,64}$", k):
                raise ToolError(f"Sarlavha nomi noto'g'ri: '{k}'")
            headers[k] = str(v)
        body_type = raw.get("body_type", "json")
        if body_type not in BODY_TYPES:
            raise ToolError(f"Tana turi: {', '.join(BODY_TYPES)}")
        spec.update(method=method, url=url, headers=headers, body=str(raw.get("body") or ""), body_type=body_type,
                    result_path=str(raw.get("result_path") or "").strip())
        used = set()
        for text in [url, spec["body"], *headers.values()]:
            used |= _placeholders(text)[0]
        unknown = used - declared
        if unknown:
            raise ToolError(f"E'lon qilinmagan parametr: {', '.join(sorted(unknown))}")
        if spec["body"] and body_type == "json":
            probe = PLACEHOLDER.sub("0", spec["body"])
            try:
                json.loads(probe)
            except json.JSONDecodeError as e:
                raise ToolError(f"Tana JSON emas: {e.msg} ({e.lineno}-qator)") from e
        risk = raw.get("risk") or ("safe" if method == "GET" else "confirm")
    elif kind == "script":
        shell = raw.get("shell") or ("powershell" if os.name == "nt" else "sh")
        if shell not in SHELLS:
            raise ToolError(f"Skript tili: {', '.join(SHELLS)}")
        script = str(raw.get("script") or "")
        if not script.strip():
            raise ToolError("Skript matni bo'sh")
        spec.update(shell=shell, script=script)
        risk = "confirm"  # skript har doim tasdiq so'raydi — o'zgartirib bo'lmaydi
    else:
        skill = str(raw.get("skill") or "").strip()
        if not skill:
            raise ToolError("Qaysi o'rgatilgan skill ishlatilishini tanlang")
        spec["skill"] = skill
        risk = raw.get("risk") or "safe"

    secrets = sorted({str(s).strip() for s in (raw.get("secrets") or []) if str(s).strip()})
    for s in set(secrets) | referenced_secrets(spec):
        if not SECRET_RE.match(s):
            raise ToolError(f"Kalit nomi KATTA_HARF bilan bo'lsin: '{s}'")
    if secrets:
        spec["secrets"] = secrets
    if risk not in ("safe", "confirm"):
        raise ToolError("Xavf: safe yoki confirm")
    spec["risk"] = risk
    return spec


# --------------------------------------------------------------------------- secrets

def secret_key(name: str) -> str:
    return SECRET_PREFIX + name


def _secret(name: str) -> str:
    v = STORE.get(secret_key(name))
    if not v:
        raise ToolError(f"'{name}' kaliti kiritilmagan (panel → Sozlamalar → Vosita kalitlari)")
    return str(v)


def _register_secret_settings(specs: list[dict]) -> None:
    STORE.unregister_group("usertools")
    names: dict[str, list[str]] = {}
    for s in specs:
        try:
            for n in referenced_secrets(s):
                names.setdefault(n, []).append(s.get("name", "?"))
        except Exception:  # noqa: BLE001
            continue
    for n, users in sorted(names.items()):
        STORE.register(Setting(key=secret_key(n), label=n, type="secret", env=(f"TOOL_SECRET_{n}",),
                               group="usertools", section="Vosita kalitlari", restart=False,
                               help="Ishlatadi: " + ", ".join(sorted(users))))


class _Redactor:
    def __init__(self) -> None:
        self.values: list[str] = []

    def add(self, v: str) -> str:
        if v and len(v) >= 4:
            self.values.append(v)
        return v

    def __call__(self, text: str) -> str:
        for v in sorted(self.values, key=len, reverse=True):
            text = text.replace(v, "***")
        return text


# --------------------------------------------------------------------------- execution

def _values(spec: dict, args: dict) -> dict:
    vals = {}
    for p in spec.get("params", []):
        if p["name"] in args and args[p["name"]] is not None:
            vals[p["name"]] = args[p["name"]]
        elif "default" in p:
            vals[p["name"]] = p["default"]
        else:
            vals[p["name"]] = ""
    return vals


def _render(template: str, vals: dict, mode: str, red: _Redactor) -> str:
    def sub(m: re.Match) -> str:
        if m.group(1):
            v = red.add(_secret(m.group(2)))
            return quote(v, safe="") if mode == "url" else v
        v = vals.get(m.group(2), "")
        if mode == "url":
            return quote(str(v), safe="")
        if mode == "json":
            return json.dumps(v, ensure_ascii=False)[1:-1] if isinstance(v, str) else json.dumps(v)
        if mode == "header":
            return re.sub(r"[\r\n]+", " ", str(v))
        return str(v)
    return PLACEHOLDER.sub(sub, template or "")


def _dig(data: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                raise ToolError(f"result_path: '{part}' topilmadi") from None
        elif isinstance(data, dict):
            if part not in data:
                raise ToolError(f"result_path: '{part}' topilmadi")
            data = data[part]
        else:
            raise ToolError(f"result_path: '{part}' topilmadi")
    return data


def _clip(data: Any, red: _Redactor) -> tuple[Any, bool]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    text = red(text)
    if len(text) > MAX_RESULT:
        return text[:MAX_RESULT], True
    if isinstance(data, str):
        return text, False
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        return text, False


def run_http(spec: dict, args: dict) -> dict:
    import httpx

    red = _Redactor()
    vals = _values(spec, args)
    try:
        url = _render(spec["url"], vals, "url", red)
        headers = {k: _render(v, vals, "header", red) for k, v in spec.get("headers", {}).items()}
        content = None
        if spec.get("body") and spec["method"] != "GET":
            mode = {"json": "json", "form": "url"}.get(spec.get("body_type", "json"), "text")
            content = _render(spec["body"], vals, mode, red).encode("utf-8")
            headers.setdefault("Content-Type", {"json": "application/json", "form": "application/x-www-form-urlencoded"}
                               .get(spec.get("body_type"), "text/plain; charset=utf-8"))
        headers.setdefault("User-Agent", "AI-yordamchi")
        with httpx.Client(timeout=spec.get("timeout", 30), follow_redirects=False) as c:
            r = c.request(spec["method"], url, headers=headers, content=content)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": red(f"{type(e).__name__}: {e}")}
    out: dict[str, Any] = {"ok": 200 <= r.status_code < 300, "status": r.status_code}
    if 300 <= r.status_code < 400:
        out["redirect"] = red(r.headers.get("location", ""))
    data: Any = r.text
    if "json" in r.headers.get("content-type", "") or data[:1] in ("{", "["):
        try:
            data = r.json()
        except ValueError:
            pass
    if out["ok"] and spec.get("result_path") and not isinstance(data, str):
        data = _dig(data, spec["result_path"])
    out["result"], out["truncated"] = _clip(data, red)
    if not out["truncated"]:
        out.pop("truncated")
    return out


def _script_cmd(spec: dict) -> list[str]:
    script = spec["script"]
    if spec["shell"] == "powershell":
        exe = "powershell" if os.name == "nt" else "pwsh"
        pre = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'\n"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", pre + script]
    if spec["shell"] == "python":
        return [sys.executable, "-I", "-c", script]
    return ["sh", "-c", script]


def run_script(spec: dict, args: dict) -> dict:
    red = _Redactor()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "GEMINI_", "SPEECH_", "TOOL_SECRET_", "YORDAMCHI_"))}
    for k, v in _values(spec, args).items():
        env[f"P_{k.upper()}"] = "" if v is None else str(v)
    for s in spec.get("secrets") or []:
        env[f"SECRET_{s}"] = red.add(_secret(s))
    env["PYTHONIOENCODING"] = "utf-8"
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(_script_cmd(spec), capture_output=True, env=env, cwd=str(Path.home()),
                           timeout=spec.get("timeout", 30),
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {spec.get('timeout', 30)} s"}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"interpreter not found: {e.filename}"}
    out = red(r.stdout.decode("utf-8", "replace"))
    err = red(r.stderr.decode("utf-8", "replace"))
    res = {"ok": r.returncode == 0, "exit_code": r.returncode, "stdout": out[-3000:]}
    if err.strip():
        res["stderr"] = err[-1500:]
    if len(out) > 3000:
        res["truncated"] = True
    return res


def run_skill_tool(spec: dict, args: dict) -> dict:
    from . import teach

    sk = teach.find_skill(spec["skill"])
    if not sk:
        raise ToolError(f"O'rgatilgan skill topilmadi: '{spec['skill']}'")
    vals = {k: v for k, v in _values(spec, args).items()}
    res = teach.run_steps(sk["steps"], params=vals)
    return {"skill": sk["name"], **res}


RUNNERS = {"http": run_http, "script": run_script, "skill": run_skill_tool}


def execute(spec: dict, args: dict) -> dict:
    """Vositani to'g'ridan-to'g'ri bajaradi (panelda "Sinash" tugmasi ham shu)."""
    tool = build_tool(spec)
    return tool.invoke(args or {})


# --------------------------------------------------------------------------- registry

def _parameters(spec: dict) -> dict:
    props, required = {}, []
    for p in spec.get("params", []):
        sch: dict[str, Any] = {"type": p["type"]}
        if p.get("description"):
            sch["description"] = p["description"]
        if "default" in p:
            sch["default"] = p["default"]
        props[p["name"]] = sch
        if p.get("required", True) and "default" not in p:
            required.append(p["name"])
    return {"type": "object", "properties": props, "required": required}


def _confirm_text(spec: dict):
    def text(args: dict) -> str:
        shown = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items()) or "parametrsiz"
        if spec["kind"] == "script":
            first = "\n    ".join(spec["script"].strip().splitlines()[:4])
            return f"'{spec['name']}' skriptini bajarish ({shown}):\n    {first}"
        if spec["kind"] == "http":
            return f"'{spec['name']}': {spec['method']} {spec['url'].split('?')[0]} ({shown})"
        return f"'{spec['name']}' ({shown})"
    return text


def build_tool(spec: dict) -> Tool:
    spec = validate(spec)
    runner = RUNNERS[spec["kind"]]
    kind_note = {"http": "Calls an external API.", "script": "Runs a user script (needs approval).",
                 "skill": "Replays steps the user taught."}[spec["kind"]]

    def fn(**kwargs):
        return runner(spec, kwargs)

    return Tool(name=spec["name"], description=f"{spec['description']}\n(User-created tool. {kind_note})", fn=fn,
                parameters=_parameters(spec), risk=Risk(spec["risk"]), confirm=_confirm_text(spec),
                timeout=float(spec["timeout"]) + 5, source=SOURCE)


def reload() -> dict[str, str]:
    """Barcha foydalanuvchi vositalarini registry'ga qayta yozadi. Xatolar {nom: sabab}."""
    with _lock:
        specs = load_specs()
        _register_secret_settings(specs)
        registry.remove_source(SOURCE)
        errors: dict[str, str] = {}
        for s in specs:
            name = s.get("name", "?")
            if s.get("_error"):
                errors[name] = s["_error"]
                continue
            if not s.get("enabled", True):
                continue
            try:
                registry.add(build_tool(s))
            except ToolError as e:
                errors[name] = str(e)
            except Exception as e:  # noqa: BLE001
                errors[name] = f"{type(e).__name__}: {e}"
        STATE["errors"], STATE["loaded"] = errors, True
        return errors


def ensure_loaded() -> None:
    if not STATE["loaded"]:
        reload()


def describe() -> dict:
    specs = load_specs()
    errors = STATE.get("errors", {})
    secrets = sorted({n for s in specs for n in referenced_secrets(s)} if specs else set())
    return {
        "tools": [{**s, "error": errors.get(s.get("name")) or s.get("_error"),
                   "registered": registry._tools.get(s.get("name", "")) is not None
                   and registry._tools[s["name"]].source == SOURCE} for s in specs],
        "secrets": [{"name": n, "is_set": bool(STORE.get(secret_key(n)))} for n in secrets],
        "kinds": KINDS, "methods": METHODS, "shells": SHELLS, "param_types": PARAM_TYPES,
        "body_types": BODY_TYPES,
    }
