# Hissa qo'shish

Rahmat! Har qanday yordam qadrlanadi: xato haqida xabar, tarjima, plugin, yangi platforma qatlami.

## Ishga tushirish

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[all,dev]"
.venv\Scripts\python -m pytest -q
```

## Qoidalar

- Yangi funksiya iloji boricha **plugin** bo'lsin ([docs/PLUGINS.md](docs/PLUGINS.md)) — yadro kichik va barqaror qoladi.
- Har bir tuzatish uchun regressiya testi (`tests/`). Testlar haqiqiy LLM, mikrofon yoki foydalanuvchi
  fayllariga tegmasligi kerak (soxta LLM, `tmp_path`, `monkeypatch`).
- Kod izohlari o'zbekcha yoki inglizcha; model ko'radigan matnlar (vosita tavsiflari, system prompt) inglizcha.
- Shaxsiy ma'lumot (ismlar, kontaktlar, ichki manzillar, loglar) commit qilinmasin — `data/`, `logs/`,
  `.env`, `private/` `.gitignore`da.
- Xavfli amal (yuborish, o'chirish, pul, tizim sozlamasi) qo'shsangiz — `risk="confirm"`.

## Pull request

1. Fork → branch → o'zgarish + test.
2. `pytest -q` yashil bo'lsin.
3. PR tavsifida nima va nima uchun o'zgarganini yozing.
