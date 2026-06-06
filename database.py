# database.py

import asyncpg
import datetime
import math
import logging
from typing import Optional, List, Tuple, Dict, Any
from datetime import date

# Konfiguratsiya faylidan kerakli o'zgaruvchilarni import qilamiz
from config import (
    ADMINS,
    BRANCH_CONFIGS,
    DATABASE_URL,
    HAS_EXPLICIT_SUPERADMINS,
    LEGACY_ADMIN_IDS,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    SUPERADMINS,
    WORK_LOG_GROUP_ID,
)

# Ulanishlar hovuzi (pool) uchun global o'zgaruvchi
pool: asyncpg.Pool = None

WEEKDAYS_UZ = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


def _set_runtime_admin_ids(
    branch_admin_ids: List[int],
    manual_superadmin_ids: Optional[List[int]] = None,
) -> List[int]:
    manual_superadmin_ids = [
        int(admin_id)
        for admin_id in (manual_superadmin_ids or [])
        if admin_id
    ]

    if not HAS_EXPLICIT_SUPERADMINS:
        inferred_superadmins = sorted(set(LEGACY_ADMIN_IDS) - set(branch_admin_ids))
        if inferred_superadmins or branch_admin_ids or manual_superadmin_ids:
            SUPERADMINS[:] = inferred_superadmins
        else:
            SUPERADMINS[:] = sorted(set(LEGACY_ADMIN_IDS))

    base_superadmins = sorted(set(SUPERADMINS) | set(manual_superadmin_ids))
    SUPERADMINS[:] = base_superadmins
    runtime_admin_ids = sorted(
        set(base_superadmins)
        | {
            int(admin_id)
            for admin_id in branch_admin_ids
            if admin_id
        }
    )
    ADMINS[:] = runtime_admin_ids
    return runtime_admin_ids


async def refresh_runtime_admins() -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
        superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
    return _set_runtime_admin_ids(
        [int(row["tg_id"]) for row in rows if row["tg_id"]],
        [int(row["tg_id"]) for row in superadmin_rows if row["tg_id"]],
    )


async def init_db():
    """Barcha asosiy ma'lumotlar bazasi jadvallarini yaratadi."""
    async with pool.acquire() as conn:
        # PostgreSQL'ga 'pg_trgm' kengaytmasini o'rnatamiz (aqlli qidiruv uchun kerak)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

        # 1. Xodimlar jadvali
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
                           ),
                               monthly_salary NUMERIC
                           (
                               12,
                               2
                           ) DEFAULT 0.00,
                               added_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                                        daily_work_hours NUMERIC (4, 2) DEFAULT 0.00,
                               work_start TIME,
                               work_end TIME,
                               is_active BOOLEAN DEFAULT TRUE
                               );
                           """)

        # 2. Ish sessiyalari jadvali
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
                               date DATE NOT NULL,
                               arrival_time TIMESTAMP WITH TIME ZONE,
                               departure_time TIMESTAMP
                             WITH TIME ZONE,
                                 total_hours NUMERIC (4, 2) DEFAULT 0.00,
                               is_friday BOOLEAN DEFAULT FALSE,
                               session_daily_hours NUMERIC
                           (
                               4,
                               2
                           ) DEFAULT 0.00,
                               late_reason TEXT,
                               UNIQUE
                           (
                               user_id,
                               date
                           )
                               );
                           """)

        # 3. Maosh to'lovlari jadvali
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
                               payment_date DATE NOT NULL,
                               amount NUMERIC
                           (
                               12,
                               2
                           ) NOT NULL,
                               payment_time TIMESTAMP
                             WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                                 );
                           """)

        # 4. Keldi-ketdi yozuvlari (lokatsiya bilan)
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
                           ),
                               timestamp TIMESTAMP
                             WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                 latitude DOUBLE PRECISION,
                                 longitude DOUBLE PRECISION,
                                 distance DOUBLE PRECISION,
                                 message TEXT,
                                 reason TEXT
                                 );
                           """)

        # 5. Umumiy sozlamalar jadvali
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
                           ),
                               rest_day INTEGER
                               );
                           """)
        await conn.execute("INSERT INTO settings(id, rest_day) VALUES (1, NULL) ON CONFLICT (id) DO NOTHING;")
        # Adminga murojaat uchun aloqa (username yoki t.me havola) — bot ichidan boshqariladi
        await conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS admin_contact TEXT;")

        # 10. AI Chat Tables
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_sessions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                title TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_messages (
                id SERIAL PRIMARY KEY,
                session_id INTEGER REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 11. Job Applications Table
        await create_applications_table()

        # 12. Admins Table
        await create_admins_table()
        await ensure_attendance_v2_schema()

    print("🛠️ Asosiy jadvallar tekshirildi va tayyor.")


async def create_applications_table():
    """Ishga kirish arizalari jadvalini yaratadi."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS job_applications (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT UNIQUE NOT NULL,
                full_name VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                status VARCHAR(50) DEFAULT 'pending', -- pending, accepted, rejected
                notification_message_id INTEGER,
                notification_chat_id BIGINT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # MIGRATSIYA: Agar jadvallar oldin yaratilgan bo'lsa, yangi ustunlarni qo'shamiz
        try:
            await conn.execute("ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS notification_message_id INTEGER;")
            await conn.execute("ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS notification_chat_id BIGINT;")
        except Exception as e:
            print(f"Job application migration error (ignorable): {e}")

async def add_application(tg_id: int, full_name: str, username: Optional[str] = None, 
                          message_id: Optional[int] = None, chat_id: Optional[int] = None):
    """Yangi ariza qo'shadi."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO job_applications (tg_id, full_name, username, status, notification_message_id, notification_chat_id)
            VALUES ($1, $2, $3, 'pending', $4, $5)
            ON CONFLICT (tg_id) DO UPDATE 
            SET full_name = $2, username = $3, status = 'pending', 
                notification_message_id = $4, notification_chat_id = $5,
                created_at = CURRENT_TIMESTAMP
        """, tg_id, full_name, username, message_id, chat_id)

async def get_pending_applications() -> List[Dict[str, Any]]:
    """Kutilayotgan arizalarni qaytaradi."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, tg_id, full_name, username, created_at 
            FROM job_applications 
            WHERE status = 'pending'
            ORDER BY created_at DESC
        """)
        return [dict(row) for row in rows]

async def update_application_status(application_id: int, status: str):
    """Ariza statusini yangilaydi."""
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE job_applications 
            SET status = $1 
            WHERE id = $2
        """, status, application_id)
        
async def get_application_by_id(application_id: int) -> Optional[Dict[str, Any]]:
    """ID orqali arizani oladi."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM job_applications WHERE id = $1", application_id)
        return dict(row) if row else None
        
