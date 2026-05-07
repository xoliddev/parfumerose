# user_handlers.py

import datetime
import logging
import re
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup  # Kerakli import
from aiogram.utils.exceptions import MessageNotModified
import html
from ai_helpers import process_employee_request
from employee_menu import get_employee_main_menu
from loader import dp, bot
from config import ADMINS, SUPERADMINS, ALLOWED_LAT, ALLOWED_LON, ALLOWED_RADIUS, LATE_EARLY_TOLERANCE_MIN

# --- TUZATISH: To'g'ri import usuli ---
# Endi butun 'database' modulini 'db' nomi bilan chaqiramiz
import database as db
from shared import (
    build_admin_home_payload,
    build_branch_selection_keyboard,
    pending_requests,
    describe_admin_action_result,
    get_admin_action_lock,
    get_admin_action_result,
    get_admin_home_text,
    get_superadmin_branch_selector_text,
    notify_admins,
    notify_admins_and_group,
    notify_selected_admins,
    register_admin_action_messages,
    resolve_admin_action,
    reset_admin_action,
)
from keyboards import get_admin_main_menu, make_mystats_years_keyboard, make_mystats_months_keyboard
from states import (
    UserAttendance, UserJoinApplication, FridayWork, EarlyLeave, LateArrival,
    HelpState, MyStatsStates
)
import pytz

# -----------------------------------------------------------------
# Regex - “kel” / “ket” (lotin + kiril, katta-kichik farqsiz)
# Bu qism o'zgarmaydi
# -----------------------------------------------------------------
kel_pattern = re.compile(r'(?i)[kк][eе][lл]')
ket_pattern = re.compile(r'(?i)[kк][eе][tт]')

tashkent_tz = pytz.timezone('Asia/Tashkent')
# -----------------------------------------------------------------
# Quick-reason tugmalari (uzoqda bo‘lsa foydali)
# Bu qism o'zgarmaydi
# -----------------------------------------------------------------
quick_reasons = [
    ("Ustanovkada", "ustanovkada"),
    ("Internet muammosi", "internet muammosi"),
    ("Esimdan chiqdi", "esimdan chiqdi"),
]


# -------------- yordamchi funksiyalar (o'zgarmaydi) --------------------
def format_hours(total_hours_float: float) -> str:
    total_minutes = int(round(float(total_hours_float or 0.0) * 60))
    return f"{total_minutes // 60} soat {total_minutes % 60} daqiqa"


# user_handlers.py faylidagi eski is_late funksiyasi o'rniga buni qo'ying

def is_late(start_str: str, tolerance_min: int) -> tuple[bool, int]:
    """
    Xodimning belgilangan vaqtdan kechikkan yoki kechikmaganini tekshiradi.
    Endi faqat ishga kelish vaqtiga bog'liq.
    """
    # Agar xodim uchun ishga kelish vaqti umuman belgilanmagan bo'lsa, u kechikkan hisoblanmaydi.
    if not start_str:
        return (False, 0)

    try:
        today = datetime.date.today()
        h, m = map(int, start_str.split(":"))

        # Aniq vaqt zonalari bilan ishlash xatoliklarning oldini oladi
        planned_arrival = tashkent_tz.localize(datetime.datetime.combine(today, datetime.time(h, m)))
        allowed_arrival = planned_arrival + datetime.timedelta(minutes=tolerance_min)

        now_tashkent = datetime.datetime.now(tashkent_tz)

        # Hozirgi vaqt ruxsat etilgan vaqtdan keyinmi?
        diff_seconds = (now_tashkent - allowed_arrival).total_seconds()

        if diff_seconds > 0:
            diff_min = int(diff_seconds // 60)
            return (True, diff_min)

        return (False, 0)
    except (ValueError, TypeError):
        # Agar start_str noto'g'ri formatda bo'lsa (masalan: None yoki bo'sh satr)
        return (False, 0)


def next_day_message() -> str:
    tom = datetime.datetime.now() + datetime.timedelta(days=1)
    return ("Yaxshi dam oling, dushanba ko‘rishguncha!"
            if tom.weekday() == 5 else  # Shanbadan keyin yakshanba
            "Yaxshi dam oling, ertaga ko‘rishguncha!")


def is_pure_number(txt: str) -> bool:
    cleaned = txt.strip().replace(" ", "")
    parts = cleaned.split(".")
    return len(parts) <= 2 and all(p.isdigit() or p == "" for p in parts)


def _to_utc(dt_value: datetime.datetime | None) -> datetime.datetime | None:
    if dt_value is None:
        return None
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=datetime.timezone.utc)
    return dt_value.astimezone(datetime.timezone.utc)


async def clear_old_employee_reply_keyboard(chat_id: int):
    try:
        temp = await bot.send_message(chat_id, "\u2063", reply_markup=types.ReplyKeyboardRemove())
        await bot.delete_message(chat_id, temp.message_id)
    except Exception:
        pass


async def get_employee_dashboard(user_id: int):
    worker = await db.get_worker_by_tg_id(user_id)
    if not worker:
        return None

    today = datetime.date.today()
    day_status = await db.get_worker_day_status(worker["id"], today) or {}
    session = await db.get_session_for_worker_on_date(worker["id"], today) or {}

    has_arrived = bool(day_status.get("clock_in_at") or session.get("arrival_time"))
    has_left = bool(
        day_status.get("clock_out_at")
        or session.get("departure_time")
        or day_status.get("day_state") == "left"
    )
    rest_marked = bool(day_status.get("rest_marked"))
    study_active = bool(day_status.get("study_active"))
    is_working = has_arrived and not has_left and not rest_marked

    if rest_marked:
        status_text = "Bugun dam"
    elif study_active:
        status_text = "O'qishda"
    elif is_working:
        status_text = "Ish jarayonida"
    elif has_left:
        status_text = "Bugungi ish yakunlangan"
    else:
        status_text = "Hali ish boshlanmagan"

    return {
        "worker": worker,
        "day_status": day_status,
        "session": session,
        "is_working": is_working,
        "study_active": study_active,
        "status_text": status_text,
    }


async def render_employee_menu(chat_id: int, user_id: int, preferred_message_id: int | None = None):
    dashboard = await get_employee_dashboard(user_id)
    if not dashboard:
        return

    full_name = dashboard["worker"]["full_name"]
    text = f"Assalomu alaykum, {full_name}.\nHolat: {dashboard['status_text']}"
    kb = get_employee_main_menu(
        is_working=dashboard["is_working"],
        study_active=dashboard["study_active"],
    )

    if preferred_message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=preferred_message_id,
                reply_markup=kb,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await bot.send_message(chat_id, text, reply_markup=kb)


def _build_live_location_prompt_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬅️ Menyuga qaytish", callback_data="empmenu:home", style="primary"))
    return kb


