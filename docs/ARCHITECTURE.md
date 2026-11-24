# Arxitektura

```
┌──────────────────────────── yordamchi (Python, bitta jarayon) ────────────────────────────┐
│                                                                                            │
│  voice/            mikrofon → VAD → uyg'otish so'zi → STT ─┐        TTS → karnay            │
│  (assistant.py)    echo cancellation, filtr, ducking       │          ▲                     │
│                                                            ▼          │                     │
│  runtime.py        navbatlar, log, kill switch ────────► agent.py ────┘                     │
│                                                            │  (LLM + tool calling)          │
│                                                            ▼                                │
│  tools/            yadro vositalari (oynalar, UIA, fayllar, ovoz, brauzer, ...)             │
│  plugins/          plugin'lar (alohida modullar): vositalar, voqealar, jadvallar, API       │
│  learning.py       retseptlar va ilova o'ziga xosliklari (avtomatik o'rganish)              │
│  teach.py          ko'rsatib o'rgatish: yozib olish va takrorlash                           │
│  settings.py       sozlamalar reyestri (env > config.json > standart; kalitlar — keyring)   │
│  api/server.py     boshqaruv paneli API'si (FastAPI) + webui/ (statik, build'siz)           │
└────────────────────────────────────────────────────────────────────────────────────────────┘
          ▲ HTTP/WebSocket (token)                     ▲ OpenAI-mos API
     brauzer / desktop / telefon                 LLM va STT/TTS serverlari
```

## Asosiy oqim

1. **Ovoz** (`voice/assistant.py`): mikrofon kadrlari echo cancellation'dan o'tadi, VAD gap boshi va
   oxirini topadi, "Jarvis" so'zi (wake word modeli yoki STT) sessiyani boshlaydi. Sessiya ichidagi
   keyingi gaplar filtrdan o'tadi (`voice/gate.py`): musiqa, televizor va boshqalarga aytilgan gaplar
   e'tiborsiz qoladi.
2. **Navbat** (`runtime.py`): matn agentga beriladi, har voqea `logs/YYYY-MM-DD.jsonl`ga yoziladi va
   plugin'larga `BUS` orqali uzatiladi.
3. **Agent** (`agent.py`): system prompt + xotira + mos retseptlar/o'ziga xosliklar + tarix → LLM →
   vosita chaqiruvlari → natija → ... → javob. Xavfli vositalar oldidan tasdiq so'raladi (ovozda yoki
   panelda). Tsikl himoyasi, kontekstni siqish, vosita osilib qolsa executor almashtiriladi.
4. **O'rganish** (`learning.py`): muvaffaqiyatli navbatdan keyin fonda LLM retsept, fakt va ilova
   o'ziga xosligini yozadi. Keyingi gap salbiy bo'lsa ("yo'q, bo'lmadi") — o'chiriladi.

## Kengaytirish nuqtalari

| Nima qo'shmoqchisiz | Qayerga |
|---|---|
| Yangi funksiya (qo'ng'iroq, xodimlar nazorati, integratsiya) | `plugins/<id>/plugin.py` — [PLUGINS.md](PLUGINS.md) |
| Yangi yadro vositasi | `yordamchi/tools/*.py` (`@registry.tool`) |
| Ilova injiqligini kod bilan hal qilish | `yordamchi/tools/popups.py` (`RULES`) |
| Yangi sozlama | `settings.py` (`_core`) yoki plugin `MANIFEST["settings"]` |
| Panel sahifasi | `webui/assets/app.js` (`pages.*`) |

## Ma'lumotlar (repoga tushmaydi)

| Fayl | Mazmuni |
|---|---|
| `data/config.json` | paneldan saqlangan sozlamalar (maxfiy emas) |
| tizim saqlagichi / `data/secrets.json` | API kalitlar |
| `data/api_token` | panel tokeni |
| `data/memory.json` | faktlar |
| `data/procedures.json` | retseptlar, o'ziga xosliklar, o'rgatilgan skill indeksi |
| `data/skills/*.json` | o'rgatilgan skill qadamlari |
| `data/plugins/<id>/` | plugin ma'lumotlari |
| `logs/*.jsonl` | navbatlar va ovoz hodisalari |

## Platformaga bog'liq qism

Kompyuterni boshqarish (`tools/windows.py`, `_input.py`, `apps.py`, `system.py`, `audio_sessions.py`,
`browser.py`, `popups.py`, `teach.py`, `voice/aec.py` loopback) hozir Windows API'lariga tayanadi.
macOS/Linux uchun xuddi shu vosita nomlari va natija formatini beradigan alohida qatlam yoziladi;
agent, o'rganish, panel va plugin'lar platformadan mustaqil.
