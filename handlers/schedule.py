# handlers/schedule.py
import asyncio
import calendar
import json
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import load_db, get_user
from settings import get_active_sheet, set_active_sheet
from services.sheets import get_async_sheets
from constants import DEPARTMENTS, STAFF_ROLE_COLUMNS
from utils import has_access, get_fullname_by_username, get_user_by_fullname
from keyboards import schedules_list_keyboard, schedule_actions_keyboard, confirm_delete_keyboard

router = Router()

# ------------------------------------------------------------------
# JSON-хранилище для типов графиков (schedules.json)
# ------------------------------------------------------------------
SCHEDULES_FILE = "schedules.json"
_schedules_lock = asyncio.Lock()

async def load_schedules():
    async with _schedules_lock:
        if not os.path.exists(SCHEDULES_FILE):
            return {}
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

async def save_schedules(data):
    async with _schedules_lock:
        with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# ------------------------------------------------------------------
# Работа с Google Spreadsheets (асинхронно)
# ------------------------------------------------------------------
async def get_spreadsheet():
    sheets = get_async_sheets()
    spreadsheet_id = os.getenv("SCHEDULE_SPREADSHEET_ID")
    return await sheets.open_by_key(spreadsheet_id)

async def get_all_sheets():
    ss = await get_spreadsheet()
    worksheets = await ss.worksheets()
    return [ws.title for ws in worksheets]

async def find_employee_row(worksheet, fullname: str):
    """Возвращает номер строки сотрудника в первом столбце"""
    try:
        col1 = await worksheet.col_values(1)
        for i, val in enumerate(col1):
            if val.strip().lower() == fullname.strip().lower():
                return i + 1
        return None
    except Exception:
        return None

async def find_date_col(worksheet, day: int, month: int):
    """Находит столбец с датой в строке 2 (формат DD.MM)"""
    try:
        date_str = f"{day:02d}.{month:02d}"
        header = await worksheet.row_values(2)
        for i, val in enumerate(header):
            if val.strip().startswith(date_str):
                return i + 1
        return None
    except Exception:
        return None

async def find_current_month_sheet():
    """Автоматически ищет лист с текущим месяцем и годом"""
    month_names = {
        1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ",
        5: "МАЙ", 6: "ИЮНЬ", 7: "ИЮЛЬ", 8: "АВГУСТ",
        9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"
    }
    now = datetime.now()
    month_num = f"{now.month:02d}"
    month_name = month_names[now.month]
    year = str(now.year)
    sheets = await get_all_sheets()
    for sheet in sheets:
        sheet_upper = sheet.upper()
        if month_num in sheet_upper and month_name in sheet_upper and year in sheet_upper:
            return sheet
    return None

async def fill_schedule_template(worksheet, row: int, work: int, rest: int,
                                 start_day: int, time_value: str, month: int, year: int):
    """Заполняет смены по циклу рабочий/выходной"""
    days_in_month = calendar.monthrange(year, month)[1]
    header = await worksheet.row_values(2)
    updates = []
    day = start_day
    working = True
    count = 0
    while day <= days_in_month:
        date_str = f"{day:02d}.{month:02d}"
        col = None
        for i, val in enumerate(header):
            if val.strip().startswith(date_str):
                col = i + 1
                break
        if col:
            updates.append({
                "range": f"{chr(64+col)}{row}",
                "values": [[time_value if working else ""]]
            })
        count += 1
        if working and count >= work:
            working = False
            count = 0
        elif not working and count >= rest:
            working = True
            count = 0
        day += 1
    if updates:
        await worksheet.batch_update(updates)

# ------------------------------------------------------------------
# Работа с листом ШТАТ
# ------------------------------------------------------------------
async def add_to_staff_sheet(fullname: str, role: str):
    """Добавляет сотрудника в лист ШТАТ, если его там ещё нет"""
    try:
        ss = await get_spreadsheet()
        worksheet = await ss.worksheet("ШТАТ")
        header_row = await worksheet.row_values(1)
        column_name = STAFF_ROLE_COLUMNS.get(role)
        if not column_name:
            return
        col_index = None
        for i, val in enumerate(header_row):
            if val.strip().lower() == column_name.strip().lower():
                col_index = i + 1
                break
        if not col_index:
            print(f"Столбец для {column_name} не найден в ШТАТ")
            return
        col_values = await worksheet.col_values(col_index)
        for val in col_values:
            if val.strip().lower() == fullname.strip().lower():
                return  # уже есть
        next_row = len(col_values) + 1
        await worksheet.update_cell(next_row, col_index, fullname)
    except Exception as e:
        print(f"Ошибка добавления в ШТАТ: {e}")