async def get_application_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    """Telegram ID orqali arizani oladi."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM job_applications WHERE tg_id = $1", tg_id)
        return dict(row) if row else None


# --- MAVJUD FUNKSIYALARINGIZ (O'ZGARTIRILMAGAN QISM) ---
async def get_worker_db_id(tg_id: int) -> Optional[int]:
    """Telegram ID bo'yicha xodimning ichki ID'sini qaytaradi."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM workers WHERE tg_id = $1", tg_id)
        return row['id'] if row else None


async def add_user(tg_id: int, full_name: str, username: Optional[str] = None, monthly_salary: float = 0.0,
                   daily_work_hours: float = 0.0, branch_id: Optional[int] = None) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO workers (tg_id, full_name, username, monthly_salary, daily_work_hours, branch_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            tg_id,
            full_name,
            username,
            monthly_salary,
            daily_work_hours,
            branch_id,
        )


async def delete_worker(worker_id: int) -> bool:
    """Xodimni bazadan butunlay o'chirib tashlaydi."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM workers WHERE id = $1", worker_id)
        return "DELETE 1" in result


async def get_rest_day() -> Optional[int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT rest_day FROM settings WHERE id = 1")
        return row['rest_day'] if row else None


async def set_rest_day(day_int: Optional[int]):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE settings SET rest_day = $1 WHERE id = 1", day_int)


async def get_admin_contact() -> Optional[str]:
    """Adminga murojaat uchun saqlangan aloqa (username/havola)ni qaytaradi."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT admin_contact FROM settings WHERE id = 1")
    value = (row["admin_contact"] if row else None) or ""
    value = value.strip()
    return value or None


