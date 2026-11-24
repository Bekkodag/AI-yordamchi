"""Ulanishni tekshirish: kalitlar to'g'rimi, server nimalarni taklif qiladi (modellar, ovozlar...).

Natija panelda ko'rsatiladi; topilgan ro'yxatlar (`options`) sozlamalarda tanlash ro'yxatiga aylanadi.
Plugin o'z tekshiruvini `ctx.check(fn)` bilan qo'shadi — masalan Gemini kaliti bilan modellar soni.

Har bir tekshiruv natijasi:
    {"id", "label", "ok": True|False|None (None = o'tkazib yuborildi), "ms", "summary",
     "details": [...], "error"}
Maxfiy qiymat natijaga hech qachon tushmaydi.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from typing import Any, Awaitable, Callable

CheckFn = Callable[[], Awaitable[dict] | dict]

LAST: dict[str, Any] = {"results": [], "options": {}, "ts": None}


def explain(e: BaseException) -> str:
    """Tarmoq xatosini odam tushunadigan qilib (o'zbekcha)."""
    text = f"{type(e).__name__}: {e}"
    low = text.lower()
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return f"Kalit noto'g'ri yoki ruxsat yo'q (HTTP {status})"
    if status == 404:
        return "Manzil topilmadi (HTTP 404): URL yoki yo'lni tekshiring"
    if status:
        try:
            body = e.response.text[:200]  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            body = ""
        if "key" in body.lower() and ("invalid" in body.lower() or "not valid" in body.lower()):
            return f"Kalit noto'g'ri (HTTP {status})"
        return f"Server xatosi (HTTP {status})" + (f": {body}" if body else "")
    if isinstance(e, ssl.SSLError) or "certificate" in low or "ssl" in low:
        return ("TLS sertifikat xatosi: 'TLS server nomi' yoki CA faylini tekshiring "
                f"(texnik: {text[:160]})")
    if "timeout" in low or isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "Server javob bermadi (vaqt tugadi)"
    if "connect" in low or "getaddrinfo" in low or "name resolution" in low:
        return f"Serverga ulanib bo'lmadi: manzil yoki tarmoqni tekshiring ({text[:160]})"
    return text[:300]


def _settings():
    from .config import Settings

    return Settings.load(require=False)


async def _get_json(http, url: str, headers: dict) -> Any:
    r = await http.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


async def _list_models(http, s, h: dict):
    """/models — Anthropic o'z kalit sarlavhasini (x-api-key) ham talab qilishi mumkin: shuni ham sinaymiz."""
    try:
        return await _get_json(http, f"{s.llm_base}/models", h)
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403) and "anthropic.com" in s.llm_base:
            return await _get_json(http, f"{s.llm_base}/models",
                                   {"x-api-key": s.gateway_token, "anthropic-version": "2023-06-01"})
        raise


