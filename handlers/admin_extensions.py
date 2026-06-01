import datetime
import logging

from aiogram import types
from aiogram.dispatcher import FSMContext

import database as db
from config import ABSENCE_REMINDER_DELAY_MIN, ADMINS, SUPERADMINS
from loader import dp
from shared import (
    build_admin_home_payload,
    build_branch_selection_keyboard,
    has_admin_operating_branch,
    format_admin_actor,
    notify_admins,
    notify_admins_and_group,
)
from states import AdminAddWorker


def _build_skip_keyboard() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("O'tkazib yuborish")


def _parse_time_text(raw_text: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None

    text = text.replace("：", ":").replace(".", ":").replace(" ", ":")
    parts = [part for part in text.split(":") if part]
    if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
        hh, mm = parts[0][:2], parts[0][2:]
    elif len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        hh, mm = parts[0], parts[1]
    else:
        return None

    hh_int = int(hh)
    mm_int = int(mm)
    if 0 <= hh_int <= 23 and 0 <= mm_int <= 59:
        return f"{hh_int:02d}:{mm_int:02d}"
    return None


def _calculate_daily_hours_from_range(start_text: str | None, end_text: str | None) -> float:
    if not start_text or not end_text:
        return 0.0

    start_time = datetime.datetime.strptime(start_text, "%H:%M").time()
    end_time = datetime.datetime.strptime(end_text, "%H:%M").time()
    today = datetime.date.today()
    start_dt = datetime.datetime.combine(today, start_time)
    end_dt = datetime.datetime.combine(today, end_time)
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)
    return round((end_dt - start_dt).total_seconds() / 3600.0, 2)


def _format_schedule_text(start_text: str | None, end_text: str | None) -> str:
    if start_text and end_text:
        return f"{start_text} - {end_text}"
    if start_text:
        return f"{start_text} - Belgilanmagan"
    if end_text:
        return f"Belgilanmagan - {end_text}"
    return "Belgilanmagan"


async def _finish_admin_add_worker(message: types.Message, state: FSMContext):
    data = await state.get_data()
    full_name = data.get("new_worker_name")
    tg_id = data.get("new_worker_tg_id")
    pay_type = data.get("new_worker_pay_type", "monthly")
    pay_amount = data.get("new_worker_pay_amount", 0.0)
    branch_id = data.get("new_worker_branch_id")
    start_text = data.get("new_worker_start_time")
    end_text = data.get("new_worker_end_time")
    daily_work_hours = _calculate_daily_hours_from_range(start_text, end_text)
    start_time = datetime.datetime.strptime(start_text, "%H:%M").time() if start_text else None
    end_time = datetime.datetime.strptime(end_text, "%H:%M").time() if end_text else None

    try:
        worker_id = await db.create_worker_record(
            full_name=full_name,
            tg_id=tg_id,
            pay_type=pay_type,
            pay_amount=pay_amount,
            daily_work_hours=daily_work_hours,
            work_start=start_time,
            work_end=end_time,
            has_phone=bool(tg_id),
            branch_id=branch_id,
        )
    except Exception as exc:
        logging.error(f"Xodim qo'shishda xatolik: {exc}")
        return await message.reply("Xodim qo'shishda xatolik yuz berdi. Balki bu Telegram ID allaqachon mavjuddir.")

    await state.finish()
    phone_text = "Telefoni bor" if tg_id else "Telefoni yo'q"
    branch = await db.get_branch_by_id(branch_id)
    branch_name = branch["name"] if branch else "Belgilanmagan"
    await message.reply(
        f"Yangi xodim qo'shildi.\n\n"
        f"ID: {worker_id}\n"
        f"Ism: {full_name}\n"
        f"Filial: {branch_name}\n"
        f"To'lov turi: {pay_type}\n"
        f"Miqdor: {pay_amount:,.0f} so'm\n"
        f"Ish vaqti: {_format_schedule_text(start_text, end_text)}\n"
        f"Holati: {phone_text}",
        reply_markup=(await build_admin_home_payload(message.from_user.id))[1],
    )


async def notify_phone_less_pending_admins():
    local_tz = datetime.timezone(datetime.timedelta(hours=5))
    now_local = datetime.datetime.now(local_tz)
    pending_workers = await db.get_phone_less_workers_pending_manual(
        now_local,
        ABSENCE_REMINDER_DELAY_MIN,
    )
    if not pending_workers:
        return

    grouped_workers = {}
    for worker in pending_workers:
        grouped_workers.setdefault(worker.get("branch_id"), []).append(worker)

    for branch_id, workers in grouped_workers.items():
        names = ", ".join(worker["full_name"] for worker in workers)
        await notify_admins(
            f"Eslatma: {names} kelgan bo'lsa, kelganini belgilashni unutmang.",
            branch_id=branch_id,
        )


