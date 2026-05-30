# 🛠 Parfumerose — To'liq Tuzatish Rejasi

> **Qanday ishlatiladi:** Har bir band `- [ ]` (bo'sh katakcha) bilan boshlanadi.
> Band bajarilgach, u `- [x] ✅` ga o'zgartiriladi. Bandlar **tartib bilan**, yuqoridan
> pastga qarab bajariladi. Har fazadan keyin tekshiruv (verify) bor.

**Konteks:** Loyiha avval "Arrizo Mebel" (mebel + buyurtma + web-admin) bo'lgan, keyin
HR/davomat botiga ("parfumerose") aylantirilgan. Shu sababli katta o'lik kod va
merge buglari bor.

---

## ⚠️ ASOSIY QAROR (Faza 2 dan oldin)

**Buxgalteriya bo'limi** (`handlers/accounting_handlers.py`, 2038 qator) — hech qayerda
import qilinmagan, butunlay o'lik. Parfyumeriya HR-boti uchun keraksiz (mebel qoldig'i).
**Qaror: O'CHIRIB TASHLANADI.** (Hammasi git'da saqlanadi — kerak bo'lsa qaytarish mumkin.)

---

## 🔴 FAZA 0 — Xavfsizlik (eng birinchi)

- [x] ✅ **0.1** `render.yaml`dagi ochiq `BOT_TOKEN`ni `sync: false` ga o'zgartirish, `ADMINS`ni ham env'ga ko'chirish.
- [ ] **0.2** BotFather'da botni `/revoke` qilib **yangi token olish** (eski token git tarixida — buzilgan hisoblanadi). *(Foydalanuvchi amali)*
- [ ] **0.3** (Ixtiyoriy) Token va sirlarni git tarixidan tozalash (`git filter-repo` yoki BFG).

---

## 🔴 FAZA 1 — Kritik runtime buglar

- [x] ✅ **1.1** `database.py` — `get_late_employees()` ni tuzatish: yopishib qolgan
  `if worker_id: ... ai_chat_sessions ...` blokini ([:1768–1782]) o'chirib, oxiriga
  `return late_employees` qo'yish. *(NameError → kech qolganlar hisoboti ishlamayapti)*
