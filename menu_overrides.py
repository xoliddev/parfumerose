from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUPERADMINS


def get_admin_main_menu(admin_tg_id: int | None = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("Xodimlar", callback_data="admin_workers", style="primary"),
        types.InlineKeyboardButton("Kunlik hisobot", callback_data="admin_daily_report", style="primary"),
    )
    kb.row(
        types.InlineKeyboardButton("Oylik hisobot", callback_data="admin_monthly_report", style="primary"),
        types.InlineKeyboardButton("Maoshlar", callback_data="salary_tree", style="primary"),
    )
    kb.row(
        types.InlineKeyboardButton("💵 Tezkor to'lov", callback_data="qpay_menu", style="success"),
    )
    kb.row(
        types.InlineKeyboardButton("Bot foydalanuvchilari", callback_data="stats_usage", style="primary"),
        types.InlineKeyboardButton("Ish soati vaqtlari", callback_data="work_hours_menu", style="primary"),
    )
    kb.row(
        types.InlineKeyboardButton("Davomat", callback_data="attendance_workers:0", style="success"),
        types.InlineKeyboardButton("Xodim qo'shish", callback_data="admin_add_worker", style="success"),
    )
    kb.row(
        types.InlineKeyboardButton("📞 Admin aloqasi", callback_data="admin_contact:menu", style="primary"),
        types.InlineKeyboardButton("📢 Bildirishnoma guruhi", callback_data="workgroup:menu", style="primary"),
    )
    if admin_tg_id in SUPERADMINS:
        kb.row(
            types.InlineKeyboardButton("Filialni almashtirish", callback_data="superbranch:menu", style="primary"),
            types.InlineKeyboardButton("Adminlar", callback_data="admin_extra", style="primary"),
        )
    return kb


def get_superadmin_extra_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("Katta adminlar", callback_data="superadmins:menu", style="primary"),
        InlineKeyboardButton("Filial adminlari", callback_data="branch_admins:menu", style="primary"),
        InlineKeyboardButton("Orqaga", callback_data="back_admin_main", style="primary"),
    )
    return kb
