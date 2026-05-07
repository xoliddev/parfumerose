import datetime
import logging
import os

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import database as db
from config import ADMINS
from keyboards import (
    get_accounting_main_menu, get_back_button, get_orders_menu, get_order_details_menu,
    get_cancel_keyboard, create_calendar, calendar_callback, get_confirm_deletion_keyboard, get_materials_menu,
    get_material_details_menu, get_material_edit_menu, get_order_edit_menu, get_status_selection_keyboard,
    get_order_material_edit_menu, make_financial_years_keyboard, make_financial_months_keyboard, get_admin_main_menu,
    get_material_category_browser, get_category_edit_menu, get_category_selection_keyboard, PER_PAGE  # <--- QO'SHILDI
)
from loader import dp, bot
from shared import build_paginated_inline, format_number
from states import Accounting, MaterialCategory

# Bu PER_PAGE va OY_NOMI o'zgaruvchilari ham kerak bo'ladi

OY_NOMI = {
    '01': "Yanvar", '02': "Fevral", '03': "Mart", '04': "Aprel", '05': "May", '06': "Iyun",
    '07': "Iyul", '08': "Avgust", '09': "Sentyabr", '10': "Oktyabr", '11': "Noyabr", '12': "Dekabr"
}


# =============================================================================
# --- BUXGALTERIYA MODULI ---
# =============================================================================
# Paginatsiya uchun sahifadagi elementlar soni


