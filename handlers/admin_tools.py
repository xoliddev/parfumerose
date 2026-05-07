# handlers/admin_tools.py
# Bu fayl adminning tabiiy tildagi so'rovlariga javob berish uchun
# sun'iy intellekt chaqiradigan "asboblar" to'plamini o'z ichiga oladi.

import database as db
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any
from pytz import timezone
from config import SUPERADMINS

uz_tz = timezone('Asia/Tashkent')


# ==============================================================================
# YORDAMCHI FUNKSIYALAR (matnli sanalarni to'g'rilash uchun)
# ==============================================================================

def _parse_date(target_date: str) -> Optional[date]:
    """"bugun", "kecha" kabi matnli sanalarni yoki "YYYY-MM-DD" formatini date obyektiga o'giradi."""
    today = datetime.now(uz_tz).date()
    target_date = target_date.lower()
    if "bugun" in target_date:
        return today
    if "kecha" in target_date:
        return today - timedelta(days=1)
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_month(month: str) -> Optional[str]:
    """"shu oy", "otgan oy" kabi matnlarni "YYYY-MM" formatiga o'giradi."""
    today = datetime.now(uz_tz).date()
    month = month.lower()
    if "shu oy" in month or "joriy" in month:
        return today.strftime("%Y-%m")
    if "otgan oy" in month:
        first_day_of_current_month = today.replace(day=1)
        last_day_of_last_month = first_day_of_current_month - timedelta(days=1)
        return last_day_of_last_month.strftime("%Y-%m")
    try:
        # Agar "2024-08" kabi formatda kelsa
        datetime.strptime(month, "%Y-%m")
        return month
    except ValueError:
        return None


def _normalize_name(value: str) -> str:
    return " ".join((value or "").lower().split())


def _candidate_label(candidate: Dict[str, Any]) -> str:
    branch_name = (candidate.get("branch_name") or "").strip()
    if branch_name:
        return f"{candidate['full_name']} [{branch_name}]"
    return candidate["full_name"]


async def _get_admin_branch_scope(admin_tg_id: Optional[int]) -> Optional[List[int]]:
    if admin_tg_id is None:
        return None
    return await db.get_admin_branch_ids(admin_tg_id)


def _build_ambiguous_worker_result(employee_name: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": "ambiguous",
        "message": f"'{employee_name}' bo'yicha bir nechta xodim topildi. Qaysi birini nazarda tutganingizni aniqlang.",
        "nomzodlar": [_candidate_label(candidate) for candidate in candidates[:5]],
    }


async def _resolve_worker_for_admin(employee_name: str, admin_tg_id: Optional[int]) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if admin_tg_id is None:
        candidates = await db.find_worker_candidates_by_name(employee_name, limit=8)
    else:
        candidates = await db.find_worker_candidates_for_admin(admin_tg_id, employee_name, limit=8)

    if not candidates:
        return None, {"status": "error", "message": f"'{employee_name}' ismli xodim topilmadi."}

    if len(candidates) == 1:
        return candidates[0], None

    normalized_query = _normalize_name(employee_name)
    exact_matches = [candidate for candidate in candidates if _normalize_name(candidate["full_name"]) == normalized_query]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        return None, _build_ambiguous_worker_result(employee_name, exact_matches)

    top_candidate = candidates[0]
    second_candidate = candidates[1]
    top_sim = float(top_candidate.get("sim") or 0.0)
    second_sim = float(second_candidate.get("sim") or 0.0)
    if top_sim >= second_sim + 0.15:
        return top_candidate, None

    return None, _build_ambiguous_worker_result(employee_name, candidates)


# ==============================================================================
# ASBOBLAR TO'PLAMI (AI SHU FUNKSIYALARNI CHAQIRADI)
# ==============================================================================

async def add_salary_payment(employee_name: str, amount: float, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Xodimga avans yoki maosh to'lovini qo'shadi va natijani tasdiqlaydi. Bu funksiya faqat pul qo'shish uchun ishlatiladi."""
    worker, error = await _resolve_worker_for_admin(employee_name, admin_tg_id)
    if error:
        return error

    async with db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO salary_payments (worker_id, payment_date, amount) VALUES ($1, CURRENT_DATE, $2)",
            worker['id'], amount
        )
    return {"status": "success",
            "message": f"{_candidate_label(worker)}ga {amount:,.0f} so'm to'lov muvaffaqiyatli qo'shildi."}


