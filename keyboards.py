# keyboards.py (To'liq yangilangan versiya)

from typing import List, Tuple, Optional
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import datetime
from aiogram.utils.callback_data import CallbackData
import calendar

from shared import build_paginated_inline
from menu_overrides import (
    get_admin_main_menu as get_overridden_admin_main_menu,
    get_superadmin_extra_menu as get_overridden_admin_extra_menu,
    get_web_login_menu as get_overridden_web_login_menu,
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


# =========================================================================
# BUHGALTERIYA UCHUN K LAVIATURALAR
# =========================================================================

# --- Umumiy va yordamchi klaviaturalar ---

def get_back_button(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Orqaga", callback_data=callback_data))


def get_confirm_deletion_keyboard(callback_prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("✅ Ha, o'chirish", callback_data=f"{callback_prefix}:confirm_delete"),
        InlineKeyboardButton("❌ Yo'q, orqaga", callback_data=f"{callback_prefix}:cancel_delete")
    )


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Bekor qilish", callback_data="acc:cancel_fsm"))


# --- Bosh menyular ---

def get_accounting_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("📋 Buyurtmalar", callback_data="acc:orders:main"),
        InlineKeyboardButton("📦 Materiallar", callback_data="acc:materials:main"),
        InlineKeyboardButton("📊 Hisob-kitob", callback_data="acc:calc:main"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin_main")
    )


def get_orders_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("➕ Yangi buyurtma", callback_data="acc:order:add"),
        InlineKeyboardButton("📋 Barcha buyurtmalar", callback_data="acc:orders:list:0"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:main")
    )


# --- Materiallar Menyusi (O'ZGARTIRILGAN) ---
def get_materials_menu() -> InlineKeyboardMarkup:
    """Endi bu menyuda Kategoriyalar uchun alohida tugma bor."""
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("🗂️ Kategoriyalar", callback_data="acc:category:browse:root"),  # Boshlang'ich kategoriya
        InlineKeyboardButton("🔍 Material qidirish", callback_data="acc:material:search_start"),
        InlineKeyboardButton("➕ Yangi material (kategoriyasiz)", callback_data="acc:material:add:root"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:main")
    )


# --- Kategoriyalar uchun YANGI klaviaturalar ---

def get_material_category_browser(path: List[dict], sub_categories: List[dict],
                                  materials: List[dict]) -> InlineKeyboardMarkup:
    """Ichma-ich kategoriyalarni va materiallarni ko'rsatadigan universal klaviatura."""
    kb = InlineKeyboardMarkup(row_width=2)

    # 1. Hozirgi joylashuvni ("breadcrumb") ko'rsatamiz
    path_str = " > ".join([p['name'] for p in path]) if path else "Bosh kategoriya"
    kb.add(InlineKeyboardButton(f"📍 {path_str}", callback_data="acc:ignore"))

    # 2. Ichki kategoriyalarni qo'shamiz
    for cat in sub_categories:
        kb.insert(InlineKeyboardButton(f"🗂️ {cat['name']}", callback_data=f"acc:category:browse:{cat['id']}"))

    # 3. Shu kategoriyadagi materiallarni qo'shamiz
    for mat in materials:
        kb.insert(InlineKeyboardButton(f"📦 {mat['name']}", callback_data=f"acc:material:{mat['id']}:view"))

    current_category_id = path[-1]['id'] if path else "root"

    # 4. Amallar tugmalarini qo'shamiz
    action_buttons = [
        InlineKeyboardButton("➕ Kategoriya qo'shish", callback_data=f"acc:category:add:{current_category_id}"),
        InlineKeyboardButton("➕ Material qo'shish", callback_data=f"acc:material:add:{current_category_id}"),
    ]

    # Faqat 'root'da bo'lmaganda "Tahrirlash" tugmasini ko'rsatamiz
    if current_category_id != "root":
        action_buttons.append(
            InlineKeyboardButton("✏️ Joriy kategoriyani tahrirlash",
                                 callback_data=f"acc:category:edit_menu:{current_category_id}")
        )

    kb.row(*action_buttons)

    # 5. "Orqaga" tugmasi
    if not path:  # Agar bosh sahifada bo'lsak
        back_cb = "acc:materials:main"
    else:
        parent_id = path[-2]['id'] if len(path) > 1 else "root"
        back_cb = f"acc:category:browse:{parent_id}"

    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb))
    return kb


