# shared.py
import asyncio
import logging
from math import ceil
from typing import List, Tuple, Optional, Dict, Any

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import SUPERADMINS
from loader import bot

# Pending join so'rovlarini saqlash uchun lug'at
pending_requests = {}
admin_action_messages: Dict[str, List[Tuple[int, int]]] = {}
admin_action_results: Dict[str, Dict[str, Any]] = {}
admin_action_locks: Dict[str, asyncio.Lock] = {}


# ===== Yordamchi funksiya: Adminlarga xabar yuborish =====
async def _resolve_notification_targets(branch_id: Optional[int], worker_id: Optional[int]) -> Tuple[Optional[int], List[int], int]:
    import database as db

    resolved_branch_id = branch_id
    if resolved_branch_id is None and worker_id is not None:
        resolved_branch_id = await db.get_worker_branch_id(worker_id)

    admin_ids = await db.get_notification_admin_ids(resolved_branch_id) if resolved_branch_id else sorted(set(SUPERADMINS))
    group_id = await db.get_notification_group_id(resolved_branch_id)
    return resolved_branch_id, admin_ids, group_id


async def _decorate_branch_text(text: str, branch_id: Optional[int]) -> str:
    if not branch_id:
        return text

    import database as db

    branch = await db.get_branch_by_id(branch_id)
    if not branch:
        return text

    branch_prefix = f"[{branch['name']}] "
    if text.startswith(branch_prefix):
        return text
    return f"{branch_prefix}{text}"


async def notify_admins(
    text,
    reply_markup=None,
    branch_id: Optional[int] = None,
    worker_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
):
    resolved_branch_id, target_admin_ids, _ = await _resolve_notification_targets(branch_id, worker_id)
    final_text = await _decorate_branch_text(text, resolved_branch_id)
    sent_messages = []
    for admin in target_admin_ids:
        try:
            msg = await bot.send_message(admin, final_text, reply_markup=reply_markup, parse_mode=parse_mode)
            sent_messages.append((msg.chat.id, msg.message_id))
        except Exception as e:
            logging.error(f"Admin {admin} ga xabar yuborishda xatolik: {e}")
    return sent_messages


async def notify_selected_admins(
    admin_ids,
    text,
    reply_markup=None,
    parse_mode: Optional[str] = None,
):
    sent_messages = []
    unique_admin_ids = sorted({int(admin_id) for admin_id in admin_ids if admin_id})
    for admin in unique_admin_ids:
        try:
            msg = await bot.send_message(admin, text, reply_markup=reply_markup, parse_mode=parse_mode)
            sent_messages.append((msg.chat.id, msg.message_id))
        except Exception as e:
            logging.error(f"Admin {admin} ga xabar yuborishda xatolik: {e}")
    return sent_messages


async def notify_admins_and_group(
    text,
    reply_markup=None,
    branch_id: Optional[int] = None,
    worker_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
):
    resolved_branch_id, _, group_id = await _resolve_notification_targets(branch_id, worker_id)
    final_text = await _decorate_branch_text(text, resolved_branch_id)
    sent_messages = await notify_admins(
        text,
        reply_markup=reply_markup,
        branch_id=resolved_branch_id,
        worker_id=worker_id,
        parse_mode=parse_mode,
    )

    if group_id:
        try:
            msg = await bot.send_message(group_id, final_text, reply_markup=reply_markup, parse_mode=parse_mode)
            sent_messages.append((msg.chat.id, msg.message_id))
        except Exception as e:
            logging.error(f"Guruhga xabar yuborishda xatolik: {e}")

    return sent_messages


def build_absence_review_keyboard(worker_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Sababli", callback_data=f"absence_review:excused:{worker_id}", style="success"),
        InlineKeyboardButton("Sababsiz", callback_data=f"absence_review:unexcused:{worker_id}", style="danger"),
    )
    return keyboard


def format_admin_actor(actor_id: int, actor_name: Optional[str] = None) -> str:
    cleaned_name = (actor_name or "").strip()
    return cleaned_name or f"Admin {actor_id}"


def build_branch_selection_keyboard(
    branches: List[Dict[str, Any]],
    callback_prefix: str,
    back_callback: Optional[str] = None,
    current_branch_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=1)
    for branch in branches:
        prefix = "✅ " if current_branch_id and int(branch["id"]) == int(current_branch_id) else ""
        keyboard.add(
            InlineKeyboardButton(
                f"{prefix}{branch['name']}",
                callback_data=f"{callback_prefix}:{branch['id']}",
                style="primary",
            )
        )
    if back_callback:
        keyboard.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=back_callback, style="primary"))
    return keyboard