async def is_in_staff(fullname: str) -> bool:
    try:
        ss = await get_spreadsheet()
        worksheet = await ss.worksheet("ШТАТ")
        all_values = await worksheet.get_all_values()
        for row in all_values:
            for cell in row:
                if cell.strip().lower() == fullname.strip().lower():
                    return True
        return False
    except Exception:
        return True  # безопасное значение

# ------------------------------------------------------------------
# FSM состояния
# ------------------------------------------------------------------
class ShiftState(StatesGroup):
    waiting_username = State()
    waiting_schedule = State()
    waiting_start_day = State()
    waiting_time = State()

class SingleShiftState(StatesGroup):
    waiting_username = State()
    waiting_day = State()
    waiting_time = State()

class DelShiftStartState(StatesGroup):
    waiting_username = State()
    waiting_day = State()

class AddScheduleState(StatesGroup):
    waiting_name = State()
    waiting_work_days = State()
    waiting_rest_days = State()

class EditScheduleState(StatesGroup):
    waiting_new_name = State()
    waiting_new_work = State()
    waiting_new_rest = State()

# ------------------------------------------------------------------
# Вспомогательные функции для удаления сообщений
# ------------------------------------------------------------------
async def delete_bot_msg(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        bot_msg_id = data.get("bot_msg_id")
        if bot_msg_id:
            await message.bot.delete_message(message.chat.id, bot_msg_id)
    except Exception:
        pass

async def delete_user_msg(message: Message):
    try:
        await message.delete()
    except Exception:
        pass

# ------------------------------------------------------------------
# Хендлеры
# ------------------------------------------------------------------

# ---------- Команда /myshift (для сотрудников) ----------
@router.message(Command("myshift"))
async def my_shift(message: Message, role: str = None):
    if role is None:
        await message.answer("Нет доступа. Обратитесь к управляющему.")
        return
    fullname = await get_fullname_by_username(message.from_user.username or "")
    if not fullname:
        await message.answer("Ты не найден в базе. Напиши /start")
        return
    active_sheet = get_active_sheet("schedule")
    if not active_sheet:
        active_sheet = await find_current_month_sheet()
    if not active_sheet:
        await message.answer("Лист для текущего месяца не найден.")
        return
    try:
        ss = await get_spreadsheet()
        ws = await ss.worksheet(active_sheet)
        row = await find_employee_row(ws, fullname)
        if not row:
            await message.answer("Ты не найден в таблице графика.")
            return
        header = await ws.row_values(2)
        row_values = await ws.row_values(row)
        text = "Твои смены:\n\n"
        has_shifts = False
        for i, val in enumerate(row_values[1:], 1):
            if val and i < len(header):
                text += f"{header[i]} — {val}\n"
                has_shifts = True
        if not has_shifts:
            await message.answer("У тебя пока нет смен.")
        else:
            await message.answer(text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# ---------- Команда /schedules (список типов графиков) ----------
@router.message(Command("schedules"))
async def list_schedules(message: Message, role: str = None, roles: list = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    data = await load_schedules()
    if not data:
        await message.answer("Нет графиков. Добавьте через /addschedule")
        return
    text = "Типы графиков:\n\n"
    for name, info in data.items():
        text += f"{name} — {info['work']} раб. / {info['rest']} вых.\n"
    await message.answer(text)

# ---------- Добавление нового типа графика (команда /addschedule) ----------
@router.message(Command("addschedule"))
async def add_schedule_start(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    sent = await message.answer("Введи название графика (например: 3/3 или 2/2 или 5/2):")
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(AddScheduleState.waiting_name)

@router.message(AddScheduleState.waiting_name)
async def add_schedule_name(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_bot_msg(message, state)
    await state.update_data(name=message.text.strip())
    sent = await message.answer("Сколько рабочих дней?")
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(AddScheduleState.waiting_work_days)

@router.message(AddScheduleState.waiting_work_days)
async def add_schedule_work(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        work = int(message.text.strip())
        if work < 1:
            raise ValueError
        await delete_bot_msg(message, state)
        await state.update_data(work=work)
        sent = await message.answer("Сколько выходных дней?")
        await state.update_data(bot_msg_id=sent.message_id)
        await state.set_state(AddScheduleState.waiting_rest_days)
    except ValueError:
        await message.answer("Введи целое число больше 0.")

@router.message(AddScheduleState.waiting_rest_days)
async def add_schedule_rest(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        rest = int(message.text.strip())
        if rest < 1:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число больше 0.")
        return
    await delete_bot_msg(message, state)
    data = await state.get_data()
    schedules = await load_schedules()
    schedules[data["name"]] = {"work": data["work"], "rest": rest}
    await save_schedules(schedules)
    await state.clear()
    await message.answer(
        f"✅ График {data['name']} добавлен — {data['work']} раб. / {rest} вых.",
        reply_markup=schedules_list_keyboard(schedules),
    )

# ---------- Поставить смены на месяц по типу графика (команда /newshift) ----------
@router.message(Command("newshift"))
async def new_shift_start(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    active_sheet = get_active_sheet("schedule")
    if not active_sheet:
        await message.answer("Активный лист не выбран! Выбери в ⚙️ Настройки")
        return
    await state.update_data(sheet_name=active_sheet)

    # Клавиатура выбора подразделения
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=dept, callback_data=f"ns_dept_{i}")]
        for i, dept in enumerate(DEPARTMENTS.keys())
    ])
    await state.update_data(departments=list(DEPARTMENTS.keys()))
    sent = await message.answer("Выбери подразделение:", reply_markup=keyboard)
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(ShiftState.waiting_username)

@router.callback_query(lambda c: c.data.startswith("ns_dept_"))
async def shift_get_department(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[8:])
    data = await state.get_data()
    departments = data.get("departments", [])
    dept_name = departments[idx]
    dept_roles = DEPARTMENTS[dept_name]
    db = await load_db()
    employees = []
    for uid, info in db.items():
        if info.get("role") in dept_roles and info.get("fullname"):
            employees.append(info["fullname"])
    if not employees:
        await callback.message.edit_text("В этом подразделении нет сотрудников.")
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp, callback_data=f"ns_emp_{i}")]
        for i, emp in enumerate(employees)
    ])
    await state.update_data(employees=employees)
    await callback.message.edit_text(
        f"Подразделение: {dept_name}\n\nВыбери сотрудника:",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("ns_emp_"))