def get_category_edit_menu(category_id: int) -> InlineKeyboardMarkup:
    """Kategoriyani tahrirlash yoki o'chirish uchun menyu."""
    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("✏️ Nomini o'zgartirish", callback_data=f"acc:category:edit_name:{category_id}"),
        InlineKeyboardButton("🗑 O'chirish", callback_data=f"acc:category:delete_prompt:{category_id}"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:category:browse:{category_id}")
    )


# --- Materiallar bilan ishlash uchun klaviaturalar ---

def get_material_details_menu(material_id: int, category_id: Optional[int]) -> InlineKeyboardMarkup:
    # `category_id` None bo'lsa, bu "root" degani
    parent_category_id = category_id if category_id is not None else "root"

    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"acc:material:{material_id}:edit"),
        InlineKeyboardButton("🗑 O'chirish", callback_data=f"acc:material:{material_id}:delete"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:category:browse:{parent_category_id}")
    )


def get_material_edit_menu(material_id: int) -> InlineKeyboardMarkup:
    """Materialni tahrirlash uchun menyuni yaratadi."""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Nomini tahrirlash", callback_data=f"acc:material:{material_id}:edit_name"),
        InlineKeyboardButton("🖼️ Rasmni o'zgartirish", callback_data=f"acc:material:{material_id}:edit_image"),
        InlineKeyboardButton("📏 Birligini tahrirlash", callback_data=f"acc:material:{material_id}:edit_unit"),
        InlineKeyboardButton("💰 Narxini tahrirlash", callback_data=f"acc:material:{material_id}:edit_cost"),
        InlineKeyboardButton("🗂️ Kategoriyasini o'zgartirish",
                             callback_data=f"acc:material:{material_id}:edit_category")
    )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:material:{material_id}:view"))
    return kb


# --- Buyurtmalar uchun klaviaturalar ---
def get_order_details_menu(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Material qo'shish", callback_data=f"acc:order:{order_id}:add_mat"),
        InlineKeyboardButton("👤 Xodim biriktirish", callback_data=f"acc:order:{order_id}:assign_worker"),
        InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"acc:order:{order_id}:edit"),
        InlineKeyboardButton("🗑 O'chirish", callback_data=f"acc:order:{order_id}:delete"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:orders:list:0")
    )


def get_order_edit_menu(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Holatini o'zgartirish", callback_data=f"acc:order:{order_id}:change_status"),
        InlineKeyboardButton("📝 Nomini tahrirlash", callback_data=f"acc:order:{order_id}:edit_name"),
        InlineKeyboardButton("👤 Mijozni tahrirlash", callback_data=f"acc:order:{order_id}:edit_client"),
        InlineKeyboardButton("📄 Tavsifni tahrirlash", callback_data=f"acc:order:{order_id}:edit_desc"),
        InlineKeyboardButton("💰 Narxini tahrirlash", callback_data=f"acc:order:{order_id}:edit_price")
    )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:order:{order_id}:view"))
    return kb


def get_status_selection_keyboard(order_id: int) -> InlineKeyboardMarkup:
    statuses = ['Yangi', 'Bajarilmoqda', 'Bajarildi', 'Bekor qilindi']
    kb = InlineKeyboardMarkup(row_width=2)
    for status in statuses:
        kb.insert(InlineKeyboardButton(status, callback_data=f"acc:order:{order_id}:set_status:{status}"))

    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:order:{order_id}:edit"))
    return kb


def get_order_material_edit_menu(entry_id: int, order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔄 Miqdorini o'zgartirish", callback_data=f"acc:ord_mat:{entry_id}:edit_qty"),
        InlineKeyboardButton("🗑 O'chirish", callback_data=f"acc:ord_mat:{entry_id}:delete")
    )
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"acc:order:{order_id}:view"))
    return kb


# --- Kalendar ---
calendar_callback = CallbackData("calendar", "action", "year", "month", "day")


