import json
import os
import urllib.parse

from dotenv import load_dotenv


APP_ENV = os.getenv("APP_ENV", "local")

if APP_ENV == "local":
    load_dotenv(dotenv_path=".env.local")
else:
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
_raw_superadmins_env = os.getenv("SUPERADMINS", "")
_raw_legacy_admins_env = os.getenv("ADMINS", "")


DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
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
    POSTGRES_HOST = os.getenv("POSTGRES_HOST")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))


REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    result = urllib.parse.urlparse(REDIS_URL)
    REDIS_HOST = result.hostname
    REDIS_PORT = result.port or 6379
    REDIS_PASSWORD = result.password
    REDIS_DB = 0
else:
    REDIS_HOST = os.getenv("REDIS_HOST")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
    REDIS_DB = int(os.getenv("REDIS_DB", 0))


ALLOWED_LAT = float(os.getenv("ALLOWED_LAT", 0.0))
ALLOWED_LON = float(os.getenv("ALLOWED_LON", 0.0))
ALLOWED_RADIUS = float(os.getenv("ALLOWED_RADIUS", 0.0))
WORK_LOG_GROUP_ID = int(os.getenv("WORK_LOG_GROUP_ID", "0") or 0)
ABSENCE_REMINDER_DELAY_MIN = int(os.getenv("ABSENCE_REMINDER_DELAY_MIN", 60))


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_id_list(raw_value) -> list[int]:
    if isinstance(raw_value, list):
        values = raw_value
    else:
        values = str(raw_value or "").split(",")

    parsed_ids = []
    for value in values:
        parsed_value = _to_int(value, 0)
        if parsed_value:
            parsed_ids.append(parsed_value)
    return parsed_ids


def _normalize_branch_config(raw_branch, index):
    if not isinstance(raw_branch, dict):
        return None

    raw_admin_ids = raw_branch.get("admin_ids", raw_branch.get("admins", []))
    admin_ids = _parse_id_list(raw_admin_ids)

    return {
        "code": str(raw_branch.get("code") or f"branch-{index}").strip(),
        "name": str(raw_branch.get("name") or f"Filial {index}").strip(),
        "latitude": _to_float(raw_branch.get("latitude", raw_branch.get("lat"))),
        "longitude": _to_float(raw_branch.get("longitude", raw_branch.get("lon"))),
        "radius": _to_float(raw_branch.get("radius", raw_branch.get("allowed_radius", ALLOWED_RADIUS))),
        "work_log_group_id": _to_int(raw_branch.get("work_log_group_id", raw_branch.get("group_id")), 0),
        "admin_ids": admin_ids,
    }


BRANCH_CONFIGS = []
_branches_json = (os.getenv("BRANCHES_JSON") or "").strip()
if _branches_json:
    try:
        parsed_branches = json.loads(_branches_json)
        if isinstance(parsed_branches, list):
            for index, branch in enumerate(parsed_branches, start=1):
                normalized_branch = _normalize_branch_config(branch, index)
                if normalized_branch:
                    BRANCH_CONFIGS.append(normalized_branch)
    except json.JSONDecodeError:
        BRANCH_CONFIGS = []

if not BRANCH_CONFIGS:
    branch_1_lat = _to_float(os.getenv("BRANCH_1_LAT", ALLOWED_LAT))
    branch_1_lon = _to_float(os.getenv("BRANCH_1_LON", ALLOWED_LON))
    branch_1_radius = _to_float(os.getenv("BRANCH_1_RADIUS", ALLOWED_RADIUS or 200))
    branch_2_lat = _to_float(os.getenv("BRANCH_2_LAT", branch_1_lat))
    branch_2_lon = _to_float(os.getenv("BRANCH_2_LON", branch_1_lon))
    branch_2_radius = _to_float(os.getenv("BRANCH_2_RADIUS", branch_1_radius or 200))

    BRANCH_CONFIGS.extend(
        [
            {
                "code": "yangi-bozor",
                "name": os.getenv("BRANCH_1_NAME", "Yangi Bozor"),
                "latitude": branch_1_lat,
                "longitude": branch_1_lon,
                "radius": branch_1_radius,
                "work_log_group_id": _to_int(os.getenv("BRANCH_1_WORK_LOG_GROUP_ID", WORK_LOG_GROUP_ID), 0),
                "admin_ids": _parse_id_list(os.getenv("BRANCH_1_ADMINS", "")),
            },
            {
                "code": "eski-shahar",
                "name": os.getenv("BRANCH_2_NAME", "Eski Shahar"),
                "latitude": branch_2_lat,
                "longitude": branch_2_lon,
                "radius": branch_2_radius,
                "work_log_group_id": _to_int(os.getenv("BRANCH_2_WORK_LOG_GROUP_ID", 0), 0),
                "admin_ids": _parse_id_list(os.getenv("BRANCH_2_ADMINS", "")),
            },
        ]
    )

BRANCH_ADMIN_IDS = sorted(
    {
        admin_id
        for branch in BRANCH_CONFIGS
        for admin_id in branch.get("admin_ids", [])
        if admin_id
    }
)
EXPLICIT_SUPERADMIN_IDS = _parse_id_list(_raw_superadmins_env)
LEGACY_ADMIN_IDS = _parse_id_list(_raw_legacy_admins_env)
HAS_EXPLICIT_SUPERADMINS = bool(EXPLICIT_SUPERADMIN_IDS)

if HAS_EXPLICIT_SUPERADMINS:
    SUPERADMINS = sorted(set(EXPLICIT_SUPERADMIN_IDS))
else:
    inferred_superadmins = sorted(set(LEGACY_ADMIN_IDS) - set(BRANCH_ADMIN_IDS))
    if inferred_superadmins or BRANCH_ADMIN_IDS:
        SUPERADMINS = inferred_superadmins
    else:
        SUPERADMINS = sorted(set(LEGACY_ADMIN_IDS))

ADMINS = sorted(set(SUPERADMINS) | set(BRANCH_ADMIN_IDS))

LATE_EARLY_TOLERANCE_MIN = int(os.getenv("LATE_EARLY_TOLERANCE_MIN", 0))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")

_openrouter_models_str = os.getenv("OPENROUTER_FREE_MODELS", "openrouter/free")
OPENROUTER_FREE_MODELS = [m.strip() for m in _openrouter_models_str.split(",") if m.strip()]

_keys_str = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [k.strip() for k in _keys_str.split(",") if k.strip()]
if not GEMINI_API_KEYS:
    _single_key = os.getenv("GEMINI_API_KEY")
    if _single_key:
        GEMINI_API_KEYS = [_single_key]

_gemini_models_str = os.getenv(
    "GEMINI_FREE_MODELS",
    "models/gemini-2.5-flash,models/gemini-2.5-flash-lite,models/gemini-2.0-flash,models/gemini-2.0-flash-lite",
)
GEMINI_FREE_MODELS = [m.strip() for m in _gemini_models_str.split(",") if m.strip()]


WEBAPP_URL = os.getenv("WEBAPP_URL", "")
ADMIN_CONTACT_TEXT = os.getenv("ADMIN_CONTACT_TEXT", "Admin: @rustamxojayev_abdulboriy")
