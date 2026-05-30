from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_employee_main_menu(is_working: bool = False, study_active: bool = False) -> InlineKeyboardMarkup:
    attend_label = "🔴 Ketyapman" if is_working else "🟢 Ishga keldim"
    study_label = "🔴 O'qishdan qaytdim" if study_active else "🟠 O'qishga ketdim"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton(attend_label, callback_data="empmenu:clock"),
        InlineKeyboardButton("🌙 Dam", callback_data="empmenu:rest"),
    )
    kb.row(
        InlineKeyboardButton(study_label, callback_data="empmenu:study"),
        InlineKeyboardButton("💰 Maoshim", callback_data="empmenu:salary"),
    )
    kb.row(
        InlineKeyboardButton("📊 Statistika", callback_data="empmenu:mystats"),
        InlineKeyboardButton("🆘 Yordam", callback_data="empmenu:help"),
    )
    kb.row(InlineKeyboardButton("🔄 Yangilash", callback_data="empmenu:home"))
    return kb
