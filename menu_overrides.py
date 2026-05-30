from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUPERADMINS


def get_admin_main_menu(admin_tg_id: int | None = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("Xodimlar", callback_data="admin_workers"),
        types.InlineKeyboardButton("Kunlik hisobot", callback_data="admin_daily_report"),
    )
    kb.row(
        types.InlineKeyboardButton("Oylik hisobot", callback_data="admin_monthly_report"),
        types.InlineKeyboardButton("Maoshlar", callback_data="salary_tree"),
    )
    kb.row(
        types.InlineKeyboardButton("Bot foydalanuvchilari", callback_data="stats_usage"),
        types.InlineKeyboardButton("Ish soati vaqtlari", callback_data="work_hours_menu"),
    )
    kb.row(
        types.InlineKeyboardButton("Davomat", callback_data="attendance_workers:0"),
        types.InlineKeyboardButton("Xodim qo'shish", callback_data="admin_add_worker"),
    )
    if admin_tg_id in SUPERADMINS:
        kb.row(
            types.InlineKeyboardButton("Filialni almashtirish", callback_data="superbranch:menu"),
            types.InlineKeyboardButton("Adminlar", callback_data="admin_extra"),
        )
    return kb


def get_superadmin_extra_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("Katta adminlar", callback_data="superadmins:menu"),
        InlineKeyboardButton("Filial adminlari", callback_data="branch_admins:menu"),
        InlineKeyboardButton("Orqaga", callback_data="back_admin_main"),
    )
    return kb
