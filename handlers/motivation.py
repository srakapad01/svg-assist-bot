import asyncio
import json
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import load_db, get_user
from services.sheets import get_async_sheets
from constants import ALL_ROLES
from utils import has_access, get_fullname_by_username, get_user_by_fullname
from settings import get_active_sheet

router = Router()

MOTIVATION_FILE = "motivation.json"
_motivation_lock = asyncio.Lock()

MOTIVATION_TYPES = {
    "💵": {"name": "Премия", "amount": 300},
    "💩": {"name": "Штраф", "amount": -300},
    "⭐": {"name": "Отзыв", "amount": 0},
    "⏰": {"name": "Опоздание", "amount": -300},
    "📒": {"name": "Жёлтая карта", "amount": -1500},
    "😎": {"name": "Снять штраф", "amount": 300},
}

# ------------------------------------------------------------------
# JSON хранилище (асинхронное)
# ------------------------------------------------------------------
async def load_motivation():
    async with _motivation_lock:
        if not os.path.exists(MOTIVATION_FILE):
            return []
        with open(MOTIVATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

async def save_motivation(data):
    async with _motivation_lock:
        with open(MOTIVATION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

async def add_motivation_record(employee_fullname: str, emoji: str, comment: str, manager_name: str):
    data = await load_motivation()
    mtype = MOTIVATION_TYPES[emoji]
    record = {
        "employee": employee_fullname,
        "emoji": emoji,
        "type": mtype["name"],
        "amount": mtype["amount"],
        "comment": comment,
        "manager": manager_name,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
    }
    data.append(record)
    await save_motivation(data)
    return record

# ------------------------------------------------------------------
# Google Sheets (асинхронно)
# ------------------------------------------------------------------
async def get_motivation_spreadsheet():
    sheets = get_async_sheets()
    spreadsheet_id = os.getenv("MOTIVATION_SPREADSHEET_ID")
    return await sheets.open_by_key(spreadsheet_id)

async def find_date_col_motivation(worksheet, day: int, month: int):
    """Ищет столбец с датой (формат DD.MM) в первой строке"""
    try:
        date_str = f"{day:02d}.{month:02d}"
        header = await worksheet.row_values(1)
        for i, val in enumerate(header):
            if val.strip() == date_str:
                return i + 1
        return None
    except Exception:
        return None

async def find_employee_row_motivation(worksheet, fullname: str):
    try:
        col1 = await worksheet.col_values(1)
        for i, val in enumerate(col1):
            if val.strip().lower() == fullname.strip().lower():
                return i + 1
        return None
    except Exception:
        return None

async def write_emoji_to_sheet(worksheet, row: int, start_col: int, emoji: str):
    """Записывает эмодзи в первую свободную ячейку из 4 столбцов"""
    for col in range(start_col, start_col + 4):
        val = await worksheet.cell(row, col)
        if not val.value:
            await worksheet.update_cell(row, col, emoji)
            return True
    return False

async def write_to_sheets(employee_fullname: str, emoji: str, day: int, month: int, sheet_name: str):
    try:
        ss = await get_motivation_spreadsheet()
        ws = await ss.worksheet(sheet_name)
        row = await find_employee_row_motivation(ws, employee_fullname)
        if not row:
            return False, "Сотрудник не найден в таблице"
        col = await find_date_col_motivation(ws, day, month)
        if not col:
            return False, "Дата не найдена в таблице"
        success = await write_emoji_to_sheet(ws, row, col, emoji)
        if not success:
            return False, "Все 4 ячейки заняты на эту дату"
        return True, "OK"
    except Exception as e:
        return False, str(e)

# ------------------------------------------------------------------
# FSM состояния
# ------------------------------------------------------------------
class MotivationState(StatesGroup):
    waiting_employee = State()
    waiting_emoji = State()
    waiting_day = State()
    waiting_comment = State()

# ------------------------------------------------------------------
# Хендлеры
# ------------------------------------------------------------------
async def motivation_start(message: Message, role: str, state: FSMContext, bot):
    if not has_access(role, ["менеджер", "сказочный_полковник"]):
        await message.answer("Нет доступа.")
        return
    db = await load_db()
    employees = []
    for uid, info in db.items():
        if info.get("role") and info.get("fullname"):
            employees.append(info["fullname"])
    if not employees:
        await message.answer("Нет сотрудников в базе.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp, callback_data=f"mot_emp_{i}")]
        for i, emp in enumerate(employees)
    ])
    await state.update_data(employees=employees)
    await message.answer("Выбери сотрудника:", reply_markup=keyboard)
    await state.set_state(MotivationState.waiting_employee)

@router.callback_query(lambda c: c.data.startswith("mot_emp_"))
async def motivation_get_employee(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[8:])
    data = await state.get_data()
    employees = data.get("employees", [])
    fullname = employees[idx]
    await state.update_data(employee_fullname=fullname)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 Премия +300₽", callback_data="mot_type_💵"),
            InlineKeyboardButton(text="💩 Штраф -300₽", callback_data="mot_type_💩"),
        ],
        [
            InlineKeyboardButton(text="⭐ Отзыв", callback_data="mot_type_⭐"),
            InlineKeyboardButton(text="⏰ Опоздание -300₽", callback_data="mot_type_⏰"),
        ],
        [
            InlineKeyboardButton(text="📒 Жёлтая карта -1500₽", callback_data="mot_type_📒"),
            InlineKeyboardButton(text="😎 Снять штраф", callback_data="mot_type_😎"),
        ],
    ])
    await callback.message.answer(f"Сотрудник: {fullname}\n\nВыбери тип записи:", reply_markup=keyboard)
    await callback.answer()
    await state.set_state(MotivationState.waiting_emoji)

