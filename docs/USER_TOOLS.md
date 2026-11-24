# O'z vositangizni yaratish

Kod yozmasdan, panelning **Mening vositalarim** sahifasidan yangi vosita qo'shasiz. U saqlangan zahoti
yordamchi uni ishlata boshlaydi, qayta ishga tushirish shart emas. Har bir vosita `data/tools/<nom>.json`
faylida saqlanadi. Bu faylni **JSON** tugmasi bilan yuklab olib boshqalarga ulashish yoki
**JSON import** bilan qo'shish mumkin. Kalitlar JSON ichiga yozilmaydi.

**Tavsif** eng muhim maydon: yordamchi vositani qachon ishlatishni shu matndan biladi.
**Parametrlar** esa yordamchi har safar to'ldiradigan qiymatlar (shahar, kontakt, matn...).

## 1. API (HTTP)

Istalgan xizmat API'sini chaqiradi.

| Maydon | Misol |
|---|---|
| URL | `https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m` |
| Sarlavhalar | `Authorization: Bearer {secret:CRM_KALIT}` |
| Tana (POST/PUT) | `{"text": "{matn}", "chat_id": 123}` |
| Javobdan olinadigan qism | `current` yoki `data.items.0.name` |

URL, sarlavha va tanada ikki xil o'rinbosar ishlatiladi:
- `{param}` o'rniga parametr qiymati qo'yiladi. U URL'da kodlanadi, JSON tanada esa ekranlanadi.
- `{secret:NOM}` o'rniga kalit qo'yiladi. Vosita kalitni birinchi marta tilga olganida, panelning
  **Vosita kalitlari** bo'limida uni kiritish maydoni paydo bo'ladi.

**Xavfsizlik qoidalari:**
- URL'ning host qismida o'rinbosar bo'lishi mumkin emas, shuning uchun model kalitni boshqa serverga
  yubora olmaydi.
- Qayta yo'naltirishlar (redirect) kuzatilmaydi.
- Kalit javobda yoki xato matnida uchrasa, `***` bilan yashiriladi.
- GET so'rovlari standart holatda xavfsiz turadi. Boshqa metodlar har safar tasdiq so'raydi.

## 2. Skript

PowerShell, sh yoki Python skripti. Parametrlar matnga qo'shilmaydi, **muhit o'zgaruvchisi** bo'lib keladi.
Shuning uchun buyruq in'ektsiyasi bo'lmaydi.

```powershell
# parametr: papka
Get-ChildItem $env:P_PAPKA | Measure-Object | Select-Object -ExpandProperty Count
```

- Python skriptlarida parametr `os.environ["P_PAPKA"]` orqali, sh skriptlarida `$P_PAPKA` orqali olinadi.
- Kalitlar `$env:SECRET_<NOM>` orqali olinadi. Yordamchining o'z kalitlari (LLM va boshqalar)
  skriptga berilmaydi.
- Skript **har doim** tasdiq so'raydi. Buni o'zgartirib bo'lmaydi.

## 3. O'rgatilgan skill (parametr bilan)

1. Ishni bir marta ko'rsatib o'rgating. Masalan, Telegramni ochib, "Ali" ni qidirib, xabar yozasiz.
2. **O'rgatilgan skill'lar** sahifasida **Tahrirlash** tugmasini bosing va yozilgan matnni
   `{kontakt}` hamda `{xabar}` ga almashtiring.
3. **Vositaga aylantirish** tugmasini bosing. Parametrlar avtomatik to'ldiriladi.

Shundan keyin "Jarvis, Telegramda Sardorga ertaga uchrashuv bor deb yoz" desangiz, yordamchi o'rgatilgan
qadamlarni `kontakt=Sardor`, `xabar=...` qiymatlari bilan takrorlaydi. Parametrli skill'ni vositaga
aylantirmasdan ham ishlatsa bo'ladi: yordamchi `run_skill(..., params=...)` ni o'zi chaqiradi.

## Murakkabroq ish kerakmi?

Fon jadvali, voqealarga obuna, o'z API yo'llari yoki kutubxonalar kerak bo'lsa, [plugin](PLUGINS.md) yozing.
