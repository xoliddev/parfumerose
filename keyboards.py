# keyboards.py (To'liq yangilangan versiya)

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from menu_overrides import (
    get_admin_main_menu as get_overridden_admin_main_menu,
    get_superadmin_extra_menu as get_overridden_admin_extra_menu,
)

# =========================================================================
# ASOSIY MENYULAR
# =========================================================================
PER_PAGE = 9


def get_admin_main_menu(admin_tg_id: int | None = None) -> types.InlineKeyboardMarkup:
    return get_overridden_admin_main_menu(admin_tg_id)


def get_admin_extra_menu() -> types.InlineKeyboardMarkup:
    return get_overridden_admin_extra_menu()


def get_weekday_select_menu() -> types.InlineKeyboardMarkup:
    from database import WEEKDAYS_UZ
    kb = types.InlineKeyboardMarkup(row_width=2)
    for i, name in enumerate(WEEKDAYS_UZ):
        kb.add(types.InlineKeyboardButton(name, callback_data=f"rest_select_{i}"))
    kb.add(types.InlineKeyboardButton("❌ Dam olish kunisiz", callback_data="rest_select_none"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_extra"))
    return kb


# =========================================================================
# FOYDALANUVCHI UCHUN STATISTIKA
# =========================================================================

def make_mystats_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    for y in years:
        kb.insert(InlineKeyboardButton(text=str(y), callback_data=f"mystats:year:{y}"))
    return kb


def make_mystats_months_keyboard(months: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    for m_num, m_name in months:
        kb.insert(InlineKeyboardButton(
            text=m_name,
            callback_data=f"mystats:month:{m_num}"
        ))
    return kb