@router.callback_query(lambda c: c.data.startswith("mot_type_"))
async def motivation_get_type(callback: CallbackQuery, state: FSMContext):
    emoji = callback.data[9:]
    await state.update_data(emoji=emoji)
    mtype = MOTIVATION_TYPES.get(emoji, {})
    now = datetime.now()
    days_in_month = __import__("calendar").monthrange(now.year, now.month)[1]
    await callback.message.answer(
        f"Тип: {emoji} {mtype.get('name', '')}\n\n"
        f"Введи день месяца (1-{days_in_month}):"
    )
    await callback.answer()
    await state.set_state(MotivationState.waiting_day)

@router.message(MotivationState.waiting_day)
async def motivation_get_day(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        now = datetime.now()
        import calendar
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        if day < 1 or day > days_in_month:
            await message.answer(f"День должен быть от 1 до {days_in_month}")
            return
        await state.update_data(day=day)
        active_sheet = get_active_sheet("motivation")
        if not active_sheet:
            await message.answer("Активный лист не выбран! Управляющий должен выбрать лист в ⚙️ Настройки")
            await state.clear()
            return
        await state.update_data(sheet_name=active_sheet)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="mot_skip")]
        ])
        await message.answer(
            f"День: {day}\n\nДобавь комментарий или нажми Пропустить:",
            reply_markup=keyboard
        )
        await state.set_state(MotivationState.waiting_comment)
    except ValueError:
        await message.answer("Введи число. Например: 15")

@router.callback_query(lambda c: c.data == "mot_skip")
async def motivation_skip_comment(callback: CallbackQuery, state: FSMContext):
    await state.update_data(comment="")
    await process_motivation(callback.message, state, callback.from_user, callback.bot)
    await callback.answer()

@router.message(MotivationState.waiting_comment)
async def motivation_get_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text.strip())
    await process_motivation(message, state, message.from_user, message.bot)

async def process_motivation(message, state: FSMContext, from_user, bot):
    data = await state.get_data()
    employee_fullname = data["employee_fullname"]
    emoji = data["emoji"]
    day = data["day"]
    sheet_name = data["sheet_name"]
    comment = data.get("comment", "")

    db = await load_db()
    manager_name = db.get(str(from_user.id), {}).get("fullname", "Менеджер")
    now = datetime.now()

    record = await add_motivation_record(employee_fullname, emoji, comment, manager_name)
    success, error = await write_to_sheets(employee_fullname, emoji, day, now.month, sheet_name)

    mtype = MOTIVATION_TYPES[emoji]
    amount_text = ""
    if mtype["amount"] > 0:
        amount_text = f" (+{mtype['amount']}₽)"
    elif mtype["amount"] < 0:
        amount_text = f" ({mtype['amount']}₽)"

    if success:
        comment_line = "Комментарий: " + comment + "\n" if comment else ""
        await message.answer(
            "Запись добавлена!\n\n"
            f"Сотрудник: {employee_fullname}\n"
            f"Тип: {emoji} {mtype['name']}{amount_text}\n"
            f"День: {day}\n"
            + comment_line +
            f"Лист: {sheet_name}"
        )
    else:
        await message.answer(f"Запись сохранена в базе, но ошибка в таблице: {error}")

    # Уведомляем сотрудника
    for uid, info in db.items():
        if info.get("fullname") == employee_fullname:
            notif_text = f"📝 Тебе добавлена запись мотивации:\n{emoji} {mtype['name']}{amount_text}\nЗа {day}.{now.month}"
            if comment:
                notif_text += f"\nКомментарий: {comment}"
            try:
                await bot.send_message(int(uid), notif_text)
            except Exception as e:
                print(f"Ошибка уведомления: {e}")
            break

    await state.clear()