# --- Umumiy yordamchilar ---
@dp.callback_query_handler(lambda c: c.data == "acc:cancel_fsm", state="*")
async def cancel_fsm_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Har qanday FSM holatini bekor qilish uchun"""
    await state.finish()
    await callback_query.message.edit_text("Amal bekor qilindi.", reply_markup=get_accounting_main_menu())
    await callback_query.answer()


# --- Bosh menyu ---
@dp.callback_query_handler(lambda c: c.data == "acc:main", state="*")
async def show_accounting_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()

    top_order = await db.get_top_profit_order()

    top_order_text = ""
    if top_order and top_order.get('net_profit', 0) > 0:
        top_order_text = (
            f"🏆 Eng foydali buyurtma: "
            f"<b>{top_order['order_name']}</b>\n"
            f"(Sof foyda: {top_order['net_profit']:,.2f} so'm)\n\n"
        )

    final_text = top_order_text + "🧾 Buxgalteriya bo'limi"

    await callback_query.message.edit_text(
        final_text,
        reply_markup=get_accounting_main_menu(),
        parse_mode="HTML"
    )
    await callback_query.answer()


# --- Buyurtmalar (Orders) blogi ---
@dp.callback_query_handler(lambda c: c.data == "acc:orders:main", state="*")
async def show_orders_menu(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text("📋 Buyurtmalar bo'limi", reply_markup=get_orders_menu())
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:orders:list:"), state="*")
async def list_all_orders(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0

    orders = await db.get_all_orders(page=page, per_page=PER_PAGE)
    total_orders = await db.get_order_count()

    if not orders:
        await callback_query.message.edit_text(
            "Hozircha buyurtmalar mavjud emas.",
            reply_markup=get_back_button("acc:orders:main")
        )
        await callback_query.answer()
        return

    items = [(f"{o['order_name']} ({o['status']})", f"acc:order:{o['id']}:view") for o in orders]

    kb = build_paginated_inline(
        items=items,
        page=page,
        total_items=total_orders,
        per_page=PER_PAGE,
        page_prefix="acc:orders:list",  # <-- "callback_prefix" nomi "page_prefix" ga o'zgartirildi
        back_cb="acc:orders:main"
    )
    await callback_query.message.edit_text(f"📋 Buyurtmalar ro'yxati (Sahifa {page + 1})", reply_markup=kb)
    await callback_query.answer()


# 1. "Xodim biriktirish" tugmasi bosilganda ishlaydi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":assign_worker"), state="*")
async def start_assign_worker_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        page = 0  # Har doim birinchi sahifadan boshlaymiz

        workers = await db.get_all_workers(page=page, per_page=PER_PAGE)
        total_workers = await db.get_worker_count()

        if not total_workers:
            return await callback_query.answer("Biriktirish uchun xodimlar topilmadi!", show_alert=True)

        items = [(w['full_name'], f"acc:order:{order_id}:select_worker:{w['id']}") for w in workers]

        page_prefix = f"acc:order:{order_id}:assign_page"

        kb = build_paginated_inline(
            items=items, page=page, total_items=total_workers, per_page=PER_PAGE,
            page_prefix=page_prefix, back_cb=f"acc:order:{order_id}:view"
        )

        await callback_query.message.edit_text("Qaysi xodimni biriktirmoqchisiz?", reply_markup=kb)
        await callback_query.answer()

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# Xodimlar ro'yxatida paginatsiyani ishlashi uchun
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and ":assign_page:" in c.data,
                           state="*")  # O'ZGARISH
async def process_assign_worker_pagination(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        # --- MANA SHU QISM TO'LIQ O'ZGARDI ---
        parts = callback_query.data.split(":")
        order_id = int(parts[2])
        page = int(parts[4])  # Endi bu ancha sodda va to'g'ri ishlaydi
        # -----------------------------------

        workers = await db.get_all_workers(page=page, per_page=PER_PAGE)
        total_workers = await db.get_worker_count()
        items = [(w['full_name'], f"acc:order:{order_id}:select_worker:{w['id']}") for w in workers]
        page_prefix = f"acc:order:{order_id}:assign_page"
        kb = build_paginated_inline(
            items=items, page=page, total_items=total_workers, per_page=PER_PAGE,
            page_prefix=page_prefix, back_cb=f"acc:order:{order_id}:view"
        )
        await callback_query.message.edit_text("Qaysi xodimni biriktirmoqchisiz?", reply_markup=kb)

    except (ValueError, IndexError):
        await callback_query.answer("Paginatsiyada xatolik.", show_alert=True)
    finally:
        await callback_query.answer()


# 2. Ro'yxatdan xodim tanlanganda ishlaydi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and ":select_worker:" in c.data, state="*")
async def select_worker_for_order_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback_query.data.split(":")
        order_id = int(parts[2])
        worker_id = int(parts[4])

        await db.assign_worker_to_order(order_id, worker_id)
        await callback_query.answer(f"Xodim muvaffaqiyatli biriktirildi!", show_alert=True)

        # Oynani yangilangan ma'lumotlar bilan qayta ko'rsatamiz
        callback_query.data = f"acc:order:{order_id}:view"
        await view_order_details(callback_query, state)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# 3. Biriktirilgan xodimni o'chirish uchun
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and ":unassign_worker:" in c.data, state="*")
async def unassign_worker_from_order_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback_query.data.split(":")
        order_id = int(parts[2])
        worker_id = int(parts[4])

        await db.unassign_worker_from_order(order_id, worker_id)
        await callback_query.answer(f"Xodim buyurtmadan olib tashlandi!", show_alert=True)

        # Oynani yangilangan ma'lumotlar bilan qayta ko'rsatamiz
        callback_query.data = f"acc:order:{order_id}:view"
        await view_order_details(callback_query, state)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


async def generate_final_report_text(order_id: int) -> str | None:
    """Buyurtma ID'si bo'yicha yakuniy foyda hisoboti matnini yaratadi."""
    order = await db.get_order_by_id(order_id)
    costs = await db.get_order_costs(order_id)
    assigned_workers = await db.get_assigned_workers_for_order(order_id)

    if not order or not assigned_workers:
        return None

    usta = assigned_workers[0]
    sale_price = float(order['total_cost'])
    material_costs = float(costs['total_expenses'])
    net_profit = sale_price - material_costs

    prorab_share = net_profit / 2
    usta_share = net_profit / 2

    report_text = (
        f"✅ <b>Buyurtma yakunlandi: \"{order['order_name']}\"</b>\n\n"
        f"💳 Sotuv narxi: {sale_price:,.2f} so'm\n"
        f"📦 Material xarajatlari: - {material_costs:,.2f} so'm\n"
        f"----------------------------------\n"
        f"💰 <b>Sof Foyda: {net_profit:,.2f} so'm</b>\n\n"
        f"👥 <b>Taqsimot (50/50):</b>\n"
        f"▫️ Prorab (Muhammadrizo) ulushi: <b>{prorab_share:,.2f} so'm</b>\n"
        f"▫️ Usta ({usta['full_name']}) ulushi: <b>{usta_share:,.2f} so'm</b>"
    )
    return report_text


async def format_order_details(order_id: int) -> str:
    """Buyurtma ma'lumotlarini barcha qo'shimchalari bilan chiroyli matn formatida qaytaradi."""
    # 1. Kerakli barcha ma'lumotlarni bazadan olamiz
    order = await db.get_order_by_id(order_id)
    if not order:
        return "Buyurtma topilmadi."

    costs = await db.get_order_costs(order_id)
    materials = await db.get_order_materials(order_id)
    assigned_workers = await db.get_assigned_workers_for_order(order_id)

    # 2. Foydani hisoblaymiz
    profit = float(order['total_cost']) - float(costs['total_expenses'])

    # 3. Matn qismlarini alohida tayyorlaymiz
    # Sarflangan materiallar ro'yxati
    mat_text = "\n".join(
        [f"  - {m['name']}: {format_number(m['quantity'])} {m['unit']} ({format_number(m['cost'])} so'm)" for m in
         materials]
    ) or "Hali material qo'shilmagan"

    # Biriktirilgan ustalar ro'yxati
    worker_text = "\n".join(
        [f"  - {w['full_name']}" for w in assigned_workers]
    ) or "Hech kim biriktirilmagan"

    # 4. Barcha qismlarni yagona matnga birlashtiramiz
    details = (
        f"<b>Buyurtma: {order['order_name']}</b>\n\n"
        f"<b>ID:</b> {order['id']}\n"
        f"<b>Holati:</b> {order['status']}\n"
        f"<b>Mijoz:</b> {order.get('client_name', 'N/A')}\n"
        f"<b>Tavsif:</b> {order.get('description', 'N/A')}\n\n"
        f"💰 <b>Moliyaviy ma'lumotlar:</b>\n"
        f"  - Sotuv narxi: {float(order['total_cost']):,.2f} so'm\n"
        f"  - Material xarajatlari: {float(costs['total_expenses']):,.2f} so'm\n"
        f"  - Sof Foyda: {profit:,.2f} so'm\n\n"
        f"👤 <b>Mas'ul xodimlar:</b>\n{worker_text}\n\n"
        f"📦 <b>Sarflangan materiallar:</b>\n{mat_text}"
    )

    return details


@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and ":view" in c.data, state="*")
async def view_order_details(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()

    try:
        # 1. Callback ma'lumotlarini ajratib olamiz (qaysi bo'lim ochiqligini bilish uchun)
        parts = callback_query.data.split(":")
        order_id = int(parts[2])

        show_workers = 0
        show_materials = 0

        # Agar callback'da bo'limlarni ochish/yopish haqida ma'lumot bo'lsa, o'qiymiz
        if "toggle" in parts:
            toggle_index = parts.index("toggle")
            try:
                show_workers = int(parts[toggle_index + 2])
                show_materials = int(parts[toggle_index + 3])

                toggle_subject = parts[toggle_index + 1]
                if toggle_subject == 'workers':
                    show_workers = 1 - show_workers
                elif toggle_subject == 'materials':
                    show_materials = 1 - show_materials
            except (IndexError, ValueError):
                pass

        # 2. Xabar matnini tayyorlaymiz
        text = await format_order_details(order_id)

        # 3. Klaviaturani yasaymiz
        kb = get_order_details_menu(order_id)

        order = await db.get_order_by_id(order_id)

        # "Bajarildi" statusi uchun "Yakuniy Hisobotni Ko'rish" tugmasini qo'shamiz
        if order and order['status'] == 'Bajarildi':
            kb.add(InlineKeyboardButton(
                "💰 Yakuniy Hisobotni Ko'rish",
                callback_data=f"acc:order:{order_id}:final_report"
            ))

        # --- Xodimlarni boshqarish bo'limi (Yig'iladigan) ---
        assigned_workers = await db.get_assigned_workers_for_order(order_id)
        if assigned_workers:
            if show_workers:  # Agar bo'lim ochiq bo'lsa
                kb.add(InlineKeyboardButton(
                    f"🔽 Xodimlarni yashirish ({len(assigned_workers)})",
                    callback_data=f"acc:order:{order_id}:view:toggle:workers:{show_workers}:{show_materials}"
                ))
                for w in assigned_workers:
                    kb.add(InlineKeyboardButton(
                        f"❌ {w['full_name']} (olib tashlash)",
                        callback_data=f"acc:order:{order_id}:unassign_worker:{w['id']}"
                    ))
            else:  # Agar bo'lim yopiq bo'lsa
                kb.add(InlineKeyboardButton(
                    f"▶️ Xodimlarni boshqarish ({len(assigned_workers)})",
                    callback_data=f"acc:order:{order_id}:view:toggle:workers:{show_workers}:{show_materials}"
                ))

        # --- Materiallarni boshqarish bo'limi (Yig'iladigan) ---
        materials = await db.get_order_materials(order_id)
        if materials:
            if show_materials:  # Agar bo'lim ochiq bo'lsa
                kb.add(InlineKeyboardButton(
                    f"🔽 Materiallarni yashirish ({len(materials)})",
                    callback_data=f"acc:order:{order_id}:view:toggle:materials:{show_workers}:{show_materials}"
                ))
                for m in materials:
                    mat_button_text = f"📦 {m['name']}: {m['quantity']} {m['unit']}"
                    kb.add(InlineKeyboardButton(mat_button_text, callback_data=f"acc:ord_mat:{m['id']}:menu"))
            else:  # Agar bo'lim yopiq bo'lsa
                kb.add(InlineKeyboardButton(
                    f"▶️ Materiallarni boshqarish ({len(materials)})",
                    callback_data=f"acc:order:{order_id}:view:toggle:materials:{show_workers}:{show_materials}"
                ))

        await callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logging.error(f"view_order_details'da kutilmagan xatolik: {e}")
        await callback_query.answer("Kutilmagan xatolik yuz berdi.", show_alert=True)
    finally:
        await callback_query.answer()


# BU YERDA BUYURTMA YARATISH, TAHRIRLASH, O'CHIRISH VA UNGA ELEMENT QO'SHISH LOGIKASI BO'LADI
# Bu qism juda katta bo'lgani uchun alohida faylga olib, import qilish tavsiya etiladi.
# Hozircha shu faylning o'zida davom ettiramiz.

# --- Buyurtma yaratish FSM zanjiri ---

# 1-qadam: Jarayonni boshlash
@dp.callback_query_handler(lambda c: c.data == "acc:order:add", state="*")
async def start_add_order(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback_query.message.edit_text(
        "📝 Yangi buyurtma nomini kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    await Accounting.GET_ORDER_NAME.set()
    await callback_query.answer()


# 2-qadam: Nomi
@dp.message_handler(state=Accounting.GET_ORDER_NAME)
async def process_order_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📄 Buyurtma uchun qisqacha tavsif kiriting:", reply_markup=get_cancel_keyboard())
    await Accounting.GET_ORDER_DESC.set()


# 3-qadam: Tavsif (Bu qism sizda yo'q edi)
@dp.message_handler(state=Accounting.GET_ORDER_DESC)
async def process_order_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text.strip())
    await message.answer("👤 Mijoz ismini kiriting:", reply_markup=get_cancel_keyboard())
    await Accounting.GET_CLIENT_NAME.set()


# 4-qadam: Mijoz ismi
@dp.message_handler(state=Accounting.GET_CLIENT_NAME)
async def process_client_name(message: types.Message, state: FSMContext):
    await state.update_data(client_name=message.text.strip())
    await message.answer("📞 Mijoz telefon raqamini kiriting:", reply_markup=get_cancel_keyboard())
    await Accounting.GET_CLIENT_CONTACT.set()


# 5-qadam: Mijoz raqami
@dp.message_handler(state=Accounting.GET_CLIENT_CONTACT)
async def process_client_contact(message: types.Message, state: FSMContext):
    await state.update_data(client_contact=message.text.strip())
    await message.answer(
        "🗓 Buyurtma boshlanish sanasini tanlang:",
        reply_markup=await create_calendar()
    )
    await Accounting.GET_START_DATE.set()


# 6-qadam: Boshlanish sanasi
@dp.callback_query_handler(calendar_callback.filter(action=["PREV-MONTH", "NEXT-MONTH", "DAY"]),
                           state=Accounting.GET_START_DATE)
async def process_start_date_calendar(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    action = callback_data.get('action')
    year = int(callback_data.get('year'))
    month = int(callback_data.get('month'))
    day = int(callback_data.get('day'))

    # Agar "Oldingi" yoki "Keyingi" oy bosilsa, kalendarni yangilaymiz
    if action in ["PREV-MONTH", "NEXT-MONTH"]:
        await callback_query.message.edit_reply_markup(reply_markup=await create_calendar(year=year, month=month))
        await callback_query.answer()
        return

    # Agar kun tanlansa, sanani saqlaymiz va keyingi qadamga o'tamiz
    if action == "DAY":
        selected_date = datetime.datetime(year, month, day)
        await callback_query.message.delete()
        await state.update_data(start_date=selected_date.isoformat())
        await callback_query.message.answer(
            f"✅ Boshlanish sanasi: {selected_date.strftime('%d-%m-%Y')}\n\n"
            f"🏁 Endi buyurtma tugash sanasini tanlang:",
            reply_markup=await create_calendar()
        )
        await Accounting.GET_DEADLINE.set()
    await callback_query.answer()


# 7-qadam: Tugash sanasi
@dp.callback_query_handler(calendar_callback.filter(action=["PREV-MONTH", "NEXT-MONTH", "DAY"]),
                           state=Accounting.GET_DEADLINE)
async def process_deadline_calendar(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    action = callback_data.get('action')
    year = int(callback_data.get('year'))
    month = int(callback_data.get('month'))
    day = int(callback_data.get('day'))

    # Agar "Oldingi" yoki "Keyingi" oy bosilsa, kalendarni yangilaymiz
    if action in ["PREV-MONTH", "NEXT-MONTH"]:
        await callback_query.message.edit_reply_markup(reply_markup=await create_calendar(year=year, month=month))
        await callback_query.answer()
        return

    # Agar kun tanlansa, sanani saqlaymiz va keyingi qadamga o'tamiz
    if action == "DAY":
        selected_date = datetime.datetime(year, month, day)
        await callback_query.message.delete()
        await state.update_data(deadline=selected_date.isoformat())
        await callback_query.message.answer(
            f"✅ Tugash sanasi: {selected_date.strftime('%d-%m-%Y')}\n\n"
            f"💰 Buyurtmaning umumiy sotuv narxini kiriting (faqat raqam, masalan, `5000000`):",
            reply_markup=get_cancel_keyboard()
        )
        await Accounting.GET_TOTAL_COST.set()
    await callback_query.answer()


# 8-qadam (YAKUNIY): Narxni olib, bazaga saqlash
@dp.message_handler(state=Accounting.GET_TOTAL_COST)
async def process_total_cost_and_save(message: types.Message, state: FSMContext):
    try:
        # 1. Kiritilgan matndan faqat raqamlar va nuqtani qoldiramiz
        cleaned_text = ''.join(c for c in message.text if c.isdigit() or c == '.')

        # 2. Tozalangan matnni songa o'giramiz
        total_cost = float(cleaned_text)

        data = await state.get_data()

        # Sanalarni to'g'ri formatga o'tkazish (bu yerda xatolik yo'q, lekin to'liqlik uchun qo'shildi)
        start_date_obj = datetime.datetime.fromisoformat(data.get('start_date')).date()
        deadline_obj = datetime.datetime.fromisoformat(data.get('deadline')).date()

        order_id = await db.create_order(
            name=data.get('name'),
            desc=data.get('desc'),
            client_name=data.get('client_name'),
            client_contact=data.get('client_contact'),
            start_date=start_date_obj,
            deadline=deadline_obj,
            total_cost=total_cost
        )

        if order_id:
            await message.answer(
                f"✅ **Yangi buyurtma muvaffaqiyatli yaratildi!**\nBuyurtma ID raqami: `{order_id}`",
                reply_markup=get_back_button("acc:orders:main"),
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "❌ Ma'lumotlar bazasiga yozishda noma'lum xatolik yuz berdi.",
                reply_markup=get_back_button("acc:orders:main")
            )
        await state.finish()
    except (ValueError, TypeError):
        await message.reply("❗️ **Xato.** Iltimos, narxni to'g'ri raqam formatida kiriting (masalan, `5000000`).")
        return
    except Exception as e:
        logging.error(f"Buyurtma saqlashda kutilmagan xato: {e}")
        await message.answer("❗️ Kutilmagan xatolik yuz berdi. Iltimos, adminga xabar bering.")
        await state.finish()


# --- Buyurtmani o'chirish ---
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":delete"), state="*")
async def prompt_delete_order(callback_query: types.CallbackQuery):
    order_id = int(callback_query.data.split(":")[2])
    order = await db.get_order_by_id(order_id)
    if not order:
        await callback_query.answer("Buyurtma topilmadi!", show_alert=True)
        return

    await callback_query.message.edit_text(
        f"<b>DIQQAT!</b> Siz \"{order['order_name']}\" nomli buyurtmani va unga bog'liq barcha ma'lumotlarni (materiallar, ishlar) o'chirmoqchimisiz?",
        reply_markup=get_confirm_deletion_keyboard(f"acc:order:{order_id}"),
        parse_mode="HTML"
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":confirm_delete"), state="*")
async def confirm_delete_order(callback_query: types.CallbackQuery):
    order_id = int(callback_query.data.split(":")[2])
    success = await db.delete_order(order_id)
    if success:
        await callback_query.message.edit_text("✅ Buyurtma muvaffaqiyatli o'chirildi.")
        await list_all_orders(callback_query, FSMContext)  # Ro'yxatni yangilash
    else:
        await callback_query.answer("❌ Xatolik yuz berdi!", show_alert=True)
        await callback_query.message.delete()
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":cancel_delete"), state="*")
async def cancel_delete_order(callback_query: types.CallbackQuery):
    order_id = int(callback_query.data.split(":")[2])
    await view_order_details(callback_query)  # Tafsilotlar oynasiga qaytish


# --- MATERIALlar blogi (xuddi buyurtmalar kabi) ---
@dp.callback_query_handler(lambda c: c.data == "acc:materials:main", state="*")
async def show_materials_menu(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text("📦 Materiallar bo'limi", reply_markup=get_materials_menu())
    await callback_query.answer()


# Bu yerda materiallar uchun ham list, view, add, edit, delete handlerlari bo'ladi.
# Ular buyurtmalarniki bilan deyarli bir xil, faqat funksiya nomlari va callback datalar o'zgaradi.
# ...

# --- HISOB-KITOB bo'limi ---
@dp.callback_query_handler(lambda c: c.data == "acc:calc:main", state="*")
async def show_calculations(callback_query: types.CallbackQuery, state: FSMContext):
    """Moliyaviy hisobot uchun yillar ro'yxatini ko'rsatadi."""
    await state.finish()
    years = await db.get_financial_years()
    if not years:
        await callback_query.message.edit_text(
            "Hozircha yakunlangan buyurtmalar bo'yicha hisobotlar mavjud emas.",
            reply_markup=get_back_button("acc:main")
        )
        return await callback_query.answer()

    kb = make_financial_years_keyboard(years)
    await callback_query.message.edit_text(
        "📊 Moliyaviy hisobot uchun yilni tanlang:",
        reply_markup=kb
    )
    await callback_query.answer()


# Materiallar bo'limining asosiy menyusi
@dp.callback_query_handler(lambda c: c.data == "acc:materials:main", state="*")
async def show_materials_menu_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Materiallar bo'limining asosiy menyusini ko'rsatadi."""
    await state.finish()
    await callback_query.message.edit_text(
        "📦 Materiallar bo'limi\n\nBu yerdan kategoriyalarni ko'rishingiz, qidirishingiz va yangi materiallar qo'shishingiz mumkin.",
        reply_markup=get_materials_menu()
    )
    await callback_query.answer()