async def check_llm() -> dict:
    from .gateway import make_http

    s = _settings()
    if not s.llm_base or not s.gateway_url:
        return {"ok": False, "summary": "LLM manzili kiritilmagan"}
    if not s.gateway_token:
        return {"ok": False, "summary": "LLM API kaliti kiritilmagan"}
    http = make_http(s, timeout=20.0)
    h = {"Authorization": f"Bearer {s.gateway_token}"}
    try:
        data = await _list_models(http, s, h)
        models = data.get("data", data) if isinstance(data, dict) else data
        models = [m for m in models if isinstance(m, dict) and m.get("id")]
        details = [{"name": m["id"],
                    "info": ", ".join(str(x) for x in (m.get("kind"), m.get("owned_by"),
                                                       f"{m['context_length']:,} token kontekst".replace(",", " ")
                                                       if m.get("context_length") else None) if x)}
                   for m in models]
        ids = [m["id"] for m in models]
        res: dict[str, Any] = {"ok": True, "summary": f"{len(ids)} ta model", "details": details,
                               "options": {"llm.model": ids}}
        if s.model and ids and s.model not in ids:
            res["ok"] = False
            res["summary"] += f"; tanlangan '{s.model}' ro'yxatda yo'q"
            return res
        # Tanlangan model haqiqatan javob beradimi (kalit + model + kvota)
        t0 = time.perf_counter()
        r = await http.post(f"{s.llm_base}/chat/completions", headers=h, json={
            "model": s.model, "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]})
        r.raise_for_status()
        res["summary"] += f"; '{s.model}' javob berdi ({round((time.perf_counter() - t0) * 1000)} ms)"
        return res
    finally:
        await http.aclose()


async def check_speech() -> dict:
    from .gateway import make_http

    s = _settings()
    from .settings import STORE

    if not s.speech_base or (s.provider != "custom" and not STORE.get("speech.base_url")):
        # OpenAI/Claude/DeepSeek... o'zbekcha STT/TTS bermaydi — nutq serveri alohida ko'rsatilishi kerak
        return {"ok": None, "summary": "Nutq serveri ko'rsatilmagan (ovozli rejim uchun kerak)"}
    http = make_http(s, timeout=20.0)
    try:
        data = await _get_json(http, f"{s.speech_base}/v1/tts/voices", {"Authorization": f"Bearer {s.speech_key}"})
    finally:
        await http.aclose()
    voices = data if isinstance(data, list) else data.get("voices", [])
    voices = [v if isinstance(v, dict) else {"name": str(v)} for v in voices]
    langs = sorted({lang for v in voices for lang in (v.get("languages") or [])})
    details = []
    for v in voices:
        bits = [v.get("checkpoint"), "/".join(v.get("languages") or []) or None,
                f"namuna {v['seconds']} s" if v.get("seconds") else None,
                None if v.get("registered", True) else "ro'yxatdan o'tmagan"]
        details.append({"name": v.get("name"), "info": ", ".join(str(b) for b in bits if b)})
    from .settings import STORE

    chosen = STORE.get("speech.voice") or ""
    names = [v.get("name") for v in voices if v.get("name")]
    res = {"ok": True, "summary": f"{len(names)} ta ovoz, tillar: {', '.join(langs) or '?'}", "details": details,
           "options": {"speech.voice": [""] + names, "speech.language": langs}}
    if chosen and chosen not in names:
        res["ok"] = False
        res["summary"] += f"; tanlangan ovoz '{chosen}' topilmadi"
    return res


def _core_checks() -> list[tuple[str, str, CheckFn]]:
    return [("llm", "LLM", check_llm), ("speech", "Nutq (STT/TTS)", check_speech)]


def _plugin_checks() -> list[tuple[str, str, CheckFn]]:
    from . import plugins

    out = []
    for pid, ctx in list(plugins.CONTEXTS.items()):
        for label, fn in getattr(ctx, "_checks", []):
            out.append((f"plugin:{pid}", label or plugins.PLUGINS[pid].name, fn))
    return out


async def _run_one(cid: str, label: str, fn: CheckFn, timeout: float) -> dict:
    t0 = time.perf_counter()
    try:
        if asyncio.iscoroutinefunction(fn):
            res = await asyncio.wait_for(fn(), timeout)
        else:  # oddiy funksiya (masalan httpx bilan) — event loop'ni to'sib qo'ymasin
            res = await asyncio.wait_for(asyncio.to_thread(fn), timeout)
        res = dict(res or {})
    except Exception as e:  # noqa: BLE001 — bitta tekshiruv boshqalarini to'xtatmaydi
        res = {"ok": False, "summary": explain(e)}
    res.setdefault("ok", False)
    opts = res.pop("options", {}) or {}
    if cid.startswith("plugin:"):  # plugin o'z kalit nomini beradi -> to'liq nom
        pid = cid.split(":", 1)[1]
        opts = {(k if k.startswith("plugin.") else f"plugin.{pid}.{k}"): v for k, v in opts.items()}
    return {"id": cid, "label": label, "ms": round((time.perf_counter() - t0) * 1000), **res, "_options": opts}


async def run_all(timeout: float = 30.0) -> dict:
    from . import plugins
    from .config import load_env

    load_env()  # .env'dagi kalitlar (masalan GEMINI_API_KEY) parallel tekshiruvlardan oldin ko'rinsin
    plugins.load_all_plugins()
    items =_core_checks() + _plugin_checks()
    results = await asyncio.gather(*[_run_one(cid, label, fn, timeout) for cid, label, fn in items])
    options: dict[str, list] = {}
    for r in results:
        options.update(r.pop("_options"))
    LAST.update(results=results, options=options, ts=time.time())
    return {"results": results, "options": options}
