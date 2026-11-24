"""Namuna plugin: o'z plugin'ingizni yozish uchun shu faylni nusxalang.

Bu plugin ko'rsatadi:
  * sozlama va maxfiy kalit e'lon qilish (panelda avtomatik forma bo'ladi)
  * vosita (tool) qo'shish — yordamchi uni o'zi chaqiradi
  * voqealarga obuna bo'lish (har javobdan keyin)
  * fon jadvali (har N soniyada)
  * o'z HTTP API'si (/api/plugins/hello_example/...)
  * o'z ma'lumot papkasi (data/plugins/hello_example/)

Standart holatda o'chiq: panel -> Plugin'lar -> yoqing -> qayta ishga tushiring.
"""

from __future__ import annotations

import json
import time
from typing import Annotated

MANIFEST = {
    "id": "hello_example",                 # papka nomi bilan bir xil bo'lishi shart
    "name": "Namuna plugin",
    "version": "0.1.0",
    "description": "Plugin yozishni o'rganish uchun namuna: vosita, sozlama, voqea, jadval, API.",
    "enabled_by_default": False,
    "settings": [
        {"key": "greeting", "label": "Salomlashish matni", "default": "Assalomu alaykum"},
        {"key": "demo_token", "label": "Namuna maxfiy kalit", "type": "secret"},
        {"key": "count_replies", "label": "Javoblarni sanash", "type": "bool", "default": True},
    ],
}


def setup(ctx) -> None:
    stats_path = ctx.data_dir / "stats.json"

    def load() -> dict:
        try:
            return json.loads(stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"replies": 0, "last": None}

    # 1) Vosita: model "salomlash" kerak bo'lganda chaqiradi
    @ctx.tool()
    def greet(name: Annotated[str, "Who to greet"]) -> dict:
        """Return a greeting for a person (example plugin tool)."""
        return {"text": f"{ctx.setting('greeting')}, {name}!",
                "has_secret": bool(ctx.setting("demo_token"))}  # kalitning o'zini hech qachon qaytarmang

    # 2) Voqea: har javobdan keyin
    def on_reply(ev: dict) -> None:
        if not ctx.setting("count_replies", True):
            return
        s = load()
        s["replies"] += 1
        s["last"] = ev.get("text", "")[:80]
        stats_path.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")

    ctx.on("reply", on_reply)

    # 3) Fon jadvali: har soatda (masalan, xodimlar javob berganini tekshirish shunday yoziladi)
    def hourly() -> None:
        ctx.log.info("hello_example: %s", time.strftime("%H:%M"))

    ctx.every(3600, hourly)

    # 4) O'z API'si: GET /api/plugins/hello_example/stats  (panel tokeni bilan himoyalangan)
    router = ctx.router()

    @router.get("/stats")
    def stats() -> dict:
        return load()
