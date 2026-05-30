# Parfumerose — Xodimlar davomati va maosh boti

Telegram bot (aiogram 2.x) — xodimlarning **ish davomatini** (geolokatsiya orqali ishga
kelish/ketish), **maoshlarini**, **ish soatlarini** va **hisobotlarini** boshqaradi.
Bir nechta **filial** (branch) va rol darajalarini (xodim, filial admini, katta admin)
qo'llab-quvvatlaydi. Adminlar uchun **AI yordamchi** (Gemini + Azure ovozdan-matnga) mavjud.

## Asosiy imkoniyatlar

- 🟢 **Davomat:** geolokatsiya bilan ishga kelish/ketish, ruxsat etilgan radius tekshiruvi
- 💰 **Maosh:** oylik maosh, to'lovlar tarixi, yakuniy hisob-kitob
- 📊 **Hisobotlar:** kunlik/oylik, kech qolganlar, ishda bo'lmaganlar
- ⏰ **Avtomatik eslatmalar:** ertalabki/kechki brifing, kech qolish va kelmaganlik eslatmalari (APScheduler)
- 🏢 **Ko'p filial:** har bir filialning o'z admini, lokatsiyasi va guruhi
- 🤖 **AI yordamchi (adminlar uchun):** tabiiy til va ovozli so'rovlar bilan ma'lumot olish

## Texnologiyalar

- **aiogram 2.25** — Telegram bot framework
- **asyncpg + PostgreSQL** — ma'lumotlar bazasi (Neon pooler bilan mos)
- **Redis** (ixtiyoriy) — FSM holatlari uchun (yo'q bo'lsa MemoryStorage)
- **APScheduler** — rejalashtirilgan vazifalar
- **google-generativeai (Gemini)** + **Azure Speech** — AI va ovozdan-matnga

## Ishga tushirish (lokal)

```bash
pip install -r requirements.txt
cp .env.example .env.local      # qiymatlarni to'ldiring
APP_ENV=local python bot.py
```

## Docker

```bash
docker compose up --build
```

## Muhit o'zgaruvchilari

Asosiylari (to'liq ro'yxat `.env.example`da):

| O'zgaruvchi | Tavsif |
|---|---|
| `BOT_TOKEN` | BotFather'dan olingan token |
| `DATABASE_URL` | PostgreSQL ulanish satri |
| `SUPERADMINS` | Katta adminlar tg_id'lari (vergul bilan) |
| `BRANCH_1_*`, `BRANCH_2_*` | Filial sozlamalari (nom, lat/lon, radius, adminlar) |
| `REDIS_URL` | (ixtiyoriy) FSM uchun Redis |
| `GEMINI_API_KEYS` | AI yordamchi uchun Gemini kalitlari |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Ovozdan-matnga |

## Deploy

`render.yaml` — Render.com blueprint: bot (Docker web-service) + PostgreSQL + Redis.
Sirlar (`BOT_TOKEN`, `GEMINI_API_KEYS`, ...) Render dashboard'da `sync: false` orqali
qo'lda kiritiladi — repozitoriyga yozilmaydi.

## Loyiha tuzilishi

```
bot.py                 — kirish nuqtasi, scheduler, startup/shutdown
loader.py              — Bot/Dispatcher/storage
config.py              — muhit o'zgaruvchilari va filial konfiguratsiyasi
database.py            — asyncpg pool, sxema (init_db), barcha DB funksiyalari
ai_helpers.py          — Gemini AI yordamchi + Azure ovozdan-matnga
shared.py / keyboards.py / states.py / menu_overrides.py / employee_menu.py
handlers/
  user_handlers.py     — xodim oqimi (start, davomat, statistika)
  admin_handlers.py    — admin oqimi (xodimlar, maosh, hisobot, AI)
  admin_extensions.py  — admin yordamchi amallar
  admin_tools.py       — AI uchun "asbob" funksiyalari
```
