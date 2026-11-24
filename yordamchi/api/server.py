"""Boshqaruv paneli API'si (FastAPI) + Web UI statik fayllari.

Xavfsizlik:
* Standart manzil 127.0.0.1 — faqat shu kompyuter. Tarmoqqa ochish (telefon) — `api.host = 0.0.0.0`.
* Har so'rov token talab qiladi: `Authorization: Bearer <token>` yoki login'dan keyingi HttpOnly cookie.
  Token data/api_token faylida (birinchi ishga tushishda yaratiladi). Panel manzili konsolda tokeni bilan
  chiqariladi; brauzer uni cookie'ga o'tkazib, manzildan olib tashlaydi.
* O'zgartiruvchi so'rovlarda Origin tekshiriladi (CSRF). Cookie SameSite=Strict.
* Maxfiy sozlamalar hech qachon qaytarilmaydi — faqat "o'rnatilgan/yo'q".
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets as pysecrets
import sys
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import ROOT
from ..settings import DATA_DIR, STORE

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"
TOKEN_PATH = DATA_DIR / "api_token"
COOKIE = "ay_token"

STATE: dict[str, Any] = {"runtime": None, "assistant": None, "server": None, "url": None, "started": time.time()}


# --------------------------------------------------------------------------- auth

def api_token() -> str:
    try:
        t = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    t = pysecrets.token_urlsafe(32)
    TOKEN_PATH.write_text(t, encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass
    return t


def _presented(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.cookies.get(COOKIE, "")


def require_auth(request: Request) -> None:
    if not hmac.compare_digest(_presented(request), api_token()):
        raise HTTPException(401, "token required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
            raise HTTPException(403, "cross-origin request refused")


Auth = Depends(require_auth)


def _runtime():
    rt = STATE["runtime"]
    if rt is None:
        raise HTTPException(503, "Yordamchi ishga tushmagan (faqat panel rejimi). Sozlab, qayta ishga tushiring.")
    return rt


async def _in_tools_thread(fn, *args):
    """Vositalar bilan bog'liq ish (UIA, COM) yordamchining yagona tools oqimida bajariladi."""
    rt = _runtime()
    return await asyncio.get_running_loop().run_in_executor(rt.executor, fn, *args)


# --------------------------------------------------------------------------- panel confirmations

class PanelConfirm:
    """Paneldan berilgan buyruqdagi xavfli amal uchun tasdiq: UI so'raydi, foydalanuvchi bosadi."""

    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}

    async def __call__(self, text: str) -> bool:
        cid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = {"id": cid, "text": text, "fut": fut, "ts": time.time()}
        try:
            return await asyncio.wait_for(fut, 120)
        except TimeoutError:
            return False
        finally:
            self.pending.pop(cid, None)

    def answer(self, cid: str, approve: bool) -> bool:
        p = self.pending.get(cid)
        if not p or p["fut"].done():
            return False
        p["fut"].get_loop().call_soon_threadsafe(p["fut"].set_result, bool(approve))
        return True


CONFIRM = PanelConfirm()


# --------------------------------------------------------------------------- app