async def shift_get_username(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[7:])
    data = await state.get_data()
    employees = data.get("employees", [])
    fullname = employees[idx]
    db = await load_db()
    found = None
    for uid, info in db.items():
        if info.get("fullname") == fullname:
            found = dict(info)
            found["uid"] = uid
            break
    if not found:
        await callback.message.edit_text("Сотрудник не найден.")
        await state.clear()
        await callback.answer()
        return
    if not await is_in_staff(found["fullname"]):
        await callback.message.edit_text(f"Сотрудник {found['fullname']} не найден в листе ШТАТ!")
        await state.clear()
        await callback.answer()
        return
    await state.update_data(employee=found)
    schedules = await load_schedules()
    if not schedules:
        await callback.message.edit_text("Нет графиков! Добавь через /addschedule")
        await state.clear()
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{name} ({info['work']}/{info['rest']})", callback_data=f"sch_{i}")]
        for i, (name, info) in enumerate(schedules.items())
    ])
    await state.update_data(schedules=schedules)
    await callback.message.edit_text(
        f"Сотрудник: {found['fullname']}\n\nВыбери тип графика:",
        reply_markup=keyboard
    )
    await callback.answer()
    await state.set_state(ShiftState.waiting_schedule)

@router.callback_query(lambda c: c.data.startswith("sch_"))
async def shift_get_schedule(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[4:])
    data = await state.get_data()
    schedules = data.get("schedules", {})
    schedule_name = list(schedules.keys())[idx]
    schedule = schedules[schedule_name]
    await state.update_data(schedule_name=schedule_name, schedule=schedule)
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    sent = await callback.message.edit_text(
        f"График: {schedule_name}\n\nС какого дня начать? (1-{days_in_month})"
    )
    await state.update_data(bot_msg_id=sent.message_id)
    await callback.answer()
    await state.set_state(ShiftState.waiting_start_day)

