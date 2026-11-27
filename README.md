# AI-yordamchi

**O'zbek tilida gaplashadigan, kompyuterni ovoz bilan boshqaradigan va o'rganib boradigan ochiq kodli yordamchi.**

> *English summary below.*

"Asalim, Word'ni och, yangi hujjat tayyorla" desangiz, yordamchi Word'ni ochadi, aktivatsiya oynasini o'zi
yopadi va yangi hujjat yaratadi. "Chromedagi bu linkni Telegramdan Aliga yubor" desangiz, havolani oladi,
Telegram'da kontaktni topadi va tasdiqlaganingizdan keyin yuboradi.

## Imkoniyatlar

- **Ovoz bilan boshqarish** — Wake word boshqaruv panelining "Ovoz" bo'limida istalgan nomga sozlanadi (masalan: Asalim, Malik, Komron),
  o'zbekcha tushunadi va javob beradi. Gapirayotganingizda boshqa ovozlarni pasaytiradi, kompyuterdan
  chiqayotgan musiqa so'zlarini buyruq deb olmaydi (echo cancellation).
- **Kompyuterni boshqarish** — dasturlar, oynalar, tugmalar, klaviatura, fayllar, brauzer vkladkalari,
  ovoz balandligi, YouTube, Telegram va boshqalar (50+ vosita).
- **O'rganadi** — muvaffaqiyatli bajarilgan vazifadan "retsept" yozib oladi; ilovalarning injiqliklarini
  ("Word ochilganda aktivatsiya oynasi chiqadi") eslab qoladi; "yo'q, bo'lmadi" desangiz noto'g'ri
  o'rganganini o'chiradi.
- **Ko'rsatib o'rgatish** — "Asalim, o'rgatish rejimi..." deysiz, kerakli ishni bir marta o'zingiz qilasiz,
  keyin yordamchi uni aynan takrorlaydi. Parollar yozib olinmaydi.
- **Boshqaruv paneli (Web UI)** — sozlamalar va maxfiy kalitlar, vositalar, retseptlar, skill'lar, faktlar,
  plugin'lar, jonli loglar va yozma suhbat. Telefondan ham ochiladi.
- **O'z vositangiz** — paneldan kod yozmasdan: API chaqiruvi (kalitlar bilan), skript yoki parametrli
  o'rgatilgan skill ("Telegramda {kontakt}ga {xabar} yoz"). [Batafsil](docs/USER_TOOLS.md)
- **Plugin'lar** — yangi funksiya asosiy kodga tegmasdan alohida modul bo'lib qo'shiladi.
- **Istalgan LLM** — panelda provayderni tanlaysiz: Claude, OpenAI, Gemini, DeepSeek, Mistral, Grok, Groq,
  OpenRouter, Together, kompyuterdagi Ollama / LM Studio yoki o'z serveringiz (istalgan OpenAI-mos API).
  "Ulanishni tekshirish" kalit ishlashini va mavjud modellar ro'yxatini ko'rsatadi.
- **Xavfsizlik** — xabar yuborish, fayl o'chirish, buyruq bajarish kabi amallar oldidan tasdiq so'raydi;
  kalitlar tizim saqlagichida (Windows Credential Manager) turadi; Ctrl+Alt+X — favqulodda to'xtatish.

## Platformalar

| | Holati |
|---|---|
| Windows 10/11 | ✅ to'liq |
| macOS, Linux | 🛠 rejada — kompyuterni boshqarish qatlami yozilishi kerak (panel va agent ishlaydi) |
| Android, iOS | 🛠 rejada — masofaviy pult (panel brauzerda hozir ham ochiladi) |

## O'rnatish

Talablar: Windows 10/11, Python 3.11+, OpenAI-mos LLM (tool calling bilan). Ovozli rejim uchun STT/TTS
serveri (masalan, o'zbek tilini qo'llaydigan o'z serveringiz).

```powershell
git clone https://github.com/Bekkodag/AI-yordamchi.git
cd AI-yordamchi
py -3 -m venv .venv
.venv\Scripts\pip install -e ".[all]"
.venv\Scripts\python -m yordamchi --panel --open      # panelda sozlamalarni kiriting
```

> **`py -3` haqida.** Windows'da `python` buyrug'i ko'pincha eski Python 2.7 ni ishga tushiradi.
> `python3` esa Microsoft Store'ni ochadi. `py` ishga tushirgichi o'rnatilgan Python 3 ni o'zi topadi.
> Bir nechta versiya bo'lsa, `py -0p` bilan ro'yxatini ko'ring va kerakligini tanlang, masalan `py -3.13`.
> `.venv` yaratilgandan keyin doim `.venv\Scripts\python` ishlatiladi.

Keyin:

```powershell
.venv\Scripts\python -m yordamchi --voice      # ovozli rejim (+ panel)
.venv\Scripts\python -m yordamchi              # yozma rejim (+ panel)
.venv\Scripts\python -m yordamchi --skills     # nimalarni bilishi
```

Panel manzili ishga tushganda konsolda chiqadi (`http://127.0.0.1:8765/?token=...`).
Sozlamalarni `.env` fayli bilan ham berish mumkin — namuna: [.env.example](.env.example).

## Hujjatlar

- [Arxitektura](docs/ARCHITECTURE.md) — qismlar qanday bog'langan
- [O'z vositangizni yaratish](docs/USER_TOOLS.md) — API, skript, parametrli skill
- [Plugin yozish](docs/PLUGINS.md) — yangi funksiya qo'shish
- [Xavfsizlik](SECURITY.md)
- [Hissa qo'shish](CONTRIBUTING.md)

## Muallif

**Bek-dag Farhod Eliboyev** — g'oya va loyiha muallifi ([github.com/Bekkodag](https://github.com/Bekkodag)).

## Litsenziya

[Apache-2.0](LICENSE). Loyihadan foydalanish, o'zgartirish va tarqatish erkin; nusxalarda muallif
haqidagi [NOTICE](NOTICE) fayli saqlanishi shart.

---

## English summary

**AI-yordamchi** ("AI assistant" in Uzbek) is an open-source voice assistant that controls a Windows PC,
speaks Uzbek, and learns from use. It works with any OpenAI-compatible LLM, has a web control panel
(settings and secrets, tools, learned recipes, taught skills, facts, plugins, live logs), lets users teach
new skills by demonstration, create their own tools (HTTP APIs, scripts, parametrised skills) without code, and is extensible through isolated plugins. Windows is fully supported;
macOS/Linux control layers and mobile remote clients are planned. Created by **Bek-dag Farhod Eliboyev**. Licensed under Apache-2.0.