# YUQORIDAGI FUNKSIYADAN KEYIN SHU KODNI TO'LIQ JOYLANG:
@dp.callback_query_handler(lambda c: c.data.startswith("acc:category:browse:"), state="*")
async def browse_material_categories_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Kategoriyalar ichida yurish (browse) uchun asosiy handler (holatlarni to'g'ri boshqaradi)."""
    # --- XATONI TUZATISH SHU YERDA ---
    # Joriy holatni tekshiramiz
    current_state = await state.get_state()
    # Agar biz maxsus "buyurtma uchun tanlash" holatida bo'lmasak, unda holatni tozalaymiz.
    # Aks holda, holatga tegmaymiz.
    if current_state != Accounting.SELECTING_MATERIAL_FOR_ORDER.state:
        await state.finish()
    # ------------------------------------

    try:
        category_id_str = callback_query.data.split(":")[-1]

        if category_id_str == "root":
            current_category_id = None
            path = []
        else:
            current_category_id = int(category_id_str)
            path = await db.get_category_path(current_category_id)

        sub_categories = await db.get_material_categories(parent_id=current_category_id)
        materials_in_category = await db.get_materials_in_category(category_id=current_category_id)

        title = path[-1]['name'] if path else "Bosh Sahifa"
        text = f"🗂️ Kategoriya: <b>{title}</b>"

        keyboard = get_material_category_browser(
            path=path,
            sub_categories=sub_categories,
            materials=materials_in_category
        )

        if callback_query.message.photo:
            await callback_query.message.delete()
            await callback_query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Kategoriyalarni ko'rsatishda xatolik: {e}")
        try:
            if callback_query.message.photo:
                await callback_query.message.delete()
                await callback_query.message.answer("Bosh menyu", reply_markup=get_admin_main_menu(callback_query.from_user.id))
            else:
                await callback_query.message.edit_text("Bosh menyu", reply_markup=get_admin_main_menu(callback_query.from_user.id))
            await callback_query.answer("❌ Xatolik yuz berdi. Bosh menyuga qayting.", show_alert=True)
        except Exception as inner_e:
            logging.error(f"Xatolikni qayta ishlashda xatolik: {inner_e}")
    finally:
        await callback_query.answer()


# 1. Tahrirlash menyusini ko'rsatish
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit"), state="*")
async def show_order_edit_menu_handler(callback_query: types.CallbackQuery):
    try:
        order_id = int(callback_query.data.split(":")[2])
        order = await db.get_order_by_id(order_id)
        if not order:
            return await callback_query.answer("Buyurtma topilmadi!", show_alert=True)

        await callback_query.message.edit_text(
            f"<b>{order['order_name']}</b> buyurtmasini tahrirlash.\n\nQaysi qismni o'zgartirmoqchisiz?",
            reply_markup=get_order_edit_menu(order_id),
            parse_mode="HTML"
        )
        await callback_query.answer()
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# 2. Holatni o'zgartirish uchun tanlov menyusini ko'rsatish
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":change_status"), state="*")
async def show_status_selection_handler(callback_query: types.CallbackQuery):
    try:
        order_id = int(callback_query.data.split(":")[2])
        await callback_query.message.edit_text(
            "Yangi holatni tanlang:",
            reply_markup=get_status_selection_keyboard(order_id)
        )
        await callback_query.answer()
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# 3. Tanlangan yangi holatni saqlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and ":set_status:" in c.data, state="*")
async def set_new_order_status_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback_query.data.split(":")
        order_id = int(parts[2])
        new_status = parts[4]

        # 1. Ma'lumotlar bazasidagi holatni yangilaymiz
        success = await db.update_order(order_id, status=new_status)

        if not success:
            await callback_query.answer("Holatni o'zgartirishda xatolik yuz berdi!", show_alert=True)
            return

        await callback_query.answer(f"Holat \"{new_status}\" ga o'zgartirildi!", show_alert=False)

        # 2. AGAR HOLAT "BAJARILDI" BO'LSA, YAKUNIY HISOBOTNI YUBORAMIZ
        if new_status == 'Bajarildi':
            # --- Hisob-kitob uchun kerakli barcha ma'lumotlarni yig'amiz ---
            order = await db.get_order_by_id(order_id)
            costs = await db.get_order_costs(order_id)
            assigned_workers = await db.get_assigned_workers_for_order(order_id)

            # --- Ma'lumotlar to'liqligini tekshiramiz ---
            if not order:
                return await callback_query.message.answer("❌ Hisobot uchun buyurtma ma'lumotlari topilmadi.")

            if not assigned_workers:
                await callback_query.message.answer(
                    f"⚠️ <b>DIQQAT:</b> \"{order['order_name']}\" buyurtmasi yakunlandi, lekin unga mas'ul usta biriktirilmagan. "
                    f"Foyda hisoboti chiqarilmadi.",
                    parse_mode="HTML"
                )
                # Oynani shunchaki yangilab qo'yamiz
                callback_query.data = f"acc:order:{order_id}:view:0:0"
                return await view_order_details(callback_query, state)

            # --- Hisob-kitobni bajaramiz ---
            usta = assigned_workers[0]  # Bizda faqat bitta usta bo'ladi
            sale_price = float(order['total_cost'])
            material_costs = float(costs['total_expenses'])
            net_profit = sale_price - material_costs

            # Prorab va Ustaning ulushlari (50/50)
            prorab_share = net_profit / 2
            usta_share = net_profit / 2

            # --- Chiroyli hisobotni formatlaymiz ---
            report_text = (
                f"✅ <b>Buyurtma yakunlandi: \"{order['order_name']}\"</b>\n\n"
                f"💳 Sotuv narxi: {sale_price:,.2f} so'm\n"
                f"📦 Material xarajatlari: - {material_costs:,.2f} so'm\n"
                f"----------------------------------\n"
                f"💰 <b>Sof Foyda: {net_profit:,.2f} so'm</b>\n\n"
                f"👥 <b>Taqsimot (50/50):</b>\n"
                f"▫️ Prorab (Muhammadrizo) ulushi: <b>{prorab_share:,.2f} so'm</b>\n"
                f"▫️ Usta ({usta['full_name']}) ulushi: <b>{usta_share:,.2f} so'm</b>"
            )

            # Hisobotni yangi xabar qilib yuboramiz
            await callback_query.message.answer(report_text, parse_mode="HTML")

        # 3. Asosiy buyurtma oynasini yangilangan ma'lumotlar bilan ko'rsatamiz
        callback_query.data = f"acc:order:{order_id}:view:0:0"
        await view_order_details(callback_query, state)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# --- Buyurtma maydonlarini tahrirlash FSM zanjirlari (YANGI, XATOSIZ VERSIYA) ---

# Asosiy tahrirlash menyusini ko'rsatadi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit"), state="*")
async def show_order_edit_menu_handler(callback_query: types.CallbackQuery):
    try:
        order_id = int(callback_query.data.split(":")[2])
        order = await db.get_order_by_id(order_id)
        if not order:
            return await callback_query.answer("Buyurtma topilmadi!", show_alert=True)

        text = f"<b>{order['order_name']}</b> buyurtmasini tahrirlash.\n\nQaysi qismni o'zgartirmoqchisiz?"
        keyboard = get_order_edit_menu(order_id)

        if callback_query.message.photo:
            await callback_query.message.edit_caption(caption=text, reply_markup=keyboard)
        else:
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

        await callback_query.answer()
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# Holatini o'zgartirish menyusini ko'rsatadi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":change_status"), state="*")
async def show_status_selection_handler(callback_query: types.CallbackQuery):
    try:
        order_id = int(callback_query.data.split(":")[2])
        text = "Yangi holatni tanlang:"
        keyboard = get_status_selection_keyboard(order_id)

        if callback_query.message.photo:
            await callback_query.message.edit_caption(caption=text, reply_markup=keyboard)
        else:
            await callback_query.message.edit_text(text, reply_markup=keyboard)

        await callback_query.answer()
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)


# 1. Nomini tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit_name"), state="*")
async def start_edit_order_name_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        await state.update_data(current_order_id=order_id, message_to_edit=callback_query.message.to_python())

        # Eski xabarni o'chirib, yangi so'rov yuboramiz. Bu eng ishonchli usul.
        await callback_query.message.delete()
        await callback_query.message.answer("✍️ Buyurtma uchun yangi nom kiriting:", reply_markup=get_cancel_keyboard())

        await Accounting.EDIT_ORDER_NAME.set()
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Buyurtma nomini tahrirlashni boshlashda xato: {e}")


@dp.message_handler(state=Accounting.EDIT_ORDER_NAME)
async def process_edit_order_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('current_order_id')

    await db.update_order(order_id, order_name=message.text.strip())
    await state.finish()

    # Foydalanuvchi yuborgan xabarni o'chiramiz
    await message.delete()

    # Yangilangan buyurtma ma'lumotlarini ko'rsatamiz
    # Bu yerda `fake_callback_query` o'rniga to'g'ridan-to'g'ri yangi xabar yuboramiz
    new_text = await format_order_details(order_id)
    new_keyboard = get_order_details_menu(order_id)
    await message.answer(new_text, reply_markup=new_keyboard)


# 2. Mijozni tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit_client"), state="*")
async def start_edit_order_client_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        await state.update_data(current_order_id=order_id)
        await callback_query.message.delete()
        await callback_query.message.answer("👤 Mijoz uchun yangi ism kiriting:", reply_markup=get_cancel_keyboard())
        await Accounting.EDIT_ORDER_CLIENT.set()
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Mijoz ismini tahrirlashni boshlashda xato: {e}")


@dp.message_handler(state=Accounting.EDIT_ORDER_CLIENT)
async def process_edit_order_client_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('current_order_id')
    await db.update_order(order_id, client_name=message.text.strip())
    await state.finish()
    await message.delete()

    new_text = await format_order_details(order_id)
    new_keyboard = get_order_details_menu(order_id)
    await message.answer(new_text, reply_markup=new_keyboard)


# 3. Tavsifni tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit_desc"), state="*")
async def start_edit_order_desc_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        await state.update_data(current_order_id=order_id)
        await callback_query.message.delete()
        await callback_query.message.answer("📄 Buyurtma uchun yangi tavsif kiriting:",
                                            reply_markup=get_cancel_keyboard())
        await Accounting.EDIT_ORDER_DESC.set()
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Tavsifni tahrirlashni boshlashda xato: {e}")


@dp.message_handler(state=Accounting.EDIT_ORDER_DESC)
async def process_edit_order_desc_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('current_order_id')
    await db.update_order(order_id, description=message.text.strip())
    await state.finish()
    await message.delete()

    new_text = await format_order_details(order_id)
    new_keyboard = get_order_details_menu(order_id)
    await message.answer(new_text, reply_markup=new_keyboard)


# 4. Narxni tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":edit_price"), state="*")
async def start_edit_order_price_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        await state.update_data(current_order_id=order_id)
        await callback_query.message.delete()
        await callback_query.message.answer("💰 Buyurtmaning yangi umumiy sotuv narxini kiriting (faqat raqam):",
                                            reply_markup=get_cancel_keyboard())
        await Accounting.EDIT_ORDER_PRICE.set()
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Narxni tahrirlashni boshlashda xato: {e}")


@dp.message_handler(state=Accounting.EDIT_ORDER_PRICE)
async def process_edit_order_price_handler(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.strip())
    except ValueError:
        return await message.reply("❗️ Xato. Iltimos, narxni faqat raqam bilan kiriting.")

    data = await state.get_data()
    order_id = data.get('current_order_id')
    await db.update_order(order_id, total_cost=price)
    await state.finish()
    await message.delete()

    new_text = await format_order_details(order_id)
    new_keyboard = get_order_details_menu(order_id)
    await message.answer(new_text, reply_markup=new_keyboard)


# --- Buyurtmadagi Materialni Tahrirlash ---

# Material ustiga bosilganda tahrirlash menyusini ko'rsatish
@dp.callback_query_handler(lambda c: c.data.startswith("acc:ord_mat:") and c.data.endswith(":menu"), state="*")
async def show_order_material_edit_menu_handler(callback_query: types.CallbackQuery, state: FSMContext):
    entry_id = int(callback_query.data.split(":")[2])
    # Buyurtma ID'sini olish uchun bazaga murojaat
    order_id = await db.pool.fetchval("SELECT order_id FROM order_materials WHERE id=$1", entry_id)
    if not order_id:
        return await callback_query.answer("Xatolik: Yozuv topilmadi.", show_alert=True)

    await callback_query.message.edit_reply_markup(reply_markup=get_order_material_edit_menu(entry_id, order_id))
    await callback_query.answer()


# Miqdorni o'zgartirishni boshlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:ord_mat:") and c.data.endswith(":edit_qty"), state="*")
async def start_edit_material_quantity_handler(callback_query: types.CallbackQuery, state: FSMContext):
    entry_id = int(callback_query.data.split(":")[2])
    await state.update_data(current_entry_id=entry_id, message_to_edit_id=callback_query.message.message_id)
    await callback_query.message.edit_text("🔄 Yangi miqdorni kiriting (masalan: `12.5`):",
                                           reply_markup=get_cancel_keyboard())
    await Accounting.EDIT_MATERIAL_QUANTITY.set()
    await callback_query.answer()


# Yangi miqdorni qabul qilish
@dp.message_handler(state=Accounting.EDIT_MATERIAL_QUANTITY)
async def process_edit_material_quantity_handler(message: types.Message, state: FSMContext):
    try:
        new_quantity = float(message.text.strip().replace(',', '.'))
    except ValueError:
        return await message.reply("❗️ Xato. Iltimos, miqdorni faqat raqam bilan kiriting.")

    data = await state.get_data()
    entry_id = data.get('current_entry_id')

    await message.delete()  # Foydalanuvchi kiritgan raqamni o'chirish

    if not entry_id:
        await state.finish()
        return await message.answer("Xatolik yuz berdi, amal bekor qilindi.")

    await db.update_order_material_quantity(entry_id, new_quantity)

    order_id = await db.pool.fetchval("SELECT order_id FROM order_materials WHERE id=$1", entry_id)

    # Eski xabarni yangilangan buyurtma ma'lumotlari bilan tahrirlaymiz
    message_id_to_edit = data.get('message_to_edit_id')
    new_text = await format_order_details(order_id)
    # Klaviaturani qayta yasaymiz
    kb = get_order_details_menu(order_id)
    materials = await db.get_order_materials(order_id)
    if materials:
        for m in materials:
            kb.add(InlineKeyboardButton(f"📦 {m['name']}: {m['quantity']} {m['unit']}",
                                        callback_data=f"acc:ord_mat:{m['id']}:menu"))

    await bot.edit_message_text(new_text, chat_id=message.chat.id, message_id=message_id_to_edit, reply_markup=kb,
                                parse_mode="HTML")
    await state.finish()


# Material yozuvini o'chirish
@dp.callback_query_handler(lambda c: c.data.startswith("acc:ord_mat:") and c.data.endswith(":delete"), state="*")
async def delete_order_material_entry_handler(callback_query: types.CallbackQuery, state: FSMContext):
    entry_id = int(callback_query.data.split(":")[2])
    order_id = await db.pool.fetchval("SELECT order_id FROM order_materials WHERE id=$1", entry_id)

    await db.delete_order_material(entry_id)
    await callback_query.answer("✅ Material yozuvi o'chirildi!", show_alert=True)

    # Buyurtma ko'rinishini yangilaymiz
    callback_query.data = f"acc:order:{order_id}:view"
    await view_order_details(callback_query, state)


@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":final_report"), state="*")
async def show_final_report_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback_query.data.split(":")[2])
        report_text = await generate_final_report_text(order_id)

        if report_text:
            await callback_query.message.answer(report_text, parse_mode="HTML")
        else:
            await callback_query.answer("Hisobotni yaratish uchun ma'lumotlar yetarli emas.", show_alert=True)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi", show_alert=True)
    finally:
        await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:calc:year:"), state="*")
async def show_financial_months_handler(callback_query: types.CallbackQuery):
    """Tanlangan yil uchun oylar ro'yxatini ko'rsatadi."""
    year = int(callback_query.data.split(":")[3])
    months = await db.get_financial_months(year)

    kb = make_financial_months_keyboard(year, months)
    await callback_query.message.edit_text(
        f"<b>{year}-yil</b> uchun oyni tanlang:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:calc:month:"), state="*")
async def show_monthly_report_handler(callback_query: types.CallbackQuery):
    """Tanlangan oy uchun yakuniy moliyaviy hisobotni ko'rsatadi."""
    parts = callback_query.data.split(":")
    year = int(parts[3])
    month = int(parts[4])

    summary = await db.get_monthly_financial_summary(year, month)

    text = (
        f"📊 <b>{year}-yil, {OY_NOMI.get(f'{month:02}', '')} oyi uchun hisobot:</b>\n\n"
        f"📥 Jami tushum: <b>{summary['total_income']:,.2f} so'm</b>\n"
        f"📤 Jami xarajat (materiallar): <b>{summary['total_expenses']:,.2f} so'm</b>\n\n"
        f"💰 Sof Foyda: <b>{summary['net_profit']:,.2f} so'm</b>"
    )

    await callback_query.message.edit_text(
        text,
        reply_markup=get_back_button(f"acc:calc:year:{year}"),
        parse_mode="HTML"
    )
    await callback_query.answer()