@router.message(ShiftState.waiting_start_day)
async def shift_get_start_day(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        day = int(message.text.strip())
        now = datetime.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        if day < 1 or day > days_in_month:
            await message.answer(f"День должен быть от 1 до {days_in_month}")
            return
        await delete_bot_msg(message, state)
        await state.update_data(start_day=day)
        sent = await message.answer("Введи время смены (например: 11-00):")
        await state.update_data(bot_msg_id=sent.message_id)
        await state.set_state(ShiftState.waiting_time)
    except ValueError:
        await message.answer("Введи число. Например: 1")

@router.message(ShiftState.waiting_time)
async def shift_get_time(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_bot_msg(message, state)
    time_value = message.text.strip()
    data = await state.get_data()
    employee = data["employee"]
    schedule = data["schedule"]
    start_day = data["start_day"]
    schedule_name = data["schedule_name"]
    sheet_name = data["sheet_name"]
    now = datetime.now()
    try:
        ss = await get_spreadsheet()
        ws = await ss.worksheet(sheet_name)
        row = await find_employee_row(ws, employee["fullname"])
        if not row:
            await message.answer(f"Сотрудник {employee['fullname']} не найден в таблице!")
            await state.clear()
            return
        await fill_schedule_template(ws, row, schedule["work"], schedule["rest"],
                                     start_day, time_value, now.month, now.year)
        await state.clear()
        await message.answer(
            f"✅ График проставлен!\n\n"
            f"Сотрудник: {employee['fullname']}\n"
            f"График: {schedule_name}\n"
            f"С {start_day} числа\n"
            f"Время: {time_value}\n"
            f"Лист: {sheet_name}"
        )
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        await state.clear()

# ---------- Добавление одной смены (команда /addshift) ----------
@router.message(Command("addshift"))
async def add_single_shift_start(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    active_sheet = get_active_sheet("schedule")
    if not active_sheet:
        await message.answer("Активный лист не выбран! Выбери в ⚙️ Настройки")
        return
    await state.update_data(sheet_name=active_sheet)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=dept, callback_data=f"as_dept_{i}")]
        for i, dept in enumerate(DEPARTMENTS.keys())
    ])
    await state.update_data(departments=list(DEPARTMENTS.keys()))
    sent = await message.answer("Выбери подразделение:", reply_markup=keyboard)
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(SingleShiftState.waiting_username)

@router.callback_query(lambda c: c.data.startswith("as_dept_"))
async def single_shift_get_department(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[8:])
    data = await state.get_data()
    departments = data.get("departments", [])
    dept_name = departments[idx]
    dept_roles = DEPARTMENTS[dept_name]
    db = await load_db()
    employees = []
    for uid, info in db.items():
        if info.get("role") in dept_roles and info.get("fullname"):
            employees.append(info["fullname"])
    if not employees:
        await callback.message.edit_text("В этом подразделении нет сотрудников.")
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp, callback_data=f"as_emp_{i}")]
        for i, emp in enumerate(employees)
    ])
    await state.update_data(employees=employees)
    await callback.message.edit_text(
        f"Подразделение: {dept_name}\n\nВыбери сотрудника:",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("as_emp_"))
async def single_shift_get_username(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[7:])
    data = await state.get_data()
    employees = data.get("employees", [])
    fullname = employees[idx]
    db = await load_db()
    found = None
    for uid, info in db.items():
        if info.get("fullname") == fullname:
            found = dict(info)
            found["uid"] = uid
            break
    if not found:
        await callback.message.edit_text("Сотрудник не найден.")
        await state.clear()
        await callback.answer()
        return
    if not await is_in_staff(found["fullname"]):
        await callback.message.edit_text(f"Сотрудник {found['fullname']} не найден в листе ШТАТ!")
        await state.clear()
        await callback.answer()
        return
    await state.update_data(employee=found)
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    sent = await callback.message.edit_text(
        f"Сотрудник: {found['fullname']}\n\nВведи день месяца (1-{days_in_month}):"
    )
    await state.update_data(bot_msg_id=sent.message_id)
    await callback.answer()
    await state.set_state(SingleShiftState.waiting_day)

