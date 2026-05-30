# ⚡ Parfumerose — Barqarorlik, DB va Tezlik Rejasi

> **Qanday ishlatiladi:** Har band `- [ ]` bilan boshlanadi; bajarilgach `- [x] ✅` ga
> o'zgartiriladi. Tartib bilan, yuqoridan pastga. Bu reja faqat **botni sindiradigan**,
> **DB bilan bog'liq** va **sekinlashtiradigan** muammolarga qaratilgan (o'lik kod alohida
> `TUZATISH_REJASI.md`da bajarilgan).

**Tahlil xulosasi:** Asosiy interaktiv yo'llar (kunlik/oylik hisobot, maosh ro'yxati)
allaqachon **to'plamli (set-based) so'rovlar** ishlatadi — tez. Pool sozlamasi
(`statement_cache_size=0`, `max_size=10`) va indekslar (UNIQUE constraint'lar orqali) yetarli.
Asosiy muammolar: **global xato-ushlagich yo'qligi** va **rejalashtirilgan joblardagi N+1**.

---

## 🟣 FAZA E — Mantiqiy xato: kelmaganlik sababi yo'qolardi (TUZATILDI)

- [x] ✅ **E1. `waiting_for_message` handleri yo'q edi → xabar yo'qolardi.**
  `bot.py` kelmagan xodimni `UserAttendance.waiting_for_message` holatiga qo'yib sabab so'rardi,
  lekin **bu holat uchun handler umuman yo'q edi** (`handle_employee_text_message` esa faqat
  `state=None` da ishlaydi). Natijada xodim yozgan sabab **na adminga, na guruhga** yetmasdi.
  **Tuzatish:** `user_handlers.py`ga `process_absence_message` handleri qo'shildi — sababni
  `absence_reason`ga yozadi, `notify_admins_and_group(...)` orqali **admin + guruhga** "Sababli/
  Sababsiz" tugmalari bilan yuboradi, xodimga tasdiq beradi. Matnsiz format uchun fallback ham bor.
- [x] ✅ **E.audit** Boshqa shunga o'xshash xatolar tekshirildi va TOPILMADI:
  barcha o'rnatilgan FSM holatlarning handleri bor; help/feedback, kechikish sababi, "uzoqda"
  kelish/ketish — hammasi `notify_admins` chaqiradi; `notify_admins` har doim superadminlarga
  tushadi (bo'sh ro'yxat yo'q); faol kodda dublikat handler yo'q; xodim menyusi (`empmenu:*`)
  7 ta amali to'liq route qilinadi (o'lik tugma yo'q).

---

## 🔴 FAZA A — Botni sindiradigan / barqarorlik (eng yuqori)

- [ ] **A1. Global xato-ushlagich qo'shish** — Hozir `@dp.errors_handler` **yo'q**. Shu sababli
  handlerdagi har qanday ushlanmagan xato (`MessageNotModified`, "message can't be edited",
  `IndexError` `split("_")`dan, `None` xabarga murojaat) — amalni **jimgina sindiradi**, callback
  "yuklanyapti" belgisi osilib qoladi. `bot.py`ga global handler qo'shish:
  `MessageNotModified` va edit-xatolarini **e'tiborsiz qoldirish**, qolganini **log qilish**.
  *(Bu bitta tuzatish 59 ta xom `edit_text`ning crash riskini bir yo'la yopadi.)*
- [ ] **A2. `safe_edit_text`ni izchil ishlatish** — `admin_handlers.py`da 71 ta `edit_text`dan
  faqat 12 tasi himoyalangan. Eng muhim/tez-tez bosiladigan callback handlerlardagi xom
  `callback_query.message.edit_text(...)` larni `safe_edit_text(...)` ga o'tkazish.
  *(A1 bajarilsa bu past-prioritetli bo'ladi.)*
- [ ] **A.V** ✅ Tekshiruv: `py_compile` + `pyflakes`; bir nechta callback'ni qayta-qayta bosib
  (bir xil natija → `MessageNotModified`) bot sinmasligini ko'rish.

---

## 🟠 FAZA B — Tezlik (rejalashtirilgan joblardagi N+1)

- [ ] **B1. Absence-prompt joblari N+1 ni yo'qotish** — `database.py`:
  `get_workers_needing_absence_prompt` (~2191) va `get_phone_less_workers_pending_manual` (~2248)
  har bir aktiv xodim uchun alohida `get_worker_day_status` + `get_session_for_worker_on_date`
  chaqiradi (2 so'rov × N xodim, **har 10 daqiqada**). Tuzatish: bugungi kun uchun **barcha**
  `worker_day_state_v2` va `work_sessions` ni **2 ta to'plamli so'rov** bilan olib, Python'da
  `worker_id` bo'yicha lug'atga solib, sikl ichida so'rovsiz birlashtirish.
- [ ] **B2. Ertalabki/kechki briefinglar N+1** — `bot.py`: `send_morning_briefings` har xodimga
  `db.pool.fetchrow("SELECT work_start ...")`, `send_evening_briefings` har xodimga
  `get_session_for_worker_on_date` chaqiradi. Tuzatish: `get_active_employees` so'roviga
  `work_start` qo'shish; kechki uchun bugungi sessiyalarni **bitta so'rov** bilan olish.
- [ ] **B.V** ✅ Tekshiruv: joblar log'da xatosiz; so'rovlar soni N×2 dan ~2 ga tushgan.

---

## 🟡 FAZA C — DB tozalash va kichik optimizatsiya

- [ ] **C1. O'lik `get_monthly_salary_stats` ni olib tashlash** — `database.py:1455`; hech qayerda
  chaqirilmaydi, lekin ichida N+2 sikl bor (xom o'lik kod). Olib tashlash.
- [ ] **C2. (Ixtiyoriy) Sana bo'yicha indeksdan foydalanishni yaxshilash** — ba'zi so'rovlar
  `EXTRACT(YEAR/MONTH FROM date)` yoki `timestamp::date = $1` ishlatadi — bu indeksdan
  foydalanishni to'sadi. `date >= '...' AND date < '...'` diapazoniga o'tkazish (katta hajmda foydali).
- [ ] **C3. (Ixtiyoriy) `salary_workers_page` IndexError himoyasi** — `text.split()[0]` bo'sh matnda
  `IndexError` beradi; `text.split()` natijasini tekshirish. *(A1 bilan ham yopiladi.)*

---

## ✅ FAZA D — Yakuniy tekshiruv

- [ ] **D1** `python -m py_compile` + `pyflakes` toza.
- [ ] **D2** Botni runtime muhitida ishga tushirib, joblar va asosiy oqimlarni kuzatish.
- [ ] **D3** Yakuniy `git commit`.

---

### 📊 Tekshirildi va MUAMMO TOPILMADI (ishonch uchun)
- ✅ SQL injection yo'q (barcha so'rovlar parametrli `$1,$2`).
- ✅ Bloklovchi sync chaqiruv yo'q (`recognize_once` executor'da; `time.sleep` faqat startup).
- ✅ DB ulanishi ushlab turilganda Telegram'ga xabar yuborilmaydi (connection leak yo'q).
- ✅ `await` tushib qolgan DB chaqiruvi topilmadi.
- ✅ Interaktiv hisobotlar (kunlik/oylik/maosh) to'plamli so'rov — N+1 yo'q.
- ✅ `ensure_attendance_v2_schema` (30 so'rov) faqat startup'da, har request'da emas.