def _build_live_location_prompt_text(is_departure: bool) -> str:
    action_text = "Ishdan ketayotgan bo'lsangiz" if is_departure else "Ishxonaga kelgan bo'lsangiz"
    return (
        f"{action_text}, jonli lokatsiya yuboring.\n\n"
        "Oddiy lokatsiya qabul qilinmaydi.\n\n"
        "Qanday yuboriladi:\n"
        "1. 📎 ni bosing\n"
        "2. Location ni tanlang\n"
        "3. Share My Live Location ni bosing\n"
        "4. 15 daqiqaga yuboring"
    )


def _build_location_issue_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📍 Lokatsiyani qayta yuborish", callback_data="empmenu:clock", style="primary"))
    kb.add(types.InlineKeyboardButton("⬅️ Menyuga qaytish", callback_data="empmenu:home", style="primary"))
    return kb


def _build_location_issue_text(nearest_branch: dict | None) -> str:
    lines = [
        "Lokatsiya tasdiqlanmadi.",
        "Siz yuborgan joy ishxona joyiga to'g'ri kelmadi.",
    ]
    if nearest_branch:
        lines.append(f"Eng yaqin filial: {nearest_branch['name']} ({int(nearest_branch['distance'])} m).")
    lines.extend([
        "",
        "Agar siz ishxonada bo'lsangiz:",
        "1. GPS ni aniq rejimga qo'ying.",
        "2. Wi-Fi yoki mobil internetni yoqing.",
        "3. 10-15 soniya kuting.",
        "4. Lokatsiyani qayta yuboring.",
        "",
        "Agar ishxonada bo'lmasangiz, sababni matn qilib yozing.",
    ])
    return "\n".join(lines)


async def show_location_issue_prompt(chat_id: int, menu_message_id: int | None, nearest_branch: dict | None):
    text = _build_location_issue_text(nearest_branch)
    kb = _build_location_issue_keyboard()
    await clear_old_employee_reply_keyboard(chat_id)

    if menu_message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=menu_message_id,
                reply_markup=kb,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await bot.send_message(chat_id, text, reply_markup=kb)


# -----------------------------------------------------------------


# =============================  /start  ==========================
@dp.message_handler(commands=['start'], state="*")
async def universal_start(message: types.Message, state: FSMContext):
    await state.finish()
    user_id = message.from_user.id

    if user_id in SUPERADMINS:
        await db.clear_superadmin_selected_branch(user_id)
        home_text, home_markup = await build_admin_home_payload(user_id)
        await message.answer(home_text, reply_markup=home_markup)
        return

    if user_id in ADMINS:
        home_text, home_markup = await build_admin_home_payload(user_id)
        await message.answer(home_text, reply_markup=home_markup)
        return

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("""
                                  SELECT id, full_name, daily_work_hours, work_start, work_end
                                  FROM workers
                                  WHERE tg_id = $1
                                  """, user_id)

    if not row:
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Xodim bo'lish", callback_data="request_join")
        )
        await message.answer(
            "Assalomu alaykum, siz xodimlar ro‘yxatida topilmadingiz.\n\n"
            "Xodim bo‘lishni istasangiz, «Xodim bo‘lish» tugmasini bosing.",
            reply_markup=kb
        )
        return

    await clear_old_employee_reply_keyboard(message.chat.id)
    await render_employee_menu(message.chat.id, user_id)
    return





# ----------------- JOIN - so‘rov ----------------------------------
@dp.callback_query_handler(lambda c: c.data == "request_join", state="*")
async def request_join(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback_query.message.edit_text(
        "Assalomu alaykum, siz xodimlar ro'yxatida topilmadingiz.\n\n"
        "Iltimos, ism va familiyangizni kiriting.\n"
        "Masalan: Ali Valiyev"
    )
    await UserJoinApplication.waiting_for_name.set()
    await callback_query.answer()


@dp.message_handler(state=UserJoinApplication.waiting_for_name, content_types=types.ContentTypes.TEXT)
async def request_join_name(message: types.Message, state: FSMContext):
    full_name = (message.text or "").strip()
    if len(full_name) < 3 or not re.search(r"[A-Za-zА-Яа-яЎўҚқҒғҲҳ]", full_name):
        await message.answer("Iltimos, ismni to'g'ri kiriting.\nMasalan: Ali Valiyev")
        return

    u = message.from_user
    action_key = f"join_request:{u.id}"
    reset_admin_action(action_key)
    pending_requests[u.id] = {
        "full_name": full_name,
        "username": u.username,
    }

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("Qabul qilish", callback_data=f"pending_accept_{u.id}", style="success"),
        types.InlineKeyboardButton("Rad etish", callback_data=f"pending_reject_{u.id}", style="danger")
    )
    sent_messages = await notify_selected_admins(
        SUPERADMINS,
        f"🆕 Ariza:\nID: {u.id}\nIsm: {full_name}\nUsername: {('@' + u.username) if u.username else '—'}",
        reply_markup=kb
    )
    await register_admin_action_messages(action_key, sent_messages)

    msg_id = None
    chat_id = None
    if sent_messages:
        chat_id, msg_id = sent_messages[0]

    await db.add_application(u.id, full_name, u.username, message_id=msg_id, chat_id=chat_id)
    await state.finish()
    await message.answer("So'rovingiz adminga yuborildi. Tez orada ko'rib chiqamiz.")


@dp.callback_query_handler(lambda c: c.data.startswith("absence_review:"), state="*")
async def absence_review_callback(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    try:
        _, verdict, worker_id_raw = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri amal.", show_alert=True)

    today = datetime.date.today()
    action_key = f"absence_review:{worker_id}:{today.isoformat()}"
    lock = get_admin_action_lock(action_key)

    try:
        async with lock:
            existing_result = get_admin_action_result(action_key)
            if existing_result:
                try:
                    await callback_query.message.edit_reply_markup()
                except Exception:
                    pass
                return await callback_query.answer(describe_admin_action_result(existing_result), show_alert=True)

            worker = await db.get_worker_by_id(worker_id)
            if not worker:
                return await callback_query.answer("Xodim topilmadi.", show_alert=True)

            normalized = "excused" if verdict == "excused" else "unexcused"
            note = "sababli deb belgiladi" if normalized == "excused" else "sababsiz deb belgiladi"

            await db.update_worker_day_status(
                worker_id,
                today,
                day_state="absent_pending",
                absence_review_status=normalized,
                absence_reviewed_by=callback_query.from_user.id,
                last_source="admin",
            )
            await db.log_worker_activity(
                worker_id,
                "absence_review",
                f"Admin kelmaganlikni {note}",
                callback_query.from_user.id,
                "admin",
                today,
            )
            await resolve_admin_action(
                action_key,
                callback_query.from_user.id,
                callback_query.from_user.full_name,
                note,
            )
            try:
                await callback_query.message.edit_reply_markup()
            except Exception:
                pass
            await notify_admins(
                f"📌 {callback_query.from_user.full_name} {worker['full_name']}ni bugun {note}.",
                worker_id=worker["id"],
            )

        await callback_query.answer("Saqlandi.")
    except Exception as exc:
        logging.exception("absence_review_callback da xatolik: %s", exc)
        await callback_query.answer("Xatolik yuz berdi. Qayta urinib ko'ring.", show_alert=True)


def _build_employee_back_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="empmenu:home", style="primary"))
    return kb