async def get_daily_attendance(employee_name: str, target_date: str, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Aniq bir xodimning belgilangan kundagi keldi-ketdi ma'lumotlarini (kelgan vaqti, ketgan vaqti, ishlagan soati) to'liq qaytaradi. `target_date` "bugun", "kecha" yoki "YYYY-MM-DD" formatida bo'lishi mumkin."""
    parsed_date = _parse_date(target_date)
    if not parsed_date:
        return {"status": "error",
                "message": "Sana formati noto'g'ri. 'bugun', 'kecha' yoki 'YYYY-MM-DD' formatida kiriting."}

    worker, error = await _resolve_worker_for_admin(employee_name, admin_tg_id)
    if error:
        return error

    async with db.pool.acquire() as conn:
        session = await conn.fetchrow("""
                                      SELECT w.full_name, ws.arrival_time, ws.departure_time, ws.total_hours, b.name AS branch_name
                                      FROM work_sessions ws
                                               JOIN workers w ON ws.user_id = w.id
                                               LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                      WHERE ws.user_id = $1
                                        AND ws.date = $2
                                      """, worker["id"], parsed_date)

    if not session:
        return {"status": "not_found", "message": f"{_candidate_label(worker)} {parsed_date} sanasida ishga kelmagan."}

    return {
        "xodim": _candidate_label({"full_name": session['full_name'], "branch_name": session['branch_name']}),
        "sana": str(parsed_date),
        "kelgan_vaqti": session['arrival_time'].astimezone(uz_tz).strftime('%H:%M:%S') if session[
            'arrival_time'] else "N/A",
        "ketgan_vaqti": session['departure_time'].astimezone(uz_tz).strftime('%H:%M:%S') if session[
            'departure_time'] else "Hali ketmagan",
        "ishlagan_soat": f"{session['total_hours'] or 0:.2f} soat"
    }


async def get_salary_report_for_month(employee_name: str, month: str, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Xodimning tanlangan oydagi maosh hisobotini (tayinlangan maosh, jami to'lovlar, qoldiq) qaytaradi. 'month' parametri "shu oy" yoki "otgan oy" bo'lishi mumkin."""
    parsed_month = _parse_month(month)
    if not parsed_month:
        return {"status": "error", "message": "Oy formati noto'g'ri. 'shu oy' yoki 'otgan oy' deb kiriting."}

    worker, error = await _resolve_worker_for_admin(employee_name, admin_tg_id)
    if error:
        return error

    async with db.pool.acquire() as conn:
        total_paid = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM salary_payments WHERE worker_id = $1 AND to_char(payment_date, 'YYYY-MM') = $2",
            worker['id'], parsed_month
        )

    base_salary = worker['monthly_salary'] or 0.0
    remaining = base_salary - total_paid

    return {
        "xodim": _candidate_label(worker),
        "oy": parsed_month,
        "tayinlangan_maosh": f"{base_salary:,.0f} so'm",
        "jami_tolangan": f"{total_paid:,.0f} so'm",
        "qoldiq": f"{remaining:,.0f} so'm"
    }


async def list_employees_at_work_now(admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Ayni vaqtda ishda bo'lgan (kelgan lekin hali ketmagan) xodimlar ro'yxatini qaytaradi."""
    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            at_work = await conn.fetch("""
                                       SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                       FROM work_sessions ws
                                                JOIN workers w ON ws.user_id = w.id
                                                LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                       WHERE ws.date = CURRENT_DATE
                                         AND ws.departure_time IS NULL
                                       ORDER BY ws.arrival_time
                                       """)
        elif not branch_scope:
            at_work = []
        else:
            at_work = await conn.fetch("""
                                       SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                       FROM work_sessions ws
                                                JOIN workers w ON ws.user_id = w.id
                                                LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                       WHERE ws.date = CURRENT_DATE
                                         AND ws.departure_time IS NULL
                                         AND COALESCE(ws.branch_id, w.branch_id) = ANY($1::int[])
                                       ORDER BY ws.arrival_time
                                       """, branch_scope)

    if not at_work:
        return {"status": "empty", "message": "Hozir ishda hech kim yo'q."}

    return {
        "status": "success",
        "xodimlar": [
            {
                "ism": _candidate_label({"full_name": row['full_name'], "branch_name": row['branch_name']}),
                "kelgan_vaqti": row['arrival_time'].astimezone(uz_tz).strftime('%H:%M')
            } for row
            in at_work
        ]
    }


async def list_absent_employees(target_date: str, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Berilgan kunda ishga kelmagan faol xodimlar ro'yxatini qaytaradi. `target_date` "bugun", "kecha" yoki "YYYY-MM-DD" formatida bo'lishi mumkin."""
    parsed_date = _parse_date(target_date)
    if not parsed_date:
        return {"status": "error", "message": "Sana formati noto'g'ri."}

    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            all_active_workers = await conn.fetch(
                "SELECT w.id, w.full_name, b.name AS branch_name FROM workers w "
                "LEFT JOIN branches b ON b.id = w.branch_id WHERE w.is_active = TRUE"
            )
            present_worker_ids = await conn.fetchval("SELECT array_agg(user_id) FROM work_sessions WHERE date = $1",
                                                     parsed_date) or []
        elif not branch_scope:
            all_active_workers = []
            present_worker_ids = []
        else:
            all_active_workers = await conn.fetch(
                "SELECT w.id, w.full_name, b.name AS branch_name FROM workers w "
                "LEFT JOIN branches b ON b.id = w.branch_id "
                "WHERE w.is_active = TRUE AND w.branch_id = ANY($1::int[])",
                branch_scope,
            )
            present_worker_ids = await conn.fetchval(
                """
                SELECT array_agg(ws.user_id)
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE ws.date = $1 AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                """,
                parsed_date,
                branch_scope,
            ) or []

    absent_workers = [
        _candidate_label({"full_name": worker['full_name'], "branch_name": worker['branch_name']})
        for worker in all_active_workers if worker['id'] not in present_worker_ids
    ]

    if not absent_workers:
        return {"status": "empty", "message": f"{parsed_date} sanasida hamma xodim ishga kelgan."}

    return {"status": "success", "sana": str(parsed_date), "xodimlar": absent_workers}


async def get_late_arrivals_report(target_date: str, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Belgilangan sanada ishga kech kelgan xodimlar ro'yxatini, kechikkan vaqtini va sababini (agar mavjud bo'lsa) qaytaradi."""
    parsed_date = _parse_date(target_date)
    if not parsed_date:
        return {"status": "error", "message": "Sana formati noto'g'ri."}

    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            late_workers = await conn.fetch("""
                                            SELECT w.full_name,
                                                   ws.arrival_time,
                                                   w.work_start,
                                                   ws.late_reason,
                                                   b.name AS branch_name,
                                                   EXTRACT(EPOCH FROM (ws.arrival_time::time - w.work_start)) / 60 AS late_minutes
                                            FROM work_sessions ws
                                                     JOIN workers w ON ws.user_id = w.id
                                                     LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                            WHERE ws.date = $1
                                              AND w.work_start IS NOT NULL
                                              AND ws.arrival_time::time > w.work_start
                                            ORDER BY late_minutes DESC
                                            """, parsed_date)
        elif not branch_scope:
            late_workers = []
        else:
            late_workers = await conn.fetch("""
                                            SELECT w.full_name,
                                                   ws.arrival_time,
                                                   w.work_start,
                                                   ws.late_reason,
                                                   b.name AS branch_name,
                                                   EXTRACT(EPOCH FROM (ws.arrival_time::time - w.work_start)) / 60 AS late_minutes
                                            FROM work_sessions ws
                                                     JOIN workers w ON ws.user_id = w.id
                                                     LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                            WHERE ws.date = $1
                                              AND w.work_start IS NOT NULL
                                              AND ws.arrival_time::time > w.work_start
                                              AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                                            ORDER BY late_minutes DESC
                                            """, parsed_date, branch_scope)

    if not late_workers:
        return {"status": "empty", "message": f"{parsed_date} sanasida hech kim ishga kechikmagan."}

    return {
        "status": "success",
        "sana": str(parsed_date),
        "kechikkanlar": [
            {
                "ism": _candidate_label({"full_name": row['full_name'], "branch_name": row['branch_name']}),
                "kelgan_vaqti": row['arrival_time'].astimezone(uz_tz).strftime('%H:%M:%S'),
                "kechikkan_daqiqa": int(row['late_minutes']),
                "sababi": row['late_reason'] or "Kiritilmagan"
            } for row in late_workers
        ]
    }


async def get_attendees_for_date(target_date: str, admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Belgilangan kunda ishga kelgan barcha xodimlar ro'yxatini va ularning kelgan vaqtini qaytaradi. `target_date` "bugun", "kecha" yoki "YYYY-MM-DD" formatida bo'lishi mumkin."""
    parsed_date = _parse_date(target_date)
    if not parsed_date:
        return {"status": "error", "message": "Sana formati noto'g'ri. 'bugun', 'kecha' yoki 'YYYY-MM-DD' formatida kiriting."}

    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            attendees = await conn.fetch("""
                                         SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                         FROM work_sessions ws
                                                  JOIN workers w ON ws.user_id = w.id
                                                  LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                         WHERE ws.date = $1
                                         ORDER BY ws.arrival_time
                                         """, parsed_date)
        elif not branch_scope:
            attendees = []
        else:
            attendees = await conn.fetch("""
                                         SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                         FROM work_sessions ws
                                                  JOIN workers w ON ws.user_id = w.id
                                                  LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                         WHERE ws.date = $1
                                           AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                                         ORDER BY ws.arrival_time
                                         """, parsed_date, branch_scope)

    if not attendees:
        return {"status": "empty", "message": f"{parsed_date} sanasida hech kim ishga kelmagan."}

    return {
        "status": "success",
        "sana": str(parsed_date),
        "kelgan_xodimlar": [
            {
                "ism": _candidate_label({"full_name": row['full_name'], "branch_name": row['branch_name']}),
                "kelgan_vaqti": row['arrival_time'].astimezone(uz_tz).strftime('%H:%M:%S')
            }
            for row in attendees
        ]
    }


async def get_last_session_date(admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Tizimdagi eng oxirgi ish sessiyasi bo'lgan sanani topib beradi."""
    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        if branch_scope is None:
            last_date = await conn.fetchval("SELECT MAX(date) FROM work_sessions")
        elif not branch_scope:
            last_date = None
        else:
            last_date = await conn.fetchval(
                """
                SELECT MAX(ws.date)
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE COALESCE(ws.branch_id, w.branch_id) = ANY($1::int[])
                """,
                branch_scope,
            )

    if not last_date:
        return {"status": "not_found", "message": "Tizimda hali birorta ham ish sessiyasi qayd etilmagan."}

    return {
        "status": "success",
        "eng_oxirgi_sana": str(last_date)
    }


async def get_attendees_for_last_session(admin_tg_id: Optional[int] = None) -> Dict[str, Any]:
    """Eng oxirgi ish sessiyasi bo'lgan kunda ishga kelgan barcha xodimlar ro'yxatini va ularning kelgan vaqtini qaytaradi."""
    branch_scope = await _get_admin_branch_scope(admin_tg_id)
    async with db.pool.acquire() as conn:
        # 1-qadam: Eng oxirgi sessiya sanasini topamiz
        if branch_scope is None:
            last_date = await conn.fetchval("SELECT MAX(date) FROM work_sessions")
        elif not branch_scope:
            last_date = None
        else:
            last_date = await conn.fetchval(
                """
                SELECT MAX(ws.date)
                FROM work_sessions ws
                JOIN workers w ON w.id = ws.user_id
                WHERE COALESCE(ws.branch_id, w.branch_id) = ANY($1::int[])
                """,
                branch_scope,
            )

        if not last_date:
            return {"status": "not_found", "message": "Tizimda hali birorta ham ish sessiyasi qayd etilmagan."}

        # 2-qadam: O'sha sanada ishga kelganlar ro'yxatini olamiz
        if branch_scope is None:
            attendees = await conn.fetch("""
                                         SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                         FROM work_sessions ws
                                                  JOIN workers w ON ws.user_id = w.id
                                                  LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                         WHERE ws.date = $1
                                         ORDER BY ws.arrival_time
                                         """, last_date)
        else:
            attendees = await conn.fetch("""
                                         SELECT w.full_name, ws.arrival_time, b.name AS branch_name
                                         FROM work_sessions ws
                                                  JOIN workers w ON ws.user_id = w.id
                                                  LEFT JOIN branches b ON b.id = COALESCE(ws.branch_id, w.branch_id)
                                         WHERE ws.date = $1
                                           AND COALESCE(ws.branch_id, w.branch_id) = ANY($2::int[])
                                         ORDER BY ws.arrival_time
                                         """, last_date, branch_scope)

    return {
        "status": "success",
        "sana": str(last_date),
        "kelgan_xodimlar": [
            {
                "ism": _candidate_label({"full_name": row['full_name'], "branch_name": row['branch_name']}),
                "kelgan_vaqti": row['arrival_time'].astimezone(uz_tz).strftime('%H:%M:%S')
            }
            for row in attendees
        ]
    }

