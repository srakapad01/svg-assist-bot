# handlers/tracker.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from database import (
    get_all_fixed_blocks, get_all_dynamic_blocks, get_employee_blocks_map,
    set_employee_block, get_employee_coefficient, load_db, get_user,
    add_dynamic_block, update_dynamic_block, delete_dynamic_block
)
from keyboards import tracker_management_menu

router = Router()

# ---------- FSM для управления трекером ----------
class TrackerState(StatesGroup):
    waiting_block_name = State()
    waiting_block_edit_name = State()
    waiting_block_edit_id = State()
    waiting_del_block_id = State()
    waiting_select_user_for_edit = State()
    waiting_select_block_for_user = State()

# ---------- Кнопка "Трекер" для сотрудников ----------
@router.message(F.text == "📊 Трекер")
async def tracker_view(message: Message, role: str = None, roles: list = None):
    user_id = message.from_user.id
    # Проверяем, что сотрудник имеет право на трекер (официант, бартендер, помощник, встречающий менеджер)
    user_roles = roles or []
    if not any(r in user_roles for r in ["официант", "бартендер", "помощник", "встреч_менеджер"]):
        await message.answer("У вас нет доступа к трекеру.")
        return
    coeff = await get_employee_coefficient(user_id)
    fixed_blocks = await get_all_fixed_blocks()
    dyn_blocks = await get_all_dynamic_blocks()
    emp_map = await get_employee_blocks_map(user_id)
    # Формируем сообщение
    text = f"📋 <b>Ваш трекер знаний</b>\n\n"
    text += f"Текущий коэффициент: <b>{coeff}</b>\n\n"
    text += "📌 <u>Фиксированные блоки</u>:\n"
    for b in fixed_blocks:
        status = "✅" if emp_map.get(b["id"], 0) == 1 else "❌"
        text += f"{status} {b['name']}\n"
    if dyn_blocks:
        text += "\n✨ <u>Спецпредложения</u>:\n"
        for b in dyn_blocks:
            status = "✅" if emp_map.get(b["id"], 0) == 1 else "❌"
            text += f"{status} {b['name']}\n"
    await message.answer(text, parse_mode="HTML")

# ---------- Кнопка "Управление трекером" для менеджеров ----------
@router.message(F.text == "⚙️ Управление трекером")
async def tracker_management(message: Message, role: str = None, roles: list = None):
    user_roles = roles or []
    if not has_access(role, ["owner", "менеджер", "сказочный_полковник"], user_roles):
        await message.answer("Нет доступа.")
        return
    await message.answer("Управление трекером:", reply_markup=tracker_management_menu())

# ---------- Обработчики подменю ----------
# Добавить глобальный блок
@router.message(F.text == "➕ Добавить глобальный блок")
async def add_global_block_start(message: Message, state: FSMContext):
    await message.answer("Введите название нового блока (спецпредложение):")
    await state.set_state(TrackerState.waiting_block_name)

@router.message(TrackerState.waiting_block_name)
async def add_global_block_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    await add_dynamic_block(name, message.from_user.id)
    await state.clear()
    await message.answer(f"Блок «{name}» добавлен для всех сотрудников (со статусом не сдан).")

# Редактировать блок
@router.message(F.text == "✏️ Редактировать блок")
async def edit_block_start(message: Message, state: FSMContext):
    dyn_blocks = await get_all_dynamic_blocks()
    if not dyn_blocks:
        await message.answer("Нет динамических блоков для редактирования.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["name"], callback_data=f"edit_block_{b['id']}")]
        for b in dyn_blocks
    ])
    await message.answer("Выберите блок для редактирования:", reply_markup=kb)
    await state.set_state(TrackerState.waiting_block_edit_id)

@router.callback_query(lambda c: c.data.startswith("edit_block_"))
async def edit_block_select(callback: CallbackQuery, state: FSMContext):
    block_id = int(callback.data.split("_")[2])
    await state.update_data(edit_block_id=block_id)
    await callback.message.answer("Введите новое название блока:")
    await state.set_state(TrackerState.waiting_block_edit_name)
    await callback.answer()

@router.message(TrackerState.waiting_block_edit_name)
async def edit_block_name(message: Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    block_id = data.get("edit_block_id")
    if not block_id:
        await message.answer("Ошибка, попробуйте снова.")
        await state.clear()
        return
    await update_dynamic_block(block_id, new_name, message.from_user.id)
    await state.clear()
    await message.answer(f"Блок переименован в «{new_name}».")

# Удалить блок
@router.message(F.text == "🗑 Удалить блок")
async def delete_block_start(message: Message, state: FSMContext):
    dyn_blocks = await get_all_dynamic_blocks()
    if not dyn_blocks:
        await message.answer("Нет динамических блоков для удаления.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["name"], callback_data=f"del_block_{b['id']}")]
        for b in dyn_blocks
    ])
    await message.answer("Выберите блок для удаления (будут удалены все отметки сотрудников):", reply_markup=kb)
    await state.set_state(TrackerState.waiting_del_block_id)