async def set_admin_contact(value: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE settings SET admin_contact = $1 WHERE id = 1", value.strip())


async def delete_admin_contact() -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE settings SET admin_contact = NULL WHERE id = 1")


async def get_active_employee_ids() -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id FROM workers WHERE is_active = TRUE AND tg_id IS NOT NULL")
        return [row['tg_id'] for row in rows] if rows else []


async def get_active_employees() -> List[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                w.id,
                w.tg_id,
                w.full_name,
                w.branch_id,
                b.name AS branch_name
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.is_active = TRUE AND w.tg_id IS NOT NULL
            ORDER BY w.full_name
            """
        )
        return [dict(row) for row in rows] if rows else []


async def get_all_workers(page: int = 0, per_page: int = 9) -> List[dict]:
    """Barcha xodimlarni sahifalab qaytaradi."""
    offset = page * per_page
    async with pool.acquire() as conn:
        return [dict(row) for row in await conn.fetch(
            """
            SELECT w.*, b.name AS branch_name
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.is_active = TRUE
            ORDER BY w.full_name ASC
            LIMIT $1 OFFSET $2
            """,
            per_page,
            offset,
        )]


async def get_worker_count() -> int:
    """Barcha faol xodimlar sonini qaytaradi."""
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM workers WHERE is_active = TRUE") or 0


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


async def get_active_branches() -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, code, name, latitude, longitude, radius, work_log_group_id
            FROM branches
            WHERE is_active = TRUE
            ORDER BY id
            """
        )
    return [dict(row) for row in rows]


async def get_branch_by_id(branch_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not branch_id:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, code, name, latitude, longitude, radius, work_log_group_id
            FROM branches
            WHERE id = $1
            """,
            branch_id,
        )
    return dict(row) if row else None


async def get_superadmin_selected_branch_id(tg_id: int) -> Optional[int]:
    if tg_id not in SUPERADMINS:
        return None

    async with pool.acquire() as conn:
        branch_id = await conn.fetchval(
            """
            SELECT sbc.branch_id
            FROM superadmin_branch_context sbc
            JOIN branches b ON b.id = sbc.branch_id
            WHERE sbc.tg_id = $1
              AND b.is_active = TRUE
            """,
            tg_id,
        )
    return int(branch_id) if branch_id else None


async def get_superadmin_selected_branch(tg_id: int) -> Optional[Dict[str, Any]]:
    branch_id = await get_superadmin_selected_branch_id(tg_id)
    if not branch_id:
        return None
    return await get_branch_by_id(branch_id)


async def set_superadmin_selected_branch(tg_id: int, branch_id: int) -> bool:
    if tg_id not in SUPERADMINS:
        return False

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM branches WHERE id = $1 AND is_active = TRUE",
            branch_id,
        )
        if not exists:
            return False

        await conn.execute(
            """
            INSERT INTO superadmin_branch_context (tg_id, branch_id, selected_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (tg_id) DO UPDATE
            SET branch_id = EXCLUDED.branch_id,
                selected_at = CURRENT_TIMESTAMP
            """,
            tg_id,
            branch_id,
        )
    return True


async def clear_superadmin_selected_branch(tg_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM superadmin_branch_context WHERE tg_id = $1",
            tg_id,
        )
    return "DELETE 1" in result


async def get_admin_scope_branch(admin_tg_id: int) -> Optional[Dict[str, Any]]:
    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if len(branch_ids) != 1:
        return None
    return await get_branch_by_id(branch_ids[0])


async def get_default_branch_id() -> Optional[int]:
    branches = await get_active_branches()
    if not branches:
        return None
    return branches[0]["id"]


async def resolve_branch_for_location(
    latitude: float,
    longitude: float,
    preferred_branch_id: Optional[int] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    branches = await get_active_branches()
    if not branches:
        return {"matched_branch": None, "nearest_branch": None}

    enriched: List[Dict[str, Any]] = []
    for branch in branches:
        candidate = dict(branch)
        candidate["distance"] = calculate_distance(
            latitude,
            longitude,
            float(branch["latitude"]),
            float(branch["longitude"]),
        )
        enriched.append(candidate)

    nearest_branch = min(enriched, key=lambda item: item["distance"])
    matching_candidates = [
        branch for branch in enriched if branch["distance"] <= float(branch.get("radius") or 0.0)
    ]

    matched_branch = None
    if preferred_branch_id:
        matched_branch = next(
            (branch for branch in matching_candidates if branch["id"] == preferred_branch_id),
            None,
        )
    if matched_branch is None and matching_candidates:
        matched_branch = min(matching_candidates, key=lambda item: item["distance"])

    return {"matched_branch": matched_branch, "nearest_branch": nearest_branch}


async def assign_worker_branch(worker_id: int, branch_id: Optional[int]) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE workers SET branch_id = $1 WHERE id = $2",
            branch_id,
            worker_id,
        )
    return "UPDATE 1" in result


async def reassign_worker_branch(
    worker_id: int,
    branch_id: Optional[int],
    move_history: bool = False,
) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE workers SET branch_id = $1 WHERE id = $2",
                branch_id,
                worker_id,
            )
            if "UPDATE 1" not in result:
                return False

            if move_history:
                await conn.execute(
                    "UPDATE work_sessions SET branch_id = $1 WHERE user_id = $2",
                    branch_id,
                    worker_id,
                )
                await conn.execute(
                    "UPDATE attendance SET branch_id = $1 WHERE user_id = $2",
                    branch_id,
                    worker_id,
                )
    return True


async def get_worker_branch_id(worker_id: int) -> Optional[int]:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT branch_id FROM workers WHERE id = $1", worker_id)


async def get_notification_admin_ids(branch_id: Optional[int] = None) -> List[int]:
    if not branch_id:
        return sorted(set(SUPERADMINS))

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tg_id FROM branch_admins WHERE branch_id = $1",
            branch_id,
        )

    branch_admin_ids = [int(row["tg_id"]) for row in rows]
    combined = sorted(set(SUPERADMINS) | set(branch_admin_ids))
    return combined or sorted(set(ADMINS))


async def get_notification_group_id(branch_id: Optional[int] = None) -> int:
    if branch_id:
        async with pool.acquire() as conn:
            group_id = await conn.fetchval(
                "SELECT COALESCE(work_log_group_id, 0) FROM branches WHERE id = $1",
                branch_id,
            )
        if group_id:
            return int(group_id)
    return WORK_LOG_GROUP_ID


async def get_admin_branch_ids(tg_id: int) -> List[int]:
    if tg_id in SUPERADMINS:
        selected_branch_id = await get_superadmin_selected_branch_id(tg_id)
        return [selected_branch_id] if selected_branch_id else []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ba.branch_id
            FROM branch_admins ba
            JOIN branches b ON b.id = ba.branch_id
            WHERE ba.tg_id = $1
              AND b.is_active = TRUE
            ORDER BY ba.branch_id
            """,
            tg_id,
        )
    branch_ids = [int(row["branch_id"]) for row in rows]
    if len(branch_ids) > 1:
        logging.warning(
            "Admin %s bir nechta filialga biriktirilgan (%s); qat'iy single-branch scope uchun birinchisi ishlatiladi.",
            tg_id,
            branch_ids,
        )
    return branch_ids[:1]


async def list_branches_for_admin(tg_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        if tg_id in SUPERADMINS:
            selected_branch_id = await get_superadmin_selected_branch_id(tg_id)
            if not selected_branch_id:
                return []
            rows = await conn.fetch(
                """
                SELECT id, code, name, latitude, longitude, radius, work_log_group_id
                FROM branches
                WHERE is_active = TRUE
                  AND id = $1
                ORDER BY id
                """,
                selected_branch_id,
            )
        else:
            branch_ids = await get_admin_branch_ids(tg_id)
            if not branch_ids:
                return []
            rows = await conn.fetch(
                """
                SELECT id, code, name, latitude, longitude, radius, work_log_group_id
                FROM branches
                WHERE is_active = TRUE
                  AND id = ANY($1::int[])
                ORDER BY id
                """,
                branch_ids,
            )
    return [dict(row) for row in rows]


async def list_branch_admins(branch_id: Optional[int] = None) -> List[Dict[str, Any]]:
    query = """
        SELECT
            ba.branch_id,
            b.code AS branch_code,
            b.name AS branch_name,
            ba.tg_id,
            ba.source,
            ba.created_at,
            w.id AS worker_id,
            w.full_name AS worker_name,
            w.username AS worker_username
        FROM branch_admins ba
        JOIN branches b ON b.id = ba.branch_id
        LEFT JOIN workers w ON w.tg_id = ba.tg_id
    """
    params: list[Any] = []
    if branch_id is not None:
        query += " WHERE ba.branch_id = $1"
        params.append(branch_id)
    query += " ORDER BY b.id, COALESCE(w.full_name, ba.tg_id::text)"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(row) for row in rows]


async def get_branch_admin_assignments(tg_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.code, b.name
            FROM branch_admins ba
            JOIN branches b ON b.id = ba.branch_id
            WHERE ba.tg_id = $1
            ORDER BY b.id
            """,
            tg_id,
        )
    return [dict(row) for row in rows]


async def assign_branch_admin(branch_id: int, tg_id: int, source: str = "manual") -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO branch_admins (branch_id, tg_id, source)
            VALUES ($1, $2, $3)
            ON CONFLICT (branch_id, tg_id) DO UPDATE SET source = EXCLUDED.source
            """,
            branch_id,
            tg_id,
            source,
        )
        success = "INSERT" in result or "UPDATE" in result
        if success:
            rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
            superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
            _set_runtime_admin_ids(
                [int(row["tg_id"]) for row in rows if row["tg_id"]],
                [int(row["tg_id"]) for row in superadmin_rows if row["tg_id"]],
            )
    return success


async def remove_branch_admin(branch_id: int, tg_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM branch_admins WHERE branch_id = $1 AND tg_id = $2",
            branch_id,
            tg_id,
        )
        success = "DELETE 1" in result
        if success:
            rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
            superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
            _set_runtime_admin_ids(
                [int(row["tg_id"]) for row in rows if row["tg_id"]],
                [int(row["tg_id"]) for row in superadmin_rows if row["tg_id"]],
            )
    return success


async def list_telegram_superadmins() -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        manual_rows = await conn.fetch(
            """
            SELECT tg_id, source, created_at
            FROM telegram_superadmins
            ORDER BY tg_id
            """
        )
        worker_rows = await conn.fetch(
            """
            SELECT w.id AS worker_id, w.tg_id, w.full_name, w.username
            FROM workers w
            WHERE w.tg_id = ANY($1::bigint[])
            """,
            [int(admin_id) for admin_id in SUPERADMINS] or [0],
        )

    manual_map = {int(row["tg_id"]): dict(row) for row in manual_rows}
    worker_map = {int(row["tg_id"]): dict(row) for row in worker_rows if row["tg_id"]}
    result: List[Dict[str, Any]] = []
    for tg_id in sorted(set(SUPERADMINS)):
        manual_row = manual_map.get(int(tg_id), {})
        worker_row = worker_map.get(int(tg_id), {})
        result.append(
            {
                "tg_id": int(tg_id),
                "source": manual_row.get("source", "config"),
                "created_at": manual_row.get("created_at"),
                "worker_id": worker_row.get("worker_id"),
                "worker_name": worker_row.get("full_name"),
                "worker_username": worker_row.get("username"),
            }
        )
    return result


async def assign_telegram_superadmin(tg_id: int, source: str = "manual") -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO telegram_superadmins (tg_id, source)
            VALUES ($1, $2)
            ON CONFLICT (tg_id) DO UPDATE SET source = EXCLUDED.source
            """,
            tg_id,
            source,
        )
        success = "INSERT" in result or "UPDATE" in result
        if success:
            branch_rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
            superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
            _set_runtime_admin_ids(
                [int(row["tg_id"]) for row in branch_rows if row["tg_id"]],
                [int(row["tg_id"]) for row in superadmin_rows if row["tg_id"]],
            )
    return success


async def remove_telegram_superadmin(tg_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM telegram_superadmins WHERE tg_id = $1",
            tg_id,
        )
        await conn.execute(
            "DELETE FROM superadmin_branch_context WHERE tg_id = $1",
            tg_id,
        )
        success = "DELETE 1" in result
        if success:
            branch_rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
            superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
            _set_runtime_admin_ids(
                [int(row["tg_id"]) for row in branch_rows if row["tg_id"]],
                [int(row["tg_id"]) for row in superadmin_rows if row["tg_id"]],
            )
    return success


async def get_admin_preferred_branch_id(tg_id: int) -> Optional[int]:
    branch_ids = await get_admin_branch_ids(tg_id)
    if len(branch_ids) == 1:
        return int(branch_ids[0])
    return None


async def admin_can_access_worker(admin_tg_id: int, worker_id: int) -> bool:
    worker_branch_id = await get_worker_branch_id(worker_id)
    if not worker_branch_id:
        return False
    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return False
    return worker_branch_id in branch_ids


async def admin_can_access_branch(admin_tg_id: int, branch_id: Optional[int]) -> bool:
    if branch_id is None:
        return False
    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return False
    return branch_id in branch_ids


async def list_workers_for_admin(admin_tg_id: int, order_by: str = "id") -> List[Dict[str, Any]]:
    order_sql = "w.id ASC" if order_by == "id" else "w.full_name ASC, w.id ASC"
    base_query = """
        SELECT w.id, w.full_name, w.branch_id, b.name AS branch_name
        FROM workers w
        LEFT JOIN branches b ON b.id = w.branch_id
    """

    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            base_query + f" WHERE w.branch_id = ANY($1::int[]) ORDER BY {order_sql}",
            branch_ids,
        )
    return [dict(row) for row in rows]


async def list_active_workers_for_admin(admin_tg_id: int, order_by: str = "id") -> List[Dict[str, Any]]:
    order_sql = "w.id ASC" if order_by == "id" else "w.full_name ASC, w.id ASC"
    base_query = """
        SELECT w.id, w.full_name, w.branch_id, b.name AS branch_name
        FROM workers w
        LEFT JOIN branches b ON b.id = w.branch_id
        WHERE w.is_active = TRUE
    """

    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            base_query + f" AND w.branch_id = ANY($1::int[]) ORDER BY {order_sql}",
            branch_ids,
        )
    return [dict(row) for row in rows]


async def count_workers_for_admin(admin_tg_id: int) -> int:
    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return 0

    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM workers WHERE branch_id = ANY($1::int[])",
            branch_ids,
        ) or 0


async def count_active_workers_for_admin(admin_tg_id: int) -> int:
    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return 0

    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM workers WHERE is_active = TRUE AND branch_id = ANY($1::int[])",
            branch_ids,
        ) or 0


async def get_user_distinct_years(tg_id: int) -> List[int]:
    worker_id = await get_worker_db_id(tg_id)
    if worker_id is None: return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT EXTRACT(YEAR FROM date)::INTEGER AS yr FROM work_sessions WHERE user_id = $1 ORDER BY yr ASC",
            worker_id)
        return [row['yr'] for row in rows]


async def get_user_distinct_months(tg_id: int, year: int) -> List[Tuple[int, str]]:
    worker_id = await get_worker_db_id(tg_id)
    if worker_id is None: return []
    query = "SELECT DISTINCT EXTRACT(MONTH FROM date)::INTEGER AS mnum FROM work_sessions WHERE user_id = $1 AND EXTRACT(YEAR FROM date) = $2 ORDER BY mnum ASC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, worker_id, year)
    names = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr",
             "Dekabr"]
    return [(row['mnum'], names[row['mnum'] - 1]) for row in rows]


# --- Maosh / statistika hisoblari ---
async def get_salary_year_count() -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(DISTINCT EXTRACT(YEAR FROM payment_date)) FROM salary_payments;") or 0


async def get_stat_year_count() -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(DISTINCT EXTRACT(YEAR FROM date)) FROM work_sessions;") or 0


async def get_stat_worker_count_for_month(month: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("""
                                   SELECT COUNT(DISTINCT user_id)
                                   FROM work_sessions
                                   WHERE to_char(date, 'YYYY-MM') = $1;
                                   """, month) or 0


# --- "Aqlli Qidiruv" uchun YANGI funksiya ---
async def get_session_for_worker_on_date(worker_id: int, target_date: date) -> Optional[dict]:
    """Belgilangan xodim uchun aniq bir sanadagi ish sessiyasini topib qaytaradi."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ws.*, b.name AS branch_name
            FROM work_sessions ws
            LEFT JOIN branches b ON b.id = ws.branch_id
            WHERE ws.user_id = $1 AND ws.date = $2
            """,
            worker_id,
            target_date,
        )
        return dict(row) if row else None


async def get_late_employees(check_date: date, threshold_time: datetime.time) -> List[dict]:
    """Berilgan vaqtdan kech qolgan yoki kelmagan xodimlarni qaytaradi."""
    async with pool.acquire() as conn:
        # threshold_time ni to'liq timestamp ga aylantiramiz (taqqoslash uchun)
        # Eslatma: Bu yerda oddiy mantiq ishlatamiz. 
        # Agar arrival_time NULL bo'lsa -> Kelmagan
        # Agar arrival_time > threshold_time -> Kech qolgan
        
        # Faol xodimlarni va ularning bugungi statusini olamiz
        rows = await conn.fetch("""
            SELECT
                w.full_name,
                w.tg_id,
                w.branch_id,
                b.name AS branch_name,
                ws.arrival_time
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            LEFT JOIN work_sessions ws ON w.id = ws.user_id AND ws.date = $1
            WHERE w.is_active = TRUE
        """, check_date)
        
        late_employees = []
        for row in rows:
            arrival_time = row['arrival_time']
            is_late = False
            status = "unknown"
            
            if arrival_time is None:
                is_late = True
                status = "kelmagan"
            else:
                # arrival_time (datetime) ni vaqtini olamiz
                # Timezonelarni hisobga olish kerak bo'lishi mumkin, lekin hozircha oddiy taqqoslash
                # Postgres TIMESTAMP WITH TIME ZONE qaytaradi -> Python datetime
                arrived_at = arrival_time.time() # time object
                if arrived_at > threshold_time:
                    is_late = True
                    status = f"kech qolgan ({arrived_at.strftime('%H:%M')})"
            
            if is_late:
                late_employees.append({
                    "full_name": row['full_name'],
                    "tg_id": row['tg_id'],
                    "branch_id": row['branch_id'],
                    "branch_name": row['branch_name'],
                    "status": status
                })
        return late_employees

async def create_chat_session(worker_id: Optional[int], title: str) -> int:
    """Yangi sessiya yaratadi."""
    async with pool.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO ai_chat_sessions (worker_id, title)
            VALUES ($1, $2)
            RETURNING id
        """, worker_id, title)

async def get_session_messages(session_id: int) -> List[Dict[str, Any]]:
    """Sessiya xabarlarini qaytaradi."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM ai_chat_messages 
            WHERE session_id = $1 
            ORDER BY created_at ASC
        """, session_id)
        return [dict(row) for row in rows]

