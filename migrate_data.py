import sqlite3
import asyncpg
import asyncio
import datetime
from pytz import timezone
from dotenv import load_dotenv
import os
from typing import Optional, Dict

# 1. To'g'ridan-to'g'ri .env faylini yuklaymiz
load_dotenv()

# 2. Baza sozlamalarini olamiz
# 2. Baza sozlamalarini olamiz
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    import urllib.parse
    result = urllib.parse.urlparse(DATABASE_URL)
    POSTGRES_USER = result.username
    POSTGRES_PASSWORD = result.password
    POSTGRES_HOST = result.hostname
    POSTGRES_PORT = result.port or 5432
    POSTGRES_DB = result.path[1:]
else:
    POSTGRES_USER = os.getenv("POSTGRES_USER")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
    POSTGRES_DB = os.getenv("POSTGRES_DB")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
SQLITE_DB_PATH = 'data/attendance_bot.db'
uz_tz = timezone('Asia/Tashkent')


def parse_time_safely(time_str: str) -> Optional[datetime.time]:
    """Har xil formatdagi vaqt matnini xavfsiz 'datetime.time' obyektiga o'giradi."""
    if not time_str:
        return None
    try:
        # PGDrive'dan keladigan 'HH:MM' yoki 'HH:MM:SS' formatini to'g'ri o'girish
        cleaned_time_str = time_str.split('.')[0]
        return datetime.time.fromisoformat(cleaned_time_str)
    except (ValueError, TypeError):
        print(f"OGOHLANTIRISH: Noto'g'ri vaqt formati o'tkazib yuborildi: '{time_str}'")
        return None