@router.callback_query(lambda c: c.data.startswith("del_block_"))
async def delete_block_confirm(callback: CallbackQuery, state: FSMContext):
    block_id = int(callback.data.split("_")[2])
    await delete_dynamic_block(block_id, callback.from_user.id)
    await state.clear()
    await callback.message.edit_text("Блок удалён.")
    await callback.answer()

# Отметить блоки сотруднику
@router.message(F.text == "👥 Отметить блоки сотруднику")
async def mark_employee_blocks_start(message: Message, state: FSMContext):
    db = await load_db()
    # Собираем сотрудников с ролями, участвующими в трекере (официант, бартендер, помощник, встречающий менеджер)
    # Упрощённо: берём всех, у кого role не None
    employees = []
    for uid, info in db.items():
        role = info.get("role")
        if role in ["официант", "бартендер", "помощник", "встреч_менеджер"]:
            employees.append((uid, info.get("fullname", "Unknown")))
    if not employees:
        await message.answer("Нет сотрудников для редактирования.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"mark_emp_{uid}")]
        for uid, name in employees
    ])
    await message.answer("Выберите сотрудника:", reply_markup=kb)
    await state.set_state(TrackerState.waiting_select_user_for_edit)

@router.callback_query(lambda c: c.data.startswith("mark_emp_"))
async def select_employee_for_blocks(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[2])
    await state.update_data(edit_user_id=user_id)
    # Получаем все блоки (фиксированные + динамические)
    fixed = await get_all_fixed_blocks()
    dynamic = await get_all_dynamic_blocks()
    all_blocks = fixed + dynamic
    emp_map = await get_employee_blocks_map(user_id)
    # Формируем клавиатуру с чекбоксами
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for b in all_blocks:
        status = "✅" if emp_map.get(b["id"], 0) == 1 else "❌"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"{status} {b['name']}", callback_data=f"toggle_block_{b['id']}_{user_id}")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_employees")])
    await callback.message.edit_text("Отметьте сданные блоки (нажмите для переключения):", reply_markup=kb)
    await state.set_state(TrackerState.waiting_select_block_for_user)
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("toggle_block_"))
async def toggle_block(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    block_id = int(parts[2])
    user_id = int(parts[3])
    emp_map = await get_employee_blocks_map(user_id)
    current = emp_map.get(block_id, 0)
    new_status = 1 if current == 0 else 0
    await set_employee_block(user_id, block_id, new_status, callback.from_user.id)
    # Обновляем клавиатуру
    fixed = await get_all_fixed_blocks()
    dynamic = await get_all_dynamic_blocks()
    all_blocks = fixed + dynamic
    emp_map = await get_employee_blocks_map(user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for b in all_blocks:
        status = "✅" if emp_map.get(b["id"], 0) == 1 else "❌"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"{status} {b['name']}", callback_data=f"toggle_block_{b['id']}_{user_id}")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_employees")])
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer(f"Блок {'сдан' if new_status else 'не сдан'}")

@router.callback_query(lambda c: c.data == "back_to_employees")
async def back_to_employees(callback: CallbackQuery, state: FSMContext):
    db = await load_db()
    employees = []
    for uid, info in db.items():
        role = info.get("role")
        if role in ["официант", "бартендер", "помощник", "встреч_менеджер"]:
            employees.append((uid, info.get("fullname", "Unknown")))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"mark_emp_{uid}")]
        for uid, name in employees
    ])
    await callback.message.edit_text("Выберите сотрудника:", reply_markup=kb)
    await state.set_state(TrackerState.waiting_select_user_for_edit)
    await callback.answer()

# Отчёт по блокам
@router.message(F.text == "📋 Отчёт по блокам")
async def report_blocks(message: Message):
    db = await load_db()
    fixed = await get_all_fixed_blocks()
    dynamic = await get_all_dynamic_blocks()
    all_blocks = fixed + dynamic
    text = "📊 <b>Отчёт по сдаче блоков</b>\n\n"
    for b in all_blocks:
        text += f"<b>{b['name']}</b>:\n"
        count_signed = 0
        for uid, info in db.items():
            role = info.get("role")
            if role in ["официант", "бартендер", "помощник", "встреч_менеджер"]:
                emp_map = await get_employee_blocks_map(int(uid))
                if emp_map.get(b["id"], 0) == 1:
                    count_signed += 1
        text += f"Сдано: {count_signed}\n\n"
    await message.answer(text, parse_mode="HTML")

# Назад
@router.message(F.text == "🔙 Назад")
async def back_to_main(message: Message, role: str = None, roles: list = None):
    from keyboards import main_menu
    await message.answer("Главное меню:", reply_markup=main_menu(role, roles))