async def _get_branch_prompt_data(admin_tg_id: int) -> tuple[list[dict], int | None]:
    branches = await db.list_branches_for_admin(admin_tg_id)
    preferred_branch_id = await db.get_admin_preferred_branch_id(admin_tg_id)
    return branches, preferred_branch_id


async def _ensure_admin_worker_access(actor_id: int, worker_id: int) -> bool:
    return await db.admin_can_access_worker(actor_id, worker_id)


async def get_worker_action_button_specs(worker_id: int) -> dict:
    today = datetime.date.today()
    day_status = await db.get_worker_day_status(worker_id, today) or {}
    session = await db.get_session_for_worker_on_date(worker_id, today) or {}

    has_arrived = bool(day_status.get("clock_in_at") or session.get("arrival_time"))
    has_left = bool(
        day_status.get("clock_out_at")
        or session.get("departure_time")
        or day_status.get("day_state") == "left"
    )
    rest_marked = bool(day_status.get("rest_marked"))
    study_active = bool(day_status.get("study_active"))

    if has_arrived and not has_left and not rest_marked:
        work_action = "out"
        work_label = "🔴 Ketdi"
        work_style = "danger"
    else:
        work_action = "in"
        work_label = "✅ Keldi"
        work_style = "success"

    if study_active:
        study_action = "study_return"
        study_label = "↩️ O'qishdan qaytdi"
        study_style = "success"
    else:
        study_action = "study_leave"
        study_label = "🎓 O'qishga ketdi"
        study_style = "primary"

    return {
        "work_action": work_action,
        "work_label": work_label,
        "work_style": work_style,
        "study_action": study_action,
        "study_label": study_label,
        "study_style": study_style,
    }


async def build_worker_action_keyboard(worker_id: int, back_callback: str) -> types.InlineKeyboardMarkup:
    specs = await get_worker_action_button_specs(worker_id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton(
            specs["work_label"],
            callback_data=f"wact:{specs['work_action']}:{worker_id}",
            style=specs["work_style"],
        ),
        types.InlineKeyboardButton("🌙 Dam", callback_data=f"wact:rest:{worker_id}", style="danger"),
    )
    kb.add(
        types.InlineKeyboardButton(
            specs["study_label"],
            callback_data=f"wact:{specs['study_action']}:{worker_id}",
            style=specs["study_style"],
        )
    )
    kb.add(
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data=back_callback, style="primary"),
    )
    return kb