# --- Kategoriya qo'shish FSM zanjiri ---

# 1-qadam: Jarayonni boshlash (YANGI VERSIYA)
@dp.callback_query_handler(lambda c: c.data.startswith("acc:category:add:"), state="*")
async def start_add_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Yangi kategoriya qo'shish jarayonini boshlaydi."""
    parent_id_str = callback_query.data.split(":")[-1]

    parent_id = int(parent_id_str) if parent_id_str != "root" else None

    # Xatolikni tuzatish uchun: tahrirlanadigan xabar ID sini eslab qolamiz
    await state.update_data(
        parent_category_id=parent_id,
        message_to_edit_id=callback_query.message.message_id
    )

    await callback_query.message.edit_text(
        "✍️ Yangi kategoriya nomini kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    await MaterialCategory.ENTERING_NAME.set()
    await callback_query.answer()


# 2-qadam: Nomni qabul qilib, bazaga saqlash va oynani yangilash (YANGI VERSIYA)
@dp.message_handler(state=MaterialCategory.ENTERING_NAME)
async def process_new_category_name_handler(message: types.Message, state: FSMContext):
    """Kiritilgan kategoriya nomini qayta ishlaydi va saqlaydi."""
    new_name = message.text.strip()
    if not new_name:
        await message.reply("❌ Kategoriya nomi bo'sh bo'lishi mumkin emas. Iltimos, qayta kiriting.")
        return

    data = await state.get_data()
    parent_id = data.get("parent_category_id")
    message_to_edit_id = data.get("message_to_edit_id")

    # Foydalanuvchi yozgan xabarni o'chirib tashlaymiz
    await message.delete()

    if message_to_edit_id is None:
        await state.finish()
        await bot.send_message(message.chat.id, "❌ Xatolik yuz berdi. Bosh menyuga qaytildi.",
                               reply_markup=get_admin_main_menu(message.from_user.id))
        return

    # Yangi kategoriyani bazaga qo'shamiz
    category_id = await db.create_material_category(name=new_name, parent_id=parent_id)
    await state.finish()

    if category_id:
        # Endi to'g'ri oynani yangilaymiz
        parent_id_str = str(parent_id) if parent_id is not None else "root"

        path = await db.get_category_path(parent_id) if parent_id else []
        sub_categories = await db.get_material_categories(parent_id=parent_id)
        materials_in_category = await db.get_materials_in_category(category_id=parent_id)

        title = path[-1]['name'] if path else "Bosh Sahifa"
        text = f"🗂️ Kategoriya: <b>{title}</b>\n\n✅ <i>\"{new_name}\" nomli yangi ichki kategoriya qo'shildi.</i>"

        keyboard = get_material_category_browser(
            path=path,
            sub_categories=sub_categories,
            materials=materials_in_category
        )

        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_to_edit_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        # Agar bunday nomli kategoriya mavjud bo'lsa yoki boshqa xatolik bo'lsa
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_to_edit_id,
            text=f"❌ \"{new_name}\" nomli kategoriya qo'shishda xatolik yuz berdi. Bu nom allaqachon mavjud bo'lishi mumkin.",
            reply_markup=get_back_button(f"acc:category:browse:{parent_id or 'root'}")
        )