async def _build_worker_salary_text(user_id: int) -> str:
    async with db.pool.acquire() as conn:
        worker_record = await conn.fetchrow(
            "SELECT id, full_name, monthly_salary FROM workers WHERE tg_id = $1",
            user_id,
        )
        if not worker_record:
            return "Siz ro'yxatdan o'tmagansiz."

        wid, full_name, monthly_salary = worker_record.values()
        monthly_salary = monthly_salary or 0.0
        year_month_str = datetime.date.today().strftime("%Y-%m")

        payments = await conn.fetch(
            """
            SELECT payment_date, payment_time, amount
            FROM salary_payments
            WHERE worker_id = $1
              AND to_char(payment_date, 'YYYY-MM') = $2
            ORDER BY payment_time
            """,
            wid,
            year_month_str,
        )

    total_paid = sum(p["amount"] for p in payments) if payments else 0.0
    remaining = (monthly_salary - total_paid) if monthly_salary > 0 else 0.0

    text = f"Hurmatli {full_name},\nTayinlangan maosh: {float(monthly_salary):,.0f} so'm\n"
    text += f"Bugungi kunga qadar olingan: {float(total_paid):,.0f} so'm\n"
    text += f"Qolgan: {float(remaining):,.0f} so'm\n\n"
    if payments:
        text += "Joriy oy to'lovlar:"
        for p in payments:
            p_datetime = p["payment_time"].astimezone()
            p_date_str = p_datetime.strftime("%d.%m.%Y")
            p_time_str = p_datetime.strftime("%H:%M")
            text += f"\n• {p_date_str} {p_time_str} — {float(p['amount']):,.0f} so'm"
    return text


@dp.callback_query_handler(lambda c: c.data.startswith("empmenu:"), state="*")
async def employee_menu_callback(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id in ADMINS:
        return await callback_query.answer()

    dashboard = await get_employee_dashboard(callback_query.from_user.id)
    if not dashboard:
        return await callback_query.answer("Siz xodimlar ro'yxatida topilmadingiz.", show_alert=True)

    action = callback_query.data.split(":", 1)[1]
    worker = dashboard["worker"]
    today = datetime.date.today()
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    if action == "home":
        await state.finish()
        await render_employee_menu(
            callback_query.message.chat.id,
            callback_query.from_user.id,
            preferred_message_id=callback_query.message.message_id,
        )
        return await callback_query.answer()

    if action == "clock":
        session = dashboard["session"]
        await state.finish()
        requested_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await state.update_data(
            departure_mode=dashboard["is_working"],
            session_id=session.get("id") if session else None,
            menu_message_id=callback_query.message.message_id,
            location_requested_at=requested_at,
        )
        await UserAttendance.waiting_for_location.set()
        await clear_old_employee_reply_keyboard(callback_query.message.chat.id)
        await callback_query.message.edit_text(
            _build_live_location_prompt_text(dashboard["is_working"]),
            reply_markup=_build_live_location_prompt_keyboard(),
        )
        return await callback_query.answer()

    if action == "rest":
        await state.finish()
        await db.update_worker_day_status(
            worker["id"],
            today,
            day_state="rest",
            rest_marked=True,
            absence_review_status="excused",
            last_source="worker",
        )
        await db.log_worker_activity(worker["id"], "rest", "Xodim bugun dam oldi", callback_query.from_user.id, "worker", today)
        await notify_admins_and_group(f"{worker['full_name']} bugun dam oldi.", worker_id=worker["id"])
        await render_employee_menu(
            callback_query.message.chat.id,
            callback_query.from_user.id,
            preferred_message_id=callback_query.message.message_id,
        )
        return await callback_query.answer("Saqlandi.")

    if action == "study":
        await state.finish()
        if dashboard["study_active"]:
            await db.update_worker_day_status(
                worker["id"],
                today,
                day_state="working",
                study_active=False,
                study_returned_at=now_dt,
                last_source="worker",
            )
            await db.log_worker_activity(worker["id"], "study_return", "Xodim o'qishdan qaytdi", callback_query.from_user.id, "worker", today)
            await notify_admins_and_group(f"{worker['full_name']} o'qishdan qaytdi.", worker_id=worker["id"])
        else:
            await db.update_worker_day_status(
                worker["id"],
                today,
                day_state="working",
                study_active=True,
                study_left_at=now_dt,
                last_source="worker",
            )
            await db.log_worker_activity(worker["id"], "study_leave", "Xodim o'qishga ketdi", callback_query.from_user.id, "worker", today)
            await notify_admins_and_group(f"{worker['full_name']} o'qishga ketdi.", worker_id=worker["id"])
        await render_employee_menu(
            callback_query.message.chat.id,
            callback_query.from_user.id,
            preferred_message_id=callback_query.message.message_id,
        )
        return await callback_query.answer("Saqlandi.")

    if action == "salary":
        text = await _build_worker_salary_text(callback_query.from_user.id)
        await callback_query.message.edit_text(text, reply_markup=_build_employee_back_keyboard())
        return await callback_query.answer()

    if action == "help":
        help_text = (
            "Bot ish vaqtini kuzatish, kelish-ketish nazorati va maosh ma'lumotlarini ko'rsatadi.\n\n"
            "Muammo yoki taklif bo'lsa, adminga yozishingiz mumkin."
        )
        await callback_query.message.edit_text(help_text, reply_markup=_build_employee_back_keyboard())
        return await callback_query.answer()

    if action == "mystats":
        years = await db.get_user_distinct_years(callback_query.from_user.id)
        if not years:
            years = [datetime.datetime.now().year]
        kb = make_mystats_years_keyboard(years)
        await callback_query.message.edit_text("Qaysi yil statistikangizni ko'rmoqchisiz?", reply_markup=kb)
        await MyStatsStates.SELECT_YEAR.set()
        return await callback_query.answer()

    await callback_query.answer("Noma'lum amal.", show_alert=True)


# ----------------- Kech kelish sababi -----------------------------
@dp.message_handler(state=LateArrival.waiting_for_reason,
                    content_types=types.ContentTypes.TEXT)
async def late_reason(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    if not txt or is_pure_number(txt):
        await message.answer("Iltimos, matn yozing.")
        return
    state_data = await state.get_data()
    sess_id = state_data.get("session_id")
    menu_message_id = state_data.get("menu_message_id")

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE work_sessions SET late_reason = $1 WHERE id = $2", txt, sess_id)
        name_record = await conn.fetchrow("""
                                          SELECT w.id, w.full_name
                                          FROM work_sessions s
                                                   JOIN workers w ON w.id = s.user_id
                                          WHERE s.id = $1
                                          """, sess_id)

    name = name_record['full_name'] if name_record else "Noma'lum xodim"
    worker_id = name_record['id'] if name_record else None
    await notify_admins(f"{name} kech kelish sababi: {txt}", worker_id=worker_id)
    await message.answer("Sabab qabul qilindi, rahmat!",
                        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True))
    await state.finish()
    await clear_old_employee_reply_keyboard(message.chat.id)
    if menu_message_id:
        await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)