async def update_session_title(session_id: int, title: str) -> bool:
    """Sessiya nomini yangilaydi."""
    async with pool.acquire() as conn:
        result = await conn.execute("UPDATE ai_chat_sessions SET title = $1 WHERE id = $2", title, session_id)
        return "UPDATE 1" in result

async def delete_chat_session(session_id: int) -> bool:
    """Sessiyani o'chiradi."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ai_chat_sessions WHERE id = $1", session_id)
        return "DELETE 1" in result

async def save_chat_message(worker_id: Optional[int], user_text: str, ai_text: str, audio_path: Optional[str] = None, session_id: Optional[int] = None) -> int:
    """Xabar va javobni saqlaydi. Agar session_id yo'q bo'lsa, yangi sessiya ochadi."""
    async with pool.acquire() as conn:
        if not session_id:
            session_id = await conn.fetchval("""
                INSERT INTO ai_chat_sessions (worker_id, title)
                VALUES ($1, $2)
                RETURNING id
            """, worker_id, user_text[:30] + "...")
        
        await conn.execute("""
            INSERT INTO ai_chat_messages (session_id, role, content, audio_path)
            VALUES ($1, 'user', $2, $3)
        """, session_id, user_text, audio_path)
        
        await conn.execute("""
            INSERT INTO ai_chat_messages (session_id, role, content)
            VALUES ($1, 'assistant', $2)
        """, session_id, ai_text)
        
        # Update session timestamp
        await conn.execute("UPDATE ai_chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = $1", session_id)
        
        return session_id

# --- SALARY CALCULATION FUNCTIONS (YANGI) ---

async def create_salary_history_table():
    """Oylik maosh tarixi jadvalini yaratadi."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS salary_history (
                id SERIAL PRIMARY KEY,
                worker_id INTEGER REFERENCES workers(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                
                total_hours NUMERIC(6, 2) DEFAULT 0.00,
                base_salary NUMERIC(15, 2) DEFAULT 0.00,
                
                calculated_salary NUMERIC(15, 2) DEFAULT 0.00,
                bonus NUMERIC(15, 2) DEFAULT 0.00,
                advance NUMERIC(15, 2) DEFAULT 0.00,
                penalty NUMERIC(15, 2) DEFAULT 0.00,
                
                final_salary NUMERIC(15, 2) DEFAULT 0.00,
                
                status VARCHAR(20) DEFAULT 'unpaid', -- unpaid, paid
                paid_date TIMESTAMP WITH TIME ZONE,
                
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(worker_id, year, month)
            );
        """)

async def get_monthly_salary_stats(year: int, month: int) -> List[Dict[str, Any]]:
    """
    Barcha xodimlar uchun oylik hisob-kitobni (preview) qaytaradi.
    Hali bazaga saqlanmagan bo'lsa, hisoblab beradi.
    Bazada bor bo'lsa, bazadagini qaytaradi.
    """
    async with pool.acquire() as conn:
        # 1. Barcha aktiv xodimlarni olamiz
        workers = await conn.fetch("""
            SELECT id, full_name, monthly_salary, daily_work_hours, pay_type, pay_amount
            FROM workers 
            WHERE is_active = TRUE
            ORDER BY full_name
        """)
        
        results = []
        for w in workers:
            w_id = w['id']
            pay_type = w['pay_type'] or 'monthly'
            base_salary = float(w['pay_amount'] or w['monthly_salary'] or 0)
            daily_hours = float(w['daily_work_hours'] or 8) # Default 8 soat
            if daily_hours == 0: daily_hours = 8 
            
            # 2. Bazada saqlangan history borligini tekshiramiz
            history = await conn.fetchrow("""
                SELECT * FROM salary_history 
                WHERE worker_id = $1 AND year = $2 AND month = $3
            """, w_id, year, month)
            
            if history:
                # Agar allaqachon saqlangan/to'langan bo'lsa
                results.append({
                    "id": history['id'], # ID kerak to'lov uchun!
                    "worker_id": w_id,
                    "full_name": w['full_name'],
                    "total_hours": float(history['total_hours']),
                    "base_salary": float(history['base_salary']),
                    "calculated_salary": float(history['calculated_salary']),
                    "bonus": float(history['bonus']),
                    "advance": float(history['advance']),
                    "penalty": float(history['penalty']),
                    "final_salary": float(history['final_salary']),
                    "status": history['status'],
                    "is_saved": True
                })
            else:
                # 3. Agar yo'q bo'lsa, davomatdan hisoblaymiz
                hours_row = await conn.fetchrow("""
                    SELECT COALESCE(SUM(total_hours), 0) as grand_total
                    FROM work_sessions
                    WHERE user_id = $1 
                      AND EXTRACT(YEAR FROM date) = $2 
                      AND EXTRACT(MONTH FROM date) = $3
                """, w_id, year, month)
                
                total_hours = float(hours_row['grand_total'])
                
                hour_rate = 0
                if daily_hours > 0:
                    if pay_type == 'daily':
                        hour_rate = base_salary / daily_hours
                    elif pay_type == 'weekly':
                        hour_rate = base_salary / (6 * daily_hours)
                    else:
                        hour_rate = base_salary / (26 * daily_hours)
                
                calculated = hour_rate * total_hours
                
                results.append({
                    "worker_id": w_id,
                    "full_name": w['full_name'],
                    "pay_type": pay_type,
                    "total_hours": total_hours,
                    "base_salary": base_salary,
                    "calculated_salary": round(calculated, 2),
                    "bonus": 0,
                    "advance": 0,
                    "penalty": 0,
                    "final_salary": round(calculated, 2),
                    "status": "new", # Frontend uchun belgi
                    "is_saved": False
                })
        
        return results

async def save_salary_record(data: dict) -> bool:
    """Oylik maoshni saqlaydi yoki yangilaydi (agar to'lanmagan bo'lsa)."""
    async with pool.acquire() as conn:
        try:
            # Check status first
            existing = await conn.fetchrow("""
                SELECT status FROM salary_history 
                WHERE worker_id = $1 AND year = $2 AND month = $3
            """, data['worker_id'], data['year'], data['month'])
            
            if existing and existing['status'] == 'paid':
                return False # To'langan bo'lsa o'zgartirib bo'lmaydi
            
            await conn.execute("""
                INSERT INTO salary_history (
                    worker_id, year, month, 
                    total_hours, base_salary, calculated_salary,
                    bonus, advance, penalty, final_salary, status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'unpaid')
                ON CONFLICT (worker_id, year, month) DO UPDATE 
                SET 
                    total_hours = $4,
                    base_salary = $5,
                    calculated_salary = $6,
                    bonus = $7,
                    advance = $8,
                    penalty = $9,
                    final_salary = $10
            """, 
            data['worker_id'], data['year'], data['month'], 
            data['total_hours'], data['base_salary'], data['calculated_salary'],
            data['bonus'], data['advance'], data['penalty'], data['final_salary'])
            
            return True
        except Exception as e:
            logging.error(f"Salary save error: {e}")
            return False

async def pay_salary(record_id: int) -> bool:
    """Oylikni to'landi deb belgilaydi va salary_payments jadvaliga ham yozadi."""
    async with pool.acquire() as conn:
        # Get the salary record details first
        record = await conn.fetchrow("""
            SELECT worker_id, final_salary, year, month
            FROM salary_history 
            WHERE id = $1
        """, record_id)
        
        if not record:
            return False
        
        # Update salary_history status
        result = await conn.execute("""
            UPDATE salary_history 
            SET status = 'paid', paid_date = CURRENT_TIMESTAMP
            WHERE id = $1
        """, record_id)
        
        # Insert into salary_payments table
        if "UPDATE 1" in result:
            await conn.execute("""
                INSERT INTO salary_payments (worker_id, payment_date, amount, payment_time)
                VALUES ($1, CURRENT_DATE, $2, CURRENT_TIMESTAMP)
            """, record['worker_id'], record['final_salary'])
            return True
        
        return False