async def create_calendar(year=None, month=None) -> InlineKeyboardMarkup:
    now = datetime.datetime.now()
    if year is None: year = now.year
    if month is None: month = now.month
    kb = InlineKeyboardMarkup(row_width=7)
    kb.add(InlineKeyboardButton(f"{datetime.datetime(year, month, 1).strftime('%B %Y')}",
                                callback_data=calendar_callback.new("IGNORE", year, month, 0)))
    week_days = ["Du", "Se", "Cho", "Pa", "Ju", "Sh", "Ya"]
    kb.add(*[InlineKeyboardButton(text=day, callback_data=calendar_callback.new("IGNORE", year, month, 0)) for day in
             week_days])
    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row_buttons = []
        for day in week:
            if day == 0:
                row_buttons.append(
                    InlineKeyboardButton(text=" ", callback_data=calendar_callback.new("IGNORE", year, month, 0)))
            else:
                row_buttons.append(
                    InlineKeyboardButton(text=str(day), callback_data=calendar_callback.new("DAY", year, month, day)))
        kb.row(*row_buttons)
    prev_month_date = datetime.datetime(year, month, 1) - datetime.timedelta(days=1)
    next_month_date = datetime.datetime(year, month, 1) + datetime.timedelta(days=31)
    kb.add(
        InlineKeyboardButton("< Oldingi", callback_data=calendar_callback.new("PREV-MONTH", prev_month_date.year,
                                                                              prev_month_date.month, 0)),
        InlineKeyboardButton("Keyingi >", callback_data=calendar_callback.new("NEXT-MONTH", next_month_date.year,
                                                                              next_month_date.month, 0))
    )
    return kb


# --- Moliyaviy hisobotlar ---
def make_financial_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    for y in years:
        kb.insert(InlineKeyboardButton(text=str(y), callback_data=f"acc:calc:year:{y}"))
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:main"))
    return kb


def make_financial_months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    OY_NOMI_LOCAL = {1: "Yan", 2: "Fev", 3: "Mar", 4: "Apr", 5: "May", 6: "Iyn", 7: "Iyl", 8: "Avg", 9: "Sen",
                     10: "Okt", 11: "Noy", 12: "Dek"}
    kb = InlineKeyboardMarkup(row_width=3)
    for m_num in months:
        kb.insert(InlineKeyboardButton(text=OY_NOMI_LOCAL.get(m_num, str(m_num)),
                                       callback_data=f"acc:calc:month:{year}:{m_num}"))
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:calc:main"))
    return kb


def get_category_selection_keyboard(page: int, total_items: int, items: List[tuple],
                                    material_id: int) -> InlineKeyboardMarkup:
    """Material uchun yangi kategoriya tanlash klaviaturasi."""
    kb = build_paginated_inline(
        items=items,
        page=page,
        per_page=PER_PAGE,  # Bu o'zgaruvchi fayl boshida e'lon qilingan
        page_prefix=f"acc:material:{material_id}:select_cat_page",
        back_cb=f"acc:material:{material_id}:edit",
        total_items=total_items
    )
    # Kategoriyasiz qilish uchun alohida tugma qo'shamiz
    kb.add(InlineKeyboardButton("❌ Kategoriyadan chiqarish",
                                callback_data=f"acc:material:{material_id}:set_category:root"))
    return kb


# =========================================================================
# WEB LOGIN SOZLAMALARI (Super Admin uchun)
# =========================================================================

def get_web_login_menu() -> InlineKeyboardMarkup:
    """Web login sozlamalari asosiy menyusi."""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("👥 Barcha adminlar", callback_data="weblogin:list"),
        InlineKeyboardButton("➕ Yangi admin qo'shish", callback_data="weblogin:add"),
        InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_extra")
    )
    return kb


def get_web_login_admin_menu(admin_id: int, is_main_admin: bool = False) -> InlineKeyboardMarkup:
    """Tanlangan admin uchun amallar menyusi."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🔑 Parolni o'zgartirish", callback_data=f"weblogin:change_pass:{admin_id}"))
    
    if not is_main_admin:  # Asosiy adminni o'chirib bo'lmaydi
        kb.add(InlineKeyboardButton("🗑 O'chirish", callback_data=f"weblogin:delete:{admin_id}"))
    
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="weblogin:list"))
    return kb