async def get_admin_home_text(admin_tg_id: int) -> str:
    import database as db

    if admin_tg_id in SUPERADMINS:
        branch = await db.get_superadmin_selected_branch(admin_tg_id)
        if branch:
            return f"Admin menyusi:\n🏢 Filial: {branch['name']}"
        return "Katta admin menyusi uchun filialni tanlang:"

    branch = await db.get_admin_scope_branch(admin_tg_id)
    if branch:
        return f"Admin menyusi:\n🏢 Filial: {branch['name']}"
    return "Admin menyusi:"


async def get_superadmin_branch_selector_text(admin_tg_id: int) -> str:
    import database as db

    current_branch = await db.get_superadmin_selected_branch(admin_tg_id)
    if current_branch:
        return (
            "Qaysi filial bilan ishlamoqchisiz?\n"
            f"Joriy filial: {current_branch['name']}"
        )
    return "Qaysi filial bilan ishlamoqchisiz?"


async def has_admin_operating_branch(admin_tg_id: int) -> bool:
    import database as db

    branch_ids = await db.get_admin_branch_ids(admin_tg_id)
    return bool(branch_ids)


async def build_admin_home_payload(admin_tg_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    import database as db
    from menu_overrides import get_admin_main_menu

    if admin_tg_id in SUPERADMINS and not await has_admin_operating_branch(admin_tg_id):
        branches = await db.get_active_branches()
        current_branch_id = await db.get_superadmin_selected_branch_id(admin_tg_id)
        return (
            await get_superadmin_branch_selector_text(admin_tg_id),
            build_branch_selection_keyboard(
                branches,
                "superbranch:select",
                current_branch_id=current_branch_id,
            ),
        )

    return (await get_admin_home_text(admin_tg_id), get_admin_main_menu(admin_tg_id))


def get_admin_action_lock(action_key: str) -> asyncio.Lock:
    lock = admin_action_locks.get(action_key)
    if lock is None:
        lock = asyncio.Lock()
        admin_action_locks[action_key] = lock
    return lock


def get_admin_action_result(action_key: str) -> Optional[Dict[str, Any]]:
    return admin_action_results.get(action_key)


def describe_admin_action_result(result: Optional[Dict[str, Any]]) -> str:
    if not result:
        return "Bu amal allaqachon bajarilgan."
    actor_name = result.get("actor_name") or f"Admin {result.get('actor_id')}"
    note = (result.get("note") or "").strip()
    if note:
        return f"Bu amal allaqachon {actor_name} tomonidan bajarilgan: {note}"
    return f"Bu amal allaqachon {actor_name} tomonidan bajarilgan."


def reset_admin_action(action_key: str):
    admin_action_messages.pop(action_key, None)
    admin_action_results.pop(action_key, None)
    admin_action_locks.pop(action_key, None)


async def _clear_reply_markup_for_messages(messages: List[Tuple[int, int]]):
    for chat_id, message_id in messages:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            continue


async def register_admin_action_messages(action_key: Optional[str], sent_messages: List[Tuple[int, int]]):
    if not action_key or not sent_messages:
        return

    normalized_messages = [(int(chat_id), int(message_id)) for chat_id, message_id in sent_messages]
    if action_key in admin_action_results:
        await _clear_reply_markup_for_messages(normalized_messages)
        return

    bucket = admin_action_messages.setdefault(action_key, [])
    existing = set(bucket)
    for item in normalized_messages:
        if item not in existing:
            bucket.append(item)
            existing.add(item)


async def resolve_admin_action(
    action_key: str,
    actor_id: int,
    actor_name: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "actor_id": actor_id,
        "actor_name": format_admin_actor(actor_id, actor_name),
        "note": (note or "").strip(),
    }
    admin_action_results[action_key] = result
    messages = admin_action_messages.pop(action_key, [])
    if messages:
        await _clear_reply_markup_for_messages(messages)
    return result


def build_paginated_inline(
        items: List[Tuple[str, str]],
        page: int,
        per_page: int,
        page_prefix: str,
        back_cb: str,
        total_items: int,
        page_separator: str = "_",
) -> InlineKeyboardMarkup:
    """
    Elementlar ro'yxati uchun universal va xatosiz paginatsiyali klaviatura yasaydi.
    Callback formatini handlerlar kutayotgan ko'rinishda sozlash imkonini beradi.

    :param items: Joriy sahifa uchun tugmalar ro'yxati [(matn, callback_data), ...].
    :param page: Joriy sahifa raqami (0 dan boshlanadi).
    :param per_page: Bir sahifadagi elementlar soni.
    :param page_prefix: Paginatsiya callback'i uchun unikal prefiks (masalan, "salwp").
    :param back_cb: "Orqaga" tugmasi uchun callback_data.
    :param total_items: Ro'yxatdagi jami elementlar soni.
    :param page_separator: Sahifa prefiksi bilan sahifa raqami orasidagi ajratgich.
    :return: Tayyor InlineKeyboardMarkup obyekti.
    """
    kb = InlineKeyboardMarkup(row_width=3)

    # 1. Asosiy elementlarni (tugmalarni) klaviaturaga qo'shamiz
    for label, cb_data in items:
        kb.insert(InlineKeyboardButton(label, callback_data=cb_data, style="primary"))

    # 2. Navigatsiya qatorini yasaymiz
    total_pages = ceil(total_items / per_page)
    nav_buttons = []

    # "Oldingi" tugmasi (agar birinchi sahifada bo'lmasak)
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("« Oldingi", callback_data=f"{page_prefix}{page_separator}{page - 1}", style="primary")
        )

    # "Keyingi" tugmasi (agar oxirgi sahifada bo'lmasak)
    if page + 1 < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("Keyingi »", callback_data=f"{page_prefix}{page_separator}{page + 1}", style="primary")
        )

    # Agar navigatsiya tugmalari mavjud bo'lsa, ularni bitta qatorga qo'shamiz
    if nav_buttons:
        kb.row(*nav_buttons)

    # 3. "Orqaga" tugmasini eng oxirgi qatorga har doim qo'shamiz
    kb.add(InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb, style="primary"))

    return kb