async def get_salary_history_by_month(year: int, month: int) -> List[Dict[str, Any]]:
    """Faqat saqlangan tarixni qaytaradi."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT sh.*, w.full_name 
            FROM salary_history sh
            JOIN workers w ON sh.worker_id = w.id
            WHERE sh.year = $1 AND sh.month = $2
            ORDER BY w.full_name
        """, year, month)
        return [dict(row) for row in rows]
        return [dict(row) for row in rows]

# --- ADMIN/ROLE MANAGEMENT (YANGI) ---

async def create_admins_table():
    """Adminlar jadvalini yaratadi."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(100),
                role VARCHAR(20) DEFAULT 'manager', -- superadmin, manager
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

async def ensure_default_admin(password_hash: str):
    """Default superadmin yaratadi (agar yo'q bo'lsa)."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO admins (username, password_hash, full_name, role)
            VALUES ('admin', $1, 'Administrator', 'superadmin')
            ON CONFLICT (username) DO NOTHING
        """, password_hash)

async def get_admin_by_username(username: str) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM admins WHERE username = $1", username)
        return dict(row) if row else None

async def create_admin(username: str, password_hash: str, full_name: str, role: str) -> bool:
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO admins (username, password_hash, full_name, role)
                VALUES ($1, $2, $3, $4)
            """, username, password_hash, full_name, role)
            return True
        except Exception as e:
            print(f"Error creating admin: {e}")
            return False