@router.message(SingleShiftState.waiting_day)
async def single_shift_get_day(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        day = int(message.text.strip())
        now = datetime.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        if day < 1 or day > days_in_month:
            await message.answer(f"День должен быть от 1 до {days_in_month}")
            return
        await delete_bot_msg(message, state)
        await state.update_data(day=day)
        sent = await message.answer("Введи время смены (например: 11-00):")
        await state.update_data(bot_msg_id=sent.message_id)
        await state.set_state(SingleShiftState.waiting_time)
    except ValueError:
        await message.answer("Введи число. Например: 15")

@router.message(SingleShiftState.waiting_time)
async def single_shift_get_time(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_bot_msg(message, state)
    time_value = message.text.strip()
    data = await state.get_data()
    employee = data["employee"]
    day = data["day"]
    sheet_name = data["sheet_name"]
    now = datetime.now()
    try:
        ss = await get_spreadsheet()
        ws = await ss.worksheet(sheet_name)
        row = await find_employee_row(ws, employee["fullname"])
        if not row:
            await message.answer(f"Сотрудник {employee['fullname']} не найден в таблице!")
            await state.clear()
            return
        col = await find_date_col(ws, day, now.month)
        if not col:
            await message.answer(f"Дата {day} не найдена в таблице!")
            await state.clear()
            return
        await ws.update_cell(row, col, time_value)
        await state.clear()
        await message.answer(
            f"✅ Смена добавлена!\n\n"
            f"Сотрудник: {employee['fullname']}\n"
            f"День: {day}\n"
            f"Время: {time_value}\n"
            f"Лист: {sheet_name}"
        )
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        await state.clear()

# ---------- Удаление смены (команда /delshift) ----------
@router.message(Command("delshift"))
async def del_shift_cmd(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    await del_shift_start(message, state)

async def del_shift_start(message: Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=dept, callback_data=f"ds_dept_{i}")]
        for i, dept in enumerate(DEPARTMENTS.keys())
    ])
    await state.update_data(departments=list(DEPARTMENTS.keys()))
    sent = await message.answer("Выбери подразделение:", reply_markup=keyboard)
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(DelShiftStartState.waiting_username)

@router.callback_query(lambda c: c.data.startswith("ds_dept_"))
async def del_shift_get_department(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[8:])
    data = await state.get_data()
    departments = data.get("departments", [])
    dept_name = departments[idx]
    dept_roles = DEPARTMENTS[dept_name]
    db = await load_db()
    employees = []
    for uid, info in db.items():
        if info.get("role") in dept_roles and info.get("fullname"):
            employees.append(info["fullname"])
    if not employees:
        await callback.message.edit_text("В этом подразделении нет сотрудников.")
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp, callback_data=f"ds_emp_{i}")]
        for i, emp in enumerate(employees)
    ])
    await state.update_data(employees=employees)
    await callback.message.edit_text(
        f"Подразделение: {dept_name}\n\nВыбери сотрудника:",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("ds_emp_"))
async def del_shift_get_username(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[7:])
    data = await state.get_data()
    employees = data.get("employees", [])
    fullname = employees[idx]
    await state.update_data(del_fullname=fullname)
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    sent = await callback.message.edit_text(
        f"Сотрудник: {fullname}\n\nВведи день месяца (1-{days_in_month}):"
    )
    await state.update_data(bot_msg_id=sent.message_id)
    await callback.answer()
    await state.set_state(DelShiftStartState.waiting_day)

