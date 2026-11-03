<div align="center">

# 📢 Kanal Bot

**Telegram kanal va guruhlarga rejali post yuboruvchi bot (AI bilan)**

Muallif: [bolaxoji3-collab](https://github.com/bolaxoji3-collab)

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)
![AI](https://img.shields.io/badge/AI-Groq-F55036)
![SQLite](https://img.shields.io/badge/DB-SQLite-003B57?logo=sqlite&logoColor=white)
![Termux](https://img.shields.io/badge/Platform-Termux-000000)

</div>

> [!NOTE]
> Bot Telegram qoidalariga va mamlakatingiz qonunlariga mos kontent uchun mo'ljallangan. Kanaldagi kontent uchun bot egasi javobgar.

---

## 📌 Umumiy ma'lumot

**Kanal Bot** o'zi admin bo'lgan kanal va guruhlarga belgilangan vaqtda xabar yuboradi. Xabar tayyor matn bo'lishi yoki AI tomonidan kanaldagi oxirgi postlarga qarab yozilishi mumkin: AI ularning tili, uslubi va uzunligini o'rganadi.

Hammasi tugmalar orqali boshqariladi. Botdan faqat `ADMIN_IDS` da ko'rsatilgan adminlar foydalana oladi.

## ✨ Imkoniyatlar

| Modul | Tavsif |
|-------|--------|
| 📢 **Kanallar** | Bot admin bo'lgan kanal va guruhlarni qo'shish, ko'rish, o'chirish |
| ⏰ **Rejalar** | Vaqt va hafta kunlari bo'yicha avtomatik post, to'xtatish, davom ettirish, hozir yuborish |
| 🤖 **AI post** | Kanal uslubida yangi post yozish, yuborishdan oldin ko'rish va qayta yozdirish |
| ✉️ **Hozir yuborish** | Matnni darhol kanalga yuborish |
| 📥 **Namunalar** | Eski postlarni forward qilib, AI'ga uslubni o'rgatish |
| ⚙️ **Sozlamalar** | AI modeli (ro'yxatdan tanlash), kontekst hajmi, vaqt zonasi |

## 🛠 O'rnatish

### Termux (Android)

```bash
pkg update -y
pkg install -y git python
cd ~
git clone https://github.com/bolaxoji3-collab/Kanal-bot.git
cd Kanal-bot
pip install -r requirements.txt
```

### Kerakli kalitlar

| Nima | Qayerdan olinadi |
|------|------------------|
| Bot tokeni | Telegramda [@BotFather](https://t.me/BotFather) → `/newbot` |
| Groq kaliti (bepul) | [console.groq.com](https://console.groq.com) → API Keys |
| Telegram ID | [@userinfobot](https://t.me/userinfobot) |

## ⚙️ Sozlash

Loyiha papkasida `.env` faylini yarating:

```bash
cat > .env << 'EOF'
BOT_TOKEN=BOTFATHER_TOKENI
AI_PROVIDER=groq
GROQ_API_KEY=GROQ_KALITI
ADMIN_IDS=SIZNING_ID
TZ_OFFSET_HOURS=5
CONTEXT_POSTS=15
EOF
```

| O'zgaruvchi | Tavsif |
|-------------|--------|
| `BOT_TOKEN` | BotFather bergan token |
| `AI_PROVIDER` | `groq` yoki `openai` |
| `GROQ_API_KEY` | Groq kaliti (`openai` tanlansa `OPENAI_API_KEY`) |
| `ADMIN_IDS` | Admin Telegram ID'lari, vergul bilan, probelsiz: `111,222` |
| `TZ_OFFSET_HOURS` | Vaqt zonasi (Toshkent va Farg'ona uchun `5`) |
| `CONTEXT_POSTS` | AI yozishda nechta oxirgi postga qarashi |
| `AI_MODEL` | Ixtiyoriy, modelni qo'lda belgilash |

## 🚀 Ishga tushirish

```bash
python bot.py
```

Ekranda **"Bot ishga tushdi"** chiqsa, bot tayyor. Botga Telegramda `/start` yozing va uni kanalga admin qilib qo'shing (kanalda **Post messages** huquqi bilan).

Fonda ishlatish:

```bash
termux-wake-lock
nohup python bot.py > bot.log 2>&1 &
```

To'xtatish: `pkill -f bot.py`

## 📖 Foydalanish

### Asosiy menyu

```
📢 Kanallarim        ➕ Kanal qo'shish
➕ Yangi reja        📋 Rejalarim
🤖 AI post           ✉️ Hozir yuborish
📥 Namuna qo'shish   ⚙️ Sozlamalar
```

### Kanal qo'shish

1. Botni kanal yoki guruhga **admin** qilib qo'shing. Bot o'zi ro'yxatga tushadi va sizga xabar beradi.
2. Yoki **➕ Kanal qo'shish** tugmasini bosib, `@username`, ID raqam yuboring yoki kanaldan bitta post forward qiling.

### Yangi reja

**➕ Yangi reja** → kanalni tanlang → vaqtni yozing (`09:00`) → hafta kunlarini tanlang → tayyor matn yoki AI → saqlash.

| Tur | Ishlashi |
|-----|----------|
| 📝 Tayyor matn | Siz yozgan matn belgilangan vaqtda yuboriladi |
| 🤖 AI | Har safar kanal uslubida yangi post yoziladi (topshiriq ixtiyoriy) |

> [!TIP]
> Kanaldagi eski postlarni botga forward qilsangiz, AI uslubni yaxshiroq o'rganadi. Yangi postlar o'zi saqlanadi.

## 📋 Talablar

| Talab | Versiya / Izoh |
|-------|----------------|
| Python | 3.10 yoki yuqori |
| `httpx` | `pip install -r requirements.txt` |
| Ma'lumotlar bazasi | SQLite (alohida o'rnatish shart emas) |
| Internet | Telegram va Groq/OpenAI API uchun |
| Platforma | Termux (Android) yoki istalgan Linux |

## 🔄 Yangilash

```bash
cd ~/Kanal-bot
git pull
```

`.env` va `bot.db` GitHub'da yo'q, shuning uchun yangilanganda o'zgarmaydi.

## 🔒 Xavfsizlik

- `.env` faylini hech qachon GitHub'ga yuklamang va hech kimga yubormang.
- Token yoki kalit oshkor bo'lsa, darhol yangilang: BotFather'da `/revoke`, Groq'da yangi kalit.
- `ADMIN_IDS` ga faqat ishonchli odamlarni qo'shing.
- Bepul AI limiti tugasa, AI post xato bilan to'xtashi mumkin. Bot sizga xabar beradi.

## ⚖️ Mas'uliyat

Loyiha ta'lim va shaxsiy foydalanish uchun. Bot orqali yuboriladigan kontent uchun bot egasi javobgar. Telegram foydalanish shartlariga va O'zbekiston qonunlariga rioya qiling.