# --- Kategoriyani Tahrirlash va O'chirish ---

# 1. "Joriy kategoriyani tahrirlash" tugmasi bosilganda menyuni ko'rsatadi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:category:edit_menu:"), state="*")
async def show_category_edit_menu_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Kategoriyani tahrirlash uchun (nomini o'zgartirish, o'chirish) menyuni ko'rsatadi."""
    try:
        category_id = int(callback_query.data.split(":")[-1])
        category = await db.get_material_category_by_id(category_id)
        if not category:
            return await callback_query.answer("Kategoriya topilmadi!", show_alert=True)

        await callback_query.message.edit_text(
            f"Tanlangan kategoriya: <b>{category['name']}</b>\n\nQuyidagi amallardan birini tanlang:",
            reply_markup=get_category_edit_menu(category_id)
        )
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
    finally:
        await callback_query.answer()


# 2. Nomini o'zgartirish jarayonini boshlaydi (YANGI VERSIYA)
@dp.callback_query_handler(lambda c: c.data.startswith("acc:category:edit_name:"), state="*")
async def start_edit_category_name_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Kategoriya nomini o'zgartirish uchun FSM holatini boshlaydi."""
    try:
        category_id = int(callback_query.data.split(":")[-1])
        # Xatolikni tuzatish uchun: tahrirlanadigan xabar ID sini eslab qolamiz
        await state.update_data(
            editing_category_id=category_id,
            message_to_edit_id=callback_query.message.message_id
        )

        await callback_query.message.edit_text(
            "✍️ Kategoriya uchun yangi nom kiriting:",
            reply_markup=get_cancel_keyboard()
        )
        await MaterialCategory.EDITING_NAME.set()
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
    finally:
        await callback_query.answer()


# 3. Kiritilgan yangi nomni saqlaydi (YANGI VERSIYA)
@dp.message_handler(state=MaterialCategory.EDITING_NAME)
async def process_edit_category_name_handler(message: types.Message, state: FSMContext):
    """Kiritilgan yangi nomni bazaga saqlaydi va oynani yangilaydi."""
    new_name = message.text.strip()
    if not new_name:
        await message.reply("❌ Nom bo'sh bo'lishi mumkin emas. Qayta kiriting.")
        return

    data = await state.get_data()
    category_id = data.get("editing_category_id")
    message_to_edit_id = data.get("message_to_edit_id")

    # Foydalanuvchi yozgan xabarni o'chirib tashlaymiz
    await message.delete()

    if not all([category_id, message_to_edit_id]):
        await state.finish()
        await bot.send_message(message.chat.id, "❌ Xatolik yuz berdi. Bosh menyuga qaytildi.",
                               reply_markup=get_admin_main_menu(message.from_user.id))
        return

    # Nomni bazada yangilaymiz
    await db.update_material_category(category_id, name=new_name)
    await state.finish()

    # Endi adashmasdan, to'g'ri oynani yangilaymiz
    # Buning uchun bizga kerakli ma'lumotlarni qayta yuklaymiz
    path = await db.get_category_path(category_id)
    sub_categories = await db.get_material_categories(parent_id=category_id)
    materials_in_category = await db.get_materials_in_category(category_id=category_id)

    title = path[-1]['name'] if path else "Bosh Sahifa"
    text = f"🗂️ Kategoriya: <b>{title}</b> (nomi o'zgartirildi)"

    keyboard = get_material_category_browser(
        path=path,
        sub_categories=sub_categories,
        materials=materials_in_category
    )

    # Eslab qolingan xabarni tahrirlaymiz
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message_to_edit_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# 4. O'chirishdan oldin foydalanuvchidan tasdiq so'raydi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:category:delete_prompt:"), state="*")
async def prompt_delete_category_handler(callback_query: types.CallbackQuery):
    """Kategoriyani o'chirishdan oldin tekshiradi va tasdiq so'raydi."""
    try:
        category_id = int(callback_query.data.split(":")[-1])

        # MUHIM TEKSHIRUV: Kategoriya bo'sh ekanligiga ishonch hosil qilamiz
        sub_categories = await db.get_material_categories(parent_id=category_id)
        materials = await db.get_materials_in_category(category_id=category_id)

        if sub_categories or materials:
            await callback_query.answer("❌ Bu kategoriyani o'chirib bo'lmaydi!", show_alert=True)
            await callback_query.message.edit_text(
                "Bu kategoriyaning ichida boshqa kategoriya yoki materiallar bor.\n\n"
                "O'chirish uchun avval ichidagi barcha elementlarni o'chiring yoki boshqa kategoriyaga ko'chiring.",
                reply_markup=get_back_button(f"acc:category:edit_menu:{category_id}")
            )
            return

        # Agar kategoriya bo'sh bo'lsa, tasdiq so'raymiz
        category = await db.get_material_category_by_id(category_id)
        await callback_query.message.edit_text(
            f"<b>DIQQAT!</b>\n\nSiz \"{category['name']}\" nomli kategoriyani butunlay o'chirmoqchimisiz? "
            f"Bu amalni orqaga qaytarib bo'lmaydi.",
            reply_markup=get_confirm_deletion_keyboard(f"acc:category:{category_id}:delete")
        )
    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
    finally:
        await callback_query.answer()


# 5. Tasdiqlangan o'chirishni amalga oshiradi (YANGI VERSIYA)
@dp.callback_query_handler(lambda c: c.data.endswith(":confirm_delete") and c.data.startswith("acc:category:"),
                           state="*")