@dp.message_handler(state=LateArrival.waiting_for_reason,
                    content_types=[types.ContentType.ANY])
async def only_text_for_late(message: types.Message):
    # Bu handlerda baza bilan ishlanmaydi, o'zgarmaydi
    await message.answer("Faqat matn yuboring.")


# ===================== LOCATION ================================
@dp.message_handler(state=[UserAttendance.waiting_for_location, UserAttendance.waiting_for_reason],
                    content_types=types.ContentTypes.LOCATION)
async def loc_handler(message: types.Message, state: FSMContext):
    if (
        message.forward_date
        or message.forward_from
        or message.forward_from_chat
        or getattr(message, "forward_origin", None)
    ):
        await message.answer("Uzatilgan lokatsiya qabul qilinmaydi.")
        return
    message_dt = _to_utc(message.date)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if message_dt and (now_utc - message_dt).total_seconds() > 30:
        await message.answer("Lokatsiya kech keldi. Qayta yuboring.")
        return

    loc = message.location
    if not getattr(loc, "live_period", None):
        await message.answer("Oddiy lokatsiya emas, jonli lokatsiya yuboring.")
        prompt_data = await state.get_data()
        await show_location_issue_prompt(
            message.chat.id,
            prompt_data.get("menu_message_id"),
            None,
        )
        return

    dist = db.calculate_distance(loc.latitude, loc.longitude,
                                 ALLOWED_LAT, ALLOWED_LON)

    data = await state.get_data()
    dep_mode = data.get("departure_mode", False)
    fri = data.get("is_friday", False)
    sess_id = data.get("session_id")
    forced = data.get("forced_early_departure", False)
    early_rs = data.get("early_reason", "")
    menu_message_id = data.get("menu_message_id")
    requested_at_raw = data.get("location_requested_at")
    requested_at = None
    if requested_at_raw:
        try:
            requested_at = datetime.datetime.fromisoformat(requested_at_raw)
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=datetime.timezone.utc)
            else:
                requested_at = requested_at.astimezone(datetime.timezone.utc)
        except ValueError:
            requested_at = None

    if requested_at and message_dt and message_dt < requested_at:
        await message.answer("Bu eski lokatsiya. Iltimos, hozir jonli lokatsiya yuboring.")
        await show_location_issue_prompt(message.chat.id, menu_message_id, None)
        return

    worker_record = await db.get_worker_by_tg_id(message.from_user.id)
    if not worker_record:
        await message.answer("Xatolik: Siz ro'yxatda topilmadingiz.")
        await state.finish()
        return

    wid = worker_record["id"]
    wname = worker_record["full_name"]
    d_hrs = worker_record.get("daily_work_hours")
    w_start = worker_record.get("work_start")
    w_end = worker_record.get("work_end")
    is_free_mode = (not d_hrs or d_hrs <= 0 or not w_start or not w_end)

    branch_resolution = await db.resolve_branch_for_location(
        loc.latitude,
        loc.longitude,
        preferred_branch_id=worker_record.get("branch_id"),
    )
    matched_branch = branch_resolution.get("matched_branch")
    nearest_branch = branch_resolution.get("nearest_branch")
    effective_branch_id = (
        matched_branch["id"]
        if matched_branch
        else worker_record.get("branch_id") or (nearest_branch["id"] if nearest_branch else None)
    )
    dist = (
        matched_branch["distance"]
        if matched_branch
        else (nearest_branch["distance"] if nearest_branch else 0.0)
    )

    if not matched_branch:
        await state.update_data(
            latitude=loc.latitude,
            longitude=loc.longitude,
            distance=dist,
            timestamp=datetime.datetime.now().isoformat(),
            branch_id=effective_branch_id,
        )
        await UserAttendance.waiting_for_reason.set()
        await show_location_issue_prompt(message.chat.id, menu_message_id, nearest_branch)
        return

    now_dt_aware = datetime.datetime.now(datetime.timezone.utc)
    today_date = now_dt_aware.date()
    branch_name = matched_branch["name"]

    async with db.pool.acquire() as conn:
        if not dep_mode:  # --- ISHGA KELISH ---
            await conn.execute(
                """
                INSERT INTO work_sessions (user_id, date, arrival_time, is_friday, session_daily_hours, branch_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id, date) DO UPDATE SET
                    branch_id = COALESCE(work_sessions.branch_id, EXCLUDED.branch_id)
                """,
                wid,
                today_date,
                now_dt_aware,
                fri,
                d_hrs or 0.0,
                effective_branch_id,
            )

            sess_id = await conn.fetchval(
                "SELECT id FROM work_sessions WHERE user_id = $1 AND date = $2",
                wid,
                today_date,
            )

            if worker_record.get("branch_id") is None and effective_branch_id:
                await db.assign_worker_branch(wid, effective_branch_id)

            await db.update_worker_day_status(
                wid,
                today_date,
                day_state="working",
                clock_in_at=now_dt_aware,
                clock_out_at=None,
                rest_marked=False,
                study_active=False,
                last_source="worker",
            )

            await notify_admins(
                f"{wname} ishga KELDI. ({now_dt_aware.astimezone().strftime('%H:%M:%S')})",
                worker_id=wid,
            )
            await message.answer(
                f"Kelishingiz {branch_name} filialida qayd qilindi. ({now_dt_aware.astimezone().strftime('%H:%M:%S')})",
                reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            )
            await state.finish()
            await clear_old_employee_reply_keyboard(message.chat.id)
            if menu_message_id:
                await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)
            return

        if sess_id is None:
            sess_id = await conn.fetchval(
                "SELECT id FROM work_sessions WHERE user_id = $1 AND date = $2 AND departure_time IS NULL",
                wid,
                today_date,
            )
            if not sess_id:
                await message.answer("Xatolik: bugun uchun aktiv ish sessiyangiz topilmadi.")
                await state.finish()
                return

        session_record = await conn.fetchrow(
            "SELECT arrival_time, session_daily_hours, branch_id FROM work_sessions WHERE id = $1",
            sess_id,
        )

        if not session_record:
            await message.answer("Xatolik: sessiya ma'lumotlari topilmadi.")
            await state.finish()
            return

        arr_dt = session_record['arrival_time']
        if not arr_dt:
            await message.answer("Xatolik: kelish vaqti topilmadi.")
            await state.finish()
            return

        session_branch_id = session_record.get("branch_id") or effective_branch_id
        session_branch = await db.get_branch_by_id(session_branch_id)
        session_branch_name = session_branch["name"] if session_branch else branch_name
        total_f = (now_dt_aware - arr_dt).total_seconds() / 3600.0

        await conn.execute(
            """
            UPDATE work_sessions
            SET departure_time = $1,
                total_hours = $2,
                branch_id = COALESCE(branch_id, $4)
            WHERE id = $3
            """,
            now_dt_aware,
            total_f,
            sess_id,
            session_branch_id,
        )

        await db.update_worker_day_status(
            wid,
            today_date,
            day_state="left",
            clock_out_at=now_dt_aware,
            study_active=False,
            last_source="worker",
        )

        admin_txt = (
            f"{wname} ishxonadan KETDI.\n"
            f"Kelish: {arr_dt.astimezone().strftime('%H:%M:%S')}, Ketish: {now_dt_aware.astimezone().strftime('%H:%M:%S')}\n"
            f"Ishlagan: {format_hours(total_f)}"
        )
        await notify_admins(admin_txt, worker_id=wid)

        await conn.execute(
            """
            INSERT INTO attendance (user_id, name, latitude, longitude, distance, branch_id, message, reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            wid,
            wname,
            loc.latitude,
            loc.longitude,
            dist,
            session_branch_id,
            "Ketish lokatsiya",
            "",
        )

        await message.answer(
            f"Ketishingiz {session_branch_name} filialida qayd qilindi. {format_hours(total_f)} ishladingiz.\n{next_day_message()}",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        )
        await state.finish()
        await clear_old_employee_reply_keyboard(message.chat.id)
        if menu_message_id:
            await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)
        return

    async with db.pool.acquire() as conn:
        worker_record = await conn.fetchrow("""
                                            SELECT id, full_name, daily_work_hours, work_start, work_end
                                            FROM workers
                                            WHERE tg_id = $1
                                            """, message.from_user.id)

        if not worker_record:
            await message.reply("Xatolik: Siz ro'yxatda topilmadingiz.")
            await state.finish()
            return

        wid, wname, d_hrs, w_start, w_end = worker_record.values()
        is_free_mode = (not d_hrs or d_hrs <= 0 or not w_start or not w_end)

        if dist > ALLOWED_RADIUS:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for title, cmd in quick_reasons:
                kb.add(types.InlineKeyboardButton(title, callback_data=f"qreason_{cmd}"))
            await message.reply(
                "Siz ishxonadan uzoqdasiz. Sababni tanlang:",
                reply_markup=kb
            )
            await state.update_data(
                latitude=loc.latitude,
                longitude=loc.longitude,
                distance=dist,
                timestamp=datetime.datetime.now().isoformat()
            )
            await UserAttendance.waiting_for_reason.set()
            return

        now_dt_aware = datetime.datetime.now(datetime.timezone.utc)
        today_date = now_dt_aware.date()

        if not dep_mode:  # --- ISHGA KELISH ---
            await conn.execute("""
                               INSERT INTO work_sessions (user_id, date, arrival_time, is_friday, session_daily_hours)
                               VALUES ($1, $2, $3, $4, $5) ON CONFLICT (user_id, date) DO NOTHING;
                               """, wid, today_date, now_dt_aware, fri, d_hrs or 0.0)

            sess_id = await conn.fetchval(
                "SELECT id FROM work_sessions WHERE user_id = $1 AND date = $2",
                wid, today_date
            )

            await db.update_worker_day_status(
                wid,
                today_date,
                day_state="working",
                clock_in_at=now_dt_aware,
                clock_out_at=None,
                rest_marked=False,
                study_active=False,
                last_source="worker",
            )

            late, late_min = is_late(w_start.strftime('%H:%M') if w_start else None, LATE_EARLY_TOLERANCE_MIN)
            if late:
                await notify_admins(f"⚠️ {wname} ishga {late_min} daqiqa kech keldi.", worker_id=wid)
                await message.reply(
                    "Ishga kech keldingiz, iltimos, sababini yozing.",
                    reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                )
                await LateArrival.waiting_for_reason.set()
                await state.update_data(session_id=sess_id)
                return

            await notify_admins(
                f"✅ {wname} ishga KELDI. ({now_dt_aware.astimezone().strftime('%H:%M:%S')})",
                worker_id=wid,
            )
            await message.reply(
                f"Kelishingiz qayd qilindi. ({now_dt_aware.astimezone().strftime('%H:%M:%S')})",
                reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            )
            await state.finish()
            await clear_old_employee_reply_keyboard(message.chat.id)
            if menu_message_id:
                await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)
            return

        else:  # --- ISHDAN KETISH ---
            if sess_id is None:
                # Agar state'da sessiya bo'lmasa, bazadan qidirib ko'ramiz
                sess_id = await conn.fetchval(
                    "SELECT id FROM work_sessions WHERE user_id = $1 AND date = $2 AND departure_time IS NULL",
                    wid, today_date
                )
                if not sess_id:
                    await message.reply("Xatolik: bugun uchun aktiv ish sessiyangiz topilmadi.")
                    await state.finish()
                    return

            session_record = await conn.fetchrow(
                "SELECT arrival_time, session_daily_hours FROM work_sessions WHERE id = $1",
                sess_id
            )

            if not session_record:
                await message.reply("Xatolik: sessiya ma'lumotlari topilmadi.")
                await state.finish()
                return

            arr_dt = session_record['arrival_time']
            if not arr_dt:
                await message.reply("Xatolik: kelish vaqti topilmadi.")
                await state.finish()
                return

            sess_req = session_record['session_daily_hours']
            total_f = (now_dt_aware - arr_dt).total_seconds() / 3600.0

            await conn.execute(
                "UPDATE work_sessions SET departure_time = $1, total_hours = $2 WHERE id = $3",
                now_dt_aware, total_f, sess_id
            )

            await db.update_worker_day_status(
                wid,
                today_date,
                day_state="left",
                clock_out_at=now_dt_aware,
                study_active=False,
                last_source="worker",
            )

            early_msg = ""
            if not is_free_mode and sess_req and float(sess_req) > 0:
                limit = arr_dt + datetime.timedelta(hours=float(sess_req)) - datetime.timedelta(
                    minutes=LATE_EARLY_TOLERANCE_MIN)
                if now_dt_aware < limit:
                    delta = int((limit - now_dt_aware).total_seconds() // 60)
                    early_msg = f"\n🟠 {delta} daqiqa erta ketdi"

            admin_txt = (
                f"🔚 {wname} ishxonadan KETDI.\n"
                f"Kelish: {arr_dt.astimezone().strftime('%H:%M:%S')}, Ketish: {now_dt_aware.astimezone().strftime('%H:%M:%S')}\n"
                f"Ishlagan: {format_hours(total_f)}{early_msg}"
            )
            if forced and early_rs:
                admin_txt += f"\nSabab: {early_rs}"

            await notify_admins(admin_txt, worker_id=wid)

            await conn.execute(
                "INSERT INTO attendance (user_id, name, latitude, longitude, distance, message, reason) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                wid, wname, loc.latitude, loc.longitude, dist, "Ketish lokatsiya", early_rs if early_msg else ""
            )

            await message.reply(
                f"Ketishingiz qayd qilindi. {format_hours(total_f)} ishladingiz.\n{next_day_message()}",
                reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            )
            await state.finish()
            await clear_old_employee_reply_keyboard(message.chat.id)
            if menu_message_id:
                await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)


# ------------- lokatsiya o‘rniga matn yuborsa --------------------
@dp.message_handler(state=UserAttendance.waiting_for_location,
                    content_types=[types.ContentType.ANY])
async def only_loc(message: types.Message):
    # Bu handlerda baza bilan ishlanmaydi, o'zgarmaydi
    await message.answer("Jonli lokatsiya yuboring: 📎 -> Location -> Share My Live Location.")


# =================================================================
#      UZOQDA → SABAB TEXT   (kel / ket / boshqa)
# =================================================================
# ---------------- QUICK-REASON callback ---------------------------------
@dp.callback_query_handler(lambda c: c.data.startswith("qreason_"),
                           state=UserAttendance.waiting_for_reason)
async def quick_reason_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await show_location_issue_prompt(
        callback_query.message.chat.id,
        data.get("menu_message_id") or callback_query.message.message_id,
        None,
    )
    await callback_query.answer(
        "Eski tugmalar bekor qilingan. Lokatsiyani qayta yuboring yoki sababni matn bilan yozing.",
        show_alert=True,
    )


@dp.message_handler(state=UserAttendance.waiting_for_reason,
                    content_types=types.ContentTypes.TEXT)
async def process_uzoqlik_reason(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    if not txt:
        await message.answer("Matn kiriting.")
        return

    d = await state.get_data()
    lat, lon, dist, ts = d["latitude"], d["longitude"], d["distance"], d["timestamp"]
    dep_mode = d.get("departure_mode", False)
    menu_message_id = d.get("menu_message_id")

    worker_record = await db.get_worker_by_tg_id(message.from_user.id)
    if not worker_record:
        await message.answer("Xodim topilmadi.")
        await state.finish()
        return
    wid, wname = worker_record['id'], worker_record['full_name']
    target_branch_id = d.get("branch_id") or worker_record.get("branch_id")

    if txt in [cmd for _, cmd in quick_reasons]:
        if dep_mode:
            await finalize_departure_far_away(message, wid, wname, txt, lat, lon, dist, ts, branch_id=target_branch_id)
        else:
            await finalize_arrival_far_away(message, wid, wname, txt, lat, lon, dist, ts, branch_id=target_branch_id)
        await state.finish()
        return

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        # Eski bazada attendance.user_id bu tg_id edi, yangi bazada ham shunday saqlaymiz
        await conn.execute("""
                           INSERT INTO attendance
                           (user_id, name, timestamp, latitude, longitude, distance, branch_id, message, reason)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                           """, wid, wname, ts, lat, lon, dist, target_branch_id, "Uzoq sabab", txt)

    found_kel = bool(kel_pattern.search(txt.lower()))
    found_ket = bool(ket_pattern.search(txt.lower()))

    if found_kel and not found_ket:
        await finalize_arrival_far_away(message, wid, wname, txt, lat, lon, dist, ts, branch_id=target_branch_id)
    elif found_ket and not found_kel:
        await finalize_departure_far_away(message, wid, wname, txt, lat, lon, dist, ts, branch_id=target_branch_id)
    else:
        link = f"http://www.google.com/maps/place/{lat},{lon}"
        await notify_admins(
            f"{wname} ishxonadan uzoqda.\nSabab: {txt}\nJoylashuv: {link}",
            worker_id=wid,
        )
        await message.answer("Uzoqdasiz, lekin xabar ichida kel/ket yozuvi topilmadi, shuning uchun hisoblamadim.")

    await state.finish()
    await clear_old_employee_reply_keyboard(message.chat.id)
    if menu_message_id:
        await render_employee_menu(message.chat.id, message.from_user.id, preferred_message_id=menu_message_id)


@dp.message_handler(state=UserAttendance.waiting_for_reason,
                    content_types=[types.ContentType.ANY])
async def only_text_reason(message: types.Message):
    # Bu handlerda baza bilan ishlanmaydi, o'zgarmaydi
    await message.answer("Lokatsiyani qayta yuboring yoki sababni matn bilan yozing.")


# -----------------------------------------------------------------
# finalize_departure_far_away  |  finalize_arrival_far_away
# -----------------------------------------------------------------
async def finalize_departure_far_away(message, wid, wname, reason, lat, lon, dist, ts, branch_id=None):
    link = f"http://www.google.com/maps/place/{lat},{lon}"
    today_date = datetime.date.today()
    now_dt_aware = datetime.datetime.now(datetime.timezone.utc)

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        sess = await conn.fetchrow("""
                                   SELECT id, arrival_time, departure_time, branch_id
                                   FROM work_sessions
                                   WHERE user_id = $1
                                     AND date = $2
                                   ORDER BY id DESC LIMIT 1
                                   """, wid, today_date)

        if sess and sess['departure_time'] is None:
            s_id, arr_dt = sess['id'], sess['arrival_time']
            session_branch_id = sess.get("branch_id") or branch_id
            total_f = (now_dt_aware - arr_dt).total_seconds() / 3600.0

            await conn.execute("""
                               UPDATE work_sessions
                               SET departure_time = $1,
                                   total_hours    = $2,
                                   branch_id      = COALESCE(branch_id, $4)
                               WHERE id = $3
                               """, now_dt_aware, total_f, s_id, session_branch_id)

            await db.update_worker_day_status(
                wid,
                today_date,
                day_state="left",
                clock_out_at=now_dt_aware,
                study_active=False,
                last_source="worker",
            )

            await notify_admins(
                f"🔚 {wname} KETDI (uzoqda)\nKelish {arr_dt.astimezone().strftime('%H:%M:%S')}, Ketish {now_dt_aware.astimezone().strftime('%H:%M:%S')}\n"
                f"Ishlagan: {format_hours(total_f)}\nSabab: {reason}\n{link}",
                worker_id=wid,
            )
            await message.answer("Ketishingiz qayd qilindi (uzoqdasiz).")
        else:
            await notify_admins(
                f"{wname} bugun kelmagan, lekin \"ket\" deb yozdi.\nSabab: {reason}\n{link}",
                worker_id=wid,
            )
            await message.answer("Ketish qayd qilindi (kelmagan bo'lsangiz ham).")


async def finalize_arrival_far_away(message, wid, wname, reason, lat, lon, dist, ts, branch_id=None):
    date = datetime.date.today()
    now_dt_aware = datetime.datetime.now(datetime.timezone.utc)

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        dhrs_record = await conn.fetchrow("SELECT daily_work_hours, branch_id FROM workers WHERE id = $1", wid)
        dhrs = dhrs_record['daily_work_hours'] if dhrs_record else 0.0
        worker_branch_id = dhrs_record['branch_id'] if dhrs_record else None

        await conn.execute("""
                           INSERT INTO work_sessions (user_id, date, arrival_time, is_friday, session_daily_hours, branch_id)
                           VALUES ($1, $2, $3, FALSE, $4, $5)
                           ON CONFLICT (user_id, date) DO UPDATE SET
                               branch_id = COALESCE(work_sessions.branch_id, EXCLUDED.branch_id);
                           """, wid, date, now_dt_aware, dhrs, branch_id or worker_branch_id)

    if branch_id:
        worker = await db.get_worker_by_id(wid)
        if worker and worker.get("branch_id") is None:
            await db.assign_worker_branch(wid, branch_id)

    await db.update_worker_day_status(
        wid,
        date,
        day_state="working",
        clock_in_at=now_dt_aware,
        clock_out_at=None,
        rest_marked=False,
        study_active=False,
        last_source="worker",
    )

    link = f"http://www.google.com/maps/place/{lat},{lon}"
    await notify_admins(f"✅ {wname} KELDI, lekin uzoqda.\nSabab: {reason}\n{link}", worker_id=wid)
    await message.answer(f"Kelish qayd qilindi (uzoqdasiz). ({now_dt_aware.astimezone().strftime('%H:%M:%S')})")


@dp.message_handler(commands=["maoshim"])
async def worker_salary(message: types.Message):
    user_id = message.from_user.id

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        worker_record = await conn.fetchrow("SELECT id, full_name, monthly_salary FROM workers WHERE tg_id = $1",
                                            user_id)

        if not worker_record:
            return await message.reply("Siz ro'yxatdan o'tmagansiz.")

        wid, full_name, monthly_salary = worker_record.values()
        monthly_salary = monthly_salary or 0.0
        year_month_str = datetime.date.today().strftime("%Y-%m")

        payments = await conn.fetch("""
                                    SELECT payment_date, payment_time, amount
                                    FROM salary_payments
                                    WHERE worker_id = $1
                                      AND to_char(payment_date, 'YYYY-MM') = $2
                                    ORDER BY payment_time
                                    """, wid, year_month_str)

    total_paid = sum(p['amount'] for p in payments) if payments else 0.0
    remaining = (monthly_salary - total_paid) if monthly_salary > 0 else 0.0

    text = f"Hurmatli {full_name},\nTayinlangan maosh: {float(monthly_salary):,.0f} so'm\n"
    text += f"Bugungi kunga qadar olingan: {float(total_paid):,.0f} so'm\n"
    text += f"Qolgan: {float(remaining):,.0f} so'm\n\n"
    if payments:
        text += "📄 Joriy oy to‘lovlar:"
        for p in payments:
            p_datetime = p['payment_time'].astimezone()
            p_date_str = p_datetime.strftime("%d.%m.%Y")
            p_time_str = p_datetime.strftime("%H:%M")
            text += f"\n• {p_date_str} {p_time_str} — {float(p['amount']):,.0f} so‘m"

    await message.reply(text)

@dp.message_handler(commands=["help"], state="*")
async def help_command_handler(message: types.Message, state: FSMContext):
    # Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
    if message.from_user.id in ADMINS:
        await message.reply("Bu buyruq oddiy userlar uchun. Siz admin ekansiz.")
        return

    help_text = (
        "Assalomu alaykum, bu bot ish vaqtini kuzatish, kelish-ketish nazorati, "
        "kechikish sabablari va maosh to'lovlarini boshqarish kabi vazifalarni bajaradi.\n\n"
        "Agar botda biror muammo yoki qo‘shimcha taklif/talab bo‘lsa,"
        "admin(lar)ga murojaat qiling.\n\n"
        "Admin bilan bog‘lanish:@rustamxojayev_abdulboriy"
    )
    await message.reply(help_text)
    await message.answer("Muammo yoki taklifingizni matn ko‘rinishida yozing. Bekor qilish uchun /cancel.")
    await HelpState.waiting_for_feedback.set()


@dp.message_handler(commands=["cancel"], state=HelpState.waiting_for_feedback)
async def help_cancel(message: types.Message, state: FSMContext):
    # Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
    await state.finish()
    await message.reply(
        "Bekor qilindi. Agar qayta taklif yoki muammo yozmoqchi bo'lsangiz, /help buyrug'ini qaytadan kiritishingiz mumkin.",
        reply_markup=types.ReplyKeyboardRemove()
    )


@dp.message_handler(state=HelpState.waiting_for_feedback, content_types=types.ContentTypes.TEXT)
async def process_help_feedback(message: types.Message, state: FSMContext):
    # Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
    feedback_text = message.text.strip()
    if not feedback_text:
        await message.reply("Iltimos, hech bo‘lmasa biror matn yozing.")
        return

    user_id = message.from_user.id
    user_name = message.from_user.full_name
    to_admins = f"[HELP] Foydalanuvchi: {user_name if user_name else ''} (ID:{user_id})\nMuammo/taklif:\n{feedback_text}"
    worker_record = await db.get_worker_by_tg_id(user_id)
    worker_id = worker_record.get("id") if worker_record else None

    if worker_id:
        await notify_admins(to_admins, worker_id=worker_id)
    else:
        await notify_selected_admins(SUPERADMINS, to_admins)

    await message.reply(
        "Xabaringiz adminga yuborildi. Tez orada javob kuting.\n"
        "Admin bilan bog‘lanish: @Z_M_ziyayev, @rustamxojayev_abdulboriy",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.finish()


@dp.message_handler(state=HelpState.waiting_for_feedback, content_types=[types.ContentType.ANY])
async def only_text_for_help(message: types.Message):
    # Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
    await message.reply(
        "Iltimos, muammo yoki taklifingiz bo'lsa uni to'g'ri yozing, agar muammo bo'layotgan bo'lsa admin bilan bog'laning, admin: @Z_M_ziyayev, @rustamxojayev_abdulboriy. Bekor qilish uchun /cancel.")


@dp.message_handler(commands=['mystats'], state=None)
async def cmd_mystats(message: types.Message):
    # --- TUZATISH: db.get_user_distinct_years ishlatiladi ---
    years = await db.get_user_distinct_years(message.from_user.id)

    if not years:
        years = [datetime.datetime.now().year]

    # Eslatma: make_mystats_years_keyboard funksiyasi o'zgartirilishi kerak (avvalgi javobda ko'rsatilgan)
    kb = make_mystats_years_keyboard(years)

    await MyStatsStates.SELECT_YEAR.set()
    await message.answer(
        "📊 Qaysi yil statistikangizni ko‘rmoqchisiz?",
        reply_markup=kb
    )


@dp.callback_query_handler(lambda c: c.data.startswith('mystats:year:'), state=MyStatsStates.SELECT_YEAR)
async def process_year(callback_query: types.CallbackQuery, state: FSMContext):
    year = int(callback_query.data.split(':')[2])
    await state.update_data(chosen_year=year)

    # --- TUZATISH: db.get_user_distinct_months ishlatiladi ---
    months = await db.get_user_distinct_months(callback_query.from_user.id, year)

    # Eslatma: make_mystats_months_keyboard funksiyasi o'zgartirilishi kerak (avvalgi javobda ko'rsatilgan)
    kb = make_mystats_months_keyboard(months)

    await callback_query.message.edit_text(
        "📊 Iltimos, oyni tanlang:",
        reply_markup=kb
    )
    await MyStatsStates.SELECT_MONTH.set()
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('mystats:month:'), state=MyStatsStates.SELECT_MONTH)
async def process_month(callback_query: types.CallbackQuery, state: FSMContext):
    month_num = int(callback_query.data.split(':')[2])
    data = await state.get_data()
    year = data['chosen_year']
    tg_id = callback_query.from_user.id

    worker_id = await db.get_worker_db_id(tg_id)
    if not worker_id:
        await callback_query.message.edit_text("❌ Siz xodimlar ro‘yxatida topilmadingiz.")
        await state.finish()
        return

    month_str = f"{year}-{month_num:02d}"
    async with db.pool.acquire() as conn:
        sessions = await conn.fetch(
            """
            SELECT date, arrival_time, departure_time, session_daily_hours, total_hours, is_friday
            FROM work_sessions
            WHERE user_id = $1
              AND to_char(date
                , 'YYYY-MM') = $2
            ORDER BY date
            """,
            worker_id, month_str
        )

    y, m = year, month_num
    next_month = m % 12 + 1
    next_year = y + (m // 12)
    last_day = (datetime.date(next_year, next_month, 1) - datetime.timedelta(days=1)).day

    day_map = {f"{month_str}-{d:02d}": {"arr": None, "dep": None, "req": None, "got": None, "fri": False} for d in
               range(1, last_day + 1)}

    for session in sessions:
        d_str = session['date'].strftime('%Y-%m-%d')
        day_map[d_str] = {"arr": session['arrival_time'], "dep": session['departure_time'],
                          "req": session['session_daily_hours'], "got": session['total_hours'],
                          "fri": bool(session['is_friday'])}

    tol = LATE_EARLY_TOLERANCE_MIN
    rest_cfg = await db.get_rest_day()
    report = [f"📊 Statistikangiz: {month_str}", ""]

    for day_str, info in sorted(day_map.items()):
        wd = datetime.datetime.strptime(day_str, "%Y-%m-%d").weekday()

        if rest_cfg is not None and wd == rest_cfg and info["arr"] is None: continue

        prefix = "🟢"
        if rest_cfg is not None and wd == rest_cfg and info["arr"]:
            prefix = "🔵"
        elif info["arr"] is None:
            prefix = "🔴"
            report.append(f"{prefix} {day_str} — botdan <i>foydalanilmagan</i>")
            continue

        # --- TUZATISH SHU YERDA ---
        arr_str = info['arr'].astimezone(tashkent_tz).strftime('%H:%M:%S') if info['arr'] else '—'
        dep_str = info['dep'].astimezone(tashkent_tz).strftime('%H:%M:%S') if info['dep'] else '—'
        # -------------------------

        line = f"{prefix} {day_str}: {arr_str} — {dep_str}"

        if info["req"] and info["got"] is not None:
            required_min = int(float(info["req"] or 0.0) * 60)
            actual_min = int(float(info["got"] or 0.0) * 60)
            diff = actual_min - required_min
            if diff > tol:
                line += f" (+{diff} daq ortiq)"
            elif diff < -tol:
                line += f" ({abs(diff)} daq kam)"
        report.append(line)

    await callback_query.message.edit_text("\n".join(report), parse_mode="HTML", reply_markup=None)
    await state.finish()
    await callback_query.answer()


# Bu bizning yangi "asbobimiz"
async def execute_forward_to_admin(user_id: int, message_text: str):
    """Xodimning xabarini bazaga yozadi va adminga yuboradi."""
    async with db.pool.acquire() as conn:
        worker_record = await conn.fetchrow(
            "SELECT id, full_name, branch_id FROM workers WHERE tg_id = $1",
            user_id,
        )
        if not worker_record:
            return

        worker_db_id = worker_record['id']
        worker_name = worker_record['full_name']
        branch_id = worker_record['branch_id']

        await conn.execute(
            """INSERT INTO attendance (user_id, name, branch_id, message, reason)
               VALUES ($1, $2, $3, $4, $5)""",
            worker_db_id, worker_name, branch_id, message_text, "xodim_xabari"
        )

    admin_message = f"✉️ Xodimdan xabar:\n\n<b>Xodim:</b> {worker_name} (TG ID: {user_id})\n<b>Xabar:</b> <i>{html.escape(message_text)}</i>"
    await notify_admins(admin_message, worker_id=worker_db_id)


# Bu yangi handler xodimlardan kelgan har qanday matnni qabul qiladi
@dp.message_handler(lambda message: message.from_user.id not in ADMINS, content_types=types.ContentTypes.TEXT)
async def handle_employee_text_message(message: types.Message):
    """Oddiy xodim yozgan matnni AIga yuborib, kerakli amalni bajaradi."""
    await types.ChatActions.typing()

    # ai_helpers dagi yangi funksiyamizni chaqiramiz
    ai_result = await process_employee_request(message.text)

    action = ai_result.get("action")

    if action == "forward":
        # Agar AI "adminga yubor" desa, yuboramiz
        message_to_send = ai_result.get("message", message.text)
        await execute_forward_to_admin(user_id=message.from_user.id, message_text=message_to_send)
        await message.reply("✅ Xabaringiz adminga yetkazildi va tizimga yozib qo'yildi.")

    elif action == "ignore":
        # Agar AI "e'tibor berma" desa, hech narsa qilmaymiz
        pass

    else:  # action == "error" yoki boshqa holat bo'lsa
        # Agar AI bilan bog'lanishda xatolik bo'lsa
        print(f"Xodim AI xatoligi: {ai_result.get('message')}")
        # Xodimga bu haqida bildirish shart emas, shunchaki adminga yuboramiz
        await execute_forward_to_admin(user_id=message.from_user.id,
                                       message_text=f"(AI tahlil qila olmadi) {message.text}")
        await message.reply("✅ Xabaringiz adminga yetkazildi.")