# ------------------------------------------------------------------
# Моя мотивация (просмотр для сотрудника)
# ------------------------------------------------------------------
async def my_motivation_handler(message: Message):
    fullname = await get_fullname_by_username(message.from_user.username or "")
    if not fullname:
        await message.answer("Ты не найден в базе.")
        return

    data = await load_motivation()
    now = datetime.now()
    current_month = now.strftime("%m.%Y")
    records = [r for r in data if r["employee"] == fullname and current_month in r["date"]]

    if not records:
        await message.answer("За этот месяц записей нет.")
        return

    total = sum(r["amount"] for r in records)
    text = f"Твоя мотивация за {now.strftime('%m.%Y')}:\n\n"
    for r in records:
        amount_text = ""
        if r["amount"] > 0:
            amount_text = f" (+{r['amount']}₽)"
        elif r["amount"] < 0:
            amount_text = f" ({r['amount']}₽)"
        text += f"{r['date'][:10]} — {r['emoji']} {r['type']}{amount_text}"
        if r["comment"]:
            text += f" — {r['comment']}"
        text += "\n"
    text += f"\nИтого: {'+' if total >= 0 else ''}{total}₽"
    await message.answer(text)

# ------------------------------------------------------------------
# Синхронизация штата → мотивация (вызывается из admin.py)
# ------------------------------------------------------------------
STAFF_TO_MOTIVATION_MAP = {
    "Бартендеры": "Бартендеры",
    "Встреч. Менеджеры": "Встреч. менеджеры",
    "Официанты": "Официанты",
    "Помощники": "Помощники",
    "Кальянные мастера": "Кальянные мастера",
}

async def sync_staff_to_motivation(motivation_sheet_name: str) -> bool:
    try:
        from handlers.schedule import get_spreadsheet as get_schedule_spreadsheet
        schedule_ss = await get_schedule_spreadsheet()
        staff_ws = await schedule_ss.worksheet("ШТАТ")

        motivation_ss = await get_motivation_spreadsheet()
        motivation_ws = await motivation_ss.worksheet(motivation_sheet_name)

        staff_header = await staff_ws.row_values(1)
        staff_data = {}
        for i, dept_name in enumerate(staff_header):
            if dept_name.strip():
                col_values = await staff_ws.col_values(i + 1)
                staff_data[dept_name.strip()] = [v for v in col_values[1:] if v.strip()]

        col1 = await motivation_ws.col_values(1)
        group_rows = {}
        for i, val in enumerate(col1):
            for dept in STAFF_TO_MOTIVATION_MAP.values():
                if val.strip().lower() == dept.strip().lower():
                    group_rows[dept] = i + 1
                    break

        updates = []
        for staff_dept, motiv_dept in STAFF_TO_MOTIVATION_MAP.items():
            if motiv_dept not in group_rows:
                continue
            group_row = group_rows[motiv_dept]
            employees = staff_data.get(staff_dept, [])
            next_group_row = len(col1) + 1
            for dept, row in group_rows.items():
                if row > group_row and row < next_group_row:
                    next_group_row = row
            for row in range(group_row + 1, next_group_row):
                updates.append({"range": f"A{row}", "values": [[""]]})
            for j, emp in enumerate(employees):
                updates.append({"range": f"A{group_row + 1 + j}", "values": [[emp]]})
        if updates:
            await motivation_ws.batch_update(updates)
        print(f"Штат синхронизирован с мотивацией: {motivation_sheet_name}")
        return True
    except Exception as e:
        print(f"Ошибка синхронизации мотивации: {e}")
        return False