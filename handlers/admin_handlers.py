# admin_handlers.py
import html
import re
import os  # Buni faylning eng yuqorisiga qo'shing
from aiogram.utils.exceptions import MessageNotModified
import datetime
import logging
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, InlineKeyboardButton

from ai_helpers import transcribe_voice_to_text, process_admin_request_with_tools, to_latin, _prepare_text_for_ai
from loader import dp, bot
from config import ADMINS, LATE_EARLY_TOLERANCE_MIN, SUPERADMINS
# --- TUZATISH: To'g'ri import usuli ---
import database as db
from states import (
    AdminAcceptPending, AdminUpdateWorker, AdminSetSalary, AdminAddSalaryPayment, AdminModifyPayment,
    AdminModifyMonthlySalary, AdminSetDailyHours, AdminSetWorkTime, AdminManualAttendance, AdminQuickAttendance, AdminBranchAdminSettings,
    AdminSuperadminSettings, AdminContact, AdminWorkGroup, SetBranchLocation
)
from shared import (
    build_branch_selection_keyboard,
    pending_requests,
    notify_admins_and_group,
    notify_selected_admins,
    build_paginated_inline,
    describe_admin_action_result,
    dismiss_reply_keyboard,
    format_admin_actor,
    format_pay_status,
    format_payment_kind,
    get_admin_home_text,
    get_admin_action_lock,
    get_admin_action_result,
    get_superadmin_branch_selector_text,
    resolve_admin_action,
)
from keyboards import (
    get_admin_extra_menu,
    get_weekday_select_menu
)
from menu_overrides import get_admin_main_menu
from handlers.admin_extensions import (
    apply_worker_action_for_admin,
    get_worker_action_button_specs,
)
import pytz

TEMP_AUDIO_DIR = "temp_audio"
if not os.path.exists(TEMP_AUDIO_DIR):
    os.makedirs(TEMP_AUDIO_DIR)

tashkent_tz = pytz.timezone('Asia/Tashkent')



def _build_web_removed_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("Orqaga", callback_data="admin_extra", style="primary"))
    return kb


@dp.message_handler(lambda message: message.from_user.id in ADMINS, commands=['webapp', 'panel'])
async def web_removed_command(message: types.Message):
    if not await _ensure_admin_operating_scope_message(message):
        return
    await message.answer(
        "Web bo'limi olib tashlangan.\n\nBarcha boshqaruv endi bot ichida ishlaydi.",
        reply_markup=get_admin_main_menu(message.from_user.id),
    )


@dp.callback_query_handler(lambda c: c.data == "web_login_menu" or c.data.startswith("weblogin:"))
async def disabled_web_admin_callbacks(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return

    await callback_query.message.edit_text(
        "Web bo'limi olib tashlangan.\n\nBarcha admin boshqaruvi endi bot ichida yuradi.",
        reply_markup=_build_web_removed_keyboard(),
    )
    await callback_query.answer("Web bo'limi o'chirilgan.")


@dp.message_handler(
    lambda message: message.chat.type == "private" and message.from_user.id in ADMINS,
    content_types=types.ContentTypes.VOICE, state=None)
async def handle_admin_voice_message(message: types.Message, state: FSMContext):
    """Adminning ovozli xabarini qabul qilib, matnga o'giradi va qayta ishlaydi."""

    if not await _ensure_admin_operating_scope_message(message, state):
        return

    await message.reply("Ovozli xabar qabul qilindi, matnga o'girilyapti...")

    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = os.path.join(TEMP_AUDIO_DIR, f"{file_id}.ogg")
    await bot.download_file(file.file_path, destination=file_path)

    transcribed_text = await transcribe_voice_to_text(file_path)

    if transcribed_text:
        await message.reply(f"🔍 Tushunilgan matn: <i>\"{transcribed_text}\"</i>\n\nEndi bu so'rov tahlil qilinadi...")

        # --- ASOSIY O'ZGARISH SHU YERDA ---
        # Matnni to'g'ridan-to'g'ri emas, "aqlli filtr" orqali o'tkazib yuboramiz
        message.text = _prepare_text_for_ai(transcribed_text)
        # ------------------------------------

        await admin_natural_language_query(message, state)
    else:
        await message.reply("❌ Nutqni aniqlab bo'lmadi. Iltimos, aniqroq va balandroq gapirib, qayta urunib ko'ring.")


async def safe_edit_text(msg: types.Message, text: str, **kwargs):
    try:
        await msg.edit_text(text, **kwargs)
        return True
    except MessageNotModified:
        return True
    except Exception:
        logging.exception("Xabarni edit qilishda xatolik", extra={"chat_id": getattr(msg.chat, "id", None), "message_id": getattr(msg, "message_id", None)})
        return False


async def _render_superadmin_branch_selector(
    admin_tg_id: int,
    *,
    message_obj: types.Message | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    back_callback: str | None = None,
):
    branches = await db.get_active_branches()
    current_branch_id = await db.get_superadmin_selected_branch_id(admin_tg_id)
    text = await get_superadmin_branch_selector_text(admin_tg_id)
    reply_markup = build_branch_selection_keyboard(
        branches,
        "superbranch:select",
        back_callback=back_callback,
        current_branch_id=current_branch_id,
    )
    if not branches:
        text = "Faol filial topilmadi."

    if message_obj is not None:
        edited = await safe_edit_text(message_obj, text, reply_markup=reply_markup)
        if not edited:
            await bot.send_message(message_obj.chat.id, text, reply_markup=reply_markup)
        return

    if message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def _render_admin_home(
    admin_tg_id: int,
    *,
    message_obj: types.Message | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
):
    if admin_tg_id in SUPERADMINS and not await db.get_superadmin_selected_branch_id(admin_tg_id):
        await _render_superadmin_branch_selector(
            admin_tg_id,
            message_obj=message_obj,
            chat_id=chat_id,
            message_id=message_id,
        )
        return

    text = await get_admin_home_text(admin_tg_id)
    reply_markup = get_admin_main_menu(admin_tg_id)

    if message_obj is not None:
        edited = await safe_edit_text(message_obj, text, reply_markup=reply_markup)
        if not edited:
            await bot.send_message(message_obj.chat.id, text, reply_markup=reply_markup)
        return

    if message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await bot.send_message(chat_id, text, reply_markup=reply_markup)


def _parse_hhmm_input(raw_text: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None

    text = text.replace("：", ":")
    parts = re.findall(r"\d+", text)
    if len(parts) == 1 and len(parts[0]) == 4:
        hh, mm = parts[0][:2], parts[0][2:]
    elif len(parts) >= 2:
        hh, mm = parts[0], parts[1]
    else:
        return None

    try:
        hh_int = int(hh)
        mm_int = int(mm)
    except ValueError:
        return None

    if 0 <= hh_int <= 23 and 0 <= mm_int <= 59:
        return f"{hh_int:02d}:{mm_int:02d}"
    return None


async def _exit_admin_fsm_to_menu(message: types.Message, state: FSMContext):
    await state.finish()
    await _render_admin_home(message.from_user.id, chat_id=message.chat.id)


def _format_worker_branch_label(worker: dict) -> str:
    branch_name = (worker.get("branch_name") or "").strip()
    if branch_name:
        return f"{worker['full_name']} [{branch_name}]"
    return worker["full_name"]


def _format_worker_option_label(worker: dict, position: int | None = None) -> str:
    """Tugma matni: '<pos>) <ism> [filial]'.

    position berilsa — pozitsion raqam (1, 2, 3...) ishlatamiz (sahifada
    tartibli). Berilmasa — DB id'siga qaytadi (eski xulq). Pozitsion raqam
    deyarli har doim afzal: DB id'lari boshqa filiallarda yoki o'chirilgan
    yozuvlarda bo'lib, "1)" tushib qolishi mumkin.
    """
    prefix = f"{position}) " if position is not None else f"{worker['id']}) "
    return f"{prefix}{_format_worker_branch_label(worker)}"


def _build_worker_branch_picker_keyboard(
    worker_id: int,
    branches: list[dict],
    current_branch_id: int | None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for branch in branches:
        prefix = "✅ " if current_branch_id and int(branch["id"]) == int(current_branch_id) else ""
        kb.add(
            InlineKeyboardButton(
                f"{prefix}{branch['name']}",
                callback_data=f"moveworkerto:{worker_id}:{branch['id']}",
                style="primary",
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"worker_{worker_id}", style="primary"))
    return kb


def _build_worker_branch_apply_keyboard(worker_id: int, branch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            "Faqat bundan keyin",
            callback_data=f"moveworkerapply:{worker_id}:{branch_id}:future",
            style="primary",
        ),
        InlineKeyboardButton(
            "Barcha eski yozuvlar bilan",
            callback_data=f"moveworkerapply:{worker_id}:{branch_id}:history",
            style="success",
        ),
        InlineKeyboardButton(
            "⬅️ Filial tanlash",
            callback_data=f"moveworker:{worker_id}",
            style="primary",
        ),
    )
    return kb


async def _ensure_worker_access_callback(callback_query: types.CallbackQuery, worker_id: int) -> bool:
    if await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return True
    await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    return False


async def _ensure_superadmin_callback(callback_query: types.CallbackQuery) -> bool:
    if callback_query.from_user.id in SUPERADMINS:
        return True
    await callback_query.answer("Bu bo'lim faqat katta admin uchun.", show_alert=True)
    return False


async def _ensure_superadmin_message(message: types.Message, state: FSMContext | None = None) -> bool:
    if message.from_user.id in SUPERADMINS:
        return True
    if state:
        await state.finish()
    await message.reply("Bu bo'lim faqat katta admin uchun.")
    return False


async def _ensure_admin_operating_scope_callback(
    callback_query: types.CallbackQuery,
    state: FSMContext | None = None,
) -> bool:
    admin_tg_id = callback_query.from_user.id
    if await db.get_admin_branch_ids(admin_tg_id):
        return True

    if state:
        await state.finish()

    if admin_tg_id in SUPERADMINS:
        await _render_superadmin_branch_selector(admin_tg_id, message_obj=callback_query.message)
        await callback_query.answer("Avval filialni tanlang.", show_alert=True)
        return False

    await callback_query.answer("Sizga filial biriktirilmagan.", show_alert=True)
    return False


async def _ensure_admin_operating_scope_message(
    message: types.Message,
    state: FSMContext | None = None,
) -> bool:
    admin_tg_id = message.from_user.id
    if await db.get_admin_branch_ids(admin_tg_id):
        return True

    if state:
        await state.finish()

    if admin_tg_id in SUPERADMINS:
        await _render_superadmin_branch_selector(admin_tg_id, chat_id=message.chat.id)
    else:
        await message.reply("Sizga filial biriktirilmagan.")
    return False


async def _get_admin_branch_scope(admin_tg_id: int) -> list[int] | None:
    return await db.get_admin_branch_ids(admin_tg_id)


# ===== Admin aloqasi (xodimlar murojaat qiladigan lichka/username) =====
def _build_admin_contact_keyboard(has_contact: bool) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "✏️ O'zgartirish" if has_contact else "➕ Kiritish",
        callback_data="admin_contact:set",
        style="primary",
    ))
    if has_contact:
        kb.add(types.InlineKeyboardButton("🗑 O'chirish", callback_data="admin_contact:delete", style="danger"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))
    return kb


async def _admin_contact_text() -> str:
    contact = await db.get_admin_contact()
    if contact:
        return (
            "📞 <b>Admin aloqasi</b>\n\n"
            f"Joriy aloqa: {html.escape(contact)}\n\n"
            "Xodimlar «🆘 Yordam» bo'limida shu manzilni ko'radi."
        )
    return (
        "📞 <b>Admin aloqasi</b>\n\n"
        "Hozircha aloqa kiritilmagan.\n\n"
        "«➕ Kiritish» orqali username (masalan, @admin) yoki t.me havola qo'shing — "
        "xodimlar yordam so'raganda shu ko'rsatiladi."
    )


@dp.callback_query_handler(lambda c: c.data == "admin_contact:menu", state="*")
async def admin_contact_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    await state.finish()
    contact = await db.get_admin_contact()
    await safe_edit_text(
        callback_query.message,
        await _admin_contact_text(),
        reply_markup=_build_admin_contact_keyboard(bool(contact)),
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data == "admin_contact:set", state="*")
async def admin_contact_set_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    await safe_edit_text(
        callback_query.message,
        "Admin aloqasini yuboring:\n\n"
        "Masalan: <code>@username</code>, <code>https://t.me/username</code> yoki telefon raqami.\n\n"
        "Bekor qilish uchun /cancel yozing.",
        parse_mode="HTML",
    )
    await AdminContact.waiting_for_contact.set()
    await callback_query.answer()