- [x] ✅ **1.2** `database_runtime_overrides.py` faylini va uning `bot.py`dagi importini o'chirish
  *(Neon `statement_cache_size=0` fix'ini bekor qilayotgan monkey-patch)*.
- [x] ✅ **1.3** `database.py`dagi dublikat `create_pool` ([:73]) ni o'chirish; to'liq versiyani ([:2688]) qoldirish.
- [x] **1.V** ✅ Tekshiruv: `py_compile` o'tadi; `pyflakes` da `undefined name 'worker_id'` va `redefinition of create_pool` yo'qolgan.

---

## 🟠 FAZA 2 — O'lik tizimlarni olib tashlash

### 2A. Buxgalteriya bo'limi (qarorga ko'ra — o'chirish)
- [x] ✅ **2.1** `handlers/accounting_handlers.py` faylini o'chirish (import qilinmagan, xavfsiz).
- [x] ✅ **2.2** `keyboards.py`dan buxgalteriya klaviaturalarini olib tashlash (orders/materials/category/calendar/financial bo'limlari).
- [x] ✅ **2.3** `states.py`dan `Accounting` va `MaterialCategory` state'larini olib tashlash.
- [x] ✅ **2.4** `handlers/admin_handlers.py`dan ishlatilmagan buxgalteriya importlarini olib tashlash ([:43–46], `Accounting` state).
- [x] ✅ **2.5** `database.py`dan faqat buxgalteriyaga tegishli funksiyalarni va `init_db`dagi
  `orders/material_categories/materials/order_materials/order_labor/order_assignments` jadvallarini
  olib tashlash. *(34 o'lik funksiya grep bilan tasdiqlab olib tashlandi; `materials` jadvali low-stock job uchun saqlandi, qolgan bo'sh jadvallar zararsiz qoldirildi.)*

### 2B. FastAPI "Web API" (umuman yo'q — buzilgan deploy)
- [x] ✅ **2.6** `Dockerfile.api`, `entrypoint.sh`, `start_server.ps1` fayllarini o'chirish (`web.main:app` mavjud emas).
- [x] ✅ **2.7** `render.yaml`dan `mojazdevbot-api` web-servisini olib tashlash.
- [x] ✅ **2.8** `requirements.txt`dan botga keraksiz `fastapi`, `uvicorn`, `python-jose`, `passlib`, `python-multipart` ni olib tashlash.
- [x] ✅ **2.9** `index.html` (hech qayerga yubormaydigan maket) faylini o'chirish.

### 2C. Web-login / admin-panel (o'chirilgan, lekin o'lik kod qolgan)
- [x] ✅ **2.10** `handlers/admin_handlers.py`dan o'lik `weblogin:*` handlerlarini ([:4298–4542]) olib tashlash.
- [x] ✅ **2.11** `handlers/admin_handlers.py`dan `WebAppInfo`/`WEBAPP_URL` o'lik kodini ([:96–110]) va `open_webapp_command`ni olib tashlash.
- [x] ✅ **2.12** `security_utils.py` faylini va undagi `hash_password` ishlatilishini ([admin_handlers:4445,4499]) olib tashlash.
- [x] ✅ **2.13** `keyboards.py`dan `get_web_login_menu` / `get_web_login_admin_menu` ni olib tashlash; `states.py`dan `AdminWebLoginSettings` ni olib tashlash.
- [x] ✅ **2.14** `config.py`dan `WEBAPP_URL` (boshqa kerak bo'lmasa) ni olib tashlash.
- [x] ✅ **2.V** ✅ Tekshiruv: `py_compile` + `pyflakes` toza; bot importlari sinmaydi.

---

## 🟡 FAZA 3 — Repo gigiyenasi & konfiguratsiya

- [x] ✅ **3.1** `README.md`ni (Koyeb CLI hujjati) o'chirib, loyihaga mos haqiqiy README yozish.
- [x] ✅ **3.2** Junk fayllarni o'chirish: `database.py.new`, `fix.py`, `gitignore` (nuqtasiz), `check_db.py`, `check_schema.py`, `check_models.py`, `debug_db.py`, `fix_payment_times.py`, `migrate_data.py`.
- [x] ✅ **3.3** `Dockerfile` va `Dockerfile.bot` dublikatini birlashtirish (bittasini qoldirish, `docker-compose.yml`ni mosrostlash).
- [x] ✅ **3.4** `.idea/` papkasini git'dan chiqarish (`git rm -r --cached .idea`) va `.gitignore`ga qo'shish.
- [x] ✅ **3.5** `config.py`dan ishlatilmaydigan `OPENAI_API_KEY`, `OPENROUTER_*`, `DEEPSEEK_API_KEY` ni; `requirements.txt`dan `openai` (kerak bo'lmasa) ni olib tashlash.

---

## 🟢 FAZA 4 — Kod sifati

- [x] ✅ **4.1** Barcha `style="primary|success|danger"` kwarg'larini olib tashlash (`menu_overrides.py`, `employee_menu.py`, `shared.py`) — Telegram API'da yo'q parametr.
- [x] ✅ **4.2** Ishlatilmagan importlarni tozalash (pyflakes ro'yxati bo'yicha barcha fayllar); `admin_handlers.py`dagi dublikat `import html` va soyalangan importlarni tuzatish.
- [x] ✅ **4.3** Dublikat funksiyalarni birlashtirish: `_prepare_text_for_ai` (ai_helpers + admin_handlers).
- [ ] **4.4** *(IXTIYORIY — keyinga qoldirildi; AI ishlayapti, bu yaxshilanish, bug emas)* (Ixtiyoriy) `ai_helpers.py`da hardcoded `models/gemini-2.5-flash` o'rniga `GEMINI_FREE_MODELS` konfigi va model-fallback'ni qo'shish.
- [x] ✅ **4.5** Placeholder'siz f-string'larni tuzatish (`database.py`, `shared.py`, va boshqalar).

---

## ✅ FAZA 5 — Yakuniy tekshiruv

- [x] ✅ **5.1** `python -m py_compile` — barcha qolgan fayllar.
- [x] ✅ **5.2** `pyflakes` — 0 ga yaqin topilma.
- [ ] **5.3** *(runtime muhiti kerak: aiogram + BOT_TOKEN + DB)* Bot'ni lokal/`.env.local` bilan ishga tushirish; xato yo'qligini ko'rish.
- [ ] **5.4** *(runtime muhiti / foydalanuvchi sinovi)* Asosiy oqimlarni qo'lda sinash: `/start`, davomat (ishga keldim/ketdim), maosh, kunlik/oylik hisobot, AI (matn + ovoz).
- [x] ✅ **5.5** Yakuniy `git commit` va xulosa.

---

### 📌 Belgilar
`- [ ]` bajarilmagan • `- [x] ✅` bajarilgan • `(Foydalanuvchi amali)` = siz bajarasiz