async def apply_worker_action_for_admin(
    worker_id: int,
    action: str,
    actor_id: int,
    actor_name: str | None = None,
) -> tuple[bool, str, dict | None]:
    """Admin tomonidan xodimga davomat amalini qo'llaydi."""
    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return False, "Xodim topilmadi.", None

    today = datetime.date.today()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    action_text = ""
    actor_label = format_admin_actor(actor_id, actor_name)

    if action == "in":
        session = await db.get_session_for_worker_on_date(worker_id, today)
        if not session or not session.get("arrival_time"):
            async with db.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO work_sessions (user_id, date, arrival_time, session_daily_hours)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, date) DO UPDATE SET arrival_time = EXCLUDED.arrival_time
                    """,
                    worker_id,
                    today,
                    now_dt,
                    worker.get("daily_work_hours") or 0.0,
                )
        await db.update_worker_day_status(
            worker_id,
            today,
            day_state="working",
            clock_in_at=now_dt,
            clock_out_at=None,
            rest_marked=False,
            study_active=False,
            last_source="admin",
        )
        await db.log_worker_activity(worker_id, "manual_clock_in", "Admin ishga keldi deb belgiladi", actor_id, "admin", today)
        await notify_admins_and_group(
            f"{worker['full_name']} ishga keldi. ({actor_label} belgiladi)",
            worker_id=worker_id,
        )
        action_text = "ishga keldi deb belgilandi"
    elif action == "out":
        session = await db.get_session_for_worker_on_date(worker_id, today)
        if session and session.get("arrival_time") and not session.get("departure_time"):
            total_f = (now_dt - session["arrival_time"]).total_seconds() / 3600.0
            async with db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE work_sessions SET departure_time = $1, total_hours = $2 WHERE id = $3",
                    now_dt,
                    total_f,
                    session["id"],
                )
        await db.update_worker_day_status(
            worker_id,
            today,
            day_state="left",
            clock_out_at=now_dt,
            study_active=False,
            last_source="admin",
        )
        await db.log_worker_activity(worker_id, "manual_clock_out", "Admin ishdan ketdi deb belgiladi", actor_id, "admin", today)
        await notify_admins_and_group(
            f"{worker['full_name']} ishdan ketdi. ({actor_label} belgiladi)",
            worker_id=worker_id,
        )
        action_text = "ishdan ketdi deb belgilandi"
    elif action == "rest":
        await db.update_worker_day_status(
            worker_id,
            today,
            day_state="rest",
            rest_marked=True,
            absence_review_status="excused",
            last_source="admin",
        )
        await db.log_worker_activity(worker_id, "manual_rest", "Admin bugun dam deb belgiladi", actor_id, "admin", today)
        await notify_admins_and_group(
            f"{worker['full_name']} bugun dam oldi. ({actor_label} belgiladi)",
            worker_id=worker_id,
        )
        action_text = "dam deb belgilandi"
    elif action == "study_leave":
        await db.update_worker_day_status(
            worker_id,
            today,
            day_state="working",
            study_active=True,
            study_left_at=now_dt,
            last_source="admin",
        )
        await db.log_worker_activity(worker_id, "manual_study_leave", "Admin o'qishga ketdi deb belgiladi", actor_id, "admin", today)
        await notify_admins_and_group(
            f"{worker['full_name']} o'qishga ketdi. ({actor_label} belgiladi)",
            worker_id=worker_id,
        )
        action_text = "o'qishga ketdi deb belgilandi"
    elif action == "study_return":
        await db.update_worker_day_status(
            worker_id,
            today,
            day_state="working",
            study_active=False,
            study_returned_at=now_dt,
            last_source="admin",
        )
        await db.log_worker_activity(worker_id, "manual_study_return", "Admin o'qishdan qaytdi deb belgiladi", actor_id, "admin", today)
        await notify_admins_and_group(
            f"{worker['full_name']} o'qishdan qaytdi. ({actor_label} belgiladi)",
            worker_id=worker_id,
        )
        action_text = "o'qishdan qaytdi deb belgilandi"
    else:
        return False, "Noma'lum amal.", worker

    if not worker.get("has_phone"):
        await notify_phone_less_pending_admins()

    return True, action_text, worker


@dp.callback_query_handler(lambda c: c.data == "admin_add_worker", state="*")
async def admin_add_worker_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await has_admin_operating_branch(callback_query.from_user.id):
        home_text, home_markup = await build_admin_home_payload(callback_query.from_user.id)
        await callback_query.message.edit_text(home_text, reply_markup=home_markup)
        return await callback_query.answer("Avval filialni tanlang.", show_alert=True)

    await state.finish()
    await callback_query.message.edit_text(
        "Yangi xodim uchun ism kiriting.\n\nMasalan: Abdulloh Karimov"
    )
    await AdminAddWorker.waiting_for_name.set()
    await callback_query.answer()


@dp.message_handler(state=AdminAddWorker.waiting_for_name, content_types=types.ContentTypes.TEXT)
async def admin_add_worker_name(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    full_name = message.text.strip()
    if len(full_name) < 3:
        return await message.reply("Ism juda qisqa ko'rinmoqda. Iltimos, to'liqroq ism kiriting.")

    await state.update_data(new_worker_name=full_name)
    await AdminAddWorker.waiting_for_tg_id.set()
    await message.reply(
        "Telegram ID kiriting.\nAgar xodimning telefoni yo'q bo'lsa yoki botdan foydalanmasa, 0 yuboring."
    )


@dp.message_handler(state=AdminAddWorker.waiting_for_tg_id, content_types=types.ContentTypes.TEXT)
async def admin_add_worker_tg_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    raw_value = message.text.strip()
    try:
        tg_id = int(raw_value)
    except ValueError:
        return await message.reply("Telegram ID raqam bo'lishi kerak. Telefoni yo'q bo'lsa, 0 yuboring.")

    await state.update_data(new_worker_tg_id=tg_id if tg_id > 0 else None)
    branches, preferred_branch_id = await _get_branch_prompt_data(message.from_user.id)
    if preferred_branch_id:
        await state.update_data(new_worker_branch_id=preferred_branch_id)
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton("Kunlik", callback_data="addworker_pay:daily", style="primary"),
            types.InlineKeyboardButton("Haftalik", callback_data="addworker_pay:weekly", style="primary"),
            types.InlineKeyboardButton("Oylik", callback_data="addworker_pay:monthly", style="primary"),
        )
        await message.reply("To'lov turini tanlang:", reply_markup=kb)
        return

    if not branches:
        if message.from_user.id in SUPERADMINS:
            return await message.reply(
                "Avval ishlaydigan filialni tanlang. /start bosing yoki admin menyusidagi "
                "'Filialni almashtirish' tugmasidan foydalaning."
            )
        return await message.reply("Sizga biror filial biriktirilmagan. Avval filial sozlamalarini tekshiring.")

    await AdminAddWorker.waiting_for_branch.set()
    await message.reply(
        "Xodim qaysi filialga tegishli?",
        reply_markup=build_branch_selection_keyboard(branches, "addworker_branch"),
    )


@dp.callback_query_handler(lambda c: c.data.startswith("addworker_branch:"), state=AdminAddWorker.waiting_for_branch)
async def admin_add_worker_branch(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    try:
        branch_id = int(callback_query.data.split(":")[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    if not await db.admin_can_access_branch(callback_query.from_user.id, branch_id):
        return await callback_query.answer("Bu filial sizga biriktirilmagan.", show_alert=True)

    await state.update_data(new_worker_branch_id=branch_id)
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("Kunlik", callback_data="addworker_pay:daily", style="primary"),
        types.InlineKeyboardButton("Haftalik", callback_data="addworker_pay:weekly", style="primary"),
        types.InlineKeyboardButton("Oylik", callback_data="addworker_pay:monthly", style="primary"),
    )
    await callback_query.message.edit_text("To'lov turini tanlang:", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("addworker_pay:"), state="*")
async def admin_add_worker_pay_type(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    pay_type = callback_query.data.split(":")[1]
    await state.update_data(new_worker_pay_type=pay_type)
    await AdminAddWorker.waiting_for_pay_amount.set()
    await callback_query.message.edit_text("To'lov miqdorini kiriting.\nMasalan: 150000")
    await callback_query.answer()


@dp.message_handler(state=AdminAddWorker.waiting_for_pay_amount, content_types=types.ContentTypes.TEXT)
async def admin_add_worker_pay_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    try:
        pay_amount = float(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        return await message.reply("Miqdorni raqam ko'rinishida kiriting.")

    await state.update_data(new_worker_pay_amount=pay_amount)
    await AdminAddWorker.waiting_for_start_time.set()
    await message.reply(
        "Ish boshlash vaqtini kiriting.\nMasalan: 09:00\n\nYoki `O'tkazib yuborish`ni bosing.",
        reply_markup=_build_skip_keyboard(),
        parse_mode="Markdown",
    )


@dp.message_handler(state=AdminAddWorker.waiting_for_start_time, content_types=types.ContentTypes.TEXT)
async def admin_add_worker_start_time(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() == "o'tkazib yuborish":
        await state.update_data(new_worker_start_time=None)
    else:
        parsed = _parse_time_text(raw_text)
        if not parsed:
            return await message.reply("Vaqtni HH:MM formatda kiriting. Masalan: 09:00 yoki `O'tkazib yuborish`ni bosing.")
        await state.update_data(new_worker_start_time=parsed)

    await AdminAddWorker.waiting_for_end_time.set()
    await message.reply(
        "Ish tugash vaqtini kiriting.\nMasalan: 18:00\n\nYoki `O'tkazib yuborish`ni bosing.",
        reply_markup=_build_skip_keyboard(),
        parse_mode="Markdown",
    )


@dp.message_handler(state=AdminAddWorker.waiting_for_end_time, content_types=types.ContentTypes.TEXT)
async def admin_add_worker_end_time(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() == "o'tkazib yuborish":
        await state.update_data(new_worker_end_time=None)
    else:
        parsed = _parse_time_text(raw_text)
        if not parsed:
            return await message.reply("Vaqtni HH:MM formatda kiriting. Masalan: 18:00 yoki `O'tkazib yuborish`ni bosing.")
        await state.update_data(new_worker_end_time=parsed)

    await _finish_admin_add_worker(message, state)


@dp.callback_query_handler(lambda c: c.data.startswith("worker_actions_"), state="*")
async def worker_actions_menu(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    worker_id = int(callback_query.data.split("_")[2])
    if not await _ensure_admin_worker_access(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return await callback_query.answer("Xodim topilmadi.", show_alert=True)

    kb = await build_worker_action_keyboard(worker_id, back_callback=f"worker_{worker_id}")
    await callback_query.message.edit_text(
        f"{worker['full_name']} uchun davomat amalini tanlang:",
        reply_markup=kb,
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("wact:"), state="*")
async def worker_actions_apply(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    _, action, worker_id_raw = callback_query.data.split(":")
    worker_id = int(worker_id_raw)
    actor_id = callback_query.from_user.id
    if not await _ensure_admin_worker_access(actor_id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)

    ok, action_text, worker = await apply_worker_action_for_admin(
        worker_id,
        action,
        actor_id,
        callback_query.from_user.full_name,
    )
    if not ok:
        return await callback_query.answer(action_text, show_alert=True)

    kb = await build_worker_action_keyboard(worker_id, back_callback=f"worker_{worker_id}")
    await callback_query.message.edit_text(
        f"Xodim: {worker['full_name']}\nOxirgi amal: {action_text}.",
        reply_markup=kb,
    )
    await callback_query.answer("Saqlandi.")