def create_app() -> FastAPI:
    app = FastAPI(title="AI-yordamchi", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    # ---------------------------------------------------------------- web ui
    @app.get("/", include_in_schema=False)
    def index(request: Request, token: str | None = None):
        if token:  # birinchi kirish: tokenni cookie'ga o'tkazamiz va manzildan olib tashlaymiz
            if not hmac.compare_digest(token, api_token()):
                return RedirectResponse("/login?bad=1", 303)
            r = RedirectResponse("/", 303)
            r.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=3600 * 24 * 365)
            return r
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), api_token()):
            return RedirectResponse("/login", 303)
        return FileResponse(WEBUI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(WEBUI_DIR / "login.html")

    @app.post("/api/login")
    def login(body: dict = Body(...)):
        t = str(body.get("token", "")).strip()
        if not hmac.compare_digest(t, api_token()):
            time.sleep(0.8)  # taxmin qilishni sekinlashtirish
            raise HTTPException(401, "Token noto'g'ri")
        r = JSONResponse({"ok": True})
        r.set_cookie(COOKIE, t, httponly=True, samesite="strict", max_age=3600 * 24 * 365)
        return r

    @app.post("/api/logout")
    def logout():
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE)
        return r

    @app.middleware("http")
    async def no_stale_assets(request: Request, call_next):
        # Yangilanishdan keyin brauzer eski app.js/style.css'ni keshdan olmasin (ETag bilan tez tekshiradi)
        resp = await call_next(request)
        if request.url.path.startswith("/assets/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/assets", StaticFiles(directory=WEBUI_DIR / "assets"), name="assets")

    # ---------------------------------------------------------------- status
    @app.get("/api/status", dependencies=[Auth])
    def status():
        from .. import plugins
        from ..teach import RECORDER

        rt, va = STATE["runtime"], STATE["assistant"]
        return {
            "version": __version__, "uptime_s": round(time.time() - STATE["started"]),
            "running": rt is not None, "voice": va is not None,
            "voice_state": getattr(getattr(va, "state", None), "value", None),
            "missing_settings": STORE.missing_required(),
            "secret_backend": STORE.secrets.backend,
            "teaching": RECORDER.name if RECORDER.active else None,
            "plugins": {p.id: p.status for p in plugins.PLUGINS.values()},
            "assistant_name": STORE.get("assistant.name"), "platform": sys.platform,
            "pending_confirmations": [{"id": c["id"], "text": c["text"]} for c in CONFIRM.pending.values()],
        }

    @app.post("/api/restart", dependencies=[Auth])
    def restart():
        """Sozlamalar kuchga kirishi uchun: xuddi shu buyruq bilan yangi jarayon, eskisi chiqadi."""
        def go():
            time.sleep(0.8)
            import subprocess

            subprocess.Popen([sys.executable, "-m", "yordamchi", *sys.argv[1:]], cwd=str(ROOT))
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()
        return {"ok": True, "restarting": True}

    # ---------------------------------------------------------------- settings
    @app.get("/api/settings", dependencies=[Auth])
    def get_settings():
        return {"settings": STORE.describe(), "secret_backend": STORE.secrets.backend}

    @app.put("/api/settings", dependencies=[Auth])
    def put_settings(body: dict = Body(...)):
        """{"values": {"llm.model": "smart", "llm.api_key": "..."}, "clear": ["speech.api_key"]}"""
        errors, saved, restart_needed = {}, [], False
        for key, value in (body.get("values") or {}).items():
            try:
                spec = STORE.specs[key]
                if spec.type == "secret" and (value is None or value == ""):
                    continue  # bo'sh maydon = o'zgarmadi
                STORE.set(key, value)
                saved.append(key)
                restart_needed |= spec.restart
            except KeyError:
                errors[key] = "noma'lum sozlama"
            except (PermissionError, ValueError, TypeError) as e:
                errors[key] = str(e)
        for key in body.get("clear") or []:
            try:
                STORE.set(key, None)
                saved.append(key)
            except (KeyError, PermissionError) as e:
                errors[key] = str(e)
        return {"ok": not errors, "saved": saved, "errors": errors, "restart_needed": restart_needed}

    # ---------------------------------------------------------------- tools
    def _tool_rows():
        from ..tools import load_all

        reg = load_all()
        cfg = STORE._config()
        dis, conf = set(cfg.get("tools.disabled", [])), set(cfg.get("tools.always_confirm", []))
        return [{"name": t.name, "description": t.description, "source": t.source, "risk": t.risk.value,
                 "disabled": t.name in dis, "always_confirm": t.name in conf,
                 "params": list((t.parameters.get("properties") or {}).keys())}
                for t in sorted(reg.all(), key=lambda x: (x.source, x.name))]

    @app.post("/api/settings/check", dependencies=[Auth])
    async def settings_check():
        from .. import checks

        return await checks.run_all()

    @app.get("/api/settings/options", dependencies=[Auth])
    def settings_options():
        from .. import checks

        return {"options": checks.LAST["options"], "results": checks.LAST["results"], "ts": checks.LAST["ts"]}

    @app.get("/api/tools", dependencies=[Auth])
    def tools():
        return {"tools": _tool_rows()}

    @app.patch("/api/tools/{name}", dependencies=[Auth])
    def patch_tool(name: str, body: dict = Body(...)):
        from ..runtime import apply_tool_prefs
        from ..tools import load_all

        if load_all()._tools.get(name) is None:
            raise HTTPException(404, "vosita topilmadi")
        with STORE._lock:
            cfg = STORE._config()
            for field, cfg_key in (("disabled", "tools.disabled"), ("always_confirm", "tools.always_confirm")):
                if field in body:
                    cur = set(cfg.get(cfg_key, []))
                    (cur.add if body[field] else cur.discard)(name)
                    cfg[cfg_key] = sorted(cur)
            STORE._save()
        apply_tool_prefs()
        return {"ok": True, "tool": next(t for t in _tool_rows() if t["name"] == name)}

    # ---------------------------------------------------------------- recipes / quirks / taught index
    @app.get("/api/procedures", dependencies=[Auth])
    def procedures(kind: str | None = None):
        from ..learning import Procedures

        items = Procedures().load()
        return {"items": [p.__dict__ for p in items if not kind or p.kind == kind]}

    @app.post("/api/procedures", dependencies=[Auth])
    def add_procedure(body: dict = Body(...)):
        from ..learning import Procedure, Procedures

        kind = body.get("kind", "task")
        if kind not in ("task", "quirk"):
            raise HTTPException(400, "kind: task yoki quirk")
        task = " ".join(str(body.get("task", "")).split())
        if not task:
            raise HTTPException(400, "task bo'sh")
        steps = [str(s).strip() for s in body.get("steps", []) if str(s).strip()]
        kw = [str(k).strip().lower() for k in body.get("keywords", []) if str(k).strip()]
        p = Procedures().add(Procedure(task=task[:200], app=str(body.get("app", ""))[:40], steps=steps[:12],
                                       keywords=kw[:15], kind=kind))
        return {"ok": True, "item": p.__dict__}

    @app.put("/api/procedures/{pid}", dependencies=[Auth])
    def edit_procedure(pid: str, body: dict = Body(...)):
        from ..learning import Procedures

        P = Procedures()
        with P._lock:
            items = P.load()
            for p in items:
                if p.id == pid:
                    for f in ("task", "app"):
                        if f in body:
                            setattr(p, f, " ".join(str(body[f]).split())[:200])
                    if "steps" in body:
                        p.steps = [str(s).strip() for s in body["steps"] if str(s).strip()][:12]
                    if "keywords" in body:
                        p.keywords = [str(k).strip().lower() for k in body["keywords"] if str(k).strip()][:15]
                    P._save(items)
                    return {"ok": True, "item": p.__dict__}
        raise HTTPException(404, "topilmadi")

    @app.delete("/api/procedures/{pid}", dependencies=[Auth])
    def delete_procedure(pid: str):
        from ..learning import Procedures

        if not Procedures().remove(pid):
            raise HTTPException(404, "topilmadi")
        return {"ok": True}

    # ---------------------------------------------------------------- facts (memory)
    @app.get("/api/facts", dependencies=[Auth])
    def facts():
        from ..tools import memory

        return {"facts": memory.load(), "max": memory.MAX_FACTS}

    @app.post("/api/facts", dependencies=[Auth])
    def add_fact(body: dict = Body(...)):
        from ..tools import memory

        r = memory.remember(str(body.get("fact", "")))
        return {"ok": True, **r}

    @app.put("/api/facts/{idx}", dependencies=[Auth])
    def edit_fact(idx: int, body: dict = Body(...)):
        from ..tools import memory

        with memory._LOCK:
            items = memory.load()
            if not 0 <= idx < len(items):
                raise HTTPException(404, "topilmadi")
            text = " ".join(str(body.get("fact", "")).split())
            if not text:
                raise HTTPException(400, "bo'sh")
            items[idx] = text
            memory._save(items)
        return {"ok": True}

    @app.delete("/api/facts/{idx}", dependencies=[Auth])
    def delete_fact(idx: int):
        from ..tools import memory

        with memory._LOCK:
            items = memory.load()
            if not 0 <= idx < len(items):
                raise HTTPException(404, "topilmadi")
            items.pop(idx)
            memory._save(items)
        return {"ok": True}

    # ---------------------------------------------------------------- taught skills
    @app.get("/api/skills", dependencies=[Auth])
    def skills():
        from .. import teach

        out = []
        for s in teach.load_skills():
            out.append({"slug": Path(s["_path"]).stem, "name": s["name"], "app": s.get("app"),
                        "created": s.get("created"), "uses": s.get("uses", 0),
                        "params": teach.placeholders(s.get("steps", [])),
                        "steps": [dict(st, text_=teach.describe(st)) for st in s.get("steps", [])]})
        return {"skills": out, "teaching": teach.RECORDER.name if teach.RECORDER.active else None}

    def _skill_path(slug: str) -> Path:
        from .. import teach

        p = (teach.SKILLS_DIR / f"{slug}.json").resolve()
        if p.parent != teach.SKILLS_DIR.resolve() or not p.exists():
            raise HTTPException(404, "skill topilmadi")
        return p

    @app.put("/api/skills/{slug}", dependencies=[Auth])
    def edit_skill(slug: str, body: dict = Body(...)):
        p = _skill_path(slug)
        data = json.loads(p.read_text(encoding="utf-8"))
        if "steps" in body:
            steps = body["steps"]
            if not isinstance(steps, list) or not all(isinstance(s, dict) and s.get("type") in
                                                      ("launch", "click", "text", "key") for s in steps):
                raise HTTPException(400, "steps: noto'g'ri format")
            data["steps"] = [{k: v for k, v in s.items() if k != "text_"} for s in steps]
        if "name" in body and str(body["name"]).strip():
            data["name"] = " ".join(str(body["name"]).split())[:80]
        from .. import teach
        from ..tools.skills import _register_procedure

        if teach.placeholders(data["steps"]):
            data["params"] = teach.placeholders(data["steps"])
        else:
            data.pop("params", None)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        _register_procedure(data["name"], data.get("app", ""))  # yordamchi parametrlarni bilsin
        return {"ok": True}

    @app.delete("/api/skills/{slug}", dependencies=[Auth])
    def delete_skill(slug: str):
        from ..learning import Procedures

        p = _skill_path(slug)
        name = json.loads(p.read_text(encoding="utf-8")).get("name")
        p.unlink()
        P = Procedures()
        for it in P.load():
            if it.kind == "taught" and it.task == name:
                P.remove(it.id)
        return {"ok": True}

    @app.post("/api/skills/{slug}/run", dependencies=[Auth])
    async def run_skill(slug: str, body: dict = Body(default={})):
        from .. import teach

        data = json.loads(_skill_path(slug).read_text(encoding="utf-8"))
        params = body.get("params") if isinstance(body.get("params"), dict) else None
        res = await _in_tools_thread(teach.run_steps, data["steps"], int(body.get("start_step", 0)), 20.0, params)
        return res

    @app.post("/api/teach/start", dependencies=[Auth])
    def teach_start(body: dict = Body(...)):
        from ..tools import load_all

        return load_all().get("start_teaching").invoke({"name": str(body.get("name", ""))})

    @app.post("/api/teach/stop", dependencies=[Auth])
    def teach_stop(body: dict = Body(default={})):
        from ..tools import load_all

        return load_all().get("stop_teaching").invoke({"save": bool(body.get("save", True))})

    # ---------------------------------------------------------------- user-created tools
    def _ut_call(fn, *args):
        from ..tools.base import ToolError

        try:
            return fn(*args)
        except ToolError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/usertools", dependencies=[Auth])
    def usertools_list():
        from .. import usertools
        from ..tools import load_all

        load_all()
        return usertools.describe()

    @app.post("/api/usertools", dependencies=[Auth])
    def usertools_create(body: dict = Body(...)):
        from .. import usertools

        spec = _ut_call(usertools.save_spec, body)
        return {"ok": True, "tool": spec, "error": usertools.STATE["errors"].get(spec["name"])}

    @app.put("/api/usertools/{name}", dependencies=[Auth])
    def usertools_update(name: str, body: dict = Body(...)):
        from .. import usertools

        if _ut_call(usertools.get_spec, name) is None:
            raise HTTPException(404, "vosita topilmadi")
        spec = _ut_call(usertools.save_spec, body, name)
        return {"ok": True, "tool": spec, "error": usertools.STATE["errors"].get(spec["name"])}

    @app.delete("/api/usertools/{name}", dependencies=[Auth])
    def usertools_delete(name: str):
        from .. import usertools

        if not _ut_call(usertools.delete_spec, name):
            raise HTTPException(404, "vosita topilmadi")
        return {"ok": True}

    @app.post("/api/usertools/test", dependencies=[Auth])
    async def usertools_test(body: dict = Body(...)):
        """Saqlamasdan sinash. Skript faqat foydalanuvchi panelda tasdiqlasa (confirm: true) bajariladi."""
        from .. import usertools

        spec = _ut_call(usertools.validate, body.get("spec") or {})
        if spec["kind"] == "script" and body.get("confirm") is not True:
            raise HTTPException(400, "skriptni sinash uchun tasdiq kerak")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if STATE["runtime"] is not None:
            return await _in_tools_thread(usertools.execute, spec, args)
        if spec["kind"] == "skill":
            raise HTTPException(503, "skill'ni sinash uchun yordamchi ishlab turishi kerak")
        return await asyncio.to_thread(usertools.execute, spec, args)

    @app.put("/api/usertools/secrets/{secret}", dependencies=[Auth])
    def usertools_secret(secret: str, body: dict = Body(...)):
        from .. import usertools

        if not usertools.SECRET_RE.match(secret):
            raise HTTPException(400, "kalit nomi KATTA_HARF bilan")
        key = usertools.secret_key(secret)
        if key not in STORE.specs:
            raise HTTPException(404, "bu kalitni hech bir vosita ishlatmaydi")
        try:
            STORE.set(key, str(body.get("value") or "") or None)
        except PermissionError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True, "is_set": bool(STORE.get(key))}

    # ---------------------------------------------------------------- plugins
    @app.get("/api/plugins", dependencies=[Auth])
    def plugins_list():
        from .. import plugins

        return {"plugins": [p.__dict__ for p in plugins.PLUGINS.values()],
                "user_dir": str(plugins.USER_DIR)}

    @app.patch("/api/plugins/{pid}", dependencies=[Auth])
    def plugin_toggle(pid: str, body: dict = Body(...)):
        from .. import plugins

        if pid not in plugins.PLUGINS:
            raise HTTPException(404, "plugin topilmadi")
        plugins.set_enabled(pid, bool(body.get("enabled", True)))
        return {"ok": True, "restart_needed": True}

    # ---------------------------------------------------------------- chat
    @app.post("/api/chat", dependencies=[Auth])
    async def chat(body: dict = Body(...)):
        """Paneldan yozma buyruq. Xavfli amal tasdig'i panelda so'raladi (/api/confirmations)."""
        rt = _runtime()
        text = " ".join(str(body.get("text", "")).split())
        if not text:
            raise HTTPException(400, "bo'sh")
        if rt._task is not None:
            raise HTTPException(409, "Yordamchi hozir boshqa buyruqni bajaryapti")
        agent = rt.agent

        async def turn():
            old = agent.confirm
            agent.confirm = CONFIRM
            try:
                return await rt.turn(text, source="panel")
            finally:
                agent.confirm = old

        fut = asyncio.run_coroutine_threadsafe(turn(), rt.loop)
        res = await asyncio.wrap_future(fut)
        if res is None:
            return {"ok": False, "cancelled": True}
        from ..agent import Reply

        if isinstance(res, Reply):
            return {"ok": True, "reply": res.text, "steps": res.steps, "ms": round(res.ms)}
        return {"ok": False, "error": getattr(res, "error", "xato")}

    @app.get("/api/chat/history", dependencies=[Auth])
    def chat_history(limit: int = 200):
        """Panel, ovoz va konsol suhbatlari — loglardan (yordamchi qayta ishga tushsa ham saqlanadi)."""
        from ..runtime import read_conversation

        items = read_conversation(_log_dir(), days=7, limit=max(1, min(limit, 2000)))
        return {"items": [{k: v for k, v in r.items() if k != "ts_epoch"} for r in items]}

    @app.post("/api/chat/cancel", dependencies=[Auth])
    def chat_cancel():
        _runtime().cancel()
        return {"ok": True}

    @app.get("/api/confirmations", dependencies=[Auth])
    def confirmations():
        return {"pending": [{"id": c["id"], "text": c["text"]} for c in CONFIRM.pending.values()]}

    @app.post("/api/confirmations/{cid}", dependencies=[Auth])
    def answer(cid: str, body: dict = Body(...)):
        if not CONFIRM.answer(cid, bool(body.get("approve"))):
            raise HTTPException(404, "tasdiq topilmadi yoki muddati o'tgan")
        return {"ok": True}

    # ---------------------------------------------------------------- logs
    def _log_dir() -> Path:
        rt = STATE["runtime"]
        return rt.settings.log_dir if rt is not None else ROOT / "logs"

    @app.get("/api/logs", dependencies=[Auth])
    def logs(day: str | None = None, limit: int = 300, kinds: str | None = None, q: str | None = None):
        d = day or date.today().isoformat()
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(400, "day: YYYY-MM-DD") from e
        path = _log_dir() / f"{d}.jsonl"
        if not path.exists():
            return {"day": d, "items": [], "days": _days()}
        want = set(kinds.split(",")) if kinds else None
        out = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if want and r.get("kind") not in want:
                    continue
                if r.get("kind") == "voice" and r.get("ev") in ("state", "vad", "gate_ms") and not want:
                    continue
                if q and q.lower() not in line.lower():
                    continue
                out.append(r)
        return {"day": d, "items": out[-max(1, min(limit, 5000)):], "days": _days()}

    def _days() -> list[str]:
        return sorted((p.stem for p in _log_dir().glob("*.jsonl")), reverse=True)[:60]

    @app.websocket("/api/logs/stream")
    async def log_stream(ws: WebSocket):
        token = ws.cookies.get(COOKIE) or ws.query_params.get("token", "")
        if not hmac.compare_digest(token, api_token()):
            await ws.close(code=4401)
            return
        await ws.accept()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def push(line: str) -> None:
            loop.call_soon_threadsafe(lambda: q.full() or q.put_nowait(line))

        from ..runtime import TurnLog

        TurnLog.listeners.append(push)
        try:
            while True:
                line = await q.get()
                await ws.send_text(line)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if push in TurnLog.listeners:
                TurnLog.listeners.remove(push)

    # ---------------------------------------------------------------- plugin routers
    from .. import plugins

    for pid, router in plugins.routers():
        app.include_router(router, prefix=f"/api/plugins/{pid}", dependencies=[Auth])

    return app


