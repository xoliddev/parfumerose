# bot.py (asosiy ishga tushiruvchi fayl)

import logging
import datetime
import os
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
from config import ABSENCE_REMINDER_DELAY_MIN, BOT_TOKEN, SUPERADMINS
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


def _resolve_backup_recipients() -> list[int]:
    """Backup faylini kim oladi?

    BACKUP_RECIPIENTS env (vergulli tg_id'lar) yoki SUPERADMINS — agar env bo'sh.
    Bu — admin'lar Telegram ID'lari (chat_id ham shu, lichkaga yuboriladi).
    """
    raw = os.getenv("BACKUP_RECIPIENTS", "").strip()
    if raw:
        ids = []
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                ids.append(int(part))
        if ids:
            return ids
    return list(SUPERADMINS)


async def send_daily_backup_job():
    """Har kuni ertalab PostgreSQL bazasining to'liq nusxasini admin(lar)ga lichkaga yuboradi.

    Mantiq: pg_dump $DATABASE_URL | gzip > /tmp/parfumerose_YYYY-MM-DD.sql.gz, so'ng
    har bir qabul qiluvchiga send_document. Telegram bot fayl limiti 50 MB — bazamiz
    bundan ancha kichik. Xato bo'lsa — log + adminlarga matnli xabar.
    """
    import asyncio
    import gzip
    import shutil
    import tempfile

    recipients = _resolve_backup_recipients()
    if not recipients:
        logging.warning("Backup: hech qanday qabul qiluvchi yo'q (SUPERADMINS bo'sh).")
        return

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logging.error("Backup: DATABASE_URL topilmadi.")
        return

    today_str = datetime.date.today().isoformat()
    tmp_dir = tempfile.mkdtemp(prefix="pgbackup_")
    sql_path = os.path.join(tmp_dir, f"parfumerose_{today_str}.sql")
    gz_path = sql_path + ".gz"

    try:
        # 1) pg_dump → sql fayl
        proc = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            "--clean",
            "--if-exists",
            "-f", sql_path,
            database_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr_bytes or b"").decode("utf-8", errors="replace")[:500]
            logging.error("Backup: pg_dump xatolik (%s): %s", proc.returncode, err)
            for tg_id in recipients:
                try:
                    await bot.send_message(
                        tg_id,
                        f"⚠️ Kunlik backup OLINMADI ({today_str}).\nXato: <code>{err}</code>",
                    )
                except Exception:
                    pass
            return

        # 2) gzip bilan siqamiz
        with open(sql_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        size_mb = os.path.getsize(gz_path) / 1024 / 1024

        # 3) har bir adminga yuboramiz
        from aiogram.types import InputFile
        caption = (
            f"💾 <b>Kunlik bazaning nusxasi</b>\n"
            f"📅 {today_str}\n"
            f"📦 Hajmi: {size_mb:.2f} MB\n\n"
            "Tiklash: <code>gunzip -c fayl.sql.gz | psql $DATABASE_URL</code>"
        )
        for tg_id in recipients:
            try:
                # Har gal yangi InputFile (aiogram fayl handle'ni iste'mol qiladi)
                await bot.send_document(
                    tg_id,
                    InputFile(gz_path, filename=os.path.basename(gz_path)),
                    caption=caption,
                )
            except Exception as exc:
                logging.error("Backup: %s ga yuborilmadi: %s", tg_id, exc)
        logging.info("✅ Kunlik backup yuborildi (%s adminga, %.2f MB)", len(recipients), size_mb)
    except Exception as exc:
        logging.exception("Backup job kutilmagan xato: %s", exc)
    finally:
        # Vaqtinchalik fayllarni tozalaymiz
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@dp.message_handler(commands=["backup"], state="*")
async def manual_backup_command(message, state):
    """Qo'lda backup chaqirish — istalgan vaqtda darhol backup olish uchun.

    Faqat SUPERADMIN'lar uchun. Backup BACKUP_RECIPIENTS env'ga (yoki bo'sh bo'lsa
    SUPERADMINS'ga) yuboriladi — buyruqni kim yuborgani emas.
    """
    if message.from_user.id not in SUPERADMINS:
        return  # boshqa foydalanuvchilar uchun jimgina e'tiborsizlik

    recipients = _resolve_backup_recipients()
    if not recipients:
        return await message.reply(
            "⚠️ BACKUP_RECIPIENTS env ham, SUPERADMINS ham bo'sh — yuborib bo'lmaydi."
        )

    await message.reply(
        "⏳ Backup tayyorlanmoqda...\n"
        f"📤 Qabul qiluvchi: {len(recipients)} ta admin\n"
        "⌛ Bir necha soniya kuting."
    )
    try:
        await send_daily_backup_job()
        await message.reply("✅ Backup yuborildi. Belgilangan admin(lar) lichkasini tekshiring.")
    except Exception as exc:
        logging.exception("Manual backup xatosi")
        await message.reply(f"❌ Backup xatosi: <code>{type(exc).__name__}: {exc}</code>")


def schedule_jobs():
    """Shaxsiy hisobot vazifalarini rejalashtiradi."""
    # Ertalabki hisobot (Dushanba-Shanba, soat 08:30 da)
    scheduler.add_job(
        send_morning_briefings,
        trigger=CronTrigger(hour=8, minute=30, day_of_week='mon-sat'),
    )
    # Kunlik baza backupi (har kuni soat 06:00, Toshkent)
    scheduler.add_job(
        send_daily_backup_job,
        trigger=CronTrigger(hour=6, minute=0),
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


_health_runner = None


async def _start_health_server():
    """Koyeb/Render 'web service' uxlab qolmasligi uchun mini HTTP health-server.

    Bot long-polling bilan ishlaydi va hech qanday port tinglamaydi. Web service
    esa $PORT'da health-check kutadi — port bo'lmasa platforma instansiyani 'bo'sh'
    deb bilib uxlatadi (cold-start sekinligi). Bu server $PORT'da 200 qaytaradi,
    shunda Koyeb instansiyani doim 'sog'lom' deb biladi va uxlatmaydi.
    """
    global _health_runner
    from aiohttp import web  # aiogram 2.x allaqachon aiohttp'ga tayanadi, qo'shimcha kerak emas

    port = int(os.getenv("PORT", "8000"))

    async def _ok(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", _ok)
    app.router.add_get("/health", _ok)

    _health_runner = web.AppRunner(app)
    await _health_runner.setup()
    await web.TCPSite(_health_runner, "0.0.0.0", port).start()
    logging.info("✅ Health-server ishga tushdi: 0.0.0.0:%s (Koyeb uxlatmasligi uchun)", port)


async def on_startup(dispatcher):
    """Bot ishga tushganda bajariladigan amallar."""
    print("Bot ishga tushmoqda...")

    # 0. Health-server (Koyeb web service uxlab qolmasligi uchun) — eng avval ishga tushadi
    try:
        await _start_health_server()
    except Exception as exc:
        logging.error("Health-server ishga tushmadi: %s", exc)

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
    # Health-serverni yopamiz
    if _health_runner:
        try:
            await _health_runner.cleanup()
        except Exception:
            pass
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