async def confirm_delete_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Tasdiqlanganidan so'ng kategoriyani bazadan o'chiradi."""
    try:
        # Tahrirlanadigan xabarni eslab qolamiz
        message_to_edit = callback_query.message

        category_id = int(callback_query.data.split(":")[2])

        # O'chirishdan oldin ota-kategoriyaning ID sini eslab qolamiz
        category_info = await db.get_material_category_by_id(category_id)
        parent_id = category_info.get('parent_id') if category_info else None

        success = await db.delete_material_category(category_id)

        if success:
            await callback_query.answer("✅ Kategoriya muvaffaqiyatli o'chirildi!", show_alert=True)

            # Endi adashmasdan, to'g'ri oynani yangilaymiz
            path = await db.get_category_path(parent_id) if parent_id else []
            sub_categories = await db.get_material_categories(parent_id=parent_id)
            materials_in_category = await db.get_materials_in_category(category_id=parent_id)

            title = path[-1]['name'] if path else "Bosh Sahifa"
            text = f"🗂️ Kategoriya: <b>{title}</b>"

            keyboard = get_material_category_browser(
                path=path,
                sub_categories=sub_categories,
                materials=materials_in_category
            )

            await message_to_edit.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

        else:
            await callback_query.answer("❌ O'chirishda xatolik yuz berdi!", show_alert=True)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
    except Exception as e:
        # MessageNotModified xatoligini ushlaymiz (agar foydalanuvchi tugmani juda tez bossa)
        if "Message is not modified" in str(e):
            await callback_query.answer()
        else:
            logging.error(f"Kategoriyani o'chirishda kutilmagan xato: {e}")
            await callback_query.answer("Kutilmagan xatolik yuz berdi.", show_alert=True)


# 6. O'chirishni bekor qilish (YAKUNIY, TO'G'RI VERSIYA)
@dp.callback_query_handler(lambda c: c.data.endswith(":cancel_delete") and c.data.startswith("acc:category:"),
                           state="*")
async def cancel_delete_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """O'chirishni bekor qiladi va kategoriyani tahrirlash menyusiga qaytaradi."""
    try:
        # Callback datadan to'g'ri ID ni olamiz: "acc:category:ID:delete:cancel_delete"
        # Bu yerda ID uchinchi element ([2])
        category_id = int(callback_query.data.split(":")[2])

        category = await db.get_material_category_by_id(category_id)
        if not category:
            await callback_query.answer("Kategoriya topilmadi!", show_alert=True)
            return

        # Xabarni to'g'ridan-to'g'ri shu yerda, boshqa funksiyani chaqirmasdan tahrirlaymiz
        await callback_query.message.edit_text(
            f"Tanlangan kategoriya: <b>{category['name']}</b>\n\nQuyidagi amallardan birini tanlang:",
            reply_markup=get_category_edit_menu(category_id)
        )
        await callback_query.answer()

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik yuz berdi (noto'g'ri ID).", show_alert=True)
    except Exception as e:
        logging.error(f"O'chirishni bekor qilishda xatolik: {e}")
        await callback_query.answer("Kutilmagan xatolik yuz berdi.", show_alert=True)


# --- Material qo'shish FSM zanjiri ---

# 1-qadam: Jarayonni boshlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:add:"), state="*")
async def start_add_material_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Yangi material qo'shish jarayonini boshlaydi."""
    category_id_str = callback_query.data.split(":")[-1]
    category_id = int(category_id_str) if category_id_str != "root" else None

    await state.update_data(
        target_category_id=category_id,
        message_to_edit_id=callback_query.message.message_id
    )

    await callback_query.message.edit_text(
        "📦 Yangi material nomini kiriting:",
        reply_markup=get_cancel_keyboard()
    )
    await Accounting.GET_MATERIAL_NAME.set()
    await callback_query.answer()


# 2-qadam: Nomni qabul qilib, o'lchov birligini so'rash
@dp.message_handler(state=Accounting.GET_MATERIAL_NAME)
async def process_material_name_handler(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())

    # Eskirgan xabarni tahrirlaymiz
    data = await state.get_data()
    message_id = data.get("message_to_edit_id")

    await message.delete()  # Foydalanuvchi yuborgan xabarni o'chiramiz

    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message_id,
        text="📏 O'lchov birligini kiriting (masalan: dona, kg, m², list):",
        reply_markup=get_cancel_keyboard()
    )
    await Accounting.GET_MATERIAL_UNIT.set()


# 3-qadam: O'lchov birligini qabul qilib, narxini so'rash
@dp.message_handler(state=Accounting.GET_MATERIAL_UNIT)
async def process_material_unit_handler(message: types.Message, state: FSMContext):
    await state.update_data(unit=message.text.strip())

    data = await state.get_data()
    message_id = data.get("message_to_edit_id")

    await message.delete()

    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message_id,
        text="💰 1 birlik uchun narxini kiriting (so'mda, faqat raqam):",
        reply_markup=get_cancel_keyboard()
    )
    await Accounting.GET_MATERIAL_COST.set()


# 4-qadam: Narxni qabul qilib, rasmini so'rash
@dp.message_handler(state=Accounting.GET_MATERIAL_COST)
async def process_material_cost_handler(message: types.Message, state: FSMContext):
    try:
        cost = float(message.text.strip())
        await state.update_data(cost=cost)
    except ValueError:
        await message.reply("❗️ Xato. Iltimos, narxni faqat raqam bilan kiriting.")
        return

    data = await state.get_data()
    message_id = data.get("message_to_edit_id")

    await message.delete()

    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message_id,
        text="🖼️ Endi material rasmini yuboring.\n\nRasm qo'shishni istamasangiz, shunchaki \"yoq\" deb yozing.",
        reply_markup=get_cancel_keyboard()
    )
    await Accounting.GET_MATERIAL_PHOTO.set()


# 5-qadam (A): Rasmni qabul qilib, materialni saqlash (YANGI VERSIYA)
@dp.message_handler(content_types=types.ContentType.PHOTO, state=Accounting.GET_MATERIAL_PHOTO)
async def process_material_photo_handler(message: types.Message, state: FSMContext):
    """Rasm qabul qilinganda ishlaydi. Ortiqcha xabar yubormaydi."""
    # Eng katta o'lchamdagi rasmning file_id sini olamiz
    photo_file_id = message.photo[-1].file_id
    await state.update_data(image_url=photo_file_id)

    # Ortiqcha "Rasm qabul qilindi..." degan xabar olib tashlandi
    # Foydalanuvchi yuborgan rasm xabarini o'chirib yuboramiz
    await message.delete()

    # Asosiy saqlash funksiyasini chaqiramiz
    await save_material_and_finish(message, state)


# 5-qadam (B): Rasm yuborilmasa ("yoq" so'zi) (YANGI VERSIYA)
@dp.message_handler(lambda message: message.text.lower() in ["yoq", "йук", "yo'q"], state=Accounting.GET_MATERIAL_PHOTO)
async def process_no_photo_handler(message: types.Message, state: FSMContext):
    """Rasm yuborish bekor qilinganda ishlaydi. Ortiqcha xabar yubormaydi."""
    await state.update_data(image_url=None)  # Rasm yo'qligini bildirish uchun

    # Ortiqcha "OK. Material rasmsiz saqlanadi" degan xabar olib tashlandi
    # Foydalanuvchi yuborgan "yoq" degan xabarni o'chirib yuboramiz
    await message.delete()

    # Asosiy saqlash funksiyasini chaqiramiz
    await save_material_and_finish(message, state)


# 5-qadam (C): Rasm o'rniga boshqa narsa yuborilsa
@dp.message_handler(state=Accounting.GET_MATERIAL_PHOTO)
async def incorrect_photo_handler(message: types.Message, state: FSMContext):
    """Rasm o'rniga matn yoki boshqa fayl yuborilganda ishlaydi."""
    await message.reply("❗️ Xato. Iltimos, rasm yuboring yoki \"yoq\" deb yozing.")


# YAKUNIY FUNKSIYA: Barcha ma'lumotlarni yig'ib, bazaga saqlash (YANGI VERSIYA)
async def save_material_and_finish(message: types.Message, state: FSMContext):
    """Barcha ma'lumotlarni olib, materialni bazaga saqlaydi va oynani yangilaydi."""
    data = await state.get_data()

    # Barcha ma'lumotlar mavjudligini tekshiramiz
    if not all(k in data for k in ['name', 'unit', 'cost', 'target_category_id', 'message_to_edit_id']):
        await state.finish()
        await message.answer("❌ Noma'lum xatolik yuz berdi. Boshidan boshlang.", reply_markup=get_admin_main_menu(message.from_user.id))
        return

    # Ma'lumotlarni bazaga yozamiz
    success = await db.create_material(
        name=data['name'],
        unit=data['unit'],
        unit_cost=data['cost'],
        category_id=data['target_category_id'],
        image_url=data.get('image_url')  # Rasm bo'lmasa None bo'ladi
    )

    await state.finish()

    if success:
        # Oynani yangilaymiz
        category_id = data['target_category_id']
        message_id = data['message_to_edit_id']  # <<<--- XATOLIK SHU YERDA EDI, BU QATOR QO'SHILDI

        path = await db.get_category_path(category_id) if category_id else []
        sub_categories = await db.get_material_categories(parent_id=category_id)
        materials_in_category = await db.get_materials_in_category(category_id=category_id)

        title = path[-1]['name'] if path else "Bosh Sahifa"
        text = f"🗂️ Kategoriya: <b>{title}</b>\n\n✅ <i>\"{data['name']}\" nomli yangi material qo'shildi.</i>"

        keyboard = get_material_category_browser(
            path=path,
            sub_categories=sub_categories,
            materials=materials_in_category
        )

        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        # Agar bunday nomli material mavjud bo'lsa yoki boshqa xatolik bo'lsa
        # Eski oynani tahrirlaymiz, yangi xabar yubormaymiz
        message_id = data['message_to_edit_id']
        category_id_str = str(data['target_category_id']) if data['target_category_id'] is not None else "root"
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_id,
            text=f"❌ \"{data['name']}\" nomli materialni saqlashda xatolik yuz berdi. Bu nom allaqachon mavjud bo'lishi mumkin.",
            reply_markup=get_back_button(f"acc:category:browse:{category_id_str}")
        )


# --- Buyurtmaga Material Qo'shish (YANGI, KATEGORIYALI VERSIYA) ---