# --------------------------------------------------------------------------- server

def free_port(host: str, start: int, tries: int = 20) -> int | None:
    """Bo'sh portni topadi. Windows'da SO_EXCLUSIVEADDRUSE: band portni "bo'sh" deb o'ylamaslik uchun."""
    import socket

    for port in range(start, start + tries):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    return None


def start_server(runtime=None, assistant=None, host: str | None = None, port: int | None = None) -> str:
    """Fon oqimida uvicorn. Panel manzilini (birinchi kirish tokeni bilan) qaytaradi."""
    import uvicorn

    STATE["runtime"], STATE["assistant"] = runtime, assistant
    if STATE["server"] is not None:
        return STATE["url"]
    host = host or STORE.get("api.host") or "127.0.0.1"
    wanted = int(port or STORE.get("api.port") or 8765)
    port = free_port(host, wanted)  # boshqa nusxa (yoki dastur) portni band qilgan bo'lsa — keyingisi
    if port is None:
        raise RuntimeError(f"{wanted}-{wanted + 19} portlarning hammasi band")
    config = uvicorn.Config(create_app(), host=host, port=port, log_level="warning", access_log=False,
                            lifespan="off")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # asosiy oqim Ctrl+C'ni o'zi boshqaradi
    threading.Thread(target=server.run, daemon=True, name="panel-api").start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        server.should_exit = True
        raise RuntimeError(f"panel {host}:{port} da ishga tushmadi")
    STATE["port_note"] = f"{wanted}-port band, {port} ishlatildi" if port != wanted else None
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    STATE["server"] = server
    STATE["url"] = f"http://{shown}:{port}/?token={api_token()}"
    return STATE["url"]
