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
