"""Ekranni ko'rish: skrinshot + Gemini (vision) — "ekranda nima yozilgan?", "bu xato nima degani?".

Java/Swing kabi ichini Windows'ga ko'rsatmaydigan dasturlar (Burp Suite) uchun ham ishlaydi.
Maxfiylik: skrinshot Google Gemini API'ga yuboriladi. Kalit kiritilmaguncha vosita ishlamaydi.
"""

from __future__ import annotations

import base64
from typing import Annotated

import httpx

MANIFEST = {
    "id": "screen_vision",
    "name": "Ekranni ko'rish (Gemini)",
    "version": "0.1.0",
    "description": "Ekran rasmini Gemini'ga yuborib, unda nima borligini tushuntiradi. Rasm Google'ga yuboriladi.",
    "settings": [
        {"key": "api_key", "label": "Gemini API kaliti", "type": "secret", "env": ["GEMINI_API_KEY"],
         "help": "aistudio.google.com dan olinadi"},
        {"key": "model", "label": "Model", "default": "gemini-2.5-flash", "help": "Rasmni tushunadigan Gemini modeli"},
        {"key": "monitor", "label": "Monitor raqami", "type": "int", "default": 1,
         "help": "1 = asosiy monitor, 0 = barcha monitorlar birga"},
    ],
}

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def screenshot_png(monitor: int = 1) -> bytes:
    import mss
    import mss.tools

    with mss.mss() as sct:
        mons = sct.monitors
        mon = mons[monitor] if 0 <= monitor < len(mons) else mons[1]
        shot = sct.grab(mon)
        return mss.tools.to_png(shot.rgb, shot.size)


def ask_gemini(api_key: str, model: str, question: str, png: bytes, timeout: float = 60.0) -> str:
    body = {"contents": [{"role": "user", "parts": [
        {"text": question},
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
    ]}]}
    r = httpx.post(API.format(model=model), json=body, timeout=timeout, headers={"x-goog-api-key": api_key})
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip() or "(bo'sh javob)"


def list_models(api_key: str, timeout: float = 20.0) -> list[dict]:
    """Kalit bilan mavjud Gemini modellari (faqat matn/rasm generatsiya qiladiganlari)."""
    out, token = [], None
    for _ in range(10):
        params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params=params, timeout=timeout,
                      headers={"x-goog-api-key": api_key})
        r.raise_for_status()
        data = r.json()
        out += [m for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def setup(ctx) -> None:
    @ctx.check
    def gemini_check() -> dict:
        key = ctx.setting("api_key")
        if not key:
            return {"ok": None, "summary": "Gemini kaliti kiritilmagan (ixtiyoriy)"}
        models = list_models(key)
        names = [m["name"].removeprefix("models/") for m in models]
        chosen = ctx.setting("model") or "gemini-2.5-flash"
        details = [{"name": m["name"].removeprefix("models/"),
                    "info": ", ".join(x for x in (m.get("displayName"),
                                                  f"{m['inputTokenLimit']:,} token".replace(",", " ")
                                                  if m.get("inputTokenLimit") else None) if x)} for m in models]
        res = {"ok": True, "summary": f"{len(names)} ta model mavjud", "details": details, "options": {"model": names}}
        if chosen not in names:
            res["ok"] = False
            res["summary"] += f"; tanlangan '{chosen}' yo'q"
        return res

    @ctx.tool(timeout=90)
    def look_at_screen(question: Annotated[str, "What to find out from the screen, in the user's language"]) -> dict:
        """Take a screenshot and ask a vision model about it: read text, explain an error, find where a
        button is, describe what is open. Use when UI elements are not readable by other tools."""
        key = ctx.setting("api_key")
        if not key:
            return {"ok": False, "error": "Gemini API key is not set (panel: Sozlamalar -> Ekranni ko'rish)"}
        png = screenshot_png(int(ctx.setting("monitor", 1) or 1))
        prompt = ("You see a screenshot of the user's Windows computer. Answer briefly and concretely in Uzbek "
                  f"(Latin script). Question: {question}")
        answer = ask_gemini(key, ctx.setting("model") or "gemini-2.5-flash", prompt, png)
        return {"answer": answer, "image_bytes": len(png)}
