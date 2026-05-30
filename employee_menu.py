from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_employee_main_menu(is_working: bool = False, study_active: bool = False) -> InlineKeyboardMarkup:
    attend_label = "🔴 Ketyapman" if is_working else "🟢 Ishga keldim"
    study_label = "🔴 O'qishdan qaytdim" if study_active else "🟠 O'qishga ketdim"
    attend_style = "danger" if is_working else "success"
    study_style = "danger" if study_active else "primary"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton(attend_label, callback_data="empmenu:clock", style=attend_style),
        InlineKeyboardButton("🌙 Dam", callback_data="empmenu:rest", style="danger"),
    )
    kb.row(
        InlineKeyboardButton(study_label, callback_data="empmenu:study", style=study_style),
        InlineKeyboardButton("💰 Maoshim", callback_data="empmenu:salary", style="primary"),
    )
    kb.row(
        InlineKeyboardButton("📊 Statistika", callback_data="empmenu:mystats", style="primary"),
        InlineKeyboardButton("🆘 Yordam", callback_data="empmenu:help", style="primary"),
    )
    kb.row(InlineKeyboardButton("🔄 Yangilash", callback_data="empmenu:home", style="primary"))
    return kb
