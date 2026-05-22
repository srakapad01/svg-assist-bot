# keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from constants import ROLE_PRIORITY, ALL_ROLES

def main_menu(role: str, roles: list = None) -> ReplyKeyboardMarkup:
    if roles is None:
        roles = [role]

    effective_role = role
    for r in ROLE_PRIORITY:
        if r in roles:
            effective_role = r
            break

    has_tips_access = "старший_смены" in roles

    if effective_role == "owner":
        buttons = [
            [KeyboardButton(text="📚 База знаний"), KeyboardButton(text="👤 Добавить сотрудника")],
            [KeyboardButton(text="📅 Управление графиком"), KeyboardButton(text="🏆 Управление мотивацией")],
            [KeyboardButton(text="📖 Управление базой знаний"), KeyboardButton(text="📊 Аналитика")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="👑 Управление ролями")],
        ]
    elif effective_role == "сказочный_полковник":
        buttons = [
            [KeyboardButton(text="📚 База знаний"), KeyboardButton(text="👤 Добавить сотрудника")],
            [KeyboardButton(text="📅 Управление графиком"), KeyboardButton(text="🏆 Управление мотивацией")],
            [KeyboardButton(text="📖 Управление базой знаний"), KeyboardButton(text="📊 Аналитика")],
            [KeyboardButton(text="⚙️ Настройки")],
        ]
    elif effective_role == "менеджер":
        buttons = [
            [KeyboardButton(text="📅 Мой график"), KeyboardButton(text="💰 Мои чаевые")],
            [KeyboardButton(text="📚 База знаний"), KeyboardButton(text="👤 Добавить сотрудника")],
            [KeyboardButton(text="📅 Управление графиками"), KeyboardButton(text="🏆 Управление мотивацией")],
            [KeyboardButton(text="📖 Управление базой знаний")],
        ]
    elif effective_role in ["официант", "бартендер", "встреч_менеджер", "помощник", "кальянный_мастер"]:
        buttons = [
            [KeyboardButton(text="📅 Мой график"), KeyboardButton(text="💰 Мои чаевые")],
            [KeyboardButton(text="📚 База знаний"), KeyboardButton(text="🏆 Моя мотивация")],
        ]
    else:
        buttons = [[KeyboardButton(text="📝 Зарегистрироваться")]]

    # Добавляем кнопки трекера
    # Для управляющих ролей
    if effective_role in ["owner", "сказочный_полковник", "менеджер"]:
        buttons.append([KeyboardButton(text="⚙️ Управление трекером")])
    # Для ролей, которые используют трекер
    if effective_role in ["официант", "бартендер", "помощник", "встреч_менеджер"]:
        buttons.append([KeyboardButton(text="📊 Трекер")])

    if has_tips_access:
        buttons.append([KeyboardButton(text="💰 Чаевые")])
    
    if effective_role and effective_role != "owner":
        buttons.append([KeyboardButton(text="🐛 Ошибка")])

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def schedule_menu() -> ReplyKeyboardMarkup:
    """Меню управления графиком для менеджеров"""
    buttons = [
        [KeyboardButton(text="📆 Добавить смены на месяц"), KeyboardButton(text="➕ Добавить смену")],
        [KeyboardButton(text="❌ Убрать смену"), KeyboardButton(text="📋 Управление графиком")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def tracker_management_menu():
    buttons = [
        [KeyboardButton(text="👥 Отметить блоки сотруднику")],
        [KeyboardButton(text="➕ Добавить глобальный блок"), KeyboardButton(text="✏️ Редактировать блок")],
        [KeyboardButton(text="🗑 Удалить блок"), KeyboardButton(text="📋 Отчёт по блокам")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def motivation_menu() -> ReplyKeyboardMarkup:
    """Меню мотивации (кнопки-эмодзи)"""
    buttons = [
        [KeyboardButton(text="💵"), KeyboardButton(text="💩"), KeyboardButton(text="⭐")],
        [KeyboardButton(text="⏰"), KeyboardButton(text="📒"), KeyboardButton(text="😎")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def knowledge_menu() -> ReplyKeyboardMarkup:
    """Меню базы знаний (заглушка)"""
    buttons = [
        [KeyboardButton(text="📤 Загрузить материал")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def roles_menu() -> ReplyKeyboardMarkup:
    """Меню управления ролями (только для owner)"""
    buttons = [
        [KeyboardButton(text="➕ Добавить роль сотруднику")],
        [KeyboardButton(text="➖ Убрать роль сотруднику")],
        [KeyboardButton(text="📋 Роли сотрудников")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# ----- Inline клавиатуры для управления типами графиков (schedules) -----

def schedules_list_keyboard(schedules: dict) -> InlineKeyboardMarkup:
    """Список типов графиков в виде inline-кнопок"""
    rows = []
    for i, (name, info) in enumerate(schedules.items()):
        label = f"{name}  ({info['work']}/{info['rest']})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mgsch_view_{i}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить график", callback_data="mgsch_add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_actions_keyboard(idx: int) -> InlineKeyboardMarkup:
    """Клавиатура действий над выбранным графиком"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"mgsch_rename_{idx}"),
            InlineKeyboardButton(text="🔢 Изменить дни",  callback_data=f"mgsch_edit_{idx}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mgsch_delete_{idx}"),
        ],
        [
            InlineKeyboardButton(text="‹ Назад к списку", callback_data="mgsch_back"),
        ],
    ])


def confirm_delete_keyboard(idx: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления графика"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"mgsch_confirm_del_{idx}"),
            InlineKeyboardButton(text="❌ Отмена",      callback_data=f"mgsch_view_{idx}"),
        ]
    ])


def department_keyboard(departments: list) -> InlineKeyboardMarkup:
    """Клавиатура выбора подразделения (используется в schedule, tips)"""
    rows = []
    for i, dept in enumerate(departments):
        rows.append([InlineKeyboardButton(text=dept, callback_data=f"dept_{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)