# states.py
from aiogram.dispatcher.filters.state import State, StatesGroup


class UserAttendance(StatesGroup):
    waiting_for_location = State()
    waiting_for_reason = State()
    waiting_for_message = State()


class UserJoinApplication(StatesGroup):
    waiting_for_name = State()


class AdminAcceptPending(StatesGroup):
    waiting_for_new_name = State()
    waiting_for_branch = State()
    waiting_for_daily_hours = State()
    waiting_for_start_time = State()
    waiting_for_end_time = State()


class AdminUpdateWorker(StatesGroup):
    waiting_for_new_name = State()


class AdminAddWorker(StatesGroup):
    waiting_for_name = State()
    waiting_for_tg_id = State()
    waiting_for_branch = State()
    waiting_for_pay_amount = State()
    waiting_for_start_time = State()
    waiting_for_end_time = State()


class AdminSetSalary(StatesGroup):
    waiting_for_salary_amount = State()


class AdminAddSalaryPayment(StatesGroup):
    waiting_for_payment_amount = State()


class AdminModifyPayment(StatesGroup):
    waiting_for_new_payment_amount = State()


class AdminModifyMonthlySalary(StatesGroup):
    waiting_for_new_monthly_salary = State()


class AdminSetDailyHours(StatesGroup):
    waiting_for_new_daily_hours = State()


class FridayWork(StatesGroup):
    waiting_for_choice = State()


class EarlyLeave(StatesGroup):
    waiting_for_reason = State()


class LateArrival(StatesGroup):
    waiting_for_reason = State()


class AdminSetWorkTime(StatesGroup):
    waiting_for_start_time = State()
    waiting_for_end_time = State()


class HelpState(StatesGroup):
    waiting_for_feedback = State()


class MyStatsStates(StatesGroup):
    SELECT_YEAR = State()  # yil tanlash
    SELECT_MONTH = State()  # oy tanlash


class AIConversation(StatesGroup):
    in_progress = State()  # Suhbat davom etayotganini bildiradi


class Disambiguation(StatesGroup):
    choosing_worker = State()


# states.py faylining oxiriga qo'shing

class AdminManualAttendance(StatesGroup):
    choosing_worker = State()
    getting_date = State()
    getting_arrival = State()
    getting_departure = State()


class AdminQuickAttendance(StatesGroup):
    waiting_for_worker_name = State()


class AdminBranchAdminSettings(StatesGroup):
    waiting_for_admin_tg_id = State()


class AdminSuperadminSettings(StatesGroup):
    waiting_for_superadmin_tg_id = State()


class Accounting(StatesGroup):
    # Buyurtma yaratish/tahrirlash
    GET_ORDER_NAME = State()
    GET_ORDER_DESC = State()
    GET_CLIENT_NAME = State()
    GET_CLIENT_CONTACT = State()
    GET_START_DATE = State()
    GET_DEADLINE = State()
    GET_TOTAL_COST = State()
    EDIT_ORDER_NAME = State()
    EDIT_ORDER_CLIENT = State()
    EDIT_ORDER_DESC = State()
    EDIT_ORDER_PRICE = State()

    # Material yaratish/tahrirlash uchun YANGI HOLATLAR
    GET_MATERIAL_NAME = State()
    GET_MATERIAL_UNIT = State()
    GET_MATERIAL_COST = State()
    GET_MATERIAL_PHOTO = State()  # Rasm uchun yangi holat

    EDIT_MATERIAL_NAME = State()
    EDIT_MATERIAL_UNIT = State()
    EDIT_MATERIAL_COST = State()

    # Buyurtmaga material qo'shish
    CHOOSE_MATERIAL_FOR_ORDER = State()
    GET_MATERIAL_QUANTITY = State()
    SELECTING_MATERIAL_FOR_ORDER = State()
    EDIT_MATERIAL_QUANTITY = State()
    WAITING_FOR_SEARCH_QUERY = State()
    # Buyurtmaga ish qo'shish
    CHOOSE_WORKER_FOR_ORDER = State()
    GET_WORKER_HOURS = State()
    GET_LABOR_COST = State()
    GET_LABOR_DESC = State()


class MaterialCategory(StatesGroup):
    ENTERING_NAME = State()
    ENTERING_PARENT = State()
    EDITING_NAME = State()


# Super Admin - Web Login Sozlamalari
class AdminWebLoginSettings(StatesGroup):
    waiting_for_username = State()
    waiting_for_password = State()
    waiting_for_fullname = State()
    waiting_for_new_password = State()