PAY_TYPE_LABELS = {
    "daily": ("🌞", "Kunlik", "kun"),
    "weekly": ("📅", "Haftalik", "hafta"),
    "monthly": ("🗓", "Oylik", "oy"),
}

PAYMENT_KIND_LABELS = {
    "salary": ("💰", "Maosh"),
    "advance": ("💸", "Avans"),
}


def format_pay_status(
    pay_type: Optional[str],
    pay_amount: Any = None,
    monthly_salary: Any = None,
    *,
    short: bool = False,
) -> str:
    """Xodimning to'lov turini bir xil ko'rinishda formatlaydi.

    short=False: "🌞 Kunlik — 150,000 so'm/kun"
    short=True:  "🌞 Kunlik" (tugmalar uchun)

    pay_amount ustun bo'lsa shuni, bo'lmasa monthly_salary'ni ishlatamiz
    (eski yozuvlar uchun moslashuv).
    """
    pt = (pay_type or "monthly").lower()
    icon, label, unit = PAY_TYPE_LABELS.get(pt, PAY_TYPE_LABELS["monthly"])
    if short:
        return f"{icon} {label}"
    # Miqdor: pay_amount ustun, lekin agar 0 yoki None bo'lsa monthly_salary'ni sinab ko'ramiz
    raw_amount = pay_amount if pay_amount not in (None, 0, 0.0) else monthly_salary
    try:
        amount = float(raw_amount or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"{icon} {label} — <b>{amount:,.0f}</b> so'm/{unit}"


def format_payment_kind(kind: Optional[str], *, short: bool = False) -> str:
    """To'lov turini formatlaydi: 'salary' yoki 'advance'."""
    k = (kind or "salary").lower()
    icon, label = PAYMENT_KIND_LABELS.get(k, PAYMENT_KIND_LABELS["salary"])
    return icon if short else f"{icon} {label}"


def pay_type_emoji(pay_type: Optional[str]) -> str:
    """Tugma matnida ishlatadigan qisqa emoji (1 ta belgi)."""
    pt = (pay_type or "monthly").lower()
    icon, _, _ = PAY_TYPE_LABELS.get(pt, PAY_TYPE_LABELS["monthly"])
    return icon


def format_number(num) -> str:
    """
    Raqamlarni chiroyli formatlaydi: agar butun son bo'lsa, kasr qismini olib tashlaydi.
    Mingliklarni ajratib ko'rsatadi.
    Masalan: 40.000 -> "40", 4360000.00 -> "4,360,000", 1.50 -> "1.5"
    """
    if num is None:
        return "0"

    # Ma'lumotlar bazasidan keladigan Decimal turni float ga o'tkazamiz
    num = float(num)

    # Agar son butun bo'lsa (masalan, 40.0, 9.0)
    if num == int(num):
        return f"{int(num):,}"
    # Agar son kasrli bo'lsa (masalan, 1.5)
    else:
        # :g formati ortiqcha nollarni avtomatik olib tashlaydi (1.500 -> 1.5)
        return f"{num:g}"