@router.message(DelShiftStartState.waiting_day)
async def del_shift_get_day(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        day = int(message.text.strip())
        now = datetime.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        if day < 1 or day > days_in_month:
            await message.answer(f"День должен быть от 1 до {days_in_month}")
            return
        await delete_bot_msg(message, state)
        data = await state.get_data()
        fullname = data["del_fullname"]
        active_sheet = get_active_sheet("schedule")
        if not active_sheet:
            await message.answer("Активный лист не выбран! Выбери в ⚙️ Настройки")
            await state.clear()
            return
        ss = await get_spreadsheet()
        ws = await ss.worksheet(active_sheet)
        row = await find_employee_row(ws, fullname)
        if not row:
            await message.answer("Сотрудник не найден в таблице.")
            await state.clear()
            return
        col = await find_date_col(ws, day, now.month)
        if not col:
            await message.answer("Дата не найдена в таблице.")
            await state.clear()
            return
        await ws.update_cell(row, col, "")
        await message.answer(f"✅ Смена удалена: {fullname}, день {day}")
        await state.clear()
    except ValueError:
        await message.answer("Введи число. Например: 15")

# ---------- Управление типами графиков (inline CRUD) ----------
async def _show_schedules_list(target, state: FSMContext):
    await state.clear()
    data = await load_schedules()
    text = "Выбери график для управления:" if data else "Нет графиков."
    kb = schedules_list_keyboard(data)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)

@router.message(F.text == "📋 Управление графиками")
async def manage_schedules_entry(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles):
        await message.answer("Нет прав.")
        return
    await _show_schedules_list(message, state)

@router.callback_query(lambda c: c.data == "mgsch_back")
async def mgsch_back(callback: CallbackQuery, state: FSMContext):
    await _show_schedules_list(callback, state)

@router.callback_query(lambda c: c.data.startswith("mgsch_view_"))
async def mgsch_view(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    idx = int(callback.data[10:])
    data = await load_schedules()
    names = list(data.keys())
    if idx >= len(names):
        await callback.answer("График не найден.", show_alert=True)
        return
    name = names[idx]
    info = data[name]
    text = f"График: {name}\nРабочих дней: {info['work']}\nВыходных дней: {info['rest']}"
    await callback.message.edit_text(text, reply_markup=schedule_actions_keyboard(idx))
    await callback.answer()

@router.callback_query(lambda c: c.data == "mgsch_add")
async def mgsch_add_start(callback: CallbackQuery, state: FSMContext):
    sent = await callback.message.answer("Введи название нового графика (например: 3/3):")
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(AddScheduleState.waiting_name)
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("mgsch_rename_"))
async def mgsch_rename_start(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[13:])
    data = await load_schedules()
    names = list(data.keys())
    if idx >= len(names):
        await callback.answer("График не найден.", show_alert=True)
        return
    await state.update_data(edit_idx=idx)
    sent = await callback.message.answer(f"Текущее название: {names[idx]}\n\nВведи новое название:")
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(EditScheduleState.waiting_new_name)
    await callback.answer()

@router.message(EditScheduleState.waiting_new_name)
async def mgsch_rename_finish(message: Message, state: FSMContext):
    await delete_user_msg(message)
    new_name = message.text.strip()
    if not new_name:
        await message.answer("Название не может быть пустым.")
        return
    data = await state.get_data()
    idx = data["edit_idx"]
    schedules = await load_schedules()
    names = list(schedules.keys())
    if idx >= len(names):
        await message.answer("График не найден.")
        await state.clear()
        return
    old_name = names[idx]
    if new_name in schedules and new_name != old_name:
        await message.answer("График с таким названием уже есть. Введи другое.")
        return
    await delete_bot_msg(message, state)
    new_schedules = {}
    for k, v in schedules.items():
        new_schedules[new_name if k == old_name else k] = v
    await save_schedules(new_schedules)
    await state.clear()
    new_idx = list(new_schedules.keys()).index(new_name)
    await message.answer(
        f"✅ Переименовано: {old_name} → {new_name}",
        reply_markup=schedule_actions_keyboard(new_idx),
    )

@router.callback_query(lambda c: c.data.startswith("mgsch_edit_"))
async def mgsch_edit_start(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[11:])
    data = await load_schedules()
    names = list(data.keys())
    if idx >= len(names):
        await callback.answer("График не найден.", show_alert=True)
        return
    name = names[idx]
    info = data[name]
    await state.update_data(edit_idx=idx)
    sent = await callback.message.answer(
        f"График: {name}\nСейчас: {info['work']} раб. / {info['rest']} вых.\n\n"
        f"Введи новое количество рабочих дней:"
    )
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(EditScheduleState.waiting_new_work)
    await callback.answer()