# 1-qadam: Jarayonni boshlaydi va kategoriya oynasini ochadi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:order:") and c.data.endswith(":add_mat"), state="*")
async def start_add_material_to_order_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Buyurtmaga material qo'shish uchun kategoriya brauzerini ochadi."""
    try:
        order_id = int(callback_query.data.split(":")[2])
        await state.update_data(current_order_id=order_id)

        # Maxsus holatga o'tkazamiz
        await Accounting.SELECTING_MATERIAL_FOR_ORDER.set()

        # Kategoriya brauzerini "root" dan boshlab chaqiramiz
        callback_query.data = "acc:category:browse:root"
        await browse_material_categories_handler(callback_query, state)

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik: Buyurtma ID'si topilmadi.", show_alert=True)


# 2-qadam: Kategoriya oynasidan material tanlanganda ishlaydi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":view"),
                           state=Accounting.SELECTING_MATERIAL_FOR_ORDER)
async def select_material_for_order_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Maxsus holatda material tanlanganda miqdorini so'raydi."""
    try:
        material_id = int(callback_query.data.split(":")[2])
        material = await db.get_material_by_id(material_id)
        if not material:
            return await callback_query.answer("Material topilmadi!", show_alert=True)

        await state.update_data(chosen_material_id=material_id)

        await callback_query.message.delete()
        await callback_query.message.answer(
            f"Tanlangan material: <b>{material['name']}</b>\n\n"
            f"Bu materialdan qancha sarfladingiz? ({material['unit']} hisobida)\n"
            f"Masalan: `1.5` yoki `10`",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        await Accounting.GET_MATERIAL_QUANTITY.set()

    except (ValueError, IndexError):
        await callback_query.answer("Xatolik: Material ID'si topilmadi.", show_alert=True)
    finally:
        await callback_query.answer()


# 3-qadam: Miqdorni qabul qilib, bazaga saqlaydi (ozgina o'zgartirilgan versiya)
@dp.message_handler(state=Accounting.GET_MATERIAL_QUANTITY)
async def process_material_quantity_handler(message: types.Message, state: FSMContext):
    """Material miqdorini qabul qiladi va uni buyurtmaga qo'shadi."""
    try:
        quantity = float(message.text.strip().replace(',', '.'))
    except ValueError:
        return await message.reply("❗️ Xato. Iltimos, miqdorni faqat raqam bilan kiriting.")

    data = await state.get_data()
    order_id = data.get('current_order_id')
    material_id = data.get('chosen_material_id')

    await message.delete()

    if not all([order_id, material_id]):
        await state.finish()
        await message.answer("❌ Kutilmagan xatolik yuz berdi. Bosh menyuga qaytildi.",
                             reply_markup=get_admin_main_menu(message.from_user.id))
        return

    await db.add_order_material(order_id=order_id, material_id=material_id, quantity=quantity)

    # Ishimiz tugagach, holatni tozalaymiz
    await state.finish()

    # Endi foydalanuvchini kategoriya oynasiga emas, buyurtma oynasiga qaytaramiz
    new_text = await format_order_details(order_id)
    new_keyboard = get_order_details_menu(order_id)
    await message.answer(new_text, reply_markup=new_keyboard, parse_mode="HTML")


# --- Materiallarni Ko'rish, Tahrirlash, O'chirish ---

# 1. Material ustiga bosilganda uning ma'lumotlarini ko'rsatish
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":view"), state="*")
async def view_material_details_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Tanlangan material haqida to'liq ma'lumotni (rasmi bilan) ko'rsatadi."""
    await state.finish()

    try:
        material_id = int(callback_query.data.split(":")[2])
        material = await db.get_material_by_id(material_id)
        if not material:
            return await callback_query.answer("❌ Material topilmadi!", show_alert=True)

        category_path = await db.get_category_path(material['category_id']) if material.get('category_id') else []
        path_str = " > ".join([p['name'] for p in category_path]) if category_path else "Kategoriyasiz"

        caption = (
            f"📦 <b>Material: {material['name']}</b>\n\n"
            f"<b>ID:</b> {material['id']}\n"
            f"<b>Kategoriya:</b> {path_str}\n"
            f"<b>O'lchov birligi:</b> {material['unit']}\n"
            f"<b>Narxi:</b> {float(material['unit_cost']):,.2f} so'm / {material['unit']}"
        )

        keyboard = get_material_details_menu(material_id, material.get('category_id'))

        # Ortiqcha oynalarni yo'qotish uchun eski xabarni o'chiramiz
        await callback_query.message.delete()

        if material.get('image_url'):
            image_url = material['image_url']
            # Agar web orqali yuklangan bo'lsa (lokal fayl)
            if image_url.startswith('/static/'):
                # Lokal fayl yo'lini to'g'irlaymiz
                # /static/images/... -> web/static/images/...
                local_path = f"web{image_url}"
                if os.path.exists(local_path):
                    with open(local_path, 'rb') as photo_file:
                        await callback_query.message.answer_photo(
                            photo=photo_file,
                            caption=caption,
                            reply_markup=keyboard
                        )
                else:
                    # Fayl topilmasa, matnli xabar
                     await callback_query.message.answer(
                        text=caption + "\n\n(Rasm fayli topilmadi)",
                        reply_markup=keyboard
                    )
            else:
                # Agar Telegram file_id bo'lsa
                await callback_query.message.answer_photo(
                    photo=image_url,
                    caption=caption,
                    reply_markup=keyboard
                )
        else:
            # Agar rasm bo'lmasa, oddiy matnli xabar yuboramiz
            await callback_query.message.answer(
                text=caption,
                reply_markup=keyboard
            )

    except Exception as e:
        logging.error(f"Materialni ko'rsatishda xatolik: {e}")
        await callback_query.answer("Xatolik yuz berdi.", show_alert=True)
    finally:
        await callback_query.answer()


# 2. Materialni o'chirish jarayoni
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":delete"), state="*")
async def prompt_delete_material_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Materialni o'chirishdan oldin tekshiradi va tasdiq so'raydi (rasmli va rasmsiz xabarlar uchun)."""
    try:
        material_id = int(callback_query.data.split(":")[2])

        # MUHIM TEKSHIRUV: Material buyurtmalarda ishlatilganmi?
        in_use_orders = await db.is_material_in_use(material_id)
        if in_use_orders:
            order_names = ", ".join(f"'{name}'" for name in in_use_orders)
            # Bu yerda ham xabarning turiga qarab tahrirlaymiz
            error_text = (f"❌ <b>Bu materialni o'chirib bo'lmaydi!</b>\n\n"
                          f"Sabab: U quyidagi buyurtma(lar)da ishlatilgan:\n<b>{order_names}</b>")
            error_keyboard = get_back_button(f"acc:material:{material_id}:view")

            if callback_query.message.photo:
                await callback_query.message.edit_caption(caption=error_text, reply_markup=error_keyboard)
            else:
                await callback_query.message.edit_text(text=error_text, reply_markup=error_keyboard, parse_mode="HTML")
            return await callback_query.answer("Amal bekor qilindi", show_alert=True)

        material = await db.get_material_by_id(material_id)
        if not material:
            return await callback_query.answer("Material topilmadi!", show_alert=True)

        # Agar material bo'sh bo'lsa, tasdiq so'raymiz
        confirmation_text = f"<b>DIQQAT!</b>\n\nSiz \"{material['name']}\" nomli materialni o'chirmoqchimisiz?"
        confirmation_keyboard = get_confirm_deletion_keyboard(f"acc:material:{material_id}:delete")

        # Xabarda rasm bor-yo'qligini tekshiramiz
        if callback_query.message.photo:
            await callback_query.message.edit_caption(
                caption=confirmation_text,
                reply_markup=confirmation_keyboard
            )
        else:
            await callback_query.message.edit_text(
                text=confirmation_text,
                reply_markup=confirmation_keyboard
            )

    except Exception as e:
        logging.error(f"Materialni o'chirishda xatolik (prompt): {e}")
    finally:
        await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":confirm_delete"),
                           state="*")
async def confirm_delete_material_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Materialni bazadan o'chiradi va kategoriya oynasini yangilaydi."""
    try:
        material_id = int(callback_query.data.split(":")[2])

        material_info = await db.get_material_by_id(material_id)
        parent_category_id = material_info.get('category_id') if material_info else None

        success = await db.delete_material(material_id)

        if success:
            await callback_query.answer("✅ Material muvaffaqiyatli o'chirildi!", show_alert=True)

            # O'chirilgandan so'ng ota-kategoriyaga qaytamiz
            parent_id_str = str(parent_category_id) if parent_category_id is not None else "root"
            callback_query.data = f"acc:category:browse:{parent_id_str}"
            await browse_material_categories_handler(callback_query, state)
        else:
            await callback_query.answer("❌ O'chirishda xatolik yuz berdi!", show_alert=True)

    except Exception as e:
        logging.error(f"Materialni o'chirishda xatolik (confirm): {e}")


# --- Materiallarni Tahrirlash FSM Zanjirlari (YAKUNIY, XATOSIZ VERSIYA) ---

async def _send_updated_material_view(message: types.Message, material_id: int, success_text: str = ""):
    """Tahrirlashdan so'ng materialning yangilangan ko'rinishini yuboruvchi yordamchi funksiya."""
    material = await db.get_material_by_id(material_id)
    if not material:
        await message.answer("❌ Material topilmadi.")
        return

    category_path = await db.get_category_path(material['category_id']) if material.get('category_id') else []
    path_str = " > ".join([p['name'] for p in category_path]) if category_path else "Kategoriyasiz"

    caption = (
        f"{success_text}\n\n"
        f"📦 <b>Material: {material['name']}</b>\n"
        f"<b>ID:</b> {material['id']}\n"
        f"<b>Kategoriya:</b> {path_str}\n"
        f"<b>O'lchov birligi:</b> {material['unit']}\n"
        f"<b>Narxi:</b> {float(material['unit_cost']):,.2f} so'm / {material['unit']}"
    )

    keyboard = get_material_details_menu(material_id, material.get('category_id'))

    if material.get('image_url'):
        image_url = material['image_url']
        if image_url.startswith('/static/'):
            local_path = f"web{image_url}"
            if os.path.exists(local_path):
                with open(local_path, 'rb') as photo_file:
                    await message.answer_photo(photo=photo_file, caption=caption, reply_markup=keyboard)
            else:
                await message.answer(text=caption + "\n\n(Rasm fayli topilmadi)", reply_markup=keyboard)
        else:
            await message.answer_photo(photo=image_url, caption=caption, reply_markup=keyboard)
    else:
        await message.answer(text=caption, reply_markup=keyboard)