async def get_all_admins() -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, username, full_name, role, created_at FROM admins ORDER BY id")
        return [dict(row) for row in rows]

async def delete_admin(admin_id: int) -> bool:
    async with pool.acquire() as conn:
        # Superadminni o'chirib bo'lmaydi (admin username bilan)
        val = await conn.fetchval("SELECT username FROM admins WHERE id = $1", admin_id)
        if val == 'admin':
            return False
            
        res = await conn.execute("DELETE FROM admins WHERE id = $1", admin_id)
        return "DELETE 1" in res

async def update_admin_password(admin_id: int, new_hash: str) -> bool:
    async with pool.acquire() as conn:
        res = await conn.execute("UPDATE admins SET password_hash = $1 WHERE id = $2", new_hash, admin_id)
        return "UPDATE 1" in res


async def ensure_attendance_v2_schema():
    """Yangi attendance oqimi uchun kerakli ustun va jadvallarni tayyorlaydi."""
    async with pool.acquire() as conn:
        try:
            await conn.execute("ALTER TABLE workers ALTER COLUMN tg_id DROP NOT NULL")
        except Exception:
            pass

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS branches (
                id SERIAL PRIMARY KEY,
                code VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                latitude DOUBLE PRECISION NOT NULL,
                longitude DOUBLE PRECISION NOT NULL,
                radius DOUBLE PRECISION NOT NULL DEFAULT 200,
                work_log_group_id BIGINT DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS branch_admins (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id) ON DELETE CASCADE,
                tg_id BIGINT NOT NULL,
                source VARCHAR(16) DEFAULT 'config',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(branch_id, tg_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_superadmins (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL UNIQUE,
                source VARCHAR(16) DEFAULT 'manual',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS superadmin_branch_context (
                tg_id BIGINT PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL,
                selected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS branch_id INTEGER")
        await conn.execute("ALTER TABLE work_sessions ADD COLUMN IF NOT EXISTS branch_id INTEGER")
        await conn.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS branch_id INTEGER")

        try:
            await conn.execute(
                "ALTER TABLE workers ADD CONSTRAINT workers_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE work_sessions ADD CONSTRAINT work_sessions_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL"
            )
        except Exception:
            pass
        try:
            await conn.execute(
                "ALTER TABLE attendance ADD CONSTRAINT attendance_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL"
            )
        except Exception:
            pass

        await conn.execute("CREATE INDEX IF NOT EXISTS idx_workers_branch_id ON workers(branch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_work_sessions_branch_id ON work_sessions(branch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_branch_id ON attendance(branch_id)")

        configured_branch_ids: List[int] = []
        for branch_cfg in BRANCH_CONFIGS:
            branch_id = await conn.fetchval(
                """
                INSERT INTO branches (code, name, latitude, longitude, radius, work_log_group_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    radius = EXCLUDED.radius,
                    work_log_group_id = EXCLUDED.work_log_group_id,
                    is_active = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                branch_cfg["code"],
                branch_cfg["name"],
                branch_cfg["latitude"],
                branch_cfg["longitude"],
                branch_cfg["radius"],
                branch_cfg["work_log_group_id"],
            )
            configured_branch_ids.append(int(branch_id))

            await conn.execute(
                "DELETE FROM branch_admins WHERE branch_id = $1 AND source = 'config'",
                branch_id,
            )
            for admin_id in branch_cfg.get("admin_ids", []):
                await conn.execute(
                    """
                    INSERT INTO branch_admins (branch_id, tg_id, source)
                    VALUES ($1, $2, 'config')
                    ON CONFLICT (branch_id, tg_id) DO NOTHING
                    """,
                    branch_id,
                    admin_id,
                )

        runtime_admin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM branch_admins")
        runtime_superadmin_rows = await conn.fetch("SELECT DISTINCT tg_id FROM telegram_superadmins")
        _set_runtime_admin_ids(
            [int(row["tg_id"]) for row in runtime_admin_rows if row["tg_id"]],
            [int(row["tg_id"]) for row in runtime_superadmin_rows if row["tg_id"]],
        )

        default_branch_id = configured_branch_ids[0] if configured_branch_ids else None
        if default_branch_id:
            await conn.execute(
                "UPDATE workers SET branch_id = $1 WHERE branch_id IS NULL",
                default_branch_id,
            )

        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS pay_type VARCHAR(16) DEFAULT 'monthly'")
        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS pay_amount NUMERIC(15, 2) DEFAULT 0.00")
        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS has_phone BOOLEAN DEFAULT TRUE")
        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS is_student BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE workers ADD COLUMN IF NOT EXISTS last_absence_prompt_at TIMESTAMP WITH TIME ZONE")

        await conn.execute(
            """
            UPDATE workers
            SET pay_type = COALESCE(pay_type, 'monthly'),
                pay_amount = CASE
                    WHEN COALESCE(pay_amount, 0) = 0 THEN COALESCE(monthly_salary, 0)
                    ELSE pay_amount
                END,
                has_phone = CASE
                    WHEN tg_id IS NULL THEN FALSE
                    ELSE TRUE
                END
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_day_state_v2 (
                id SERIAL PRIMARY KEY,
                worker_id INTEGER REFERENCES workers(id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                day_state VARCHAR(32) DEFAULT 'idle',
                clock_in_at TIMESTAMP WITH TIME ZONE,
                clock_out_at TIMESTAMP WITH TIME ZONE,
                study_active BOOLEAN DEFAULT FALSE,
                study_left_at TIMESTAMP WITH TIME ZONE,
                study_returned_at TIMESTAMP WITH TIME ZONE,
                rest_marked BOOLEAN DEFAULT FALSE,
                absence_reason TEXT,
                absence_prompted_at TIMESTAMP WITH TIME ZONE,
                absence_review_status VARCHAR(16),
                absence_reviewed_by BIGINT,
                last_source VARCHAR(16) DEFAULT 'worker',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(worker_id, work_date)
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_activity_log_v2 (
                id SERIAL PRIMARY KEY,
                worker_id INTEGER REFERENCES workers(id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                event_type VARCHAR(32) NOT NULL,
                note TEXT,
                actor_tg_id BIGINT,
                actor_role VARCHAR(16) DEFAULT 'worker',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await conn.execute(
            """
            UPDATE work_sessions ws
            SET branch_id = w.branch_id
            FROM workers w
            WHERE ws.user_id = w.id AND ws.branch_id IS NULL
            """
        )
        await conn.execute(
            """
            UPDATE attendance a
            SET branch_id = w.branch_id
            FROM workers w
            WHERE a.user_id = w.id AND a.branch_id IS NULL
            """
        )


async def get_worker_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT w.*, b.name AS branch_name, b.code AS branch_code
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.tg_id = $1
            """,
            tg_id,
        )
        return dict(row) if row else None


async def get_worker_by_id(worker_id: int) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT w.*, b.name AS branch_name, b.code AS branch_code
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.id = $1
            """,
            worker_id,
        )
        return dict(row) if row else None


async def find_worker_candidates_by_name(name_query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Ismga yaqin xodimlarni similarity asosida qaytaradi."""
    cleaned = (name_query or "").strip()
    if not cleaned:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                w.id,
                w.full_name,
                w.tg_id,
                w.has_phone,
                w.pay_type,
                w.branch_id,
                b.name AS branch_name,
                similarity(w.full_name, $1) AS sim
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.is_active = TRUE
              AND (
                w.full_name ILIKE ('%' || $1 || '%')
                OR similarity(w.full_name, $1) > 0.20
              )
            ORDER BY
              (w.full_name ILIKE ($1 || '%')) DESC,
              similarity(w.full_name, $1) DESC,
              w.full_name ASC
            LIMIT $2
            """,
            cleaned,
            int(limit),
        )
    return [dict(row) for row in rows]


async def find_worker_candidates_for_admin(
    admin_tg_id: int,
    name_query: str,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    cleaned = (name_query or "").strip()
    if not cleaned:
        return []

    base_query = """
        SELECT
            w.id,
            w.full_name,
            w.tg_id,
            w.has_phone,
            w.pay_type,
            w.branch_id,
            b.name AS branch_name,
            similarity(w.full_name, $1) AS sim
        FROM workers w
        LEFT JOIN branches b ON b.id = w.branch_id
        WHERE w.is_active = TRUE
          AND (
            w.full_name ILIKE ('%' || $1 || '%')
            OR similarity(w.full_name, $1) > 0.20
          )
    """

    branch_ids = await get_admin_branch_ids(admin_tg_id)
    if not branch_ids:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            base_query
            + """
              AND w.branch_id = ANY($3::int[])
            ORDER BY
                (w.full_name ILIKE ($1 || '%')) DESC,
                similarity(w.full_name, $1) DESC,
                w.full_name ASC
            LIMIT $2
            """,
            cleaned,
            int(limit),
            branch_ids,
        )
    return [dict(row) for row in rows]


async def create_worker_record(
    full_name: str,
    tg_id: Optional[int] = None,
    username: Optional[str] = None,
    pay_type: str = "monthly",
    pay_amount: float = 0.0,
    daily_work_hours: float = 0.0,
    work_start: Optional[datetime.time] = None,
    work_end: Optional[datetime.time] = None,
    has_phone: Optional[bool] = None,
    branch_id: Optional[int] = None,
) -> int:
    resolved_has_phone = has_phone if has_phone is not None else tg_id is not None
    monthly_salary = pay_amount if pay_type == "monthly" else 0.0
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO workers (
                tg_id, full_name, username, monthly_salary, daily_work_hours, work_start, work_end, pay_type, pay_amount, has_phone, branch_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            tg_id,
            full_name,
            username,
            monthly_salary,
            daily_work_hours,
            work_start,
            work_end,
            pay_type,
            pay_amount,
            resolved_has_phone,
            branch_id,
        )


async def ensure_worker_day_status(worker_id: int, work_date: date, source: str = "worker") -> Dict[str, Any]:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO worker_day_state_v2 (worker_id, work_date, last_source)
            VALUES ($1, $2, $3)
            ON CONFLICT (worker_id, work_date) DO NOTHING
            """,
            worker_id,
            work_date,
            source,
        )
        row = await conn.fetchrow(
            "SELECT * FROM worker_day_state_v2 WHERE worker_id = $1 AND work_date = $2",
            worker_id,
            work_date,
        )
        return dict(row)


async def get_worker_day_status(worker_id: int, work_date: date) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM worker_day_state_v2 WHERE worker_id = $1 AND work_date = $2",
            worker_id,
            work_date,
        )
        return dict(row) if row else None


async def update_worker_day_status(worker_id: int, work_date: date, **fields) -> Dict[str, Any]:
    allowed = {
        "day_state",
        "clock_in_at",
        "clock_out_at",
        "study_active",
        "study_left_at",
        "study_returned_at",
        "rest_marked",
        "absence_reason",
        "absence_prompted_at",
        "absence_review_status",
        "absence_reviewed_by",
        "last_source",
    }
    clean_fields = {key: value for key, value in fields.items() if key in allowed}
    await ensure_worker_day_status(worker_id, work_date, clean_fields.get("last_source", "worker"))

    if not clean_fields:
        return await ensure_worker_day_status(worker_id, work_date)

    assignments = []
    values = [worker_id, work_date]
    for index, (key, value) in enumerate(clean_fields.items(), start=3):
        assignments.append(f"{key} = ${index}")
        values.append(value)
    assignments.append("updated_at = CURRENT_TIMESTAMP")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE worker_day_state_v2 SET "
            + ", ".join(assignments)
            + " WHERE worker_id = $1 AND work_date = $2",
            *values,
        )
        row = await conn.fetchrow(
            "SELECT * FROM worker_day_state_v2 WHERE worker_id = $1 AND work_date = $2",
            worker_id,
            work_date,
        )
        return dict(row)


async def log_worker_activity(
    worker_id: int,
    event_type: str,
    note: Optional[str] = None,
    actor_tg_id: Optional[int] = None,
    actor_role: str = "worker",
    work_date: Optional[date] = None,
):
    activity_date = work_date or datetime.date.today()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO worker_activity_log_v2 (worker_id, work_date, event_type, note, actor_tg_id, actor_role)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            worker_id,
            activity_date,
            event_type,
            note,
            actor_tg_id,
            actor_role,
        )


async def get_worker_activity_history(worker_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM worker_activity_log_v2
            WHERE worker_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            worker_id,
            limit,
        )
        return [dict(row) for row in rows]


async def get_workers_needing_absence_prompt(
    reference_dt: datetime.datetime,
    delay_minutes: int = 60,
    phone_only: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    today = reference_dt.date()
    async with pool.acquire() as conn:
        workers = await conn.fetch(
            """
            SELECT
                w.id,
                w.tg_id,
                w.full_name,
                w.work_start,
                w.has_phone,
                w.is_active,
                w.branch_id,
                b.name AS branch_name
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.is_active = TRUE
            ORDER BY w.full_name
            """
        )

    result: List[Dict[str, Any]] = []
    for row in workers:
        worker = dict(row)
        if phone_only is True and not worker.get("has_phone"):
            continue
        if phone_only is False and worker.get("has_phone"):
            continue

        work_start = worker.get("work_start")
        if not work_start:
            continue

        planned_dt = datetime.datetime.combine(today, work_start, tzinfo=reference_dt.tzinfo)
        if reference_dt < planned_dt + datetime.timedelta(minutes=delay_minutes):
            continue

        day_status = await get_worker_day_status(worker["id"], today)
        session = await get_session_for_worker_on_date(worker["id"], today)

        if day_status and (day_status.get("rest_marked") or day_status.get("clock_in_at")):
            continue
        if session and session.get("arrival_time"):
            continue
        if day_status and day_status.get("absence_prompted_at"):
            continue

        worker["day_status"] = day_status
        worker["session"] = session
        result.append(worker)

    return result


async def get_phone_less_workers_pending_manual(
    reference_dt: datetime.datetime,
    delay_minutes: int = 60,
) -> List[Dict[str, Any]]:
    today = reference_dt.date()
    async with pool.acquire() as conn:
        workers = await conn.fetch(
            """
            SELECT
                w.id,
                w.tg_id,
                w.full_name,
                w.work_start,
                w.has_phone,
                w.is_active,
                w.branch_id,
                b.name AS branch_name
            FROM workers w
            LEFT JOIN branches b ON b.id = w.branch_id
            WHERE w.is_active = TRUE AND COALESCE(w.has_phone, FALSE) = FALSE
            ORDER BY w.full_name
            """
        )

    result: List[Dict[str, Any]] = []
    for row in workers:
        worker = dict(row)
        work_start = worker.get("work_start")
        if not work_start:
            continue

        planned_dt = datetime.datetime.combine(today, work_start, tzinfo=reference_dt.tzinfo)
        if reference_dt < planned_dt + datetime.timedelta(minutes=delay_minutes):
            continue

        day_status = await get_worker_day_status(worker["id"], today)
        session = await get_session_for_worker_on_date(worker["id"], today)

        if day_status and (day_status.get("rest_marked") or day_status.get("clock_in_at")):
            continue
        if session and session.get("arrival_time"):
            continue

        worker["day_status"] = day_status
        worker["session"] = session
        result.append(worker)

    return result


async def create_pool():
    """Bot ishga tushganda ma'lumotlar bazasi bilan ulanishlar hovuzini yaratadi."""
    global pool
    try:
        if DATABASE_URL:
            pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=1,
                max_size=10,
                statement_cache_size=0,  # Neon pooler talab qiladi
                max_inactive_connection_lifetime=60.0,  # Neon auto-suspend uchun
            )
        else:
            pool = await asyncpg.create_pool(
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                database=POSTGRES_DB,
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                min_size=1,
                max_size=10,
                statement_cache_size=0,
                max_inactive_connection_lifetime=60.0,
            )
        print("✅ PostgreSQL Connection Pool muvaffaqiyatli yaratildi.")
    except Exception as e:
        print(f"❌ PostgreSQL ulanishda xatolik: {e}")
