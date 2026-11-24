# Plugin yozish

Plugin — yordamchiga yangi funksiya qo'shadigan alohida modul. Asosiy kodga tegmaydi: plugin buzilsa
ham yordamchi ishlashda davom etadi, panelda esa plugin "xato" holati va sababi bilan ko'rinadi.

Masalan: kritik vaziyatda telefon qilish, xodimlarga berilgan topshiriqlar javobini nazorat qilish,
CRM/pochta/Telegram bot integratsiyasi, uy jihozlari.

## Tuzilishi

```
plugins/
  my_plugin/            # papka nomi = plugin id (a-z, 0-9, _)
    plugin.py           # MANIFEST + setup(ctx)
    ...                 # istalgan qo'shimcha modul
```

To'liq namuna: [`plugins/hello_example/plugin.py`](../plugins/hello_example/plugin.py).

```python
from typing import Annotated

MANIFEST = {
    "id": "phone_alerts",
    "name": "Telefon qo'ng'irog'i",
    "version": "0.1.0",
    "description": "Kritik vaziyatda egasiga qo'ng'iroq qiladi",
    "enabled_by_default": True,
    "settings": [
        {"key": "api_key", "label": "Provayder kaliti", "type": "secret", "env": ["PHONE_API_KEY"]},
        {"key": "phone", "label": "Telefon raqami", "required": True},
        {"key": "retries", "label": "Qayta urinishlar", "type": "int", "default": 2},
    ],
}


def setup(ctx):
    @ctx.tool(risk="confirm")
    def call_owner(message: Annotated[str, "What to say on the call"]) -> dict:
        """Call the owner's phone and read the message aloud."""
        ...
        return {"called": True}

    ctx.on("error", lambda ev: ctx.log.warning("xato: %s", ev["error"]))
    ctx.every(300, lambda: None)            # har 5 daqiqada fon ishi
```

## `ctx` (PluginContext) imkoniyatlari

| | |
|---|---|
| `@ctx.tool(risk="safe"\|"confirm", name=, timeout=, confirm=, confirm_if=)` | Vosita qo'shish. Docstring va `Annotated` izohlari modelga ko'rsatiladi. Yadro vositasi nomini egallab bo'lmaydi. |
| `ctx.setting(key, default)` / `ctx.set_setting(key, value)` | `MANIFEST["settings"]`dagi qiymatlar (panelda avtomatik forma). `secret` turidagilar tizim saqlagichida. |
| `ctx.on(event, fn)` | Voqealar: `startup`, `shutdown`, `user_text`, `tool_finished`, `reply`, `error`, `voice`. |
| `ctx.every(seconds, fn, first_after=)` | Fon jadvali. Xato bo'lsa yoziladi, jadval davom etadi. |
| `ctx.router()` | O'z HTTP yo'llari: `/api/plugins/<id>/...` (panel tokeni bilan himoyalangan). |
| `ctx.notify(text, level)` | Foydalanuvchiga xabar (log va panel). |
| `ctx.data_dir` | Plugin'ning o'z papkasi: `data/plugins/<id>/`. |
| `ctx.runtime` | Ishlayotgan runtime (agent, log). Panel rejimida `None`. |
| `ctx.log` | `logging.Logger`. |

## Qoidalar

- Maxfiy qiymatni hech qachon natija, log yoki API javobida qaytarmang.
- Tashqi xizmatga ma'lumot yuboradigan vosita — `MANIFEST["description"]`da aniq yozing.
- Foydalanuvchi nomidan xabar yuborish, pul, o'chirish kabi amallar — `risk="confirm"`.
- Uzoq ish — `ctx.every` yoki o'z oqimingizda; vosita funksiyasi `timeout` ichida tugashi kerak.

## Yoqish

Panel → **Plugin'lar** → yoqing → **Qayta ishga tushirish**. Sozlamalar **Sozlamalar** sahifasida plugin
nomi bilan alohida bo'limda chiqadi.