# Tahrirlashning asosiy menyusi
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit"), state="*")
async def show_material_edit_menu_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    try:
        material_id = int(callback_query.data.split(":")[2])
        material = await db.get_material_by_id(material_id)
        if not material:
            return await callback_query.answer("Material topilmadi!", show_alert=True)

        text = f"✏️ <b>\"{material['name']}\"</b> materialini tahrirlash\n\nQaysi qismini o'zgartirmoqchisiz?"
        keyboard = get_material_edit_menu(material_id)

        if callback_query.message.photo:
            await callback_query.message.edit_caption(caption=text, reply_markup=keyboard)
        else:
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Material tahrirlash menyusini ko'rsatishda xato: {e}")
    finally:
        await callback_query.answer()


# Nomini tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit_name"), state="*")
async def start_edit_material_name_handler(callback_query: types.CallbackQuery, state: FSMContext):
    material_id = int(callback_query.data.split(":")[2])
    await state.update_data(editing_material_id=material_id)
    await callback_query.message.delete()
    await callback_query.message.answer("✍️ Material uchun yangi nom kiriting:", reply_markup=get_cancel_keyboard())
    await Accounting.EDIT_MATERIAL_NAME.set()
    await callback_query.answer()


@dp.message_handler(state=Accounting.EDIT_MATERIAL_NAME)
async def process_edit_material_name_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    material_id = data.get("editing_material_id")
    await db.update_material(material_id, name=message.text.strip())
    await state.finish()
    await message.delete()
    await _send_updated_material_view(message, material_id, "✅ Nomi muvaffaqiyatli o'zgartirildi!")


# Rasm tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit_image"), state="*")
async def start_edit_material_image_handler(callback_query: types.CallbackQuery, state: FSMContext):
    material_id = int(callback_query.data.split(":")[2])
    await state.update_data(editing_material_id=material_id)
    await callback_query.message.delete()
    await callback_query.message.answer("🖼️ Yangi rasmni yuboring yoki \"ochirish\" deb yozib, rasmni olib tashlang.",
                                        reply_markup=get_cancel_keyboard())
    await Accounting.GET_MATERIAL_PHOTO.set()
    await callback_query.answer()


@dp.message_handler(lambda message: message.text.lower() in ["ochirish", "o'chirish"],
                    state=Accounting.GET_MATERIAL_PHOTO)
async def process_remove_photo_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    material_id = data.get("editing_material_id")
    await db.update_material(material_id, image_url=None)
    await state.finish()
    await message.delete()
    await _send_updated_material_view(message, material_id, "✅ Rasm muvaffaqiyatli o'chirildi!")


@dp.message_handler(content_types=types.ContentType.PHOTO, state=Accounting.GET_MATERIAL_PHOTO)
async def process_edit_material_photo_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    material_id = data.get("editing_material_id")
    photo_file_id = message.photo[-1].file_id
    await db.update_material(material_id, image_url=photo_file_id)
    await state.finish()
    await message.delete()
    await _send_updated_material_view(message, material_id, "✅ Rasm muvaffaqiyatli yangilandi!")


# O'lchov birligini tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit_unit"), state="*")
async def start_edit_material_unit_handler(callback_query: types.CallbackQuery, state: FSMContext):
    material_id = int(callback_query.data.split(":")[2])
    await state.update_data(editing_material_id=material_id)
    await callback_query.message.delete()
    await callback_query.message.answer("📏 Material uchun yangi o'lchov birligini kiriting (masalan: dona, list, m²):",
                                        reply_markup=get_cancel_keyboard())
    await Accounting.EDIT_MATERIAL_UNIT.set()
    await callback_query.answer()


@dp.message_handler(state=Accounting.EDIT_MATERIAL_UNIT)
async def process_edit_material_unit_handler(message: types.Message, state: FSMContext):
    new_unit = message.text.strip()
    data = await state.get_data()
    material_id = data.get("editing_material_id")
    await db.update_material(material_id, unit=new_unit)
    await state.finish()
    await message.delete()
    await _send_updated_material_view(message, material_id, "✅ O'lchov birligi o'zgartirildi!")


# Narxini tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit_cost"), state="*")
async def start_edit_material_cost_handler(callback_query: types.CallbackQuery, state: FSMContext):
    material_id = int(callback_query.data.split(":")[2])
    await state.update_data(editing_material_id=material_id)
    await callback_query.message.delete()
    await callback_query.message.answer("💰 Material uchun yangi narxni kiriting (faqat raqam):",
                                        reply_markup=get_cancel_keyboard())
    await Accounting.EDIT_MATERIAL_COST.set()
    await callback_query.answer()


@dp.message_handler(state=Accounting.EDIT_MATERIAL_COST)
async def process_edit_material_cost_handler(message: types.Message, state: FSMContext):
    try:
        new_cost = float(message.text.strip())
    except ValueError:
        return await message.reply("❗️ Xato. Iltimos, narxni faqat raqam bilan kiriting.")
    data = await state.get_data()
    material_id = data.get("editing_material_id")
    await db.update_material(material_id, unit_cost=new_cost)
    await state.finish()
    await message.delete()
    await _send_updated_material_view(message, material_id, "✅ Narxi muvaffaqiyatli o'zgartirildi!")


# Kategoriyasini tahrirlash
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and c.data.endswith(":edit_category"),
                           state="*")
async def start_edit_material_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    material_id = int(callback_query.data.split(":")[2])
    page = 0
    all_categories = await db.get_all_material_categories_flat()
    total_items = len(all_categories)

    start_index = page * PER_PAGE
    end_index = start_index + PER_PAGE
    page_items_data = all_categories[start_index:end_index]

    items = [(cat['name'], f"acc:material:{material_id}:set_category:{cat['id']}") for cat in page_items_data]
    keyboard = get_category_selection_keyboard(page, total_items, items, material_id)

    text = "🗂️ Material uchun yangi kategoriyani tanlang:"
    if callback_query.message.photo:
        await callback_query.message.edit_caption(caption=text, reply_markup=keyboard)
    else:
        await callback_query.message.edit_text(text, reply_markup=keyboard)
    await callback_query.answer()


# Kategoriya tanlash oynasi uchun paginatsiya
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and ":select_cat_page:" in c.data, state="*")
async def process_edit_material_category_pagination_handler(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(":")
    material_id = int(parts[2])
    page = int(parts[4])
    all_categories = await db.get_all_material_categories_flat()
    total_items = len(all_categories)

    start_index = page * PER_PAGE
    end_index = start_index + PER_PAGE
    page_items_data = all_categories[start_index:end_index]

    items = [(cat['name'], f"acc:material:{material_id}:set_category:{cat['id']}") for cat in page_items_data]
    keyboard = get_category_selection_keyboard(page, total_items, items, material_id)

    await callback_query.message.edit_text("🗂️ Material uchun yangi kategoriyani tanlang:", reply_markup=keyboard)
    await callback_query.answer()


# Yangi kategoriya tanlanganda
@dp.callback_query_handler(lambda c: c.data.startswith("acc:material:") and ":set_category:" in c.data, state="*")
async def process_select_new_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(":")
    material_id = int(parts[2])
    new_category_id_str = parts[4]

    new_category_id = int(new_category_id_str) if new_category_id_str != "root" else None
    await db.update_material(material_id, category_id=new_category_id)

    await callback_query.message.delete()
    await _send_updated_material_view(callback_query, material_id, "✅ Kategoriya muvaffaqiyatli o'zgartirildi!")
    await callback_query.answer()


# --- Aqlli Qidiruv (Smart Search) ---

# 1. Qidiruv jarayonini boshlaydi
@dp.callback_query_handler(lambda c: c.data == "acc:material:search_start", state="*")
async def start_material_search_handler(callback_query: types.CallbackQuery, state: FSMContext):
    """Foydalanuvchidan qidiruv so'rovini kiritishni so'raydi."""
    await state.set_state(Accounting.WAITING_FOR_SEARCH_QUERY)
    await callback_query.message.delete()
    await callback_query.message.answer(
        "🔍 Qidirish uchun material nomini, turini yoki kategoriyasini yozing:",
        reply_markup=get_back_button("acc:materials:main")
    )
    await callback_query.answer()


# 2. Qidiruv so'rovini qayta ishlaydi va natijalarni ko'rsatadi
@dp.message_handler(state=Accounting.WAITING_FOR_SEARCH_QUERY)
async def process_material_search_handler(message: types.Message, state: FSMContext):
    """Kiritilgan matn bo'yicha bazadan materiallarni qidiradi va natijalarni tugmalar ko'rinishida qaytaradi."""
    query = message.text.strip()
    await state.finish()

    if len(query) < 2:
        await message.reply("Qidiruv uchun kamida 2 ta harf kiriting.")
        return

    # database.py dagi aqlli qidiruv funksiyamizni chaqiramiz
    results = await db.search_materials(query)

    if not results:
        await message.reply(f"❌ \"{query}\" bo'yicha hech narsa topilmadi.", reply_markup=get_materials_menu())
        return

    # Natijalarni tugma ko'rinishiga o'tkazamiz
    items = []
    for material in results:
        # Tugma matni va bosilganda yuboriladigan ma'lumotni tayyorlaymiz
        button_text = f"📦 {material['name']}"
        callback_data = f"acc:material:{material['id']}:view"
        items.append((button_text, callback_data))

    # Bizda tayyor paginatsiya funksiyasi bor, undan foydalanamiz
    # Hozircha paginatsiyasiz, bitta ro'yxatda chiqaramiz. Agar kerak bo'lsa, keyin sahifalarga bo'lamiz.
    keyboard = InlineKeyboardMarkup(row_width=1)
    for text, data in items:
        keyboard.add(InlineKeyboardButton(text=text, callback_data=data))

    keyboard.add(InlineKeyboardButton("⬅️ Orqaga", callback_data="acc:materials:main"))

    await message.answer(
        f"✅ \"{query}\" bo'yicha topilgan natijalar ({len(results)} ta):\n\n"
        f"To'liq ma'lumot uchun kerakli tugmani bosing.",
        reply_markup=keyboard
    )