async def create_tables(pool):
    """Kerakli barcha jadvallarni yaratadi va UNIQUE qoidalarini qo'shadi."""
    async with pool.acquire() as conn:
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS workers
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               tg_id
                               BIGINT
                               UNIQUE
                               NOT
                               NULL,
                               full_name
                               VARCHAR
                           (
                               255
                           ) NOT NULL,
                               username VARCHAR
                           (
                               255
                           ), monthly_salary NUMERIC
                           (
                               12,
                               2
                           ) DEFAULT 0.00,
                               added_date TIMESTAMP WITH TIME ZONE,
                                                        daily_work_hours NUMERIC (4, 2) DEFAULT 0.00, work_start TIME, work_end TIME
                               );
                           """)
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS work_sessions
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               user_id
                               INTEGER
                               REFERENCES
                               workers
                           (
                               id
                           ) ON DELETE CASCADE,
                               date DATE NOT NULL, arrival_time TIMESTAMP WITH TIME ZONE,
                               departure_time TIMESTAMP
                             WITH TIME ZONE, total_hours NUMERIC (4, 2) DEFAULT 0.00,
                               is_friday BOOLEAN DEFAULT FALSE, session_daily_hours NUMERIC
                           (
                               4,
                               2
                           ) DEFAULT 0.00,
                               late_reason TEXT,
                               CONSTRAINT work_sessions_user_id_date_key UNIQUE
                           (
                               user_id,
                               date
                           ) -- <--- ASOSIY QOIDA
                               );
                           """)
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS salary_payments
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               worker_id
                               INTEGER
                               REFERENCES
                               workers
                           (
                               id
                           ) ON DELETE CASCADE,
                               payment_date DATE NOT NULL, amount NUMERIC
                           (
                               12,
                               2
                           ) NOT NULL,
                               payment_time TIMESTAMP
                             WITH TIME ZONE,
                                 CONSTRAINT salary_payments_worker_id_payment_time_key UNIQUE (worker_id, payment_time) -- <--- DUBLIKAT OLDINI OLISH
                               );
                           """)
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS attendance
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               user_id
                               INTEGER
                               REFERENCES
                               workers
                           (
                               id
                           ) ON DELETE CASCADE,
                               name VARCHAR
                           (
                               255
                           ), timestamp TIMESTAMP
                             WITH TIME ZONE,
                                 latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, distance DOUBLE PRECISION,
                                 message TEXT, reason TEXT,
                                 CONSTRAINT attendance_user_id_timestamp_key UNIQUE (user_id, timestamp) -- <--- DUBLIKAT OLDINI OLISH
                               );
                           """)
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS settings
                           (
                               id
                               INTEGER
                               PRIMARY
                               KEY
                               CHECK
                           (
                               id =
                               1
                           ), rest_day INTEGER
                               );
                           """)
        await conn.execute("INSERT INTO settings(id, rest_day) VALUES (1, NULL) ON CONFLICT (id) DO NOTHING;")
    print("🛠️ PostgreSQL jadvallari tekshirildi/yaratildi.")


async def main():
    print("Ma'lumotlarni ko'chirish boshlandi...")
    pg_pool = None
    sqlite_conn = None
    try:
        pg_pool = await asyncpg.create_pool(user=POSTGRES_USER, password=POSTGRES_PASSWORD, database=POSTGRES_DB,
                                            host=POSTGRES_HOST, port=POSTGRES_PORT)
        print("✅ PostgreSQL bazasiga muvaffaqiyatli ulandi.")

        # Jadvallarni yaratish va kerakli qoidalarni o'rnatish
        await create_tables(pg_pool)

        if not os.path.exists(SQLITE_DB_PATH):
            print(f"⚠️ DIQQAT: SQLite bazasi topilmadi: {SQLITE_DB_PATH}")
            print("ℹ️ Ma'lumotlar migratsiyasi o'tkazib yuboriladi. Ilova bo'sh baza bilan ishga tushadi.")
            return

        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()
        print("✅ SQLite bazasiga muvaffaqiyatli ulandi.")

        async with pg_pool.acquire() as pg_conn:
            async with pg_conn.transaction():
                # 1-QADAM: XODIMLAR
                print("\n1. 'workers' jadvalidan ma'lumotlar ko'chirilmoqda...")
                sqlite_cursor.execute("SELECT * FROM workers")
                all_workers = sqlite_cursor.fetchall()
                tg_id_to_worker_id_map = {worker['tg_id']: worker['id'] for worker in all_workers}
                insert_query_workers = """
                                       INSERT INTO workers (id, tg_id, full_name, username, monthly_salary, added_date, \
                                                            daily_work_hours, work_start, work_end)
                                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT (tg_id) DO \
                                       UPDATE SET
                                           full_name = EXCLUDED.full_name, username = EXCLUDED.username, monthly_salary = EXCLUDED.monthly_salary, \
                                           added_date = EXCLUDED.added_date, daily_work_hours = EXCLUDED.daily_work_hours, \
                                           work_start = EXCLUDED.work_start, work_end = EXCLUDED.work_end; \
                                       """
                for worker in all_workers:
                    # Sizning asl kodingizdagi sana va vaqtni o'girish mantiqi - u to'g'ri ishlagan
                    work_start_obj = parse_time_safely(worker['work_start'])
                    work_end_obj = parse_time_safely(worker['work_end'])
                    added_date_obj = datetime.datetime.fromisoformat(worker['added_date']).astimezone(uz_tz) if worker[
                        'added_date'] else None

                    await pg_conn.execute(insert_query_workers, worker['id'], worker['tg_id'], worker['full_name'],
                                          worker['username'], worker['monthly_salary'], added_date_obj,
                                          worker['daily_work_hours'], work_start_obj, work_end_obj)
                print(f"✅ 'workers' jadvali muvaffaqiyatli ko'chirildi. Jami: {len(all_workers)} ta xodim.")
                if all_workers:
                    await pg_conn.execute("SELECT setval('workers_id_seq', (SELECT MAX(id) FROM workers));")
                    print("   > 'workers' jadvali ID ketma-ketligi sozlandi.")

                # 2-QADAM: ISH SESSIYALARI
                print("\n2. 'work_sessions' jadvalidan ma'lumotlar ko'chirilmoqda...")
                sqlite_cursor.execute("SELECT * FROM work_sessions")
                all_sessions = sqlite_cursor.fetchall()

                # <--- ASOSIY TUZATISH: TRUNCATE o'rniga ON CONFLICT ishlatiladi ---
                insert_query_sessions = """
                                        INSERT INTO work_sessions (user_id, date, arrival_time, departure_time, \
                                                                   total_hours, is_friday, session_daily_hours, \
                                                                   late_reason)
                                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (user_id, date) DO NOTHING \
                                        """
                for session in all_sessions:
                    arrival_obj = datetime.datetime.fromisoformat(
                        f"{session['date']} {session['arrival_time']}").astimezone(uz_tz) if session['arrival_time'] and \
                                                                                             session['date'] else None
                    departure_obj = datetime.datetime.fromisoformat(
                        f"{session['date']} {session['departure_time']}").astimezone(uz_tz) if session[
                                                                                                   'departure_time'] and \
                                                                                               session['date'] else None
                    await pg_conn.execute(insert_query_sessions, session['user_id'],
                                          datetime.date.fromisoformat(session['date']) if session['date'] else None,
                                          arrival_obj, departure_obj, session['total_hours'],
                                          bool(session['is_friday']), session['session_daily_hours'],
                                          session['late_reason'])
                print(f"✅ 'work_sessions' jadvali ko'chirildi. Jami urinishlar: {len(all_sessions)}.")

                # 3-QADAM: MAOSH TO'LOVLARI
                print("\n3. 'salary_payments' jadvalidan ma'lumotlar ko'chirilmoqda...")
                sqlite_cursor.execute("SELECT * FROM salary_payments")
                all_payments = sqlite_cursor.fetchall()
                insert_query_payments = """
                                        INSERT INTO salary_payments (worker_id, payment_date, amount, payment_time) \
                                        VALUES ($1, $2, $3, $4) ON CONFLICT (worker_id, payment_time) DO NOTHING \
                                        """
                for payment in all_payments:
                    payment_time_obj = datetime.datetime.fromisoformat(
                        f"{payment['payment_date']} {payment['payment_time']}").astimezone(uz_tz) if payment[
                                                                                                         'payment_date'] and \
                                                                                                     payment[
                                                                                                         'payment_time'] else None
                    await pg_conn.execute(insert_query_payments, payment['worker_id'],
                                          datetime.date.fromisoformat(payment['payment_date']) if payment[
                                              'payment_date'] else None,
                                          payment['amount'], payment_time_obj)
                print(f"✅ 'salary_payments' jadvali ko'chirildi. Jami urinishlar: {len(all_payments)}.")

                # 4-QADAM: ATTENDANCE
                print("\n4. 'attendance' jadvalidan ma'lumotlar ko'chirilmoqda...")
                sqlite_cursor.execute("SELECT * FROM attendance")
                all_attendance = sqlite_cursor.fetchall()
                insert_query_attendance = """
                                          INSERT INTO attendance (user_id, name, "timestamp", latitude, longitude, \
                                                                  distance, message, reason)
                                          VALUES ($1, $2, $3, $4, $5, $6, $7, \
                                                  $8) ON CONFLICT (user_id, "timestamp") DO NOTHING \
                                          """
                for att in all_attendance:
                    internal_worker_id = tg_id_to_worker_id_map.get(att['user_id'])
                    if not internal_worker_id:
                        continue
                    timestamp_obj = datetime.datetime.fromisoformat(att['timestamp']).astimezone(uz_tz) if att[
                        'timestamp'] else None
                    await pg_conn.execute(insert_query_attendance, internal_worker_id, att['name'], timestamp_obj,
                                          att['latitude'], att['longitude'], att['distance'], att['message'],
                                          att['reason'])
                print(f"✅ 'attendance' jadvali ko'chirildi. Jami urinishlar: {len(all_attendance)}.")

        print("\n🎉 Ma'lumotlarni ko'chirish muvaffaqiyatli yakunlandi!")

    except Exception as e:
        print(f"❌ Jarayonda xatolik yuz berdi: {e}")
    finally:
        if sqlite_conn:
            sqlite_conn.close()
        if pg_pool:
            await pg_pool.close()
            print("Ulanishlar yopildi.")


if __name__ == "__main__":
    asyncio.run(main())