@dp.message_handler(state=AdminContact.waiting_for_contact, content_types=types.ContentTypes.TEXT)
async def admin_contact_save(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    value = (message.text or "").strip()
    if value.lower().lstrip("/") == "cancel":
        await state.finish()
        contact = await db.get_admin_contact()
        return await message.answer(
            await _admin_contact_text(),
            reply_markup=_build_admin_contact_keyboard(bool(contact)),
            parse_mode="HTML",
        )
    if len(value) < 3:
        return await message.reply("Juda qisqa. To'g'ri username (@...) yoki havola yuboring.")
    await db.set_admin_contact(value)
    await state.finish()
    await message.answer(
        f"✅ Admin aloqasi saqlandi:\n{html.escape(value)}",
        reply_markup=_build_admin_contact_keyboard(True),
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data == "admin_contact:delete", state="*")
async def admin_contact_delete(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    await state.finish()
    await db.delete_admin_contact()
    await safe_edit_text(
        callback_query.message,
        await _admin_contact_text(),
        reply_markup=_build_admin_contact_keyboard(False),
        parse_mode="HTML",
    )
    await callback_query.answer("O'chirildi.")


# ===== Filial joylashuvi (lat/lon) — botdan o'zgartirish (faqat katta admin) =====
#
# Koordinata DB'da saqlanadi va restart'da yo'qolmaydi (init_db endi mavjud
# qiymatni saqlaydi). Lokatsiya yuborish yoki "lat, lon" yozish mumkin.

@dp.message_handler(commands=["filial_joylashuv", "branch_location"], state="*")
async def branch_location_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPERADMINS:
        return
    await state.finish()
    branches = await db.get_active_branches()
    if not branches:
        return await message.reply("Faol filial yo'q.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for b in branches:
        kb.add(types.InlineKeyboardButton(
            f"🏢 {b['name']}  ({float(b['latitude']):.5f}, {float(b['longitude']):.5f})",
            callback_data=f"setbranchloc:{b['id']}",
            style="primary",
        ))
    await message.reply(
        "📍 <b>Filial joylashuvini o'zgartirish</b>\n\n"
        "Qaysi filialning koordinatasini yangilaymiz?",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data.startswith("setbranchloc:"), state="*")
async def branch_location_pick(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in SUPERADMINS:
        return await callback_query.answer("Faqat katta admin uchun.", show_alert=True)
    try:
        branch_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)
    branch = await db.get_branch_by_id(branch_id)
    if not branch:
        return await callback_query.answer("Filial topilmadi.", show_alert=True)
    await state.update_data(loc_branch_id=branch_id, loc_branch_name=branch["name"])
    await SetBranchLocation.waiting_for_location.set()
    await callback_query.message.edit_text(
        f"🏢 <b>{html.escape(str(branch['name']))}</b> uchun yangi joylashuvni yuboring:\n\n"
        f"Joriy: <code>{float(branch['latitude']):.6f}, {float(branch['longitude']):.6f}</code>\n\n"
        "📍 <b>Telegram lokatsiyasini</b> yuboring (📎 → Location)\n"
        "yoki koordinatani yozing: <code>40.754362, 72.357826</code>\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.message_handler(commands=["cancel"], state=SetBranchLocation.waiting_for_location)
async def branch_location_cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply("✅ Bekor qilindi.")


@dp.message_handler(state=SetBranchLocation.waiting_for_location, content_types=types.ContentTypes.LOCATION)
async def branch_location_from_geo(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPERADMINS:
        await state.finish()
        return
    loc = message.location
    await _save_branch_location(message, state, loc.latitude, loc.longitude)


@dp.message_handler(state=SetBranchLocation.waiting_for_location, content_types=types.ContentTypes.TEXT)
async def branch_location_from_text(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPERADMINS:
        await state.finish()
        return
    txt = (message.text or "").strip()
    if txt.lower().lstrip("/") == "cancel":
        await state.finish()
        return await message.reply("✅ Bekor qilindi.")
    # "lat, lon" / "lat lon" / "lat;lon" — barchasini qabul qilamiz
    parts = [p for p in txt.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except (ValueError, IndexError):
        return await message.reply(
            "❌ Koordinatani o'qib bo'lmadi.\n"
            "Masalan: <code>40.754362, 72.357826</code>\n"
            "yoki Telegram lokatsiyasini yuboring.",
            parse_mode="HTML",
        )
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return await message.reply("❌ Koordinata diapazondan tashqarida (lat: -90..90, lon: -180..180).")
    await _save_branch_location(message, state, lat, lon)


async def _save_branch_location(message: types.Message, state: FSMContext, lat: float, lon: float):
    data = await state.get_data()
    branch_id = data.get("loc_branch_id")
    name = data.get("loc_branch_name", "")
    await state.finish()
    if not branch_id:
        return await message.reply("Filial topilmadi. /filial_joylashuv dan qaytadan boshlang.")
    try:
        ok = await db.set_branch_location(branch_id, lat, lon)
    except Exception as exc:
        logging.exception("set_branch_location xatosi: %s", exc)
        return await message.reply(
            f"❌ Saqlashda xatolik: <code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:150]}</code>",
            parse_mode="HTML",
        )
    if not ok:
        return await message.reply("❌ Saqlanmadi (filial topilmadi).")
    await message.reply(
        f"✅ <b>{html.escape(str(name))}</b> joylashuvi yangilandi:\n"
        f"📍 <code>{lat:.6f}, {lon:.6f}</code>\n\n"
        "Endi xodimlar shu nuqtaga yaqin (radius ichida) joydan kelish/ketish "
        "belgilashlari mumkin. Bu o'zgarish restart'da ham saqlanadi.",
        parse_mode="HTML",
    )


# ===== Bildirishnoma guruhi CRUD (filial-scoped) =====
#
# Kelish/ketish/dam/sabab log'lari shu Telegram guruhga yuboriladi.
# Scope: superadmin -> joriy tanlangan filial; oddiy admin -> o'z filiali.
# Group ID odatda manfiy (-100...) — supergruh/kanal. Botni guruhga avval
# admin sifatida qo'shish kerak.

async def _resolve_admin_scope_branch_id(actor_tg_id: int) -> "int | None":
    branch_ids = await db.get_admin_branch_ids(actor_tg_id)
    return int(branch_ids[0]) if branch_ids else None


def _build_workgroup_keyboard(has_group: bool) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "✏️ O'zgartirish" if has_group else "➕ Kiritish",
        callback_data="workgroup:set",
        style="primary",
    ))
    if has_group:
        kb.add(types.InlineKeyboardButton(
            "🧪 Sinov xabar yuborish",
            callback_data="workgroup:test",
            style="primary",
        ))
        kb.add(types.InlineKeyboardButton(
            "🗑 O'chirish",
            callback_data="workgroup:delete",
            style="danger",
        ))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))
    return kb


async def _workgroup_text(branch_id: "int | None") -> str:
    if not branch_id:
        return (
            "📢 <b>Bildirishnoma guruhi</b>\n\n"
            "⚠️ Filial topilmadi. Avval filialni tanlang yoki sizga filial biriktirilishini kuting."
        )
    branch = await db.get_branch_by_id(branch_id)
    branch_name = (branch or {}).get("name") or f"#{branch_id}"
    gid = await db.get_branch_work_log_group_id(branch_id)
    head = (
        f"📢 <b>Bildirishnoma guruhi</b>\n"
        f"🏢 Filial: <b>{html.escape(str(branch_name))}</b>\n\n"
    )
    if gid:
        return head + (
            f"Joriy guruh ID: <code>{gid}</code>\n\n"
            "Kelish/ketish/dam/sabab log'lari shu guruhga yuboriladi."
        )
    return head + (
        "Hozircha guruh biriktirilmagan.\n\n"
        "«➕ Kiritish» orqali guruh ID'ni qo'shing.\n"
        "💡 Telegram guruh ID odatda manfiy son (masalan, <code>-1001234567890</code>).\n"
        "Botni o'sha guruhga avval admin sifatida qo'shing."
    )


@dp.callback_query_handler(lambda c: c.data == "workgroup:menu", state="*")
async def workgroup_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    await state.finish()
    branch_id = await _resolve_admin_scope_branch_id(callback_query.from_user.id)
    gid = await db.get_branch_work_log_group_id(branch_id) if branch_id else None
    await safe_edit_text(
        callback_query.message,
        await _workgroup_text(branch_id),
        reply_markup=_build_workgroup_keyboard(bool(gid)),
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data == "workgroup:set", state="*")
async def workgroup_set_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    branch_id = await _resolve_admin_scope_branch_id(callback_query.from_user.id)
    if not branch_id:
        return await callback_query.answer("Filial topilmadi. Avval tanlang.", show_alert=True)
    await state.update_data(workgroup_branch_id=branch_id)
    await safe_edit_text(
        callback_query.message,
        "Guruh <b>chat ID</b>'sini yuboring.\n\n"
        "Masalan: <code>-1001234567890</code>\n\n"
        "ID'ni qanday topish kerak:\n"
        "1) Botni o'sha guruhga admin sifatida qo'shing\n"
        "2) Guruhga istalgan xabar yozing\n"
        "3) <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code> ochib, "
        "   <code>chat.id</code> qiymatini oling\n\n"
        "Bekor qilish uchun /cancel yozing.",
        parse_mode="HTML",
    )
    await AdminWorkGroup.waiting_for_id.set()
    await callback_query.answer()


@dp.message_handler(state=AdminWorkGroup.waiting_for_id, content_types=types.ContentTypes.TEXT)
async def workgroup_set_save(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    value = (message.text or "").strip()
    if value.lower().lstrip("/") == "cancel":
        await state.finish()
        branch_id = await _resolve_admin_scope_branch_id(message.from_user.id)
        gid = await db.get_branch_work_log_group_id(branch_id) if branch_id else None
        return await message.answer(
            await _workgroup_text(branch_id),
            reply_markup=_build_workgroup_keyboard(bool(gid)),
            parse_mode="HTML",
        )
    # ID raqam bo'lishi shart (manfiy ham bo'lishi mumkin)
    try:
        group_id = int(value)
    except ValueError:
        return await message.reply(
            "Guruh ID raqam bo'lishi kerak (masalan, <code>-1001234567890</code>). "
            "Qayta yuboring yoki /cancel.",
            parse_mode="HTML",
        )

    data = await state.get_data()
    branch_id = data.get("workgroup_branch_id") or await _resolve_admin_scope_branch_id(message.from_user.id)
    if not branch_id:
        await state.finish()
        return await message.reply("Filial topilmadi. /start bosing va qaytadan urinib ko'ring.")

    saved = await db.set_branch_work_log_group_id(branch_id, group_id)
    await state.finish()
    if not saved:
        return await message.reply("Saqlash muvaffaqiyatsiz tugadi. Filial topilmadi.")

    # Saqlangach guruhga sinov xabar yuborib, bot o'sha yerda admin ekanini tasdiqlaymiz
    try:
        await bot.send_message(
            group_id,
            "✅ Bu guruh endi <b>Parfumerose bot</b> bildirishnomalarini oladi.\n"
            "Kelish/ketish/dam/sabab xabarlari shu yerda chiqadi.",
            parse_mode="HTML",
        )
        verify_note = "✅ Botning guruhga ulanishi tasdiqlandi (sinov xabar yuborildi)."
    except Exception as exc:
        verify_note = (
            "⚠️ Saqlandi, lekin botning guruhga sinov xabari yuborilmadi.\n"
            f"<code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:120]}</code>\n"
            "Botni o'sha guruhga admin sifatida qo'shganingizni tekshiring."
        )

    await message.answer(
        await _workgroup_text(branch_id) + "\n\n" + verify_note,
        reply_markup=_build_workgroup_keyboard(True),
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data == "workgroup:test", state="*")
async def workgroup_test(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    branch_id = await _resolve_admin_scope_branch_id(callback_query.from_user.id)
    gid = await db.get_branch_work_log_group_id(branch_id) if branch_id else None
    if not gid:
        return await callback_query.answer("Guruh biriktirilmagan.", show_alert=True)
    try:
        await bot.send_message(
            gid,
            "🧪 <b>Sinov xabar</b> — Parfumerose bot guruhga ulangan va xabar yuborishi mumkin.",
            parse_mode="HTML",
        )
        await callback_query.answer("✅ Yuborildi.", show_alert=True)
    except Exception as exc:
        await callback_query.answer(
            f"❌ Yuborib bo'lmadi: {type(exc).__name__}",
            show_alert=True,
        )


@dp.callback_query_handler(lambda c: c.data == "workgroup:delete", state="*")
async def workgroup_delete(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    await state.finish()
    branch_id = await _resolve_admin_scope_branch_id(callback_query.from_user.id)
    if not branch_id:
        return await callback_query.answer("Filial topilmadi.", show_alert=True)
    await db.clear_branch_work_log_group_id(branch_id)
    await safe_edit_text(
        callback_query.message,
        await _workgroup_text(branch_id),
        reply_markup=_build_workgroup_keyboard(False),
        parse_mode="HTML",
    )
    await callback_query.answer("O'chirildi.")


@dp.callback_query_handler(lambda c: c.data == "superbranch:menu", state="*")
async def superadmin_branch_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return
    await state.finish()
    await _render_superadmin_branch_selector(
        callback_query.from_user.id,
        message_obj=callback_query.message,
        back_callback="back_admin_main",
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("superbranch:select:"), state="*")
async def superadmin_branch_select(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return

    try:
        branch_id = int(callback_query.data.split(":")[2])
    except (IndexError, ValueError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    saved = await db.set_superadmin_selected_branch(callback_query.from_user.id, branch_id)
    if not saved:
        return await callback_query.answer("Filialni saqlab bo'lmadi.", show_alert=True)

    await state.finish()
    await _render_admin_home(callback_query.from_user.id, message_obj=callback_query.message)
    await callback_query.answer("Filial tanlandi.")


async def _get_accessible_payment_record(admin_tg_id: int, payment_id: int):
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                sp.id,
                sp.worker_id,
                sp.payment_date,
                sp.payment_time,
                sp.amount,
                w.full_name,
                w.tg_id,
                w.monthly_salary,
                w.branch_id,
                b.name AS branch_name
            FROM salary_payments sp
            JOIN workers w ON w.id = sp.worker_id
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE sp.id = $1
            """,
            payment_id,
        )
    if not row:
        return None
    payment = dict(row)
    if not await db.admin_can_access_worker(admin_tg_id, int(payment["worker_id"])):
        return None
    return payment


async def _edit_admin_message_or_send(
    chat_id: int,
    message_id: int | None,
    text: str,
    **kwargs,
):
    if message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                **kwargs,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass
    await bot.send_message(chat_id, text, **kwargs)


async def _cleanup_admin_input_message(message: types.Message):
    try:
        await message.delete()
    except Exception:
        pass


def _build_skip_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("O'tkazib yuborish")


def _calculate_hours_from_schedule(start_time: str | None, end_time: str | None) -> float:
    if not start_time or not end_time:
        return 0.0

    start_obj = datetime.datetime.strptime(start_time, "%H:%M").time()
    end_obj = datetime.datetime.strptime(end_time, "%H:%M").time()
    today = datetime.date.today()
    start_dt = datetime.datetime.combine(today, start_obj)
    end_dt = datetime.datetime.combine(today, end_obj)
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)
    return round((end_dt - start_dt).total_seconds() / 3600.0, 2)


def _format_schedule_window(start_time: str | None, end_time: str | None) -> str:
    if start_time and end_time:
        return f"{start_time} - {end_time}"
    if start_time:
        return f"{start_time} - Belgilanmagan"
    if end_time:
        return f"Belgilanmagan - {end_time}"
    return "Belgilanmagan"


# Bu o'zgaruvchilar o'zgarmaydi
OY_NOMI = {
    '01': "Yanvar", '02': "Fevral", '03': "Mart", '04': "Aprel",
    '05': "May", '06': "Iyun", '07': "Iyul", '08': "Avgust",
    '09': "Sentyabr", '10': "Oktyabr", '11': "Noyabr", '12': "Dekabr"
}


# === Qo‘shimcha menyu (“⚙️ Qo‘shimcha imkoniyatlar”) ==========================
# Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
@dp.callback_query_handler(lambda c: c.data == "admin_extra")
async def admin_extra_menu(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    await callback_query.message.edit_text(
        "Superadmin boshqaruvi:", reply_markup=get_admin_extra_menu()
    )
    await callback_query.answer()


# --- Dam olish kunlari menyusi -------------------------------------------------
@dp.callback_query_handler(lambda c: c.data == "rest_day_menu")
async def rest_day_menu(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    await callback_query.message.edit_text(
        "Dam olish kunini tanlang:", reply_markup=get_weekday_select_menu()
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("rest_select_"))
async def rest_day_select(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return

    tag = callback_query.data.split("_")[2]
    if tag == "none":
        # --- TUZATISH: db.set_rest_day ishlatiladi ---
        await db.set_rest_day(None)
        txt = "✅ Dam olish kuni bekor qilindi."
    else:
        day_int = int(tag)
        # --- TUZATISH: db.set_rest_day va db.WEEKDAYS_UZ ishlatiladi ---
        await db.set_rest_day(day_int)
        txt = f"✅ {db.WEEKDAYS_UZ[day_int]} dam olish kuni qilib belgilandi."

    await callback_query.message.edit_text(txt, reply_markup=get_admin_extra_menu())
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data == "back_admin_main", state="*")
async def back_admin_main(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    await callback_query.answer()
    await state.finish()
    await _render_admin_home(callback_query.from_user.id, message_obj=callback_query.message)


@dp.callback_query_handler(lambda c: c.data == "workers:back", state="*")
async def back_admin_main_from_workers(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    await callback_query.answer()
    await state.finish()
    await _render_admin_home(callback_query.from_user.id, message_obj=callback_query.message)


def format_hours(total_hours_float: float) -> str:
    total_minutes = int(round(float(total_hours_float or 0.0) * 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours} soat {minutes} daqiqa"


# Bu blokda baza bilan ishlanmaydi, o'zgarmaydi
@dp.callback_query_handler(lambda c: c.data.startswith("pending_")
                                     and not c.data.startswith("pending_accept_")
                                     and not c.data.startswith("pending_reject_"))
async def pending_menu(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    user_id = callback_query.data.split("_")[1]
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Qabul qilish", callback_data=f"pending_accept_{user_id}", style="success"),
        types.InlineKeyboardButton("Rad etish", callback_data=f"pending_reject_{user_id}", style="danger")
    )
    await notify_selected_admins(SUPERADMINS, f"Pending ariza (ID: {user_id}) uchun amallar:", reply_markup=keyboard)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("pending_accept_"))
async def pending_accept(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return
    user_id = int(callback_query.data.split("_")[2])
    action_key = f"join_request:{user_id}"
    actor_name = format_admin_actor(callback_query.from_user.id, callback_query.from_user.full_name)
    lock = get_admin_action_lock(action_key)
    async with lock:
        existing_result = get_admin_action_result(action_key)
        if existing_result:
            await callback_query.answer(describe_admin_action_result(existing_result), show_alert=True)
            return

        app = await db.get_application_by_tg_id(user_id)
        if app and app.get("status") != "pending":
            await callback_query.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
            return
        if not app and user_id not in pending_requests:
            await callback_query.answer("Ariza topilmadi yoki allaqachon yopilgan.", show_alert=True)
            return

        pending_info = pending_requests.get(user_id, {})
        suggested_name = pending_info.get("full_name") or (app.get("full_name") if app else "")
        await state.update_data(
            pending_user_id=user_id,
            pending_action_actor_id=callback_query.from_user.id,
            pending_action_actor_name=actor_name,
        )
        await AdminAcceptPending.waiting_for_new_name.set()
        prompt_lines = [
            f"Qabul qilinayotgan xodim ID: {user_id}",
            "Iltimos, xodimning tasdiqlanadigan ismini yuboring.",
        ]
        if suggested_name:
            prompt_lines.append(f"Hozirgi arizadagi ism: {suggested_name}")
            prompt_lines.append("Agar shu to'g'ri bo'lsa, o'shani qayta yuboring yoki tahrirlangan variantini yozing.")
        await callback_query.message.reply("\n".join(prompt_lines))
        # DEKUPLING: avval bu yerda resolve_admin_action chaqirilardi — u arizani
        # DARHOL "bajarilgan" deb belgilab BARCHA adminlar nusxasidagi tugmalarni
        # o'chirardi. Natijada admin jarayonni tashlab ketsa (ism kiritmasa), ariza
        # butunlay qulflanib qolardi — hech kim qabul qila olmasdi.
        # Endi START'da faqat SHU admin o'z xabaridagi tugmani yashiradi; boshqa
        # adminlar tugmasi qoladi. To'liq tozalash QABUL TUGAGANDA bo'ladi
        # (pending_accept_branch -> resolve_admin_action).
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback_query.answer("Ism kiritish oynasi ochildi.")


@dp.callback_query_handler(lambda c: c.data.startswith("pending_reject_"))
async def pending_reject(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    
    # ID endi Telegram ID emas, balki Application ID bo'lishi mumkinligi uchun
    # lekin hozircha biz callback_data da user_id ni (tg_id) yuboryapmiz.
    # Keling, tg_id orqali applicationni topib reject qilamiz.
    
    target_tg_id = int(callback_query.data.split("_")[2])
    action_key = f"join_request:{target_tg_id}"
    actor_name = format_admin_actor(callback_query.from_user.id, callback_query.from_user.full_name)
    lock = get_admin_action_lock(action_key)

    async with lock:
        existing_result = get_admin_action_result(action_key)
        if existing_result:
            await callback_query.answer(describe_admin_action_result(existing_result), show_alert=True)
            return

        app = await db.get_application_by_tg_id(target_tg_id)
        if app and app.get("status") != "pending":
            await callback_query.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
            return
        if app:
            await db.update_application_status(app['id'], 'rejected')

        pending_requests.pop(target_tg_id, None)
        await resolve_admin_action(
            action_key,
            callback_query.from_user.id,
            callback_query.from_user.full_name,
            "arizani rad etdi",
        )

        try:
            await bot.send_message(target_tg_id, "Admin sizni xodimlar ro'yxatiga qabul qilmadi. Rahmat.")
        except Exception as e:
            logging.error(e)
        await notify_selected_admins(SUPERADMINS, f"🧾 {actor_name} arizani rad etdi: TG ID {target_tg_id}")

    await callback_query.answer("Pending ariza rad etildi.")


# --------------------  MAOSHLAR DARAХTI  --------------------------------------
async def _salary_year_items() -> list[tuple[str, str]]:
    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        records = await conn.fetch("""
                                   SELECT DISTINCT EXTRACT(YEAR FROM payment_date) ::INTEGER as y
                                   FROM salary_payments
                                   WHERE payment_date IS NOT NULL
                                   """)
    present = {rec['y'] for rec in records if rec['y']}
    cur = datetime.datetime.now(tashkent_tz).year
    recent = {cur - i for i in range(5)}
    years = sorted(present | recent, reverse=True)
    return [(str(y), f"salary_year_{y}") for y in years]


async def send_salary_years(msg_obj: types.Message, page: int = 0):
    items = await _salary_year_items()  # Bu o'zgarmaydi
    total_years = await db.get_salary_year_count()  # YANGI QATOR

    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=10,
        page_prefix="salary_year_page",
        back_cb="back_admin_main",
        total_items=total_years  # YANGI ARGUMENT
    )
    await safe_edit_text(msg_obj, "Qaysi yilni tanlaysiz?", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "salary_tree")
async def handle_salary_tree(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return
    await send_salary_years(callback_query.message, page=0)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("salary_year_page_"))
async def salary_year_page(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    page = int(callback_query.data.split("_")[3])
    await send_salary_years(callback_query.message, page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("salary_year_page:"))
async def salary_year_page_legacy(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    try:
        page = int(callback_query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Sahifa topilmadi.", show_alert=True)
    await send_salary_years(callback_query.message, page)
    await callback_query.answer()


# 2)  OYLAR  -------------------------------------------------------------------
@dp.callback_query_handler(lambda c: c.data.startswith("salary_year_"))
async def handle_salary_year(callback_query: types.CallbackQuery):
    # Bu handlerda baza bilan ishlanmaydi, o'zgarmaydi
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    year = callback_query.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=3)
    for i in range(1, 13):
        month = f"{i:02}"
        kb.insert(types.InlineKeyboardButton(
            text=OY_NOMI[month], callback_data=f"salary_month_{year}_{month}", style="primary"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="salary_tree", style="primary"))
    await callback_query.message.edit_text(
        f"{year} yil uchun oylardan birini tanlang:", reply_markup=kb
    )
    await callback_query.answer()


async def _salary_workers_items(
    admin_tg_id: int,
    year: str,
    month: str,
    page: int,
    per_page: int,
) -> tuple[list[tuple[str, str]], int]:
    """Joriy sahifa items + jami xodimlar soni. Slicing + pozitsion raqam."""
    rows = await db.list_active_workers_for_admin(admin_tg_id, order_by="id")
    total = len(rows)
    if not rows:
        return [], 0
    start = page * per_page
    page_rows = rows[start : start + per_page]
    items = [
        (_format_worker_option_label(row, position=start + i + 1),
         f"salary_worker_{row['id']}_{year}_{month}")
        for i, row in enumerate(page_rows)
    ]
    return items, total


async def send_salary_workers(msg_obj: types.Message, admin_tg_id: int, year: str, month: str, page: int = 0):
    per_page = 10
    items, total_workers = await _salary_workers_items(admin_tg_id, year, month, page, per_page)

    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=per_page,
        page_prefix="salwp",
        back_cb=f"salary_year_{year}",
        total_items=total_workers,
    )
    caption = f"{year}-{month} oyi uchun barcha xodimlar:"
    await safe_edit_text(msg_obj, caption, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data.startswith("salary_month_"))
async def handle_salary_month(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    _, _, year, month = callback_query.data.split("_")
    await send_salary_workers(callback_query.message, callback_query.from_user.id, year, month, page=0)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("salwp_"))
async def salary_workers_page(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    parts = callback_query.data.split("_")
    page = int(parts[1])
    text = callback_query.message.text or ""
    year_month = text.split()[0]
    try:
        year, month = year_month.split("-")
    except ValueError:
        return await send_salary_workers(callback_query.message, callback_query.from_user.id, "0000", "00", page)
    await send_salary_workers(callback_query.message, callback_query.from_user.id, year, month, page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("salwp:"))
async def salary_workers_page_legacy(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    try:
        page = int(callback_query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Sahifa topilmadi.", show_alert=True)
    text = callback_query.message.text or ""
    year_month = text.split()[0]
    try:
        year, month = year_month.split("-")
    except ValueError:
        return await send_salary_workers(callback_query.message, callback_query.from_user.id, "0000", "00", page)
    await send_salary_workers(callback_query.message, callback_query.from_user.id, year, month, page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("salary_worker_"))
async def handle_salary_worker(callback_query: types.CallbackQuery, state: FSMContext):
    # --- 1-QADAM: Xatoliklarni ushlash uchun umumiy try-except bloki ---
    try:
        if callback_query.from_user.id not in ADMINS:
            await callback_query.answer("Ruxsat yo'q", show_alert=True)
            return

        _, _, worker_id_str, year, month = callback_query.data.split("_")
        worker_id = int(worker_id_str)
        if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
            await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
            return
        year_month_str = f"{year}-{month}"

        async with db.pool.acquire() as conn:
            worker_record = await conn.fetchrow(
                "SELECT w.full_name, w.monthly_salary, w.pay_type, w.pay_amount, "
                "       b.name AS branch_name "
                "FROM workers w LEFT JOIN branches b ON b.id = w.branch_id "
                "WHERE w.id = $1",
                worker_id,
            )
            if not worker_record:
                await callback_query.message.edit_text("❌ Xodim topilmadi.")
                return

            full_name = html.escape(worker_record['full_name'] or "Noma'lum xodim")
            branch_name = html.escape(worker_record['branch_name'] or "")
            monthly_salary = float(worker_record['monthly_salary'] or 0.0)
            pay_type = worker_record['pay_type']
            pay_amount = float(worker_record['pay_amount'] or 0.0)

            # Maosh va avans alohida hisoblanadi
            paid_breakdown = await conn.fetch(
                """
                SELECT COALESCE(kind, 'salary') AS kind, SUM(amount) AS total
                FROM salary_payments
                WHERE worker_id = $1 AND to_char(payment_date, 'YYYY-MM') = $2
                GROUP BY COALESCE(kind, 'salary')
                """,
                worker_id, year_month_str,
            )

        salary_paid = 0.0
        advance_paid = 0.0
        for row in paid_breakdown:
            if row['kind'] == 'advance':
                advance_paid = float(row['total'] or 0)
            else:
                salary_paid = float(row['total'] or 0)
        total_paid = salary_paid + advance_paid
        remaining = (monthly_salary - total_paid) if monthly_salary > 0 else 0.0

        text = f"<b>{full_name}</b> uchun {year}-{month} statistikasi:\n\n"
        if branch_name:
            text += f"🏢 Filial: <b>{branch_name}</b>\n"
        text += (
            f"💼 To'lov turi: {format_pay_status(pay_type, pay_amount, monthly_salary)}\n\n"
            f"💳 Tayinlangan maosh: <b>{monthly_salary:,.0f}</b> so'm\n"
            f"💰 Maosh to'langan: <b>{salary_paid:,.0f}</b> so'm\n"
            f"💸 Avans to'langan: <b>{advance_paid:,.0f}</b> so'm\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ Jami to'langan: <b>{total_paid:,.0f}</b> so'm\n"
            f"❗️ Qolgan: <b>{remaining:,.0f}</b> so'm\n"
        )

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("📌 Maosh tayinlash", callback_data=f"set_salary_{worker_id}", style="primary"),
            types.InlineKeyboardButton("➕ To‘lov qo‘shish", callback_data=f"add_payment_{worker_id}_{year}_{month}", style="success")
        )
        keyboard.add(
            types.InlineKeyboardButton("📋 To‘lovlar tafsiloti",
                                       callback_data=f"payment_details_{worker_id}_{year}_{month}", style="primary")
        )
        keyboard.add(
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"salary_month_{year}_{month}", style="primary")
        )

        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.update_data(selected_worker=worker_id, selected_year=year, selected_month=month)

    except Exception as e:
        # --- 3-QADAM: Har qanday xatolik yuz bersa, adminga xabar berish ---
        logging.error(f"handle_salary_worker xatoligi: {e}")
        await callback_query.answer("❗️Xatolik yuz berdi. Konsolni tekshiring.", show_alert=True)
        # Qo'shimcha ravishda adminga xabarni tahrirlab, xatolikni ko'rsatishimiz mumkin
        try:
            await callback_query.message.edit_text(
                f"❌ Xodim (ID: {worker_id_str}) ma'lumotlarini ochishda xatolik yuz berdi. Iltimos, uning maosh yoki to'lov ma'lumotlarini tekshiring.")
        except Exception:
            pass
    finally:
        # Har qanday holatda "so'rov qabul qilindi" degan javobni yuboramiz
        await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("set_salary_"))
async def handle_set_salary(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("Ruxsat yo'q", show_alert=True)
        return
    worker_id = int(callback_query.data.split("_")[2])
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        worker = await conn.fetchrow(
            "SELECT w.full_name, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )

    if not worker:
        await callback_query.message.edit_text("Xodim topilmadi.")
        return

    await state.update_data(set_salary_worker=worker_id)
    await AdminSetSalary.waiting_for_salary_amount.set()
    worker_label = worker["full_name"] + (f" [{worker['branch_name']}]" if worker["branch_name"] else "")
    await callback_query.message.edit_text(
        f"<b>{worker_label}</b> uchun yangi oylik maosh miqdorini kiriting:",
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data.startswith("add_payment_"))
async def handle_add_payment(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("Ruxsat yo'q", show_alert=True)
        return
    _, _, worker_id, year, month = callback_query.data.split("_")
    worker_id = int(worker_id)
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        worker = await conn.fetchrow(
            "SELECT w.full_name, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )

    if not worker:
        await callback_query.message.edit_text("Xodim topilmadi.")
        return

    await state.update_data(payment_worker_id=worker_id, payment_year=year, payment_month=month)
    worker_label = worker["full_name"] + (f" [{worker['branch_name']}]" if worker["branch_name"] else "")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton(
            "💰 Maosh",
            callback_data=f"pay_kind:salary:{worker_id}_{year}_{month}",
            style="success",
        ),
        types.InlineKeyboardButton(
            "💸 Avans",
            callback_data=f"pay_kind:advance:{worker_id}_{year}_{month}",
            style="primary",
        ),
    )
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"salary_worker_{worker_id}_{year}_{month}", style="primary"))
    await callback_query.message.edit_text(
        f"<b>{html.escape(worker_label)}</b> uchun {year}-{month} oyiga "
        f"to'lov turini tanlang:\n\n"
        f"💰 <b>Maosh</b> — odatdagi oylik to'lov\n"
        f"💸 <b>Avans</b> — oylik tugamasdan oldingi to'lov",
        parse_mode="HTML",
        reply_markup=kb,
    )


@dp.callback_query_handler(lambda c: c.data.startswith("pay_kind:"))
async def handle_payment_kind(callback_query: types.CallbackQuery, state: FSMContext):
    """Avans yoki maosh tanlangach miqdor so'raymiz."""
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    try:
        _, kind, rest = callback_query.data.split(":", 2)
        worker_id_str, year, month = rest.split("_")
        worker_id = int(worker_id_str)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri ma'lumot.", show_alert=True)
    if kind not in ("salary", "advance"):
        return await callback_query.answer("Noto'g'ri to'lov turi.", show_alert=True)
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)

    await state.update_data(
        payment_worker_id=worker_id,
        payment_year=year,
        payment_month=month,
        payment_kind=kind,
    )
    await AdminAddSalaryPayment.waiting_for_payment_amount.set()
    kind_label = format_payment_kind(kind)
    await callback_query.message.edit_text(
        f"{kind_label} miqdorini kiriting (masalan: 500000):",
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("payment_details_"))
async def handle_payment_details(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("Ruxsat yo'q", show_alert=True)
        return
    _, _, worker_id, year, month = callback_query.data.split("_")
    worker_id = int(worker_id)
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    year_month_str = f"{year}-{month}"

    # --- TUZATISH: pool o'rniga db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, payment_date, amount, kind
            FROM salary_payments
            WHERE worker_id = $1
              AND to_char(payment_date, 'YYYY-MM') = $2
            ORDER BY payment_date
            """,
            worker_id, year_month_str,
        )

    if not rows:
        await callback_query.message.edit_text(f"{year}-{month} oyi uchun to'lovlar topilmadi.")
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for i, row in enumerate(rows, start=1):
        date_str = row['payment_date'].strftime("%d.%m.%Y")
        amount = float(row['amount'] or 0.0)
        kind_emoji = format_payment_kind(row['kind'], short=True)
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"{i}) {kind_emoji} {date_str} — {amount:,.0f} so'm",
                callback_data=f"payment_detail_{row['id']}", style="primary")
        )
    keyboard.add(
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"salary_worker_{worker_id}_{year}_{month}", style="primary")
    )
    await callback_query.message.edit_text(
        f"{year}-{month} oyi uchun to‘lovlar ro‘yxati:",
        reply_markup=keyboard
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("payment_detail_"))
async def handle_payment_detail(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    payment_id = int(callback_query.data.split("_")[2])
    row = await _get_accessible_payment_record(callback_query.from_user.id, payment_id)

    if not row:
        await callback_query.message.edit_text("To‘lov topilmadi yoki sizga tegishli emas.")
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    date_str = row['payment_date'].strftime("%d.%m.%Y")
    amount = float(row['amount'] or 0.0)

    text = (
        f"📆 <b>{date_str}</b> kuni uchun to‘lov: <b>{amount:,.0f} so‘m</b>\n"
        f"👤 {_format_worker_branch_label(row)}"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"edit_payment_{payment_id}", style="primary"),
        types.InlineKeyboardButton("🗑 O‘chirish", callback_data=f"delete_payment_{payment_id}", style="danger")
    )
    keyboard.add(
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="salary_tree", style="primary")
    )
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@dp.callback_query_handler(lambda c: c.data.startswith("edit_payment_"))
async def handle_edit_payment(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    payment_id = int(callback_query.data.split("_")[2])
    payment = await _get_accessible_payment_record(callback_query.from_user.id, payment_id)

    if not payment:
        await callback_query.message.edit_text("To‘lov topilmadi.")
        return

    await state.update_data(edit_payment_id=payment_id)
    await AdminModifyPayment.waiting_for_new_payment_amount.set()
    await callback_query.message.edit_text(
        f"{_format_worker_branch_label(payment)}\n"
        f"Hozirgi to‘lov: {float(payment['amount']):,.0f} so‘m\n\nYangi to‘lov miqdorini kiriting:"
    )


@dp.callback_query_handler(lambda c: c.data.startswith("delete_payment_"))
async def handle_delete_payment(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    payment_id = int(callback_query.data.split("_")[2])
    payment = await _get_accessible_payment_record(callback_query.from_user.id, payment_id)
    if not payment:
        await callback_query.message.edit_text("To‘lov topilmadi.")
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    async with db.pool.acquire() as conn:
        await conn.execute("DELETE FROM salary_payments WHERE id = $1", payment_id)

    amount, payment_date = float(payment['amount']), payment['payment_date']
    date_str = payment_date.strftime("%d.%m.%Y")

    await callback_query.message.edit_text(
        f"🗑 {date_str} sanasidagi <b>{amount:,.0f} so‘m</b> to‘lov o‘chirildi.\n"
        f"👤 {_format_worker_branch_label(payment)}",
        parse_mode="HTML"
    )
    await callback_query.answer("O'chirildi!")


@dp.message_handler(state=AdminSetSalary.waiting_for_salary_amount, content_types=types.ContentTypes.TEXT)
async def process_salary_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.reply("Iltimos, raqam kiriting (masalan: 2500000)")
        return

    data = await state.get_data()
    worker_id = data.get("set_salary_worker")
    if not worker_id or not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        worker = await conn.fetchrow(
            "SELECT w.full_name, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )
        await conn.execute(
            "UPDATE workers SET monthly_salary = $1, pay_amount = $1, pay_type = 'monthly' WHERE id = $2",
            amount,
            worker_id,
        )

    await state.finish()
    label = _format_worker_branch_label(dict(worker)) if worker else f"Xodim ID: {worker_id}"
    await message.reply(f"✅ {label} uchun yangi oylik maosh {amount:,.0f} so'm qilib belgilandi.")


@dp.message_handler(state=AdminAddSalaryPayment.waiting_for_payment_amount, content_types=types.ContentTypes.TEXT)
async def process_payment_amount_combined(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    try:
        amount = float(''.join(filter(str.isdigit, txt)))
    except (ValueError, TypeError):
        return await message.reply("Iltimos, faqat raqam kiriting (masalan: 1500000).")

    data = await state.get_data()
    worker_id = data.get("payment_worker_id")
    year = data.get("payment_year")
    month = data.get("payment_month")
    kind = data.get("payment_kind", "salary")  # eski oqim bilan ham mos kelsin
    if kind not in ("salary", "advance"):
        kind = "salary"
    if not worker_id or not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    oy = f"{year}-{month}"
    payment_date = datetime.date(int(year), int(month), 1)

    async with db.pool.acquire() as conn:
        worker_record = await conn.fetchrow(
            "SELECT w.full_name, w.monthly_salary, w.tg_id, b.name AS branch_name "
            "FROM workers w LEFT JOIN branches b ON b.id = w.branch_id "
            "WHERE w.id = $1",
            worker_id,
        )
        if not worker_record:
            return await message.reply("Xodim topilmadi.")

        full_name = worker_record["full_name"]
        monthly_salary = worker_record["monthly_salary"]
        worker_tg = worker_record["tg_id"]
        branch_name = worker_record["branch_name"]
        monthly_salary = monthly_salary or 0.0

        await conn.execute(
            "INSERT INTO salary_payments (worker_id, payment_date, amount, kind) VALUES ($1, $2, $3, $4)",
            worker_id, payment_date, amount, kind,
        )

        sum_payment_record = await conn.fetchval(
            "SELECT SUM(amount) FROM salary_payments WHERE worker_id = $1 AND to_char(payment_date, 'YYYY-MM') = $2",
            worker_id, oy,
        )
        sum_payment = sum_payment_record or 0.0

    rem_text = ""
    if monthly_salary > 0:
        remaining = monthly_salary - sum_payment
        rem_text = f"\nQolgan: {float(remaining):,.0f} so'm"

    payment_time_str = datetime.datetime.now(tashkent_tz).strftime('%H:%M:%S')
    worker_label = full_name + (f" [{branch_name}]" if branch_name else "")
    kind_label = format_payment_kind(kind)
    reply = (
        f"✅ {kind_label} qabul qilindi.\n"
        f"Xodim: {worker_label}\n"
        f"Sana: {payment_date.strftime('%Y-%m-%d')} {payment_time_str}\n"
        f"Miqdor: {amount:,.0f} so'm\n"
        f"O'sha oy jami: {float(sum_payment):,.0f} so'm{rem_text}"
    )
    await message.reply(reply)

    try:
        await bot.send_message(
            worker_tg,
            f"🟢 Sizga {payment_date.strftime('%Y-%m-%d')} kuni "
            f"{kind_label} sifatida {amount:,.0f} so'm to'lov tushdi.\n"
            f"Jami shu oyda: {float(sum_payment):,.0f} so'm." + rem_text,
        )
    except Exception as e:
        logging.warning(f"Xodimga xabar yuborishda xatolik: {e}")

    await state.finish()


@dp.message_handler(
    state=AdminAcceptPending.waiting_for_new_name,
    content_types=types.ContentTypes.TEXT,
)
async def process_admin_accept(message: types.Message, state: FSMContext):
    if not await _ensure_superadmin_message(message, state):
        return
    try:
        new_name = message.text.strip()
        data = await state.get_data()
        pending_user_id = data.get("pending_user_id")

        if not pending_user_id:
            await message.reply(
                "⚠️ Jarayon ma'lumotlari topilmadi (bot qayta ishga tushgan bo'lishi mumkin).\n"
                "Iltimos, arizani <b>«Qabul qilish»</b> tugmasidan qaytadan boshlang.",
                parse_mode="HTML",
            )
            await state.finish()
            return

        # Bazadan application topish
        app = await db.get_application_by_tg_id(pending_user_id)
        username_val = None
        # pending_requests xotirada bo'lsa uni olamiz
        pending_info = pending_requests.get(pending_user_id)

        if pending_info:
            username_val = pending_info.get("username")
        elif app:
            username_val = app.get("username")

        # Bu oqimga faqat katta admin (superadmin) kiradi va u istalgan faol filialga
        # xodim biriktira oladi. Shuning uchun joriy tanlangan filialdan qat'i nazar,
        # HAR DOIM "qaysi filialga biriktiramiz?" deb so'raymiz.
        branch_choices = await db.get_active_branches()
        if not branch_choices:
            await state.finish()
            await message.reply(
                "Hozircha faol filial yo'q. Avval filial qo'shing yoki faollashtiring, "
                "so'ngra xodimni qabul qiling."
            )
            return

        current_branch_id = await db.get_superadmin_selected_branch_id(message.from_user.id)
        await state.update_data(
            pending_final_name=new_name,
            pending_username=username_val,
        )
        await AdminAcceptPending.waiting_for_branch.set()
        await message.reply(
            "Bu xodim qaysi filialga biriktiriladi?\n"
            "(Joriy filial ✅ bilan belgilangan, lekin istalganini tanlashingiz mumkin.)",
            reply_markup=build_branch_selection_keyboard(
                branch_choices,
                "pending_accept_branch",
                current_branch_id=current_branch_id,
            ),
        )
    except Exception as exc:
        logging.exception("process_admin_accept xatosi: %s", exc)
        try:
            await state.finish()
        except Exception:
            pass
        try:
            await message.reply(
                "❌ Ism saqlashda xatolik yuz berdi:\n"
                f"<code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:200]}</code>\n\n"
                "Iltimos, arizani «Qabul qilish» tugmasidan qaytadan boshlang.",
                parse_mode="HTML",
            )
        except Exception:
            pass


@dp.callback_query_handler(lambda c: c.data.startswith("pending_accept_branch:"), state=AdminAcceptPending.waiting_for_branch)
async def pending_accept_branch(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return

    try:
        branch_id = int(callback_query.data.split(":")[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    # Katta admin yangi xodimni istalgan FAOL filialga biriktira oladi
    # (joriy tanlangan filial bilan cheklanmaydi). Shu sabab faollikni tekshiramiz.
    active_branch_ids = {int(b["id"]) for b in await db.get_active_branches()}
    if branch_id not in active_branch_ids:
        return await callback_query.answer("Bu filial faol emas yoki topilmadi.", show_alert=True)

    data = await state.get_data()
    pending_user_id = data.get("pending_user_id")
    final_name = data.get("pending_final_name")
    username_val = data.get("pending_username")
    if not pending_user_id or not final_name:
        await state.finish()
        return await callback_query.answer("Jarayon ma'lumotlari topilmadi.", show_alert=True)

    try:
        app = await db.get_application_by_tg_id(pending_user_id)
        await db.add_user(
            tg_id=pending_user_id,
            full_name=final_name,
            username=username_val,
            branch_id=branch_id,
        )
        if app:
            await db.update_application_status(app['id'], 'accepted')
        # Ariza endi HAQIQATAN qabul qilindi — barcha adminlar nusxasidagi
        # "Qabul/Rad" tugmalarini tozalaymiz (boshqa admin endi bosa olmaydi).
        await resolve_admin_action(
            f"join_request:{pending_user_id}",
            callback_query.from_user.id,
            callback_query.from_user.full_name,
            "qabul qildi",
        )
    except Exception as exc:
        logging.exception("pending_accept_branch — xodim qo'shishda xato: %s", exc)
        try:
            await state.finish()
        except Exception:
            pass
        try:
            await callback_query.message.answer(
                "❌ Xodimni saqlashda xatolik yuz berdi:\n"
                f"<code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:200]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return await callback_query.answer("Xatolik yuz berdi.", show_alert=True)

    await state.update_data(final_name=final_name)
    await AdminAcceptPending.waiting_for_start_time.set()
    await callback_query.message.edit_text(
        "Yangi xodimning ish boshlash vaqtini kiriting. (Masalan: 09:00)\n"
        "Agar hozir kiritmasangiz, 'O'tkazib yuborish' tugmasini bosing."
    )
    await callback_query.message.answer(
        "Ixtiyoriy: ish boshlash vaqtini kiriting yoki `O'tkazib yuborish`ni bosing.",
        reply_markup=_build_skip_reply_keyboard(),
        parse_mode="Markdown",
    )
    await callback_query.answer("Filial saqlandi.")


@dp.message_handler(state=AdminAcceptPending.waiting_for_start_time, content_types=types.ContentTypes.TEXT)
async def process_admin_start_time(message: types.Message, state: FSMContext):
    if not await _ensure_superadmin_message(message, state):
        return

    raw_text = message.text.strip()
    if raw_text.lower() == "o'tkazib yuborish":
        await state.update_data(pending_work_start=None)
    else:
        parsed = _parse_hhmm_input(raw_text)
        if not parsed:
            await message.reply("Vaqtni HH:MM formatda kiriting yoki 'O'tkazib yuborish'ni bosing.")
            return
        await state.update_data(pending_work_start=parsed)

    await AdminAcceptPending.waiting_for_end_time.set()
    await message.reply(
        "Yangi xodimning ish tugash vaqtini kiriting. (Masalan: 18:00)\n"
        "Agar hozir kiritmasangiz, 'O'tkazib yuborish' tugmasini bosing.",
        reply_markup=_build_skip_reply_keyboard(),
    )


@dp.message_handler(state=AdminAcceptPending.waiting_for_end_time, content_types=types.ContentTypes.TEXT)
async def process_admin_end_time(message: types.Message, state: FSMContext):
    if not await _ensure_superadmin_message(message, state):
        return

    raw_text = message.text.strip()
    if raw_text.lower() == "o'tkazib yuborish":
        end_time = None
    else:
        end_time = _parse_hhmm_input(raw_text)
        if not end_time:
            await message.reply("Vaqtni HH:MM formatda kiriting yoki 'O'tkazib yuborish'ni bosing.")
            return

    await state.update_data(pending_work_end=end_time)
    # Butun yakuniy blok try/finally bilan o'ralgan — UPDATE yoki reply xato
    # bersa ham, oqim "qotib qolmasin" (state.finish har doim bajariladi).
    try:
        data = await state.get_data()
        pending_user_id = data.get("pending_user_id", None)
        final_name = data.get("final_name", "")
        actor_name = data.get("pending_action_actor_name") or format_admin_actor(message.from_user.id, message.from_user.full_name)
        worker_record = await db.get_worker_by_tg_id(pending_user_id) if pending_user_id else None
        branch_name = worker_record.get("branch_name") if worker_record else None
        worker_id = worker_record.get("id") if worker_record else None
        start_time = data.get("pending_work_start")
        daily_hours = _calculate_hours_from_schedule(start_time, end_time)
        start_obj = datetime.datetime.strptime(start_time, "%H:%M").time() if start_time else None
        end_obj = datetime.datetime.strptime(end_time, "%H:%M").time() if end_time else None

        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE workers SET work_start = $1, work_end = $2, daily_work_hours = $3 WHERE tg_id = $4",
                start_obj,
                end_obj,
                daily_hours,
                pending_user_id,
            )

        pending_requests.pop(pending_user_id, None)

        branch_suffix = f" Filial: {branch_name}." if branch_name else ""
        schedule_text = _format_schedule_window(start_time, end_time)
        if not start_time and not end_time:
            reply_text = (
                f"Foydalanuvchi qabul qilindi, ismi: {final_name}.{branch_suffix}\n"
                "Ish vaqti hozircha belgilanmadi.\n"
                "Endi o‘sha xodim /start bosib botdan foydalanishi mumkin."
            )
            worker_text = f"Siz qabul qilindingiz! Sizning ismingiz: {final_name}\n"
            if branch_name:
                worker_text += f"Filialingiz: {branch_name}\n"
            worker_text += "Endi /start bosib botdan foydalanishingiz mumkin."
        else:
            reply_text = (
                f"Foydalanuvchi qabul qilindi, ismi: {final_name}.{branch_suffix}\n"
                f"Ish vaqti: {schedule_text}.\n"
                "Endi /start bosib botdan foydalanishingiz mumkin."
            )
            worker_text = f"Siz qabul qilindingiz! Sizning ismingiz: {final_name}\n"
            if branch_name:
                worker_text += f"Filialingiz: {branch_name}\n"
            worker_text += f"Ish vaqti: {schedule_text}.\n"
            worker_text += "Endi /start bosib botdan foydalanishingiz mumkin."

        # Oqim tugadi — "O'tkazib yuborish" reply klaviaturasini olib tashlaymiz
        await dismiss_reply_keyboard(message.chat.id)
        await message.reply(reply_text)
        try:
            await bot.send_message(pending_user_id, worker_text)
        except Exception as e:
            logging.error(f"Xodimga xabar yuborishda xatolik: {e}")

        await notify_admins_and_group(
            f"✅ {actor_name} arizani qabul qildi: {final_name}",
            worker_id=worker_id,
        )
    except Exception as exc:
        logging.exception("Ariza qabul oxirgi bosqichida xatolik: %s", exc)
        try:
            await message.reply(
                "❌ Ish vaqtini saqlashda xatolik yuz berdi.\n"
                f"<code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))[:200]}</code>\n\n"
                "Xodim yaratilgan bo'lsa-da, ish vaqti belgilanmagan bo'lishi mumkin. "
                "Admin menyusidan tahrirlash orqali to'g'rilang.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        try:
            await state.finish()
        except Exception:
            pass


@dp.callback_query_handler(
    lambda c: c.data.startswith("worker_")
    and not c.data.startswith("worker_actions_")
    and c.data.split("_", 1)[1].isdigit()
)
async def worker_menu(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("Ruxsat yo'q", show_alert=True)
        return
    worker_id = int(callback_query.data.split("_")[1])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return
    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return await callback_query.answer("Xodim topilmadi.", show_alert=True)

    specs = await get_worker_action_button_specs(worker_id)
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton("📋 Ko'rish", callback_data=f"view_{worker_id}", style="primary"),
        types.InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"update_{worker_id}", style="primary"),
    )
    if callback_query.from_user.id in SUPERADMINS:
        branch_button_text = "🏢 Filialni o'zgartirish"
        if not worker.get("branch_id"):
            branch_button_text = "🏢 Filialga biriktirish"
        keyboard.add(
            types.InlineKeyboardButton(
                branch_button_text,
                callback_data=f"moveworker:{worker_id}",
                style="primary",
            )
        )
    keyboard.add(
        types.InlineKeyboardButton("🗑 O'chirish", callback_data=f"delete_{worker_id}", style="danger")
    )
    keyboard.row(
        types.InlineKeyboardButton(
            specs["work_label"],
            callback_data=f"wact:{specs['work_action']}:{worker_id}",
            style=specs["work_style"],
        ),
        types.InlineKeyboardButton("🌙 Dam", callback_data=f"wact:rest:{worker_id}", style="danger"),
    )
    keyboard.add(
        types.InlineKeyboardButton(
            specs["study_label"],
            callback_data=f"wact:{specs['study_action']}:{worker_id}",
            style=specs["study_style"],
        )
    )
    keyboard.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_workers", style="primary"))

    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)} (ID: {worker_id})\nAmalni tanlang:",
        reply_markup=keyboard,
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("moveworker:"), state="*")
async def move_worker_branch_start(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        worker_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri xodim.", show_alert=True)

    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return await callback_query.answer("Xodim topilmadi.", show_alert=True)

    branches = await db.get_active_branches()
    if not branches:
        return await callback_query.answer("Faol filial topilmadi.", show_alert=True)

    current_branch_name = worker.get("branch_name") or "Belgilanmagan"
    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)}\n"
        f"Joriy filial: {current_branch_name}\n\n"
        "Qaysi filialga biriktirmoqchisiz?",
        reply_markup=_build_worker_branch_picker_keyboard(worker_id, branches, worker.get("branch_id")),
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("moveworkerto:"), state="*")
async def move_worker_branch_choose_mode(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        _, worker_id_raw, branch_id_raw = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
        branch_id = int(branch_id_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    worker = await db.get_worker_by_id(worker_id)
    branch = await db.get_branch_by_id(branch_id)
    if not worker or not branch:
        return await callback_query.answer("Xodim yoki filial topilmadi.", show_alert=True)
    if worker.get("branch_id") == branch_id:
        return await callback_query.answer("Bu xodim allaqachon shu filialga biriktirilgan.", show_alert=True)

    worker_tg_id = worker.get("tg_id")
    if worker_tg_id and worker_tg_id not in SUPERADMINS:
        assignments = await db.get_branch_admin_assignments(int(worker_tg_id))
        assigned_branch_ids = {item["id"] for item in assignments}
        if assigned_branch_ids and branch_id not in assigned_branch_ids:
            assigned_names = ", ".join(item["name"] for item in assignments)
            return await callback_query.answer(
                f"Bu xodimning TG ID si hozir filial admini sifatida biriktirilgan: {assigned_names}. "
                "Avval admin biriktirishini to'g'rilang.",
                show_alert=True,
            )

    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)}\n"
        f"Yangi filial: {branch['name']}\n\n"
        "Qanday qo'llaymiz?\n"
        "Faqat kelajakdagi ishlashlari uchunmi, yoki eski davomat yozuvlarini ham shu filialga o'tkazamizmi?",
        reply_markup=_build_worker_branch_apply_keyboard(worker_id, branch_id),
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("moveworkerapply:"), state="*")
async def move_worker_branch_apply(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        _, worker_id_raw, branch_id_raw, mode = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
        branch_id = int(branch_id_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri amal.", show_alert=True)

    if mode not in {"future", "history"}:
        return await callback_query.answer("Noto'g'ri ko'chirish turi.", show_alert=True)

    worker = await db.get_worker_by_id(worker_id)
    branch = await db.get_branch_by_id(branch_id)
    if not worker or not branch:
        return await callback_query.answer("Xodim yoki filial topilmadi.", show_alert=True)

    worker_tg_id = worker.get("tg_id")
    if worker_tg_id and worker_tg_id not in SUPERADMINS:
        assignments = await db.get_branch_admin_assignments(int(worker_tg_id))
        assigned_branch_ids = {item["id"] for item in assignments}
        if assigned_branch_ids and branch_id not in assigned_branch_ids:
            assigned_names = ", ".join(item["name"] for item in assignments)
            return await callback_query.answer(
                f"Bu TG ID hali ham boshqa filial adminiga bog'langan: {assigned_names}.",
                show_alert=True,
            )

    move_history = mode == "history"
    success = await db.reassign_worker_branch(worker_id, branch_id, move_history=move_history)
    if not success:
        return await callback_query.answer("Xodim filialini yangilab bo'lmadi.", show_alert=True)

    try:
        if worker_tg_id:
            history_note = (
                "Eski davomat yozuvlaringiz ham shu filialga o'tkazildi."
                if move_history else
                "Yangi davomatlaringiz endi shu filialga yoziladi."
            )
            await bot.send_message(
                worker_tg_id,
                f"Siz {branch['name']} filialiga biriktirildingiz.\n{history_note}",
            )
    except Exception as exc:
        logging.warning("Xodimga filial o'zgargani haqida xabar yuborilmadi: %s", exc)

    done_keyboard = InlineKeyboardMarkup(row_width=1)
    done_keyboard.add(
        InlineKeyboardButton("⬅️ Xodim kartasi", callback_data=f"worker_{worker_id}", style="primary"),
        InlineKeyboardButton("⬅️ Xodimlar ro'yxati", callback_data="admin_workers", style="primary"),
    )
    history_text = (
        "Barcha eski davomat va attendance yozuvlari ham shu filialga ko'chirildi."
        if move_history else
        "Faqat xodim kartasi yangilandi; yangi yozuvlar endi shu filialga tushadi."
    )
    await callback_query.message.edit_text(
        f"✅ {_format_worker_branch_label(worker)} endi <b>{branch['name']}</b> filialiga biriktirildi.\n"
        f"{history_text}",
        reply_markup=done_keyboard,
        parse_mode="HTML",
    )
    await callback_query.answer("Filial yangilandi.")


@dp.callback_query_handler(lambda c: c.data.startswith("view_"))
async def view_worker(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    worker_id = int(callback_query.data.split("_")[1])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    async with db.pool.acquire() as conn:
        worker = await conn.fetchrow(
            "SELECT w.*, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )

    if not worker:
        await callback_query.answer("Xodim topilmadi.", show_alert=True)
        return

    monthly_salary = float(worker['monthly_salary'] or 0.0)
    pay_type = worker['pay_type'] or 'monthly'
    pay_amount = float(worker['pay_amount'] or monthly_salary or 0.0)
    phone_status = "Telefoni bor" if worker['tg_id'] else "Telefoni yo'q"
    daily_hrs = float(worker['daily_work_hours'] or 0.0)
    # --- TUZATISH SHU YERDA: .strftime() olib tashlandi ---
    w_start = worker['work_start'] or 'Belgilanmagan'
    w_end = worker['work_end'] or 'Belgilanmagan'
    added_date = worker['added_date'].astimezone(tashkent_tz).strftime('%Y-%m-%d %H:%M')

    text = (
        f"👤 <b>{html.escape(str(worker['full_name']))}</b>\n\n"
        f"🏢 Filial: {html.escape(str(worker['branch_name'] or 'Belgilanmagan'))}\n"
        f"🆔 TG ID: {worker['tg_id'] or 'yoq'}\n"
        f"📛 Username: {('@' + worker['username']) if worker['username'] else 'Yoq'}\n\n"
        f"💼 {format_pay_status(pay_type, pay_amount, monthly_salary)}\n\n"
        f"📞 Aloqa holati: {phone_status}\n"
        f"🕒 Kunlik ish soati: {daily_hrs if daily_hrs > 0 else 'Belgilanmagan'} soat\n"
        f"⏰ Ish vaqti: {w_start} - {w_end}\n"
        f"📅 Qo'shilgan sana: {added_date}"
    )

    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_workers", style="primary")
    )
    await callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(
    lambda c: c.data.startswith("delete_")
              and not c.data.startswith(("delete_dw_", "delete_worktime_")))
async def delete_worker(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    try:
        worker_id = int(callback_query.data.split("_")[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Xatolik.", show_alert=True)
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    # --- TUZATISH: db.delete_worker ishlatiladi ---
    success = await db.delete_worker(worker_id)

    if success:
        await callback_query.message.edit_text(
            f"✅ Xodim (ID: {worker_id}) muvaffaqiyatli butunlay o'chirildi."
        )
        await callback_query.answer("Muvaffaqiyatli o'chirildi!")
    else:
        await callback_query.answer("Xatolik: Xodim topilmadi.", show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith("update_"))
async def update_worker_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    worker_id = int(callback_query.data.split("_")[1])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return
    await state.update_data(update_worker_id=worker_id)

    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return await callback_query.answer("Xodim topilmadi.", show_alert=True)

    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)}\n\nUchun yangi ismni kiriting:"
    )
    await AdminUpdateWorker.waiting_for_new_name.set()
    await callback_query.answer()


@dp.message_handler(state=AdminUpdateWorker.waiting_for_new_name, content_types=types.ContentTypes.TEXT)
async def process_worker_update(message: types.Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    worker_id = data.get("update_worker_id")
    if not worker_id:
        await message.reply("Xatolik yuz berdi.")
        await state.finish()
        return
    if not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        old_record = await conn.fetchrow("SELECT tg_id, full_name FROM workers WHERE id = $1", worker_id)
        if not old_record:
            await message.reply("Xodim topilmadi.")
            await state.finish()
            return
        await conn.execute("UPDATE workers SET full_name = $1 WHERE id = $2", new_name, worker_id)

    worker_tg_id, old_name = old_record['tg_id'], old_record['full_name']
    await message.reply(f"✅ Xodim (ID: {worker_id}) ismi yangilandi: {old_name} -> {new_name}")
    try:
        await bot.send_message(worker_tg_id, f"Hurmatli {new_name}, administrator sizning ismingizni o'zgartirdi.")
    except Exception as ex:
        logging.error(f"Xodimga ism o'zgargani haqida yuborishda xato: {ex}")
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "work_hours_menu")
async def admin_work_hours_menu(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return

    rows = await db.list_active_workers_for_admin(callback_query.from_user.id, order_by="id")

    if not rows:
        await callback_query.message.edit_text("Xodimlar topilmadi.")
        await callback_query.answer()
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for i, row in enumerate(rows, start=1):
        kb.insert(types.InlineKeyboardButton(
            _format_worker_option_label(row, position=i),
            callback_data=f"editwh_{row['id']}",
            style="primary",
        ))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))
    await callback_query.message.edit_text("Ish vaqtlarini sozlash uchun xodimni tanlang:", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("editwh_"))
async def show_worker_work_hours(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    wid = int(callback_query.data.split("_")[1])
    if not await _ensure_worker_access_callback(callback_query, wid):
        return

    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT w.id, w.full_name, w.daily_work_hours, w.work_start, w.work_end, b.name AS branch_name "
            "FROM workers w LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id=$1",
            wid
        )

    if not row:
        await callback_query.answer("Xodim topilmadi", show_alert=True)
        return

    dw_str = f"{float(row['daily_work_hours'])} soat" if row['daily_work_hours'] and float(
        row['daily_work_hours']) > 0 else 'Belgilanmagan'
    # --- TUZATISH SHU YERDA: .strftime() olib tashlandi ---
    w_start_str = row['work_start'] or '---'
    w_end_str = row['work_end'] or '---'

    desc = (
        f"👤 <b>{html.escape(str(row['full_name']))}</b>\n"
        f"🏢 Filial: <b>{html.escape(str(row['branch_name'] or 'Belgilanmagan'))}</b>\n\n"
        f"🕒 Kunlik ish soati: <b>{dw_str}</b>\n"
        f"⏰ Ish vaqti: <b>{w_start_str} - {w_end_str}</b>"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✏️ Ish vaqtini belgilash/o'zgartirish", callback_data=f"setworktime_{wid}", style="primary"))
    kb.add(types.InlineKeyboardButton("🕒 Kunlik ish soatini tahrirlash", callback_data=f"editdw_yes_{wid}", style="primary"))
    kb.add(types.InlineKeyboardButton("❌ Ish vaqtini bekor qilish", callback_data=f"delete_worktime_{wid}", style="danger"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="work_hours_menu", style="primary"))
    await callback_query.message.edit_text(desc, parse_mode="HTML", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("setworktime_"))
async def set_work_time_callback(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    worker_id = int(callback_query.data.split("_")[1])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return
    await state.update_data(work_time_worker_id=worker_id)
    await callback_query.message.edit_text(
        "Ish boshlanish vaqtini <b>HH:MM</b> formatda kiriting (masalan, <b>09:00</b>):",
        parse_mode="HTML"
    )
    await AdminSetWorkTime.waiting_for_start_time.set()
    await callback_query.answer()


@dp.message_handler(state=AdminSetWorkTime.waiting_for_start_time, content_types=types.ContentTypes.TEXT)
async def set_work_time_start(message: types.Message, state: FSMContext):
    raw_text = message.text.strip()
    if raw_text.lower() in {"/start", "/cancel", "bekor qilish", "bekor"}:
        await _exit_admin_fsm_to_menu(message, state)
        return

    start_time = _parse_hhmm_input(raw_text)
    if not start_time:
        await message.reply("Iltimos, vaqtni <b>HH:MM</b> formatda kiriting (masalan: <b>09:00</b>).",
                            parse_mode="HTML")
        return
    await state.update_data(work_start=start_time)
    await message.reply("Endi ish tugash vaqtini <b>HH:MM</b> formatda kiriting (masalan, <b>18:00</b>):",
                        parse_mode="HTML")
    await AdminSetWorkTime.waiting_for_end_time.set()


@dp.message_handler(state=AdminSetWorkTime.waiting_for_end_time, content_types=types.ContentTypes.TEXT)
async def set_work_time_end(message: types.Message, state: FSMContext):
    raw_text = message.text.strip()
    if raw_text.lower() in {"/start", "/cancel", "bekor qilish", "bekor"}:
        await _exit_admin_fsm_to_menu(message, state)
        return

    end_time = _parse_hhmm_input(raw_text)
    if not end_time:
        await message.reply("Iltimos, vaqtni <b>HH:MM</b> formatda kiriting (masalan: <b>18:00</b>).",
                            parse_mode="HTML")
        return

    data = await state.get_data()
    start_time = data.get("work_start")
    worker_id = data.get("work_time_worker_id")
    if not worker_id or not start_time:
        await message.reply("Xatolik yuz berdi. Qaytadan boshlang.")
        await state.finish()
        return
    if not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    fmt = "%H:%M"
    try:
        start_dt = datetime.datetime.strptime(start_time, fmt)
        end_dt = datetime.datetime.strptime(end_time, fmt)
        diff_sec = (end_dt - start_dt).total_seconds()
        if diff_sec <= 0:
            await message.reply("Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak.")
            return
    except Exception:
        await message.reply("Vaqtni hisoblashda xatolik. To'g'ri formatda kiriting.")
        return

    daily_hrs_float = diff_sec / 3600.0
    time_str = format_hours(daily_hrs_float)

    try:
        async with db.pool.acquire() as conn:
            await conn.execute("""
                               UPDATE workers
                               SET work_start=$1,
                                   work_end=$2,
                                   daily_work_hours=$3
                               WHERE id = $4
                               """, start_dt.time(), end_dt.time(), daily_hrs_float, worker_id)

            worker_record = await conn.fetchrow("SELECT tg_id, full_name FROM workers WHERE id=$1", worker_id)
    except Exception as ex:
        logging.exception("Ish vaqtini saqlashda xatolik: %s", ex)
        await message.reply("Ish vaqtini saqlashda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return

    await message.reply(
        f"✅ Ish vaqti <b>{start_time} - {end_time}</b> ga belgilandi.\nKunlik ish soati: <b>{time_str}</b>.",
        parse_mode="HTML")

    if worker_record:
        worker_tg, worker_name = worker_record['tg_id'], worker_record['full_name']
        try:
            await bot.send_message(
                worker_tg,
                f"Hurmatli {worker_name}, ish vaqtingiz {start_time} - {end_time} ga belgilandi.\n"
                f"Kunlik ish soati: {time_str}."
            )
        except Exception as ex:
            logging.error(f"Xodimga ish vaqti o'zgargani haqida yuborishda xato: {ex}")

    await state.finish()


@dp.callback_query_handler(lambda c: c.data.startswith("delete_dw_"))
async def delete_daily_work_hours(callback_query: types.CallbackQuery):
    await callback_query.answer("Iltimos, 'Ish vaqtini bekor qilish' tugmasidan foydalaning.", show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith("delete_worktime_"))
async def delete_work_time(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    try:
        worker_id = int(callback_query.data.split("_")[2])
    except (IndexError, ValueError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
        return
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE workers SET work_start = NULL, work_end = NULL, daily_work_hours = 0 WHERE id = $1",
            worker_id
        )

    await callback_query.message.edit_text(
        f"✅ Xodim (ID: {worker_id}) uchun ish vaqtlari va kunlik ish soati bekor qilindi.")
    await callback_query.answer("Muvaffaqiyatli bekor qilindi!")


@dp.callback_query_handler(lambda c: c.data.startswith("editdw_yes_"))
async def editdw_yes_callback(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    parts = callback_query.data.split("_")
    if len(parts) < 3:
        await callback_query.answer("Xatolik: ID topilmadi.", show_alert=True)
        return

    worker_id = int(parts[2])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return
    await state.update_data(dw_worker_id=worker_id)

    await callback_query.message.edit_text(
        "Xodimning kunlik ish soatini kiriting (masalan: 8.5):\n"
        "<b>DIQQAT:</b> Bu sozlama faqat ish vaqti (09:00-18:00 kabi) belgilanmagan bo'lsa ishlaydi.",
        parse_mode="HTML"
    )
    await AdminSetDailyHours.waiting_for_new_daily_hours.set()
    await callback_query.answer()


@dp.message_handler(state=AdminSetDailyHours.waiting_for_new_daily_hours, content_types=types.ContentTypes.TEXT)
async def save_daily_work_hours(message: types.Message, state: FSMContext):
    try:
        daily_hrs = float(message.text.strip())
    except (ValueError, TypeError):
        await message.reply("Iltimos, son kiriting (masalan: 8.5).")
        return

    data = await state.get_data()
    worker_id = data.get("dw_worker_id")
    if not worker_id:
        await message.reply("Xatolik yuz berdi.")
        await state.finish()
        return
    if not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        old_record = await conn.fetchrow(
            "SELECT tg_id, full_name, daily_work_hours FROM workers WHERE id=$1", worker_id
        )
        if not old_record:
            await message.reply("Xodim topilmadi.")
            await state.finish()
            return
        await conn.execute("UPDATE workers SET daily_work_hours=$1 WHERE id=$2", daily_hrs, worker_id)

    worker_tg, worker_name, old_dw = old_record['tg_id'], old_record['full_name'], old_record['daily_work_hours']
    new_time_str = format_hours(daily_hrs)

    await message.reply(
        f"✅ Xodim (ID: {worker_id}) uchun kunlik ish soati endi: <b>{new_time_str}</b> "
        f"(Oldingi: {format_hours(old_dw or 0.0)}).",
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            worker_tg,
            f"Hurmatli {html.escape(str(worker_name))}, kunlik ish soatingiz endi <b>{new_time_str}</b>.\n(Oldingi: {format_hours(old_dw or 0.0)})",
            parse_mode="HTML"
        )
    except Exception as ex:
        logging.error(f"Xodimga ish soati o'zgargani haqida yuborishda xato: {ex}")

    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "admin_daily_report")
async def admin_daily_report(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("Ruxsat yo'q", show_alert=True)
        return
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return

    today = datetime.date.today()
    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)

    async with db.pool.acquire() as conn:
        if branch_scope is None:
            attendance_records = await conn.fetch(
                """
                SELECT a.name, a.timestamp, a.message, b.name AS branch_name
                FROM attendance a
                LEFT JOIN branches b ON b.id = a.branch_id
                WHERE a.timestamp::date = $1
                ORDER BY a.timestamp
                """,
                today,
            )
            sessions = await conn.fetch(
                """
                SELECT
                    w.full_name,
                    ws.arrival_time,
                    ws.departure_time,
                    ws.total_hours,
                    ws.is_friday,
                    ws.session_daily_hours,
                    b.name AS branch_name
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                WHERE ws.date = $1
                ORDER BY ws.id
                """,
                today,
            )
        elif not branch_scope:
            attendance_records = []
            sessions = []
        else:
            attendance_records = await conn.fetch(
                """
                SELECT a.name, a.timestamp, a.message, b.name AS branch_name
                FROM attendance a
                LEFT JOIN branches b ON b.id = a.branch_id
                WHERE a.timestamp::date = $1
                  AND a.branch_id = ANY($2::int[])
                ORDER BY a.timestamp
                """,
                today,
                branch_scope,
            )
            sessions = await conn.fetch(
                """
                SELECT
                    w.full_name,
                    ws.arrival_time,
                    ws.departure_time,
                    ws.total_hours,
                    ws.is_friday,
                    ws.session_daily_hours,
                    b.name AS branch_name
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                WHERE ws.date = $1
                  AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                ORDER BY ws.id
                """,
                today,
                branch_scope,
            )

    text_att = "<b>Bugungi qo'shimcha yozuvlar:</b>\n" if attendance_records else "Bugun qo'shimcha yozuvlar mavjud emas.\n"
    for record in attendance_records:
        ts_str = record['timestamp'].astimezone(tashkent_tz).strftime('%H:%M:%S')
        branch_suffix = f" [{record['branch_name']}]" if record.get('branch_name') else ""
        text_att += f"<code>{ts_str}</code> - {record['name']}{branch_suffix}: <i>{record['message']}</i>\n"

    if not sessions:
        text_sess = "<b>Bugun hech kim ishga kelgani qayd qilinmadi.</b>\n"
    else:
        text_sess = "<b>Bugungi ish soatlari:</b>\n"
        for i, session in enumerate(sessions, start=1):
            full_name = session["full_name"]
            arr = session["arrival_time"]
            dep = session["departure_time"]
            total_hours = session["total_hours"]
            is_friday = session["is_friday"]
            sess_req = session["session_daily_hours"]
            branch_name = session["branch_name"]
            arr_str = arr.astimezone(tashkent_tz).strftime('%H:%M:%S')
            branch_suffix = f" [{branch_name}]" if branch_name else ""

            if dep is None:
                text_sess += f"{i}. ▶️ {full_name}{branch_suffix} - Keldi: <b>{arr_str}</b>, hali ketmadi.\n"
            else:
                dep_str = dep.astimezone(tashkent_tz).strftime('%H:%M:%S')
                actual_str = format_hours(total_hours or 0.0)
                expected_str = format_hours(sess_req or 0.0) if sess_req and float(sess_req) > 0 else "Belgilanmagan"

                diff_str = ""
                if sess_req and float(sess_req) > 0 and total_hours is not None:
                    diff_minutes = int(float(sess_req) * 60) - int(float(total_hours) * 60)
                    if diff_minutes > LATE_EARLY_TOLERANCE_MIN:
                        diff_str = f" (⚠️ {format_hours(diff_minutes / 60)} kam)"

                friday_tag = "(Juma) " if is_friday else ""
                text_sess += (f"{i}. ✅ {full_name}{branch_suffix} {friday_tag}- <b>{arr_str}</b> da keldi, <b>{dep_str}</b> da ketdi.\n"
                              f"   Ishlagan: <i>{actual_str}</i> | Kutilgan: <i>{expected_str}</i>{diff_str}\n")

    final_text = text_sess + "\n" + text_att
    await bot.send_message(callback_query.from_user.id, final_text, parse_mode="HTML")
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data == "admin_monthly_report")
async def show_months(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return

    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            months_records = await conn.fetch(
                """
                SELECT DISTINCT to_char(ws.date, 'YYYY-MM') AS month
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                ORDER BY month DESC
                """
            )
        elif not branch_scope:
            months_records = []
        else:
            months_records = await conn.fetch(
                """
                SELECT DISTINCT to_char(ws.date, 'YYYY-MM') AS month
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE COALESCE(ws.branch_id, w.branch_id) = ANY($1::int[])
                ORDER BY month DESC
                """,
                branch_scope,
            )

    if not months_records:
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary")
        )
        await callback_query.message.edit_text(
            "Hozircha oylik ma'lumotlar mavjud emas.",
            reply_markup=kb
        )
        return await callback_query.answer()

    kb = types.InlineKeyboardMarkup(row_width=3)
    for record in months_records:
        m = record['month']
        kb.insert(types.InlineKeyboardButton(text=m, callback_data=f"month_{m}", style="primary"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))

    await callback_query.message.edit_text(
        "Iltimos, oy tanlang:",
        reply_markup=kb
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("month_"))
async def show_days_in_month(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    month = callback_query.data.split("_", 1)[1]
    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)

    async with db.pool.acquire() as conn:
        if branch_scope is None:
            days_records = await conn.fetch(
                """
                SELECT DISTINCT ws.date
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE to_char(ws.date, 'YYYY-MM') = $1
                ORDER BY ws.date DESC
                """,
                month,
            )
        elif not branch_scope:
            days_records = []
        else:
            days_records = await conn.fetch(
                """
                SELECT DISTINCT ws.date
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE to_char(ws.date, 'YYYY-MM') = $1
                  AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                ORDER BY ws.date DESC
                """,
                month,
                branch_scope,
            )

    if not days_records:
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_monthly_report", style="primary")
        )
        await callback_query.message.edit_text(
            f"{month} oyida ma'lumotlar topilmadi.",
            reply_markup=kb
        )
        return await callback_query.answer()

    kb = types.InlineKeyboardMarkup(row_width=3)
    for record in days_records:
        d_str = record['date'].strftime('%Y-%m-%d')
        kb.insert(types.InlineKeyboardButton(text=d_str, callback_data=f"day_{d_str}", style="primary"))

    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_monthly_report", style="primary"))

    await callback_query.message.edit_text(
        f"{month} oyidagi kunlarni tanlang:",
        reply_markup=kb
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("day_"))
async def show_employees_in_day(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo‘q", show_alert=True)

    day = callback_query.data.split("_", 1)[1]
    day_date = datetime.datetime.strptime(day, "%Y-%m-%d").date()
    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)

    async with db.pool.acquire() as conn:
        if branch_scope is None:
            workers = await conn.fetch(
                """
                SELECT DISTINCT w.id, w.full_name, b.name AS branch_name
                FROM workers w
                JOIN work_sessions s ON w.id = s.user_id
                LEFT JOIN branches b ON b.id = COALESCE(s.branch_id, w.branch_id)
                WHERE s.date = $1
                ORDER BY w.id ASC
                """,
                day_date,
            )
        elif not branch_scope:
            workers = []
        else:
            workers = await conn.fetch(
                """
                SELECT DISTINCT w.id, w.full_name, b.name AS branch_name
                FROM workers w
                JOIN work_sessions s ON w.id = s.user_id
                LEFT JOIN branches b ON b.id = COALESCE(s.branch_id, w.branch_id)
                WHERE s.date = $1
                  AND COALESCE(s.branch_id, w.branch_id) = ANY($2::int[])
                ORDER BY w.id ASC
                """,
                day_date,
                branch_scope,
            )

    if not workers:
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"month_{day[:7]}", style="primary")
        )
        await callback_query.message.edit_text(f"{day} da xodim ma'lumotlari topilmadi.", reply_markup=kb)
        return await callback_query.answer()

    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, worker in enumerate(workers, start=1):
        kb.insert(types.InlineKeyboardButton(
            text=_format_worker_option_label(dict(worker), position=i),
            callback_data=f"daily_details_{worker['id']}_{day}",
            style="primary",
        ))

    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"month_{day[:7]}", style="primary"))
    await callback_query.message.edit_text(f"{day} da quyidagi xodimlar mavjud:", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("daily_details_"))
async def show_daily_details(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    parts = callback_query.data.split("_")
    if len(parts) < 3:
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
        return

    worker_id = int(parts[2])
    day = parts[3]
    day_date = datetime.datetime.strptime(day, "%Y-%m-%d").date()
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    async with db.pool.acquire() as conn:
        worker = await conn.fetchrow(
            "SELECT w.full_name, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )
        sessions = await conn.fetch(
            "SELECT * FROM work_sessions WHERE user_id = $1 AND date = $2 ORDER BY id",
            worker_id, day_date
        )
        attendances = await conn.fetch(
            "SELECT timestamp, message, reason FROM attendance WHERE user_id = $1 AND timestamp::date = $2 ORDER BY timestamp",
            worker_id, day_date
        )

    worker_label = worker["full_name"] if worker else f"Xodim {worker_id}"
    if worker and worker["branch_name"]:
        worker_label += f" [{worker['branch_name']}]"
    details = f"<b>{day} kungi to'liq ma'lumot:</b>\n<b>{worker_label}</b>\n\n"
    if sessions:
        for idx, session in enumerate(sessions, start=1):
            arr_time = session['arrival_time'].astimezone(tashkent_tz).strftime('%H:%M:%S') if session['arrival_time'] else '—'
            dep_time = session['departure_time'].astimezone(tashkent_tz).strftime('%H:%M:%S') if session[
                'departure_time'] else 'Hali ketmagan'
            total_h_str = format_hours(session['total_hours'] or 0.0)
            daily_h_str = format_hours(session['session_daily_hours'] or 0.0)

            details += f"<b>Sessiya {idx}:</b>\n"
            details += f"  Kelgan: {arr_time}\n"
            details += f"  Ketgan: {dep_time}\n"
            details += f"  Ishlagan vaqt: {total_h_str}\n"
            if session['session_daily_hours'] and float(session['session_daily_hours']) > 0:
                details += f"  Kunlik talab: {daily_h_str}\n"
            if session['is_friday']:
                details += "  (Juma kuni)\n"
            if session['late_reason']:
                details += f"  Sabab: <i>{session['late_reason']}</i>\n"
            details += "\n"
    else:
        details += "Bu kun uchun ish sessiyalari topilmadi.\n\n"

    if attendances:
        details += "<b>Qo'shimcha yozuvlar:</b>\n"
        for att in attendances:
            ts = att['timestamp'].astimezone(tashkent_tz).strftime('%H:%M:%S')
            details += f"<code>{ts}</code>: {att['message']}"
            if att['reason']:
                details += f" (Sabab: <i>{att['reason']}</i>)"
            details += "\n"

    await bot.send_message(callback_query.from_user.id, details, parse_mode="HTML")
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("modify_salary_"))
async def modify_salary_options(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    parts = callback_query.data.split("_")
    if len(parts) < 3:
        await callback_query.answer("Xatolik yuz berdi.")
        return
    worker_id = int(parts[2])
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("To'lovlarni tahrirlash", callback_data=f"modifypayments_{worker_id}", style="primary"),
        types.InlineKeyboardButton("Oylik maoshni tahrirlash", callback_data=f"modify_monthly_{worker_id}", style="primary")
    )
    await bot.send_message(callback_query.from_user.id,
                           f"Xodim (ID: {worker_id}) uchun maosh tahrirlash variantlarini tanlang:",
                           reply_markup=keyboard)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("modifypayments_"))
async def modify_payment_list_new(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    parts = callback_query.data.split("_")
    if len(parts) < 2:
        await callback_query.answer("Xatolik yuz berdi.")
        return
    worker_id = int(parts[1])
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    current_month = datetime.datetime.now(tashkent_tz).strftime("%Y-%m")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        payments = await conn.fetch(
            """
            SELECT id, payment_date, amount, payment_time, kind
            FROM salary_payments
            WHERE worker_id = $1
              AND to_char(payment_date, 'YYYY-MM') = $2
            """,
            worker_id, current_month,
        )

    if not payments:
        await bot.send_message(callback_query.from_user.id, "Bu oy uchun to'lovlar mavjud emas.")
        await callback_query.answer()
        return

    text = "<b>Joriy oy to'lovlari:</b>\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for i, p in enumerate(payments, start=1):
        p_datetime = p['payment_time'].astimezone(tashkent_tz)
        date_str = p_datetime.strftime("%d.%m.%Y")
        time_str = p_datetime.strftime("%H:%M")
        amount_str = f"{float(p['amount']):,.0f}"
        kind_label = format_payment_kind(p['kind'])
        text += f"<b>{i}.</b> {kind_label} — {date_str} {time_str} — <b>{amount_str} so'm</b>\n"
        keyboard.add(
            types.InlineKeyboardButton(text=f"✏️ Tahrirlash ({i})", callback_data=f"change_{p['id']}", style="primary")
        )
    await bot.send_message(callback_query.from_user.id, text, reply_markup=keyboard, parse_mode="HTML")
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("modify_monthly_"))
async def modify_monthly_salary_callback(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    parts = callback_query.data.split("_")
    if len(parts) < 3:
        await callback_query.answer("Xatolik yuz berdi.")
        return
    worker_id = int(parts[2])
    if not await db.admin_can_access_worker(callback_query.from_user.id, worker_id):
        return await callback_query.answer("Bu xodim sizning filialingizga tegishli emas.", show_alert=True)
    await state.update_data(modify_monthly_worker_id=worker_id)
    await bot.send_message(callback_query.from_user.id,
                           f"Iltimos, xodim (ID: {worker_id}) uchun yangi oylik maosh miqdorini kiriting:")
    await AdminModifyMonthlySalary.waiting_for_new_monthly_salary.set()
    await callback_query.answer()


@dp.message_handler(state=AdminModifyMonthlySalary.waiting_for_new_monthly_salary,
                    content_types=types.ContentTypes.TEXT)
async def process_modify_monthly_salary(message: types.Message, state: FSMContext):
    try:
        text = message.text.lower().replace(" ", "")
        if "million" in text or "milliyon" in text:
            number_text = text.replace("million", "").replace("milliyon", "")
            new_monthly_salary = float(number_text) * 1_000_000
        else:
            new_monthly_salary = float(''.join(filter(lambda c: c.isdigit() or c == '.', text)))
    except (ValueError, TypeError):
        await message.reply("Iltimos, to'g'ri miqdorni kiriting (misol: 5000000 yoki 5 million).")
        return

    data = await state.get_data()
    worker_id = data.get("modify_monthly_worker_id")
    if not worker_id:
        await message.reply("Xatolik yuz berdi.")
        await state.finish()
        return
    if not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        old_record = await conn.fetchrow("SELECT tg_id, full_name, monthly_salary FROM workers WHERE id=$1", worker_id)
        if not old_record:
            await message.reply("Xodim topilmadi.")
            await state.finish()
            return
        await conn.execute(
            "UPDATE workers SET monthly_salary = $1, pay_amount = $1, pay_type = 'monthly' WHERE id = $2",
            new_monthly_salary,
            worker_id,
        )

    worker_tg, worker_name, old_salary = old_record['tg_id'], old_record['full_name'], old_record['monthly_salary']
    old_salary_str = f"{float(old_salary or 0.0):,.0f}"
    new_salary_str = f"{float(new_monthly_salary):,.0f}"

    await message.reply(
        f"✅ Xodim (ID: {worker_id}) uchun oylik maosh {old_salary_str} so'mdan {new_salary_str} so'mga o'zgartirildi.")
    try:
        await bot.send_message(worker_tg,
                               f"Hurmatli {worker_name}, sizning oylik maoshingiz {new_salary_str} so‘m etib belgilandi.")
    except Exception as ex:
        logging.error(ex)
    await state.finish()


@dp.callback_query_handler(lambda c: c.data.startswith("change_"))
async def change_payment_amount(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    payment_id = int(callback_query.data.split("_")[1])
    payment = await _get_accessible_payment_record(callback_query.from_user.id, payment_id)
    if not payment:
        return await callback_query.answer("To'lov topilmadi yoki sizga tegishli emas.", show_alert=True)
    await state.update_data(edit_payment_id=payment_id)
    await bot.send_message(callback_query.from_user.id,
                           f"Iltimos, to'lov (ID: {payment_id}) uchun yangi miqdorni kiriting:")
    await AdminModifyPayment.waiting_for_new_payment_amount.set()
    await callback_query.answer("Yangi miqdorni kiriting.")


@dp.message_handler(
    state=AdminModifyPayment.waiting_for_new_payment_amount,
    content_types=types.ContentTypes.TEXT
)
async def process_modify_payment(message: types.Message, state: FSMContext):
    txt = message.text.lower().replace(" ", "")
    try:
        if "million" in txt or "milliyon" in txt:
            num_txt = txt.replace("million", "").replace("milliyon", "")
            new_amt = float(num_txt) * 1_000_000
        else:
            new_amt = float(''.join(ch for ch in txt if ch.isdigit() or ch == '.'))
    except ValueError:
        return await message.reply("Iltimos, miqdorni to‘g‘ri kiriting.\nMisol: 1500000 yoki 1.5 million")

    data = await state.get_data()
    payment_id = data.get("edit_payment_id")
    if not payment_id:
        await state.finish()
        return await message.reply("Kontekst topilmadi. Qaytadan urinib ko‘ring.")
    payment = await _get_accessible_payment_record(message.from_user.id, int(payment_id))
    if not payment:
        await state.finish()
        return await message.reply("To‘lov sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    async with db.pool.acquire() as conn:
        old_record = await conn.fetchrow("""
                                         SELECT sp.worker_id,
                                                sp.amount,
                                                sp.payment_date,
                                                w.tg_id,
                                                w.full_name,
                                                w.monthly_salary,
                                                b.name AS branch_name
                                         FROM salary_payments sp
                                                  JOIN workers w ON w.id = sp.worker_id
                                                  LEFT JOIN branches b ON b.id = w.branch_id
                                         WHERE sp.id = $1
                                         """, payment_id)
        if not old_record:
            await state.finish()
            return await message.reply("To‘lov topilmadi.")

        worker_id = old_record["worker_id"]
        old_amt = old_record["amount"]
        payment_date = old_record["payment_date"]
        tg_id = old_record["tg_id"]
        full_name = old_record["full_name"]
        monthly_salary = old_record["monthly_salary"]
        branch_name = old_record["branch_name"]
        await conn.execute("UPDATE salary_payments SET amount = $1 WHERE id = $2", new_amt, payment_id)
        cur_month = payment_date.strftime("%Y-%m")
        total_paid = await conn.fetchval("""
                                         SELECT SUM(amount)
                                         FROM salary_payments
                                         WHERE worker_id = $1
                                           AND to_char(payment_date, 'YYYY-MM') = $2
                                         """, worker_id, cur_month)

    total_paid = total_paid or 0.0
    remaining = (monthly_salary - total_paid) if monthly_salary else None

    worker_label = full_name + (f" [{branch_name}]" if branch_name else "")
    await message.reply(
        f"✅ To‘lov (ID: {payment_id}) {float(old_amt):,.0f} so‘mdan "
        f"{new_amt:,.0f} so‘mga yangilandi.\n"
        f"👤 {worker_label}"
    )
    try:
        msg = (f"Hurmatli {full_name}, to‘lov (ID:{payment_id}) {new_amt:,.0f} so‘mga yangilandi.\n"
               f"Bu oy jami olganingiz: {float(total_paid):,.0f} so‘m.")
        if remaining is not None:
            msg += f"\nQolgan: {float(remaining):,.0f} so‘m."
        await bot.send_message(tg_id, msg)
    except Exception as ex:
        logging.error(ex)
    await state.finish()


@dp.message_handler(commands=['old_payments'], state="*")
async def old_payments_handler(message: types.Message):
    if message.from_user.id not in ADMINS:
        return await message.reply("Bu buyruq faqat admin uchun.")
    if not await _ensure_admin_operating_scope_message(message):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply("Iltimos, oyni ham kiriting: /old_payments 2025-02 (yoki /old_payments 2025-02 3)")
        return

    month_arg = args[1].strip()
    try:
        datetime.datetime.strptime(month_arg, "%Y-%m")
    except ValueError:
        await message.reply("Oy formati noto'g'ri. Masalan: 2025-02")
        return

    worker_id = None
    if len(args) >= 3:
        try:
            worker_id = int(args[2])
        except ValueError:
            await message.reply("worker_id noto'g'ri. /old_payments 2025-02 3")
            return
        if not await db.admin_can_access_worker(message.from_user.id, worker_id):
            return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # --- TUZATISH: db.pool ishlatiladi ---
    branch_scope = await _get_admin_branch_scope(message.from_user.id)
    async with db.pool.acquire() as conn:
        if worker_id is None:
            query = """
                    SELECT w.full_name, sp.payment_time, sp.amount, sp.kind, b.name AS branch_name
                    FROM salary_payments sp
                             JOIN workers w ON sp.worker_id = w.id
                             LEFT JOIN branches b ON b.id = w.branch_id
                    WHERE to_char(sp.payment_date, 'YYYY-MM') = $1
                    """
            params = [month_arg]
            if branch_scope is not None:
                if not branch_scope:
                    rows = []
                else:
                    query += " AND w.branch_id = ANY($2::int[])"
                    params.append(branch_scope)
                    query += " ORDER BY sp.payment_time DESC"
                    rows = await conn.fetch(query, *params)
            else:
                query += " ORDER BY sp.payment_time DESC"
                rows = await conn.fetch(query, *params)
        else:
            query = """
                    SELECT w.full_name, sp.payment_time, sp.amount, sp.kind, b.name AS branch_name
                    FROM salary_payments sp
                             JOIN workers w ON sp.worker_id = w.id
                             LEFT JOIN branches b ON b.id = w.branch_id
                    WHERE to_char(sp.payment_date, 'YYYY-MM') = $1
                      AND sp.worker_id = $2
                    ORDER BY sp.payment_time DESC \
                    """
            rows = await conn.fetch(query, month_arg, worker_id)

    if not rows:
        await message.reply("Bu oyga hech qanday to'lov topilmadi.")
        return

    text = f"<b>To'lovlar (oy: {month_arg}):</b>\n\n"
    for i, row in enumerate(rows, start=1):
        p_datetime = row['payment_time'].astimezone(tashkent_tz)
        date_str = p_datetime.strftime('%d.%m.%Y')
        time_str = p_datetime.strftime('%H:%M')
        branch_suffix = f" [{row['branch_name']}]" if row.get('branch_name') else ""
        kind_emoji = format_payment_kind(row.get('kind'), short=True)
        text += f"<b>{i}.</b> {kind_emoji} {date_str} {time_str} | {row['full_name']}{branch_suffix}: <b>{float(row['amount']):,.0f} so'm</b>\n"

    await message.reply(text, parse_mode="HTML")


# -----------------  BOT FOYDALANUVCHILARI STATISTIKASI  ----------------------
async def _stat_year_items(admin_tg_id: int) -> list[tuple[str, str]]:
    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            records = await conn.fetch(
                """
                SELECT DISTINCT to_char(ws.date, 'YYYY') AS y
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                ORDER BY y DESC
                """
            )
        elif not branch_scope:
            records = []
        else:
            records = await conn.fetch(
                """
                SELECT DISTINCT to_char(ws.date, 'YYYY') AS y
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE COALESCE(ws.branch_id, w.branch_id) = ANY($1::int[])
                ORDER BY y DESC
                """,
                branch_scope,
            )
    return [(rec['y'], f"statsy_{rec['y']}") for rec in records] if records else []


async def send_stats_years(msg_obj: types.Message, admin_tg_id: int, page: int = 0):
    items = await _stat_year_items(admin_tg_id)
    total_years = len(items)

    if not items:
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary")
        )
        return await safe_edit_text(msg_obj, "Hozircha statistik yozuvlar kiritilmagan.", reply_markup=kb)

    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=10,
        page_prefix="statsy_page",
        back_cb="back_admin_main",
        total_items=total_years
    )
    await safe_edit_text(msg_obj, "Yilni tanlang:", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "stats_usage")
async def stats_usage_years(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return
    await send_stats_years(callback_query.message, callback_query.from_user.id, page=0)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("statsy_page_"))
async def stats_year_page(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    page = int(callback_query.data.split("_")[2])
    await send_stats_years(callback_query.message, callback_query.from_user.id, page=page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("statsy_page:"))
async def stats_year_page_legacy(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    try:
        page = int(callback_query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Sahifa topilmadi.", show_alert=True)
    await send_stats_years(callback_query.message, callback_query.from_user.id, page=page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("statsy_"))
async def stats_usage_months(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    year = callback_query.data.split("_")[1]
    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)

    async with db.pool.acquire() as conn:
        if branch_scope is None:
            records = await conn.fetch("""
                                       SELECT DISTINCT to_char(ws.date, 'YYYY-MM') AS m
                                       FROM work_sessions ws
                                       JOIN workers w ON w.id = ws.user_id
                                       WHERE EXTRACT(YEAR FROM ws.date) = $1
                                       ORDER BY m DESC
                                       """, int(year))
        elif not branch_scope:
            records = []
        else:
            records = await conn.fetch("""
                                       SELECT DISTINCT to_char(ws.date, 'YYYY-MM') AS m
                                       FROM work_sessions ws
                                       JOIN workers w ON w.id = ws.user_id
                                       WHERE EXTRACT(YEAR FROM ws.date) = $1
                                         AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                                       ORDER BY m DESC
                                       """, int(year), branch_scope)

    kb = types.InlineKeyboardMarkup(row_width=3)
    for record in records:
        kb.insert(types.InlineKeyboardButton(record['m'], callback_data=f"statsm_{record['m']}", style="primary"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="stats_usage", style="primary"))
    await callback_query.message.edit_text(f"{year} yilidagi oylar:", reply_markup=kb)
    await callback_query.answer()


async def _stat_workers_items(admin_tg_id: int, month: str) -> list[tuple[str, str]]:
    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            rows = await conn.fetch("""
                                    SELECT DISTINCT w.id, w.full_name, b.name AS branch_name
                                    FROM work_sessions s
                                             JOIN workers w ON w.id = s.user_id
                                             LEFT JOIN branches b ON b.id = COALESCE(s.branch_id, w.branch_id)
                                    WHERE to_char(s.date, 'YYYY-MM') = $1
                                    ORDER BY w.id ASC
                                    """, month)
        elif not branch_scope:
            rows = []
        else:
            rows = await conn.fetch("""
                                    SELECT DISTINCT w.id, w.full_name, b.name AS branch_name
                                    FROM work_sessions s
                                             JOIN workers w ON w.id = s.user_id
                                             LEFT JOIN branches b ON b.id = COALESCE(s.branch_id, w.branch_id)
                                    WHERE to_char(s.date, 'YYYY-MM') = $1
                                      AND COALESCE(s.branch_id, w.branch_id) = ANY($2::int[])
                                    ORDER BY w.id ASC
                                    """, month, branch_scope)
    return [dict(row) for row in rows] if rows else []


async def send_stat_workers(msg_obj: types.Message, admin_tg_id: int, month: str, page: int = 0):
    per_page = 10
    all_rows = await _stat_workers_items(admin_tg_id, month)
    total_workers_in_month = len(all_rows)
    start = page * per_page
    page_rows = all_rows[start : start + per_page]
    items = [
        (_format_worker_option_label(row, position=start + i + 1),
         f"statsw_{row['id']}_{month}")
        for i, row in enumerate(page_rows)
    ]

    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=per_page,
        page_prefix=f"stwpage_{month}",
        back_cb=f"statsy_{month[:4]}",
        total_items=total_workers_in_month,
    )
    await safe_edit_text(msg_obj, f"{month} oyi uchun xodim tanlang:", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data.startswith("statsm_") and not c.data.startswith("statsm_back_"))
async def stats_usage_workers(callback_query: types.CallbackQuery):
    month = callback_query.data.split("_", 1)[1]
    await send_stat_workers(callback_query.message, callback_query.from_user.id, month, page=0)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("stwpage_"))
async def stat_workers_page(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        # --- O'ZGARISH SHU YERDA: Ma'lumotni to'g'ri ajratib olamiz ---
        # callback_data formati: "stwpage_YYYY-MM:PAGE"

        # Avval prefiks va ma'lumot qismini ajratamiz
        prefix_part, data_part = callback_query.data.split("_", 1)

        # Keyin ma'lumot qismidan oy va sahifani ajratamiz
        month, page_str = data_part.split(":", 1)
        page = int(page_str)

        await send_stat_workers(callback_query.message, callback_query.from_user.id, month, page)

    except (IndexError, ValueError) as e:
        logging.error(f"Pagination xatoligi: {e}, data: {callback_query.data}")
        await callback_query.answer("Sahifalashda xatolik yuz berdi.", show_alert=True)

    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("statsm_back_"))
async def stats_months_back(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer()
    year = callback_query.data.split("_", 2)[2]
    branch_scope = await _get_admin_branch_scope(callback_query.from_user.id)

    async with db.pool.acquire() as conn:
        if branch_scope is None:
            records = await conn.fetch("""
                                       SELECT DISTINCT to_char(ws.date, 'YYYY-MM') as m
                                       FROM work_sessions ws
                                       JOIN workers w ON w.id = ws.user_id
                                       WHERE EXTRACT(YEAR FROM ws.date) = $1
                                       ORDER BY m DESC
                                       """, int(year))
        elif not branch_scope:
            records = []
        else:
            records = await conn.fetch("""
                                       SELECT DISTINCT to_char(ws.date, 'YYYY-MM') as m
                                       FROM work_sessions ws
                                       JOIN workers w ON w.id = ws.user_id
                                       WHERE EXTRACT(YEAR FROM ws.date) = $1
                                         AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                                       ORDER BY m DESC
                                       """, int(year), branch_scope)

    kb = types.InlineKeyboardMarkup(row_width=3)
    for record in records:
        kb.insert(types.InlineKeyboardButton(record['m'], callback_data=f"statsm_{record['m']}", style="primary"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="stats_usage", style="primary"))
    await callback_query.message.edit_text(f"{year} yilidagi oylar:", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("statsw_") and c.data.count("_") >= 2, state="*")
async def stats_usage_one(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        _tag, wid_str, month = callback_query.data.split("_", 2)
        wid = int(wid_str)
    except Exception:
        return await callback_query.answer("Xato ID", show_alert=True)
    if not await _ensure_worker_access_callback(callback_query, wid):
        return

    async with db.pool.acquire() as conn:
        sessions = await conn.fetch("""
                                    SELECT date, arrival_time, departure_time, session_daily_hours, total_hours
                                    FROM work_sessions
                                    WHERE user_id = $1
                                      AND to_char(date
                                        , 'YYYY-MM') = $2
                                    ORDER BY date
                                    """, wid, month)
        emp_row = await conn.fetchrow(
            "SELECT w.full_name, b.name AS branch_name FROM workers w "
            "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id=$1",
            wid,
        )

    if not emp_row:
        emp_name = f"Xodim (ID: {wid})"
    else:
        emp_name = emp_row["full_name"]
        if emp_row["branch_name"]:
            emp_name += f" [{emp_row['branch_name']}]"

    y, m = map(int, month.split('-'))
    import calendar  # Lokal import
    last_day = calendar.monthrange(y, m)[1]

    day_map = {f"{month}-{d:02d}": {"arr": None, "dep": None, "req": None, "got": None} for d in range(1, last_day + 1)}

    for s in sessions:
        d_str = s['date'].strftime('%Y-%m-%d')
        day_map[d_str] = {"arr": s['arrival_time'], "dep": s['departure_time'], "req": s['session_daily_hours'],
                          "got": s['total_hours']}

    rest_cfg = await db.get_rest_day()
    report = [f"📊 <b>{emp_name}</b> ({month})", ""]

    for day, info in sorted(day_map.items()):
        wd = datetime.datetime.strptime(day, "%Y-%m-%d").weekday()
        if rest_cfg is not None and wd == rest_cfg and info["arr"] is None: continue

        prefix = "🟢"
        if rest_cfg is not None and wd == rest_cfg and info["arr"]:
            prefix = "🔵"
        elif info["arr"] is None:
            prefix = "🔴"
            report.append(f"{prefix} {day} — botdan <i>foydalanilmagan</i>")
            continue

        # --- TUZATISH SHU YERDA ---
        arr_str = info['arr'].astimezone(tashkent_tz).strftime('%H:%M:%S') if info['arr'] else '—'
        dep_str = info['dep'].astimezone(tashkent_tz).strftime('%H:%M:%S') if info['dep'] else '—'
        # -------------------------

        line = f"{prefix} {day}: {arr_str} — {dep_str}"

        if info["req"] and info["got"] is not None:
            required_min = int(float(info["req"] or 0.0) * 60)
            actual_min = int(float(info["got"] or 0.0) * 60)
            diff_min = actual_min - required_min
            if diff_min > 5:
                line += f" (+{diff_min} daq ortiq)"
            elif diff_min < -5:
                line += f" ({abs(diff_min)} daq kam)"
        report.append(line)

    await bot.send_message(callback_query.from_user.id, "\n".join(report), parse_mode="HTML")
    await callback_query.answer()


# ==================== XODIMLAR RO‘YXATI PAGINATION ============================
# ==================== XODIMLAR RO‘YXATI PAGINATION ============================
async def _workers_items(
    admin_tg_id: int,
    page: int,
    per_page: int,
    hide_branch: bool,
) -> list[tuple[str, str]]:
    """Xodimlar ro'yxatining JORIY SAHIFASI tugmalari (paginatsiya + pozitsion raqam).

    Avval butun ro'yxat 'id' bo'yicha qaytarilardi va build_paginated_inline
    items'ni slice qilmagani uchun BARCHASI bir sahifada chiqardi — paginatsiya
    aslida ishlamasdi. Endi shu yerda sahifa kesimi va 1, 2, 3... pozitsion
    raqam beramiz (DB id'siga bog'liq emas, "1)" tushib qolmaydi).
    """
    rows = await db.list_workers_for_admin(admin_tg_id, order_by="id")
    if not rows:
        return []
    start = page * per_page
    page_rows = rows[start : start + per_page]
    out: list[tuple[str, str]] = []
    for i, row in enumerate(page_rows):
        pos = start + i + 1
        if hide_branch:
            # Yuqorida sarlavhada filial nomi bor — tugmalarda takrorlash kerakmas
            # (tugma qisqaradi → 2 ustun yaxshi joylanadi).
            label = f"{pos}) {row['full_name']}"
        else:
            label = f"{pos}) {_format_worker_branch_label(row)}"
        out.append((label, f"worker_{row['id']}"))
    return out


async def send_admin_workers(msg_obj: types.Message, admin_tg_id: int, page: int = 0):
    per_page = 10
    scope_branch = await db.get_admin_scope_branch(admin_tg_id)
    # Admin scope-da bitta filial bo'lsa, har tugmada nomini takrorlamaymiz
    hide_branch = scope_branch is not None
    items = await _workers_items(admin_tg_id, page, per_page=per_page, hide_branch=hide_branch)
    total_workers = await db.count_workers_for_admin(admin_tg_id)
    scope_suffix = f" - {scope_branch['name']}" if scope_branch else ""

    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=per_page,
        page_prefix="page",
        back_cb="workers:back",
        total_items=total_workers,
    )
    text = f"Xodimlar ro‘yxati{scope_suffix}:" if items else "Hozircha xodimlar yo‘q."
    await safe_edit_text(msg_obj, text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "admin_workers")
async def admin_workers_handler(callback_query: types.CallbackQuery):
    # Bu handler logikasi o'zgarmaydi
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo‘q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query):
        return
    await send_admin_workers(callback_query.message, callback_query.from_user.id, page=0)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("page_"))
async def admin_workers_pagination(callback_query: types.CallbackQuery):
    # Bu handler logikasi o'zgarmaydi
    # Sahifalash callback'i "page_" bilan boshlanadi, lekin 'salwp' yoki 'statsy_page' emasligini tekshirishimiz mumkin
    if not callback_query.data.startswith("page_") or callback_query.data.startswith(
            "salwp_") or callback_query.data.startswith("statsy_page_"):
        # Boshqa paginationlar bilan aralashmaslik uchun. Agar kerak bo'lsa.
        return

    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo‘q", show_alert=True)
    page = int(callback_query.data.split("_")[1])
    await send_admin_workers(callback_query.message, callback_query.from_user.id, page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("page:"))
async def admin_workers_pagination_legacy(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    try:
        page = int(callback_query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return await callback_query.answer("Sahifa topilmadi.", show_alert=True)
    await send_admin_workers(callback_query.message, callback_query.from_user.id, page)
    await callback_query.answer()


async def _attendance_workers_items(admin_tg_id: int, page: int) -> list[tuple[str, str]]:
    rows = await db.list_active_workers_for_admin(admin_tg_id, order_by="name")
    return [
        (_format_worker_branch_label(row), f"attworker:{row['id']}:{page}")
        for row in rows
    ]


async def send_admin_attendance_workers(msg_obj: types.Message, admin_tg_id: int, page: int = 0):
    per_page = 15
    all_items = await _attendance_workers_items(admin_tg_id, page)
    total_workers = len(all_items)
    scope_branch = await db.get_admin_scope_branch(admin_tg_id)
    scope_suffix = f" - {scope_branch['name']}" if scope_branch else ""
    total_pages = max(1, (total_workers + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    items = all_items[start:start + per_page]
    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=per_page,
        page_prefix="attendance_workers",
        back_cb="back_admin_main",
        total_items=total_workers,
        page_separator=":",
    )
    text = f"Davomat uchun xodimni tanlang{scope_suffix}:" if items else "Hozircha xodimlar yo'q."
    await safe_edit_text(msg_obj, text, reply_markup=kb)


async def _build_attendance_action_keyboard(worker_id: int, page: int) -> InlineKeyboardMarkup:
    specs = await get_worker_action_button_specs(worker_id)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton(
            specs["work_label"],
            callback_data=f"wactatt:{specs['work_action']}:{worker_id}:{page}",
            style=specs["work_style"],
        ),
        InlineKeyboardButton("🌙 Dam", callback_data=f"wactatt:rest:{worker_id}:{page}", style="danger"),
    )
    kb.add(
        InlineKeyboardButton(
            specs["study_label"],
            callback_data=f"wactatt:{specs['study_action']}:{worker_id}:{page}",
            style=specs["study_style"],
        )
    )
    kb.add(
        InlineKeyboardButton("⬅️ Orqaga", callback_data=f"attendance_workers:{page}", style="primary")
    )
    return kb


@dp.callback_query_handler(lambda c: c.data.startswith("attendance_workers:"), state="*")
async def attendance_workers_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query, state):
        return

    await state.finish()
    try:
        page = int(callback_query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0

    await send_admin_attendance_workers(callback_query.message, callback_query.from_user.id, page)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("attworker:"), state="*")
async def attendance_worker_actions_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    await state.finish()
    try:
        _, worker_id_raw, page_raw = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
        page = int(page_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri tanlov.", show_alert=True)
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    worker = await db.get_worker_by_id(worker_id)
    if not worker:
        return await callback_query.answer("Xodim topilmadi.", show_alert=True)

    keyboard = await _build_attendance_action_keyboard(worker_id, page)
    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)}\nDavomat amalini tanlang:",
        reply_markup=keyboard,
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("wactatt:"), state="*")
async def attendance_worker_action_apply(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    await state.finish()
    try:
        _, action, worker_id_raw, page_raw = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
        page = int(page_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri amal.", show_alert=True)
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    ok, action_text, worker = await apply_worker_action_for_admin(
        worker_id,
        action,
        callback_query.from_user.id,
        callback_query.from_user.full_name,
    )
    if not ok:
        return await callback_query.answer(action_text, show_alert=True)

    keyboard = await _build_attendance_action_keyboard(worker_id, page)
    await callback_query.message.edit_text(
        f"Xodim: {_format_worker_branch_label(worker)}\nOxirgi amal: {action_text}.",
        reply_markup=keyboard,
    )
    await callback_query.answer("Saqlandi.")


@dp.callback_query_handler(lambda c: c.data.startswith("attendance_quick:"), state="*")
async def attendance_quick_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    await state.finish()
    await send_admin_attendance_workers(callback_query.message, callback_query.from_user.id, page=0)
    await callback_query.answer()


@dp.message_handler(state=AdminQuickAttendance.waiting_for_worker_name, content_types=types.ContentTypes.TEXT)
async def attendance_quick_pick_worker(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    raw_text = (message.text or "").strip()
    data = await state.get_data()
    action = data.get("quick_attendance_action")
    prompt_message_id = data.get("quick_attendance_prompt_message_id")

    if raw_text.lower() in {"/start", "/cancel", "bekor", "bekor qilish", "orqaga"}:
        await state.finish()
        await _cleanup_admin_input_message(message)
        await _render_admin_home(
            message.from_user.id,
            chat_id=message.chat.id,
            message_id=prompt_message_id,
        )
        return

    if action not in AI_ATTENDANCE_ACTION_TITLES:
        await state.finish()
        await _cleanup_admin_input_message(message)
        await _render_admin_home(
            message.from_user.id,
            chat_id=message.chat.id,
            message_id=prompt_message_id,
        )
        return

    name_query = _clean_worker_name_candidate(raw_text) or raw_text
    if len(name_query.strip()) < 2:
        await _cleanup_admin_input_message(message)
        await _edit_admin_message_or_send(
            message.chat.id,
            prompt_message_id,
            _build_quick_attendance_prompt_text(action) + "\n\n❌ Iltimos, xodim ismini aniqroq yozing.",
            reply_markup=_build_quick_attendance_prompt_keyboard(),
            parse_mode="HTML",
        )
        return

    candidates = await db.find_worker_candidates_for_admin(message.from_user.id, name_query, limit=8)
    action_title = AI_ATTENDANCE_ACTION_TITLES.get(action, action)

    if not candidates:
        await _cleanup_admin_input_message(message)
        await _edit_admin_message_or_send(
            message.chat.id,
            prompt_message_id,
            _build_quick_attendance_prompt_text(action)
            + f"\n\n❌ '{html.escape(name_query)}' bo'yicha xodim topilmadi.",
            reply_markup=_build_quick_attendance_prompt_keyboard(),
            parse_mode="HTML",
        )
        return

    if len(candidates) == 1:
        worker = candidates[0]
        ok, action_text, worker_obj = await apply_worker_action_for_admin(
            int(worker["id"]),
            action,
            message.from_user.id,
            message.from_user.full_name,
        )
        await _cleanup_admin_input_message(message)
        await state.finish()
        if not ok:
            await _edit_admin_message_or_send(
                message.chat.id,
                prompt_message_id,
                f"❌ {action_text}",
                reply_markup=_build_quick_attendance_done_keyboard(),
            )
            return
        await _edit_admin_message_or_send(
            message.chat.id,
            prompt_message_id,
            f"✅ Saqlandi: {_format_worker_branch_label(worker_obj)} {action_text}.",
            reply_markup=_build_quick_attendance_done_keyboard(),
        )
        return

    selection_style = "primary"
    if action in {"in", "study_return"}:
        selection_style = "success"
    elif action in {"out", "rest"}:
        selection_style = "danger"

    kb = InlineKeyboardMarkup(row_width=1)
    for worker in candidates:
        phone_status = "📱" if worker.get("has_phone") else "📴"
        kb.add(
            InlineKeyboardButton(
                f"{phone_status} {_format_worker_branch_label(worker)} (ID:{worker['id']})",
                callback_data=f"aiatt:{action}:{worker['id']}",
                style=selection_style,
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))

    await _cleanup_admin_input_message(message)
    await state.finish()
    await _edit_admin_message_or_send(
        message.chat.id,
        prompt_message_id,
        f"🔎 Bir nechta o'xshash ism topildi.\nQaysi xodimni <b>{action_title}</b> deb belgilaymiz?",
        reply_markup=kb,
        parse_mode="HTML",
    )


# 1-QADAM: Jarayonni boshlash va xodimni tanlash
@dp.callback_query_handler(lambda c: c.data == "manual_attendance_start", state="*")
async def manual_attendance_start(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)
    if not await _ensure_admin_operating_scope_callback(callback_query, state):
        return

    workers = await db.list_active_workers_for_admin(callback_query.from_user.id, order_by="name")

    if not workers:
        await callback_query.answer("Hozircha xodimlar mavjud emas.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(row_width=2)
    for i, worker in enumerate(workers, start=1):
        kb.insert(InlineKeyboardButton(
            _format_worker_option_label(worker, position=i),
            callback_data=f"manual_worker_{worker['id']}",
            style="primary",
        ))

    await callback_query.message.edit_text("Qaysi xodim uchun davomat kiritmoqchisiz?", reply_markup=kb)
    await AdminManualAttendance.choosing_worker.set()
    await callback_query.answer()


# 2-QADAM: Sanani olish
@dp.callback_query_handler(lambda c: c.data.startswith("manual_worker_"), state=AdminManualAttendance.choosing_worker)
async def manual_attendance_get_date(callback_query: types.CallbackQuery, state: FSMContext):
    worker_id = int(callback_query.data.split("_")[2])
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return
    worker = await db.get_worker_by_id(worker_id)
    await state.update_data(
        worker_id=worker_id,
        worker_branch_id=worker.get("branch_id") if worker else None,
        worker_label=_format_worker_branch_label(worker) if worker else f"Xodim {worker_id}",
    )

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    date_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    date_kb.row(
        types.KeyboardButton("Bugun uchun", style="success"),
        types.KeyboardButton("Bekor qilish", style="danger"),
    )
    await callback_query.message.edit_text("Xodim tanlandi. Sana kiritish oynasi ochildi.")
    worker_prompt_label = worker.get("full_name") if worker else str(worker_id)
    if worker and worker.get("branch_name"):
        worker_prompt_label += f" [{worker.get('branch_name')}]"
    await callback_query.message.answer(
        f"Tanlangan xodim: {worker_prompt_label}\n"
        f"Iltimos, sanani YYYY-MM-DD formatida kiriting.\n"
        f"Masalan: {today_str}\n\n"
        f"Agar bugungi kun uchun kiritmoqchi bo'lsangiz, 'Bugun uchun' tugmasini bosing.",
        reply_markup=date_kb,
    )
    await AdminManualAttendance.getting_date.set()
    await callback_query.answer()


# 3-QADAM: Kelish vaqtini olish
@dp.message_handler(state=AdminManualAttendance.getting_date)
async def manual_attendance_get_arrival(message: types.Message, state: FSMContext):
    date_text = message.text.strip().lower()

    if date_text in {"bekor qilish", "bekor", "cancel", "/cancel"}:
        await state.finish()
        await message.answer("Amal bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        await _render_admin_home(message.from_user.id, chat_id=message.chat.id)
        return

    if date_text in {"ok", "bugun uchun", "bugun", "today"}:
        selected_date = datetime.date.today()
    else:
        try:
            selected_date = datetime.datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            date_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            date_kb.row(
                types.KeyboardButton("Bugun uchun", style="success"),
                types.KeyboardButton("Bekor qilish", style="danger"),
            )
            await message.reply(
                "Sana formati noto'g'ri. Iltimos, YYYY-MM-DD formatida kiriting yoki tugmadan foydalaning.",
                reply_markup=date_kb,
            )
            return

    await state.update_data(selected_date=selected_date.isoformat())
    await message.answer(
        "Endi xodimning ishga kelgan vaqtini HH:MM formatida kiriting (masalan, 09:05):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await AdminManualAttendance.getting_arrival.set()


# 4-QADAM: Ketish vaqtini olish
@dp.message_handler(state=AdminManualAttendance.getting_arrival)
async def manual_attendance_get_departure(message: types.Message, state: FSMContext):
    raw_text = message.text.strip()
    if raw_text.lower() in {"/start", "/cancel", "bekor qilish", "bekor"}:
        await _exit_admin_fsm_to_menu(message, state)
        return

    parsed_arrival = _parse_hhmm_input(raw_text)
    if not parsed_arrival:
        await message.reply("Vaqt formati noto'g'ri. Iltimos, `HH:MM` formatida kiriting (masalan, `09:05`).")
        return
    arrival_time = datetime.datetime.strptime(parsed_arrival, "%H:%M").time()

    await state.update_data(arrival_time=arrival_time.isoformat())
    await message.answer(
        "Endi xodimning ishdan ketgan vaqtini `HH:MM` formatida kiriting (masalan, `18:30`).\n\n"
        "Agar xodim hali ketmagan bo'lsa, shunchaki `yoq` deb yozing."
    )
    await AdminManualAttendance.getting_departure.set()


# 5-QADAM: Ma'lumotlarni qayta ishlash va bazaga yozish
@dp.message_handler(state=AdminManualAttendance.getting_departure)
async def manual_attendance_process(message: types.Message, state: FSMContext):
    data = await state.get_data()
    worker_id = data['worker_id']
    selected_date = datetime.date.fromisoformat(data['selected_date'])
    arrival_time = datetime.time.fromisoformat(data['arrival_time'])
    branch_id = data.get("worker_branch_id")
    worker_label = data.get("worker_label") or f"Xodim {worker_id}"
    if not await db.admin_can_access_worker(message.from_user.id, int(worker_id)):
        await state.finish()
        return await message.reply("Bu xodim sizning filialingizga tegishli emas.")

    # Kelish vaqtini to'liq datetime obyektiga aylantiramiz
    arrival_dt = tashkent_tz.localize(datetime.datetime.combine(selected_date, arrival_time))

    departure_dt = None
    total_hours = 0.0

    raw_text = message.text.strip()
    if raw_text.lower() in {"/start", "/cancel", "bekor qilish", "bekor"}:
        await _exit_admin_fsm_to_menu(message, state)
        return

    departure_text = raw_text.lower()
    if departure_text != 'yoq':
        try:
            normalized_departure = _parse_hhmm_input(departure_text)
            if not normalized_departure:
                raise ValueError("bad time")
            departure_time = datetime.datetime.strptime(normalized_departure, "%H:%M").time()
            if departure_time <= arrival_time:
                await message.reply("Ketish vaqti kelish vaqtidan keyin bo'lishi kerak!")
                return

            departure_dt = tashkent_tz.localize(datetime.datetime.combine(selected_date, departure_time))
            total_hours = round((departure_dt - arrival_dt).total_seconds() / 3600, 2)
        except ValueError:
            await message.reply("Vaqt formati noto'g'ri. `HH:MM` formatida kiriting yoki `yoq` deb yozing.")
            return

    async with db.pool.acquire() as conn:
        worker_info = await conn.fetchrow(
            "SELECT w.full_name, w.daily_work_hours, w.branch_id, b.name AS branch_name "
            "FROM workers w LEFT JOIN branches b ON b.id = w.branch_id WHERE w.id = $1",
            worker_id,
        )

        # Bu yerda biz ON CONFLICT dan foydalanamiz.
        # Agar shu xodim uchun shu sanada yozuv bo'lsa, uni YANGILAYDI.
        # Aks holda, YANGI yozuv qo'shadi. Bu juda qulay.
        await conn.execute("""
                            INSERT INTO work_sessions (user_id, date, arrival_time, departure_time, total_hours,
                                                       session_daily_hours, branch_id)
                            VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (user_id, date) DO
                            UPDATE SET
                                arrival_time = EXCLUDED.arrival_time,
                                departure_time = EXCLUDED.departure_time,
                                total_hours = EXCLUDED.total_hours,
                                session_daily_hours = EXCLUDED.session_daily_hours,
                                branch_id = COALESCE(work_sessions.branch_id, EXCLUDED.branch_id);
                            """, worker_id, selected_date, arrival_dt, departure_dt, total_hours,
                            worker_info['daily_work_hours'] or 0.0,
                            branch_id or worker_info['branch_id'])

    response_text = (
        f"✅ Bajarildi!\n\n"
        f"👤 Xodim: {worker_label}\n"
        f"📅 Sana: {selected_date.strftime('%Y-%m-%d')}\n"
        f"➡️ Keldi: {arrival_time.strftime('%H:%M')}\n"
    )

    if departure_dt:
        response_text += f"⬅️ Ketdi: {departure_dt.strftime('%H:%M')}\n"
        response_text += f"⏱ Jami: {format_hours(total_hours)}"
    else:
        response_text += "⬅️ Ketdi: (Hali ketmadi)"

    await message.answer(response_text)
    await state.finish()


AI_ATTENDANCE_ACTION_TITLES = {
    "in": "ishga keldi",
    "out": "ishdan ketdi",
    "rest": "dam oldi",
    "study_leave": "o'qishga ketdi",
    "study_return": "o'qishdan qaytdi",
}


def _build_quick_attendance_prompt_text(action: str) -> str:
    action_title = AI_ATTENDANCE_ACTION_TITLES.get(action, action)
    return (
        f"Qaysi xodimni <b>{action_title}</b> deb belgilaymiz?\n\n"
        f"Xodim ismini yozing."
    )


def _build_quick_attendance_prompt_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main", style="primary"))
    return kb


def _build_quick_attendance_done_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⬅️ Admin menyusi", callback_data="back_admin_main", style="primary"))
    return kb


def _normalize_admin_attendance_text(text: str) -> str:
    normalized = to_latin(text or "").lower()
    for ch in ("’", "`", "ʻ", "ʼ", "‘", "´"):
        normalized = normalized.replace(ch, "'")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _clean_worker_name_candidate(raw: str) -> str:
    stop_words = {
        "deb",
        "belgila",
        "belgilang",
        "qilib",
        "qilin",
        "qoy",
        "qoying",
        "hozir",
        "iltimos",
        "bugun",
        "admin",
        "xodim",
        "xodimni",
        "xodimga",
        "ishga",
        "ishdan",
        "keldi",
        "ketdi",
        "dam",
        "oldi",
        "oqishga",
        "oqishda",
        "oqishdan",
        "qaytdi",
        "darsda",
    }
    cleaned = re.sub(r"[^a-z0-9\s'\-]", " ", raw.lower())
    parts = [part for part in cleaned.split() if part not in stop_words]
    candidate = " ".join(parts).strip(" '-")
    if candidate in {"", "kim", "kimlar", "qaysi", "qaysilar"}:
        return ""
    return candidate


def _parse_admin_attendance_intent(text: str) -> tuple[str, str] | None:
    norm = _normalize_admin_attendance_text(text)
    patterns: list[tuple[str, list[str]]] = [
        ("study_return", ["o'qishdan qaytdi", "oqishdan qaytdi", "darsdan qaytdi"]),
        ("study_leave", ["o'qishga ketdi", "oqishga ketdi", "darsga ketdi", "o'qishda", "oqishda", "darsda"]),
        ("rest", ["dam oldi", "bugun dam", "damga chiqdi", "dam"]),
        ("out", ["ishdan ketdi", "chiqib ketdi", "ketib qoldi", "ketdi"]),
        ("in", ["ishga keldi", "keldi"]),
    ]

    for action, phrases in patterns:
        for phrase in phrases:
            if phrase not in norm:
                continue
            before, _, after = norm.partition(phrase)
            raw_name = before.strip() or after.strip()
            name_query = _clean_worker_name_candidate(raw_name)
            if name_query and len(name_query) >= 2:
                return action, name_query
    return None


async def _try_handle_ai_attendance_command(
    message: types.Message,
    processing_message: types.Message,
) -> bool:
    parsed = _parse_admin_attendance_intent(message.text or "")
    if not parsed:
        return False

    action, name_query = parsed
    candidates = await db.find_worker_candidates_for_admin(message.from_user.id, name_query, limit=8)
    action_title = AI_ATTENDANCE_ACTION_TITLES.get(action, action)

    if not candidates:
        await processing_message.edit_text(
            f"❌ '{name_query}' bo'yicha xodim topilmadi. Ismni aniqroq yozib qayta urinib ko'ring."
        )
        return True

    if len(candidates) == 1:
        worker = candidates[0]
        ok, action_text, worker_obj = await apply_worker_action_for_admin(
            int(worker["id"]),
            action,
            message.from_user.id,
            message.from_user.full_name,
        )
        if not ok:
            await processing_message.edit_text(f"❌ {action_text}")
            return True
        await processing_message.edit_text(
            f"✅ AI orqali saqlandi: {_format_worker_branch_label(worker_obj)} {action_text}."
        )
        return True

    selection_style = "primary"
    if action in {"in", "study_return"}:
        selection_style = "success"
    elif action in {"out", "rest"}:
        selection_style = "danger"

    kb = InlineKeyboardMarkup(row_width=1)
    for worker in candidates:
        phone_status = "📱" if worker.get("has_phone") else "📴"
        kb.add(
            InlineKeyboardButton(
                f"{phone_status} {_format_worker_branch_label(worker)} (ID:{worker['id']})",
                callback_data=f"aiatt:{action}:{worker['id']}",
                style=selection_style,
            )
        )
    kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data="aiatt:cancel", style="danger"))

    await processing_message.edit_text(
        f"🔎 Bir nechta o'xshash ism topildi.\nQaysi xodimni <b>{action_title}</b> deb belgilaymiz?",
        reply_markup=kb,
        parse_mode="HTML",
    )
    return True


@dp.callback_query_handler(lambda c: c.data.startswith("aiatt:"), state="*")
async def ai_attendance_disambiguation_callback(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("Ruxsat yo'q", show_alert=True)

    if callback_query.data == "aiatt:cancel":
        await callback_query.message.edit_text("Amal bekor qilindi.")
        return await callback_query.answer()

    try:
        _, action, worker_id_raw = callback_query.data.split(":")
        worker_id = int(worker_id_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri tanlov.", show_alert=True)
    if not await _ensure_worker_access_callback(callback_query, worker_id):
        return

    ok, action_text, worker = await apply_worker_action_for_admin(
        worker_id,
        action,
        callback_query.from_user.id,
        callback_query.from_user.full_name,
    )
    if not ok:
        return await callback_query.answer(action_text, show_alert=True)

    await callback_query.message.edit_text(
        f"✅ AI orqali saqlandi: {_format_worker_branch_label(worker)} {action_text}."
    )
    await callback_query.answer("Saqlandi.")


@dp.message_handler(
    lambda message: message.chat.type == "private"
    and message.from_user.id in ADMINS
    and message.text
    and not message.text.startswith('/'),
    content_types=types.ContentTypes.TEXT, state=None)
async def admin_natural_language_query(message: types.Message, state: FSMContext):
    await state.finish()
    processing_message = await message.answer("🤔 So'rovingiz tahlil qilinmoqda...")
    try:
        if message.from_user.id in SUPERADMINS and not await db.get_superadmin_selected_branch_id(message.from_user.id):
            await _render_superadmin_branch_selector(
                message.from_user.id,
                chat_id=processing_message.chat.id,
                message_id=processing_message.message_id,
                back_callback="back_admin_main",
            )
            return

        handled = await _try_handle_ai_attendance_command(message, processing_message)
        if handled:
            return

        # Qolgan so'rovlar uchun umumiy AI tool-flow ishlaydi.
        final_answer = await process_admin_request_with_tools(message.text, admin_tg_id=message.from_user.id)
        await processing_message.edit_text(final_answer)
    except Exception as e:
        logging.error(f"admin_natural_language_query da kutilmagan xato: {e}")
        await processing_message.edit_text("❌ So'rovingizni qayta ishlashda kutilmagan xatolik yuz berdi.")


# =========================================================================
# ADMIN BOSHQARUVI
# =========================================================================


def _format_branch_admin_display(admin_row: dict) -> str:
    worker_name = (admin_row.get("worker_name") or "").strip()
    username = (admin_row.get("worker_username") or "").strip()
    label = worker_name if worker_name else f"TG:{admin_row['tg_id']}"
    if username:
        label += f" (@{username})"
    return label


async def _render_superadmin_list(
    message_obj,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
):
    admins = await db.list_telegram_superadmins()
    lines = ["👑 <b>Katta adminlar</b>", ""]
    if not admins:
        lines.append("Hozircha katta admin topilmadi.")
    else:
        for row in admins:
            source_label = "config" if row.get("source") == "config" else "manual"
            lines.append(
                f"• {_format_branch_admin_display(row)} | <code>{row['tg_id']}</code> | {source_label}"
            )

    if any((row.get("source") or "") == "config" for row in admins):
        lines.extend(["", "Config bilan qo'shilgan katta adminlarni bot ichidan o'chirib bo'lmaydi."])

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ Katta admin qo'shish", callback_data="superadmins:add", style="success"))
    for row in admins:
        if row.get("source") != "manual":
            continue
        kb.add(
            InlineKeyboardButton(
                f"🗑 {_format_branch_admin_display(row)}",
                callback_data=f"superadmins:askremove:{row['tg_id']}",
                style="danger",
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_extra", style="primary"))

    final_text = "\n".join(lines)
    if message_obj is not None:
        await safe_edit_text(message_obj, final_text, reply_markup=kb, parse_mode="HTML")
        return

    await _edit_admin_message_or_send(
        chat_id,
        message_id,
        final_text,
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data == "superadmins:menu")
async def superadmins_menu(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    await _render_superadmin_list(callback_query.message)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data == "superadmins:add")
async def superadmins_add_start(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return
    await state.update_data(
        superadmin_origin_chat_id=callback_query.message.chat.id,
        superadmin_origin_message_id=callback_query.message.message_id,
    )
    await AdminSuperadminSettings.waiting_for_superadmin_tg_id.set()
    await callback_query.message.edit_text(
        "Yangi katta admin uchun Telegram ID yuboring.\n\nBekor qilish uchun <code>bekor</code> deb yozing.",
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.message_handler(state=AdminSuperadminSettings.waiting_for_superadmin_tg_id, content_types=types.ContentTypes.TEXT)
async def superadmins_add_finish(message: types.Message, state: FSMContext):
    if not await _ensure_superadmin_message(message, state):
        return

    raw_value = (message.text or "").strip()
    data = await state.get_data()
    origin_chat_id = data.get("superadmin_origin_chat_id")
    origin_message_id = data.get("superadmin_origin_message_id")

    if raw_value.lower() in {"bekor", "bekor qilish", "cancel", "/cancel", "orqaga"}:
        await state.finish()
        await message.reply("Katta admin qo'shish bekor qilindi.")
        await _render_superadmin_list(
            None,
            chat_id=origin_chat_id or message.chat.id,
            message_id=origin_message_id,
        )
        return

    try:
        tg_id = int(raw_value)
    except ValueError:
        return await message.reply("Telegram ID raqam bo'lishi kerak.")
    if tg_id <= 0:
        return await message.reply("Telegram ID musbat raqam bo'lishi kerak.")

    if tg_id in SUPERADMINS:
        await state.finish()
        await message.reply("Bu foydalanuvchi allaqachon katta admin.")
        await _render_superadmin_list(
            None,
            chat_id=origin_chat_id or message.chat.id,
            message_id=origin_message_id,
        )
        return

    assignments = await db.get_branch_admin_assignments(tg_id)
    if assignments:
        assigned_names = ", ".join(item["name"] for item in assignments)
        return await message.reply(
            f"Bu TG ID hozir filial adminiga biriktirilgan: {assigned_names}.\n"
            "Katta admin qilishdan oldin uni filial adminlar bo'limidan oling."
        )

    worker_record = await db.get_worker_by_tg_id(tg_id)
    worker_label = worker_record["full_name"] if worker_record else f"TG ID {tg_id}"
    if worker_record and worker_record.get("branch_name"):
        worker_label += f" [{worker_record['branch_name']}]"

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            "✅ Tasdiqlash",
            callback_data=f"superadmins:confirmadd:{tg_id}",
            style="success",
        ),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="superadmins:menu", style="primary"),
    )
    await state.finish()
    await message.reply(
        f"Quyidagi foydalanuvchini katta admin qilamizmi?\n\n{worker_label}\n<code>{tg_id}</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data.startswith("superadmins:confirmadd:"))
async def superadmins_confirm_add(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        tg_id = int(callback_query.data.split(":")[2])
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri TG ID.", show_alert=True)

    if tg_id in SUPERADMINS:
        return await callback_query.answer("Bu foydalanuvchi allaqachon katta admin.", show_alert=True)

    assignments = await db.get_branch_admin_assignments(tg_id)
    if assignments:
        assigned_names = ", ".join(item["name"] for item in assignments)
        return await callback_query.answer(
            f"Bu TG ID filial admini bo'lib turibdi: {assigned_names}.",
            show_alert=True,
        )

    success = await db.assign_telegram_superadmin(tg_id, source="manual")
    if not success:
        return await callback_query.answer("Katta adminni saqlab bo'lmadi.", show_alert=True)

    worker_record = await db.get_worker_by_tg_id(tg_id)
    target_label = worker_record["full_name"] if worker_record else str(tg_id)
    try:
        await bot.send_message(
            tg_id,
            "Siz botda katta admin sifatida belgilandingiz.\n/start bosib menyuni yangilang.",
        )
    except Exception as exc:
        logging.warning("Yangi katta adminga xabar yuborilmadi: %s", exc)

    await _render_superadmin_list(callback_query.message)
    await callback_query.answer(f"{target_label} katta admin qilindi.")


@dp.callback_query_handler(lambda c: c.data.startswith("superadmins:askremove:"))
async def superadmins_ask_remove(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        tg_id = int(callback_query.data.split(":")[2])
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri TG ID.", show_alert=True)

    admin_row = next((row for row in await db.list_telegram_superadmins() if int(row["tg_id"]) == tg_id), None)
    if not admin_row:
        return await callback_query.answer("Katta admin topilmadi.", show_alert=True)
    if admin_row.get("source") != "manual":
        return await callback_query.answer(
            "Bu katta admin config orqali berilgan. Uni env yoki server sozlamasidan o'zgartiring.",
            show_alert=True,
        )
    if tg_id == callback_query.from_user.id:
        return await callback_query.answer("O'zingizni bu yerda katta admindan tushira olmaysiz.", show_alert=True)
    if len(set(SUPERADMINS)) <= 1:
        return await callback_query.answer("Tizimda kamida bitta katta admin qolishi shart.", show_alert=True)

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            "🗑 Ha, olib tashlash",
            callback_data=f"superadmins:remove:{tg_id}",
            style="danger",
        ),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="superadmins:menu", style="primary"),
    )
    await callback_query.message.edit_text(
        f"Haqiqatan ham {_format_branch_admin_display(admin_row)} ni katta admindan olamizmi?\n"
        "Bu amal darhol kuchga kiradi.",
        reply_markup=kb,
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("superadmins:remove:"))
async def superadmins_remove(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        tg_id = int(callback_query.data.split(":")[2])
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri TG ID.", show_alert=True)

    admin_row = next((row for row in await db.list_telegram_superadmins() if int(row["tg_id"]) == tg_id), None)
    if not admin_row:
        return await callback_query.answer("Katta admin topilmadi.", show_alert=True)
    if admin_row.get("source") != "manual":
        return await callback_query.answer(
            "Bu katta admin config orqali berilgan. Uni env yoki server sozlamasidan o'zgartiring.",
            show_alert=True,
        )
    if tg_id == callback_query.from_user.id:
        return await callback_query.answer("O'zingizni bu yerda katta admindan tushira olmaysiz.", show_alert=True)
    if len(set(SUPERADMINS)) <= 1:
        return await callback_query.answer("Tizimda kamida bitta katta admin qolishi shart.", show_alert=True)

    removed = await db.remove_telegram_superadmin(tg_id)
    if not removed:
        return await callback_query.answer("Katta adminni olib tashlab bo'lmadi.", show_alert=True)

    try:
        await bot.send_message(
            tg_id,
            "Sizning botdagi katta admin huquqingiz olib tashlandi.",
        )
    except Exception as exc:
        logging.warning("Olib tashlangan katta adminga xabar yuborilmadi: %s", exc)

    await _render_superadmin_list(callback_query.message)
    await callback_query.answer("Katta admin olib tashlandi.")


async def _render_branch_admin_branch_view(
    message_obj,
    branch_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
):
    branch = await db.get_branch_by_id(branch_id)
    branch_name = branch["name"] if branch else f"Filial #{branch_id}"
    admins = await db.list_branch_admins(branch_id)

    lines = [f"🏢 <b>{branch_name}</b> filial adminlari", ""]
    if not admins:
        lines.append("Hozircha bu filialga admin biriktirilmagan.")
    else:
        for row in admins:
            source_label = "config" if row.get("source") == "config" else "manual"
            lines.append(
                f"• {_format_branch_admin_display(row)} | <code>{row['tg_id']}</code> | {source_label}"
            )

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ Admin qo'shish", callback_data=f"branch_admins:add:{branch_id}", style="success"))
    for row in admins:
        delete_label = f"🗑 {_format_branch_admin_display(row)}"
        if row.get("source") == "config":
            delete_label += " (config)"
        kb.add(
            InlineKeyboardButton(
                delete_label,
                callback_data=f"branch_admins:remove:{branch_id}:{row['tg_id']}",
                style="danger",
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Filiallar", callback_data="branch_admins:menu", style="primary"))

    final_text = "\n".join(lines)
    if message_obj is not None:
        await safe_edit_text(message_obj, final_text, reply_markup=kb, parse_mode="HTML")
        return
    await _edit_admin_message_or_send(
        chat_id,
        message_id,
        final_text,
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query_handler(lambda c: c.data == "branch_admins:menu")
async def branch_admins_menu(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return

    branches = await db.get_active_branches()
    branch_admin_rows = await db.list_branch_admins()
    counts = {}
    for row in branch_admin_rows:
        counts[row["branch_id"]] = counts.get(row["branch_id"], 0) + 1

    kb = InlineKeyboardMarkup(row_width=1)
    for branch in branches:
        count = counts.get(branch["id"], 0)
        kb.add(
            InlineKeyboardButton(
                f"{branch['name']} ({count} admin)",
                callback_data=f"branch_admins:view:{branch['id']}",
                style="primary",
            )
        )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_extra", style="primary"))

    await callback_query.message.edit_text(
        "Qaysi filial adminlarini boshqarmoqchisiz?",
        reply_markup=kb,
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("branch_admins:view:"))
async def branch_admins_view(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        branch_id = int(callback_query.data.split(":")[2])
    except (IndexError, ValueError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    await _render_branch_admin_branch_view(callback_query.message, branch_id)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("branch_admins:add:"))
async def branch_admins_add_start(callback_query: types.CallbackQuery, state: FSMContext):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        branch_id = int(callback_query.data.split(":")[2])
    except (IndexError, ValueError):
        return await callback_query.answer("Noto'g'ri filial.", show_alert=True)

    branch = await db.get_branch_by_id(branch_id)
    if not branch:
        return await callback_query.answer("Filial topilmadi.", show_alert=True)

    await state.update_data(
        branch_admin_target_branch_id=branch_id,
        branch_admin_origin_chat_id=callback_query.message.chat.id,
        branch_admin_origin_message_id=callback_query.message.message_id,
    )
    await AdminBranchAdminSettings.waiting_for_admin_tg_id.set()
    await callback_query.message.edit_text(
        f"<b>{branch['name']}</b> uchun yangi filial adminining Telegram ID sini yuboring.\n\n"
        "Bekor qilish uchun <code>bekor</code> deb yozing.",
        parse_mode="HTML",
    )
    await callback_query.answer()


@dp.message_handler(state=AdminBranchAdminSettings.waiting_for_admin_tg_id, content_types=types.ContentTypes.TEXT)
async def branch_admins_add_finish(message: types.Message, state: FSMContext):
    if not await _ensure_superadmin_message(message, state):
        return

    raw_value = (message.text or "").strip()
    data = await state.get_data()
    branch_id = data.get("branch_admin_target_branch_id")
    origin_chat_id = data.get("branch_admin_origin_chat_id")
    origin_message_id = data.get("branch_admin_origin_message_id")

    if raw_value.lower() in {"bekor", "bekor qilish", "cancel", "/cancel", "orqaga"}:
        await state.finish()
        await message.reply("Filial adminini qo'shish bekor qilindi.")
        if branch_id:
            await _render_branch_admin_branch_view(
                None,
                int(branch_id),
                chat_id=origin_chat_id or message.chat.id,
                message_id=origin_message_id,
            )
        return

    try:
        tg_id = int(raw_value)
    except ValueError:
        return await message.reply("Telegram ID raqam bo'lishi kerak.")
    if tg_id <= 0:
        return await message.reply("Telegram ID musbat raqam bo'lishi kerak.")

    if not branch_id:
        await state.finish()
        return await message.reply("Filial ma'lumoti topilmadi.")

    branch = await db.get_branch_by_id(int(branch_id))
    if not branch:
        await state.finish()
        return await message.reply("Filial topilmadi.")

    if tg_id in SUPERADMINS:
        return await message.reply("Bu foydalanuvchi allaqachon katta admin. Uni filial admin sifatida biriktirish shart emas.")

    worker_record = await db.get_worker_by_tg_id(tg_id)
    if worker_record:
        worker_branch_id = worker_record.get("branch_id")
        worker_branch_name = worker_record.get("branch_name") or "Belgilanmagan"
        if not worker_branch_id:
            return await message.reply(
                "Bu TG ID xodim sifatida bazada bor, lekin unga hali filial biriktirilmagan.\n"
                "Avval xodimni to'g'ri filialga biriktiring, keyin admin qiling."
            )
        if int(worker_branch_id) != int(branch_id):
            return await message.reply(
                f"Bu TG ID {html.escape(str(worker_record['full_name']))} xodimiga tegishli va hozir <b>{html.escape(str(worker_branch_name))}</b> filialida.\n"
                "Xodim filiali bilan admin filiali bir xil bo'lishi kerak.",
                parse_mode="HTML",
            )

    assignments = await db.get_branch_admin_assignments(tg_id)
    if assignments:
        assigned_branch_ids = {item["id"] for item in assignments}
        if int(branch_id) in assigned_branch_ids:
            await state.finish()
            await message.reply("Bu tg_id allaqachon shu filialga biriktirilgan.")
            return await _render_branch_admin_branch_view(
                None,
                int(branch_id),
                chat_id=origin_chat_id or message.chat.id,
                message_id=origin_message_id,
            )

        assigned_names = ", ".join(item["name"] for item in assignments)
        return await message.reply(
            f"Bu tg_id allaqachon boshqa filialga biriktirilgan: {assigned_names}.\n"
            "Avval o'sha filialdan olib tashlang, keyin bu yerga qo'shing."
        )

    await db.assign_branch_admin(int(branch_id), tg_id, source="manual")
    await state.finish()
    await message.reply(
        f"✅ <b>{branch['name']}</b> filialiga yangi admin biriktirildi: <code>{tg_id}</code>",
        parse_mode="HTML",
    )
    try:
        await bot.send_message(
            tg_id,
            f"Siz {branch['name']} filialiga admin sifatida biriktirildingiz.\n"
            "Botda /start bosib ishni davom ettirishingiz mumkin.",
        )
    except Exception as exc:
        logging.warning("Yangi filial adminiga xabar yuborilmadi: %s", exc)

    await _render_branch_admin_branch_view(
        None,
        int(branch_id),
        chat_id=origin_chat_id or message.chat.id,
        message_id=origin_message_id,
    )


@dp.callback_query_handler(lambda c: c.data.startswith("branch_admins:remove:"))
async def branch_admins_remove(callback_query: types.CallbackQuery):
    if not await _ensure_superadmin_callback(callback_query):
        return
    try:
        _, _, _, branch_id_raw, tg_id_raw = callback_query.data.split(":")
        branch_id = int(branch_id_raw)
        tg_id = int(tg_id_raw)
    except (ValueError, IndexError):
        return await callback_query.answer("Noto'g'ri admin.", show_alert=True)

    admins = await db.list_branch_admins(branch_id)
    admin_row = next((row for row in admins if int(row["tg_id"]) == tg_id), None)
    if not admin_row:
        return await callback_query.answer("Admin topilmadi.", show_alert=True)

    if admin_row.get("source") == "config":
        return await callback_query.answer(
            "Bu admin config orqali biriktirilgan. Uni env yoki BRANCHES_JSON dan o'zgartiring.",
            show_alert=True,
        )

    removed = await db.remove_branch_admin(branch_id, tg_id)
    if not removed:
        return await callback_query.answer("Adminni olib tashlab bo'lmadi.", show_alert=True)

    branch = await db.get_branch_by_id(branch_id)
    branch_name = branch["name"] if branch else f"Filial #{branch_id}"
    try:
        await bot.send_message(
            tg_id,
            f"Sizning {branch_name} filialidagi admin huquqingiz olib tashlandi.",
        )
    except Exception as exc:
        logging.warning("Olib tashlangan filial adminiga xabar yuborilmadi: %s", exc)

    await _render_branch_admin_branch_view(callback_query.message, branch_id)
    await callback_query.answer("Filial admini olib tashlandi.")
