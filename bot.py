# bot.py (asosiy ishga tushiruvchi fayl)

import logging
import datetime
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Asosiy bot va dispatcher obyektlarini `loader`dan olamiz
from loader import dp, bot
from aiogram.utils.exceptions import (
    MessageNotModified,
    MessageToEditNotFound,
    MessageCantBeEdited,
    MessageToDeleteNotFound,
    MessageTextIsEmpty,
    InvalidQueryID,
)

# Ma'lumotlar bazasi bilan ishlash uchun kerakli funksiyalar
import database as db
from config import ABSENCE_REMINDER_DELAY_MIN, BOT_TOKEN
from states import UserAttendance
from shared import (
    build_absence_review_keyboard,
    register_admin_action_messages,
    notify_admins,
)

# Barcha handlerlarimizni dispatcherga ro'yxatdan o'tkazish uchun import qilamiz
import handlers.admin_handlers
import handlers.admin_extensions
import handlers.user_handlers

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)

# AsyncIOScheduler nusxasini yaratamiz, vaqt zonasi - Toshkent
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")


# ===== Global xato-ushlagich =====
# Handlerdagi har qanday kutilmagan xato butun amalni "muzlatib" qo'ymasligi uchun.
# "Yumshoq" Telegram xatolarini (xabar o'zgarmadi, tahrirlab bo'lmadi, callback eskirdi)
# jimgina e'tiborsiz qoldiramiz; qolganini log qilamiz va callback'ni yopamiz
# (foydalanuvchida "yuklanyapti" belgisi osilib qolmasin).
_SILENT_TELEGRAM_ERRORS = (
    MessageNotModified,
    MessageToEditNotFound,
    MessageCantBeEdited,
    MessageToDeleteNotFound,
    MessageTextIsEmpty,
    InvalidQueryID,
)


@dp.errors_handler()
async def global_error_handler(update, exception):
    callback_query = getattr(update, "callback_query", None)

    if isinstance(exception, _SILENT_TELEGRAM_ERRORS):
        if callback_query:
            try:
                await callback_query.answer()
            except Exception:
                pass
        return True  # ushlandi — polling davom etadi

    logging.exception("Handler xatosi (global ushlagich): %s", exception)
    if callback_query:
        try:
            await callback_query.answer(
                "Xatolik yuz berdi. Iltimos qayta urinib ko'ring.",
                show_alert=False,
            )
        except Exception:
            pass
    return True


async def send_morning_briefings():
    """Barcha faol xodimlarga ertalabki eslatma yuboradi."""
    logging.info("Ertalabki eslatmalarni yuborish boshlandi...")
    active_workers = await db.get_active_employees()

    for worker in active_workers:
        try:
            # Xodimning bugungi ish boshlash vaqtini olamiz
            worker_info = await db.pool.fetchrow("SELECT work_start FROM workers WHERE id = $1", worker['id'])
            start_time_str = worker_info['work_start'].strftime('%H:%M') if worker_info and worker_info[
                'work_start'] else "belgilanmagan"

            text = (
                f"Assalomu alaykum, {worker['full_name']}!\n\n"
                f"⏰ Bugungi ish boshlanish vaqtingiz: <b>{start_time_str}</b>.\n\n"
                f"Yaxshi kun tilayman!"
            )
            await bot.send_message(worker['tg_id'], text)
        except Exception as e:
            logging.error(f"{worker['id']} ID'li xodimga ertalabki xabar yuborishda xatolik: {e}")


async def send_evening_briefings():
    """Barcha faol xodimlarga shaxsiy kechki hisobotni yuboradi."""
    logging.info("Kechki shaxsiy hisobotlarni yuborish boshlandi...")
    active_workers = await db.get_active_employees()
    today = datetime.date.today()

    for worker in active_workers:
        try:
            # Xodimning bugungi ish sessiyasini olamiz
            session = await db.get_session_for_worker_on_date(worker['id'], today)

            # Agar xodim bugun ishga kelgan va ketgan bo'lsa (yoki hali ham ishda bo'lsa)
            if session and session.get('total_hours') is not None:
                work_hours_str = handlers.admin_handlers.format_hours(session.get('total_hours') or 0.0)

                text = (
                    f"Bugungi mehnatingiz uchun rahmat, {worker['full_name']}!\n\n"
                    f"📈 Bugun siz <b>{work_hours_str}</b> ishladingiz.\n\n"
                    f"Yaxshi dam oling!"
                )
                await bot.send_message(worker['tg_id'], text)
        except Exception as e:
            logging.error(f"{worker['id']} ID'li xodimga kechki xabar yuborishda xatolik: {e}")

