# Xavfsizlik

AI-yordamchi kompyuteringizni boshqaradi, shuning uchun xavfsizlik birinchi o'rinda.

## Himoya choralari

- **Tasdiq**: xabar yuborish (Telegram va boshqa messenjerlar), fayllarni o'chirish yoki ko'p fayl ko'chirish,
  PowerShell buyruqlari, jarayonni o'ldirish, kompyuterni o'chirish, ovozni keskin pasaytirish — bajarilishidan
  oldin ovozda yoki panelda so'raladi. Panelda istalgan vositani "doim tasdiq so'rasin" qilish mumkin;
  xavf darajasini pasaytirib bo'lmaydi.
- **Favqulodda to'xtatish**: Ctrl+Alt+X yoki ovozda "to'xta".
- **Kalitlar**: tizim saqlagichida (Windows Credential Manager, macOS Keychain, Secret Service) saqlanadi,
  `config.json`ga yozilmaydi va panel/API hech qachon qaytarmaydi.
- **Panel**: standart holatda faqat `127.0.0.1`. Har so'rov token talab qiladi (HttpOnly, SameSite=Strict
  cookie yoki `Authorization: Bearer`), boshqa saytdan o'zgartiruvchi so'rovlar rad etiladi.
  Tarmoqqa ochsangiz (`api.host = 0.0.0.0`), uni faqat ishonchli tarmoqda yoki VPN/HTTPS proksi orqali qiling.
- **Foydalanuvchi vositalari**: faqat paneldan (token bilan) yaratiladi, model o'zi vosita yarata olmaydi.
  Skript vositasi doim tasdiq so'raydi, parametrlar matnga emas muhit o'zgaruvchisiga beriladi. API vositasi
  URL hostini model o'zgartira olmaydi, redirect kuzatilmaydi, kalitlar natijada `***` bilan yashiriladi.
- **O'rgatish rejimi**: parol maydonlariga yozilgan matn saqlanmaydi; takrorlashda o'sha joyda to'xtaydi.
- **Maxfiylik**: loglar va xotira faqat sizning kompyuteringizda (`logs/`, `data/`). Kutish rejimida
  eshitilgan qisqa gaplar STT serveringizga yuboriladi (o'chirish: `voice.stt_wake = false`).
  "Ekranni ko'rish" plugin'i skrinshotni Google Gemini'ga yuboradi (faqat kalit kiritilsa).

## Zaiflik haqida xabar berish

Iltimos, ommaviy issue ochmang — repozitoriy egasiga GitHub'ning "Report a vulnerability"
(private security advisory) orqali yozing.