@router.message(EditScheduleState.waiting_new_work)
async def mgsch_edit_work(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        work = int(message.text.strip())
        if work < 1:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число больше 0.")
        return
    await delete_bot_msg(message, state)
    await state.update_data(new_work=work)
    sent = await message.answer("Теперь введи количество выходных дней:")
    await state.update_data(bot_msg_id=sent.message_id)
    await state.set_state(EditScheduleState.waiting_new_rest)

@router.message(EditScheduleState.waiting_new_rest)
async def mgsch_edit_rest(message: Message, state: FSMContext):
    await delete_user_msg(message)
    try:
        rest = int(message.text.strip())
        if rest < 1:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число больше 0.")
        return
    await delete_bot_msg(message, state)
    data = await state.get_data()
    idx = data["edit_idx"]
    new_work = data["new_work"]
    schedules = await load_schedules()
    names = list(schedules.keys())
    if idx >= len(names):
        await message.answer("График не найден.")
        await state.clear()
        return
    name = names[idx]
    schedules[name] = {"work": new_work, "rest": rest}
    await save_schedules(schedules)
    await state.clear()
    await message.answer(
        f"✅ График {name} обновлён: {new_work} раб. / {rest} вых.",
        reply_markup=schedule_actions_keyboard(idx),
    )

@router.callback_query(lambda c: c.data.startswith("mgsch_delete_"))
async def mgsch_delete_ask(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[13:])
    data = await load_schedules()
    names = list(data.keys())
    if idx >= len(names):
        await callback.answer("График не найден.", show_alert=True)
        return
    name = names[idx]
    await callback.message.edit_text(
        f"Удалить график «{name}»?\n\nУже выставленные смены в таблице останутся.",
        reply_markup=confirm_delete_keyboard(idx),
    )
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("mgsch_confirm_del_"))
async def mgsch_delete_confirm(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[18:])
    schedules = await load_schedules()
    names = list(schedules.keys())
    if idx >= len(names):
        await callback.answer("График не найден.", show_alert=True)
        return
    name = names[idx]
    del schedules[name]
    await save_schedules(schedules)
    await state.clear()
    text = "Выбери график для управления:" if schedules else "Нет графиков."
    await callback.message.edit_text(text, reply_markup=schedules_list_keyboard(schedules))
    await callback.answer(f"График «{name}» удалён.")

# ---------- Синхронизация ШТАТ → график (вызывается из admin.py) ----------
STAFF_TO_SCHEDULE_MAP = {
    "Менеджеры": "Менеджеры",
    "Бартендеры": "Бартендеры",
    "Встреч. Менеджеры": "Встреч. менеджеры",
    "Официанты": "Официанты",
    "Помощники": "Помощники",
    "Кальянные мастера": "Кальянные мастера",
}

async def sync_staff_to_schedule(schedule_sheet_name: str) -> bool:
    try:
        ss = await get_spreadsheet()
        staff_ws = await ss.worksheet("ШТАТ")
        schedule_ws = await ss.worksheet(schedule_sheet_name)

        staff_header = await staff_ws.row_values(1)
        staff_data = {}
        for i, dept_name in enumerate(staff_header):
            if dept_name.strip():
                col_values = await staff_ws.col_values(i + 1)
                staff_data[dept_name.strip()] = [v for v in col_values[1:] if v.strip()]

        schedule_values = await schedule_ws.col_values(1)
        group_rows = {}
        for i, val in enumerate(schedule_values):
            for dept in STAFF_TO_SCHEDULE_MAP.values():
                if val.strip().lower() == dept.strip().lower():
                    group_rows[dept] = i + 1
                    break

        updates = []
        for staff_dept, schedule_dept in STAFF_TO_SCHEDULE_MAP.items():
            if schedule_dept not in group_rows:
                continue
            group_row = group_rows[schedule_dept]
            employees = staff_data.get(staff_dept, [])
            next_group_row = len(schedule_values) + 1
            for dept, row in group_rows.items():
                if row > group_row and row < next_group_row:
                    next_group_row = row
            for row in range(group_row + 1, next_group_row):
                updates.append({"range": f"A{row}", "values": [[""]]})
            for j, emp in enumerate(employees):
                updates.append({"range": f"A{group_row + 1 + j}", "values": [[emp]]})
        if updates:
            await schedule_ws.batch_update(updates)
        print(f"Штат синхронизирован с графиком: {schedule_sheet_name}")
        return True
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")
        return False