async def check_late_arrivals_job():
    """Ishga kech qolganlarni tekshiradi va Adminga hisobot beradi."""
    try:
        today = datetime.date.today()
        # Dushanba-Shanba kunlari tekshiramiz
        if today.weekday() == 6:  # Yakshanba
            return

        threshold_time = datetime.time(9, 0) # Soat 09:00 dan kech qolganlar
        
        late_employees = await db.get_late_employees(today, threshold_time)
        
        if late_employees:
            grouped_employees = {}
            for employee in late_employees:
                grouped_employees.setdefault(employee.get("branch_id"), []).append(employee)

            for branch_id, employees in grouped_employees.items():
                branch_text = f"⏰ <b>KECH QOLGANLAR HISOBOTI ({today}):</b>\n\n"
                for emp in employees:
                    branch_text += f"👤 {emp['full_name']} - <b>{emp['status']}</b>\n"
                await notify_admins(branch_text, branch_id=branch_id, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Kech qolganlarni tekshirishda (check_late_arrivals_job) xatolik: {e}")


async def check_absence_followup_job():
    """Belgilangan vaqtdan o'tib ham start bosmaganlar bo'yicha eslatma yuboradi."""
    try:
        local_tz = datetime.timezone(datetime.timedelta(hours=5))
        now_local = datetime.datetime.now(local_tz)
        today = now_local.date()

        phone_workers = await db.get_workers_needing_absence_prompt(
            now_local,
            ABSENCE_REMINDER_DELAY_MIN,
            phone_only=True,
        )
        for worker in phone_workers:
            if not worker.get("tg_id"):
                continue
            try:
                action_key = f"absence_review:{worker['id']}:{today.isoformat()}"
                await bot.send_message(
                    worker["tg_id"],
                    "Bugun ishga kelmadingizmi? Agar kelgan bo'lsangiz iltimos botga kiriting, agar yo'q bo'lsa kelmaganingiz sababini yozing.",
                )
                user_state = dp.current_state(user=worker["tg_id"], chat=worker["tg_id"])
                await user_state.set_state(UserAttendance.waiting_for_message.state)
                await db.update_worker_day_status(
                    worker["id"],
                    today,
                    day_state="absent_pending",
                    absence_review_status="pending",
                    absence_prompted_at=datetime.datetime.now(datetime.timezone.utc),
                    last_source="system",
                )
                await db.log_worker_activity(
                    worker["id"],
                    "absence_prompt",
                    "Bot xodimdan kelmaganlik sababini so'radi",
                    None,
                    "system",
                    today,
                )
                admin_text = (
                    f"⚠️ {worker['full_name']} belgilangan vaqtdan {ABSENCE_REMINDER_DELAY_MIN} daqiqa o'tib ham "
                    f"start bosmadi.\nXodimga ogohlantirish yuborildi. Hozircha sabab yozilmagan."
                )
                sent_messages = await notify_admins(
                    admin_text,
                    reply_markup=build_absence_review_keyboard(worker["id"]),
                    worker_id=worker["id"],
                )
                await register_admin_action_messages(action_key, sent_messages)
            except Exception as exc:
                logging.error(f"Kelmaganlik eslatmasini yuborishda xatolik ({worker['id']}): {exc}")

        phone_less_workers = await db.get_workers_needing_absence_prompt(
            now_local,
            ABSENCE_REMINDER_DELAY_MIN,
            phone_only=False,
        )
        if phone_less_workers:
            grouped_workers = {}
            for worker in phone_less_workers:
                grouped_workers.setdefault(worker.get("branch_id"), []).append(worker)

            for branch_id, workers in grouped_workers.items():
                names = ", ".join(worker["full_name"] for worker in workers)
                branch_text = f"📌 {names} kelgan bo'lsa, kelganini belgilashni unutmang."
                await notify_admins(branch_text, branch_id=branch_id)
            for worker in phone_less_workers:
                await db.update_worker_day_status(
                    worker["id"],
                    today,
                    day_state="absent_pending",
                    absence_prompted_at=datetime.datetime.now(datetime.timezone.utc),
                    last_source="system",
                )
                await db.log_worker_activity(
                    worker["id"],
                    "manual_attendance_reminder",
                    "Telefonisiz xodim uchun admin reminder yuborildi",
                    None,
                    "system",
                    today,
                )
    except Exception as e:
        logging.error(f"Kelmaganlik reminder job xatoligi: {e}")


def schedule_jobs():
    """Shaxsiy hisobot vazifalarini rejalashtiradi."""
    # Ertalabki hisobot (Dushanba-Shanba, soat 08:30 da)
    scheduler.add_job(
        send_morning_briefings,
        trigger=CronTrigger(hour=8, minute=30, day_of_week='mon-sat'),
    )
    # Kechki hisobot (Dushanba-Shanba, soat 19:30 da)
    scheduler.add_job(
        send_evening_briefings,
        trigger=CronTrigger(hour=19, minute=30, day_of_week='mon-sat'),
    )
    
    scheduler.add_job(
        check_absence_followup_job,
        trigger=CronTrigger(minute="*/10", day_of_week='mon-sat'),
    )
    
    scheduler.add_job(
        check_late_arrivals_job,
        trigger=CronTrigger(hour=9, minute=30, day_of_week='mon-sat'),
    )
    
    logging.info("Shaxsiy hisobotlar muvaffaqiyatli rejalashtirildi.")


async def on_startup(dispatcher):
    """Bot ishga tushganda bajariladigan amallar."""
    print("Bot ishga tushmoqda...")

    # 1. PostgreSQL bilan ulanishlar hovuzini (pool) yaratamiz
    await db.create_pool()

    # 2. Baza jadvallarini yaratamiz yoki tekshiramiz
    await db.init_db()

    # 3. Rejalashtirilgan vazifalarni qo'shamiz
    schedule_jobs()

    # 4. Scheduler'ni ishga tushiramiz
    scheduler.start()

    print("Bot ishga tushdi. So'rovlarni kutmoqda...")


async def on_shutdown(dispatcher):
    """Bot to'xtaganda bajariladigan amallar."""
    logging.warning("Bot to'xtamoqda...")
    # Ulanishlar hovuzini yopamiz
    if db.pool:
        await db.pool.close()
    logging.warning("Baza bilan ulanish yopildi.")


if __name__ == "__main__":
    from aiogram import executor

    # `on_startup` va `on_shutdown` funksiyalarini executorga bog'laymiz
    print("🚀 Bot polling boshlanmoqda...")
    if not BOT_TOKEN:
        print("❌ FATAL: BOT_TOKEN topilmadi! Iltimos .env faylni yoki Render sozlamalarini tekshiring.")
        exit(1)
    
    # Kichik delay (Render loglarini ushlash uchun)
    import time
    time.sleep(2)
    
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown, skip_updates=True)
