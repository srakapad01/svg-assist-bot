import asyncio
import json
import os
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import load_db
from services.sheets import get_async_sheets
from utils import has_access, get_fullname_by_username
from settings import get_active_sheet
from database import get_employee_coefficient, get_user, load_db

router = Router()

TIPS_FILE = "tips.json"
_tips_lock = asyncio.Lock()

# ------------------------------------------------------------------
# Роли и коэффициенты (соответствуют constants.py)
# ------------------------------------------------------------------
FIXED_5_EACH = []  # никто не получает 5% индивидуально
FIXED_5_DEPT = ["менеджер", "кальянный_мастер", "встреч_менеджер"]

SHARE_ROLES = {
    "официант": 1.0,
    "бартендер": 1.0,
    "помощник": 0.5,
}

TIPS_ROLES = list(FIXED_5_EACH) + list(FIXED_5_DEPT) + list(SHARE_ROLES.keys())

# ------------------------------------------------------------------
# JSON хранилище
# ------------------------------------------------------------------
async def load_tips() -> dict:
    async with _tips_lock:
        if not os.path.exists(TIPS_FILE):
            return {}
        with open(TIPS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)

async def save_tips(data: dict):
    async with _tips_lock:
        with open(TIPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

async def save_tip_entry(date_str: str, total: int, breakdown: dict):
    tips = await load_tips()
    tips[date_str] = {
        "total": total,
        "breakdown": breakdown,
        "recorded_at": datetime.now().isoformat(),
    }
    await save_tips(tips)

# ------------------------------------------------------------------
# Google Sheets (асинхронно)
# ------------------------------------------------------------------
async def get_tips_spreadsheet():
    sheets = get_async_sheets()
    spreadsheet_id = os.getenv("TIPS_SPREADSHEET_ID")
    return await sheets.open_by_key(spreadsheet_id)

async def get_or_create_month_sheet(month: int, year: int):
    MONTH_NAMES = {
        1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ",
        5: "МАЙ", 6: "ИЮНЬ", 7: "ИЮЛЬ", 8: "АВГУСТ",
        9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ",
    }
    sheet_name = f"{month:02d} {MONTH_NAMES[month]} {year}"
    ss = await get_tips_spreadsheet()
    try:
        return await ss.worksheet(sheet_name)
    except Exception:
        return await ss.add_worksheet(title=sheet_name, rows=100, cols=50)

async def find_or_create_date_col(ws, date_str: str) -> int:
    row1 = await ws.row_values(1)
    for i, val in enumerate(row1):
        if val.strip() == date_str:
            return i + 1
    col = len(row1) + 1
    await ws.update_cell(1, col, date_str)
    return col

async def find_or_create_employee_row(ws, fullname: str, role: str) -> int:
    ROLE_HEADERS = {
        "менеджер": "Менеджеры",
        "бартендер": "Бар",
        "встреч_менеджер": "Хостес",
        "официант": "Официанты",
        "помощник": "Помощники",
        "кальянный_мастер": "Кальянные мастера",
    }
    col1 = await ws.col_values(1)
    # Ищем имя
    for i, val in enumerate(col1):
        if val.strip().lower() == fullname.strip().lower():
            return i + 1
    # Не найдено — ищем заголовок группы
    header = ROLE_HEADERS.get(role, role)
    for i, val in enumerate(col1):
        if val.strip().lower() == header.strip().lower():
            insert_row = i + 2
            while insert_row <= len(col1) and col1[insert_row - 1].strip() and col1[insert_row - 1].strip() != header:
                insert_row += 1
            await ws.insert_row([fullname], insert_row)
            return insert_row
    # Заголовка нет — создаём в конце
    next_row = len(col1) + 1
    await ws.update_cell(next_row, 1, header)
    await ws.update_cell(next_row + 1, 1, fullname)
    return next_row + 1

async def write_tips_to_sheet(date_str: str, total: int, breakdown: dict, role_map: dict):
    try:
        parts = date_str.split(".")
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        full_year = 2000 + year if year < 100 else year
        ws = await get_or_create_month_sheet(month, full_year)

        col = await find_or_create_date_col(ws, date_str)

        # Строка 2 — "Всего за смену"
        cell_a2 = await ws.cell(2, 1)
        if not cell_a2.value or cell_a2.value.strip() != "Всего за смену":
            await ws.update_cell(2, 1, "Всего за смену")
        await ws.update_cell(2, col, total)

        updates = []
        for fullname, amount in breakdown.items():
            role = role_map.get(fullname, "официант")
            row = await find_or_create_employee_row(ws, fullname, role)
            updates.append({
                "range": f"{chr(64+col)}{row}",
                "values": [[amount]],
            })
        if updates:
            await ws.batch_update(updates)
        return True
    except Exception as e:
        print(f"Ошибка записи чаевых в Sheets: {e}")
        return False

# ------------------------------------------------------------------
# Калькулятор чаевых
# ------------------------------------------------------------------
async def calculate_tips(total: int, staff: list[dict]) -> dict:
    """
    staff – список {'fullname': str, 'user_id': int, 'role': str}
    Возвращает {fullname: amount}
    """
    # Группируем
    fixed_dept_roles = ["менеджер", "кальянный_мастер", "встреч_менеджер"]
    tracker_roles = ["официант", "бартендер", "помощник"]
    fixed_staff = [s for s in staff if s["role"] in fixed_dept_roles]
    tracker_staff = [s for s in staff if s["role"] in tracker_roles]
    # 5% от общих чаевых на фиксированные подразделения (поровну)
    fixed_total = round(total * 0.05)
    fixed_each = round(fixed_total / len(fixed_staff)) if fixed_staff else 0
    result = {}
    for s in fixed_staff:
        result[s["fullname"]] = fixed_each
    # Остаток на трекерные роли
    remainder = total - fixed_total
    if remainder > 0 and tracker_staff:
        # Получаем коэффициенты для каждого
        coeffs = []
        for s in tracker_staff:
            coeff = await get_employee_coefficient(s["user_id"])
            coeffs.append(coeff)
        total_coeff = sum(coeffs)
        if total_coeff > 0:
            for s, coeff in zip(tracker_staff, coeffs):
                amount = round(remainder * coeff / total_coeff)
                result[s["fullname"]] = amount
    return result

# ------------------------------------------------------------------
# Получить сотрудников в смене из графика (асинхронно)
# ------------------------------------------------------------------
async def get_staff_on_date(date_str: str) -> list[dict]:
    """
    Получает список сотрудников, у которых есть смена на указанную дату.
    date_str — '02.05' (день.месяц)
    Возвращает список словарей: [{'fullname': str, 'user_id': int, 'role': str}, ...]
    """
    from handlers.schedule import get_spreadsheet
    from database import load_db

    active_sheet = get_active_sheet("schedule")
    if not active_sheet:
        return []

    try:
        ss = await get_spreadsheet()
        ws = await ss.worksheet(active_sheet)

        # Ищем столбец с нужной датой (в первой строке)
        header = await ws.row_values(1)
        col = None
        for i, val in enumerate(header):
            if val.strip().startswith(date_str):
                col = i + 1
                break
        if not col:
            return []

        # Получаем значения в этом столбце и первом столбце (имена)
        col_values = await ws.col_values(col)
        col1 = await ws.col_values(1)

        # Загружаем базу пользователей
        db = await load_db()

        # Строим карту: имя -> роль (из БД)
        name_to_role = {}
        name_to_userid = {}
        for uid, info in db.items():
            fullname = info.get("fullname")
            if not fullname:
                continue
            roles = info.get("roles", [info.get("role")])
            if isinstance(roles, str):
                roles = [roles]
            # Берём первую подходящую роль (только из списка TIPS_ROLES)
            from handlers.tips import TIPS_ROLES
            for r in roles:
                if r in TIPS_ROLES:
                    name_to_role[fullname.strip().lower()] = r
                    name_to_userid[fullname.strip().lower()] = int(uid)
                    break

        staff = []
        for i, val in enumerate(col_values):
            if val.strip() and i < len(col1):
                name = col1[i].strip()
                role = name_to_role.get(name.lower())
                user_id = name_to_userid.get(name.lower())
                if role:
                    staff.append({
                        "fullname": name,
                        "user_id": user_id,
                        "role": role
                    })
        return staff

    except Exception as e:
        print(f"Ошибка получения смены: {e}")
        return []

# ------------------------------------------------------------------
# FSM состояния
# ------------------------------------------------------------------
class TipsState(StatesGroup):
    choosing_date = State()
    entering_date = State()
    confirming_date = State()
    entering_total = State()
    confirming_staff = State()
    confirming_calc = State()

# ------------------------------------------------------------------
# Вспомогательные клавиатуры
# ------------------------------------------------------------------
def date_choice_keyboard() -> InlineKeyboardMarkup:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%y")
    before_yes = (datetime.now() - timedelta(days=2)).strftime("%d.%m.%y")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 Вчера ({yesterday})", callback_data=f"tipsdt_{yesterday}")],
        [InlineKeyboardButton(text=f"📅 Позавчера ({before_yes})", callback_data=f"tipsdt_{before_yes}")],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="tipsdt_manual")],
    ])

def staff_confirm_keyboard(staff: list[dict], removed: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for s in staff:
        if s["fullname"] in removed:
            continue
        rows.append([InlineKeyboardButton(
            text=f"❌ Убрать {s['fullname']}",
            callback_data=f"tips_remove_{s['fullname']}"
        )])
    rows.append([InlineKeyboardButton(text="✅ Список верный, считать", callback_data="tips_calc")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def calc_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и сохранить", callback_data="tips_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tips_cancel")],
    ])

# ------------------------------------------------------------------
# Хендлеры ввода чаевых
# ------------------------------------------------------------------
async def tips_entry(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    user_roles = roles or []
    if not has_access(role, ["старший_смены", "сказочный_полковник", "owner"], user_roles):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await message.answer(
        "За какую смену вводим чаевые?",
        reply_markup=date_choice_keyboard()
    )
    await state.set_state(TipsState.choosing_date)

@router.callback_query(lambda c: c.data.startswith("tipsdt_"))
async def tips_choose_date(callback: CallbackQuery, state: FSMContext):
    val = callback.data[7:]
    if val == "manual":
        await callback.message.answer("Введи дату в формате ДД.ММ.ГГ (например: 02.05.26):")
        await state.set_state(TipsState.entering_date)
        await callback.answer()
        return
    await state.update_data(date_str=val)
    await callback.message.answer(
        f"Дата смены: {val}\nВсё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Верно", callback_data="tips_date_ok")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="tips_date_change")],
        ])
    )
    await state.set_state(TipsState.confirming_date)
    await callback.answer()

@router.message(TipsState.entering_date)
async def tips_enter_date_manual(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        datetime.strptime(raw, "%d.%m.%y")
    except ValueError:
        await message.answer("Неверный формат. Введи дату как ДД.ММ.ГГ, например: 02.05.26")
        return
    await state.update_data(date_str=raw)
    await message.answer(
        f"Дата смены: {raw}\nВсё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Верно", callback_data="tips_date_ok")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="tips_date_change")],
        ])
    )
    await state.set_state(TipsState.confirming_date)

@router.callback_query(lambda c: c.data == "tips_date_change")
async def tips_date_change(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "За какую смену вводим чаевые?",
        reply_markup=date_choice_keyboard()
    )
    await state.set_state(TipsState.choosing_date)
    await callback.answer()

@router.callback_query(lambda c: c.data == "tips_date_ok")
async def tips_date_confirmed(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_str = data["date_str"]
    short_date = ".".join(date_str.split(".")[:2])
    staff = await get_staff_on_date(short_date)
    await state.update_data(staff=staff, removed=[])
    await callback.message.answer("Введи общую сумму чаевых за смену (только цифры, например: 19350):")
    await state.set_state(TipsState.entering_total)
    await callback.answer()

@router.message(TipsState.entering_total)
async def tips_enter_total(message: Message, state: FSMContext):
    try:
        total = int(message.text.strip().replace(" ", ""))
        if total <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число больше 0:")
        return

    await state.update_data(total=total)
    data = await state.get_data()
    staff = data.get("staff", [])
    removed = data.get("removed", [])

    if not staff:
        await message.answer(
            "⚠️ Не удалось найти сотрудников в графике на эту дату.\n"
            "Проверь что активный лист графика выбран в ⚙️ Настройки.\n\n"
            "Пока что ручной ввод не реализован. Обратись к администратору."
        )
        await state.clear()
        return

    active = [s for s in staff if s["fullname"] not in removed]
    text = f"Сумма: {total:,} ₽\n\nСотрудники в смене:\n"
    for s in active:
        text += f"• {s['fullname']} ({s['role']})\n"
    text += "\nМожешь убрать кого-то из списка или подтвердить:"

    await message.answer(text, reply_markup=staff_confirm_keyboard(staff, removed))
    await state.set_state(TipsState.confirming_staff)

@router.callback_query(lambda c: c.data.startswith("tips_remove_"))
async def tips_remove_staff(callback: CallbackQuery, state: FSMContext):
    name = callback.data[len("tips_remove_"):]
    data = await state.get_data()
    staff = data.get("staff", [])
    removed = data.get("removed", [])
    total = data.get("total", 0)

    if name not in removed:
        removed.append(name)
    await state.update_data(removed=removed)

    active = [s for s in staff if s["fullname"] not in removed]
    text = f"Сумма: {total:,} ₽\n\nСотрудники в смене:\n"
    for s in active:
        text += f"• {s['fullname']} ({s['role']})\n"
    text += "\nМожешь убрать кого-то из списка или подтвердить:"

    await callback.message.edit_text(text, reply_markup=staff_confirm_keyboard(staff, removed))
    await callback.answer(f"{name} убран из списка")

@router.callback_query(lambda c: c.data == "tips_calc")
async def tips_do_calc(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    staff = data.get("staff", [])
    removed = data.get("removed", [])
    total = data.get("total", 0)

    active = [s for s in staff if s["fullname"] not in removed]
    if not active:
        await callback.answer("Список сотрудников пуст!", show_alert=True)
        return

    breakdown = calculate_tips(total, active)
    await state.update_data(breakdown=breakdown, active_staff=active)

    # Формируем красивый итог
    text = f"💰 Чаевые за смену: {total:,} ₽\n\n"
    role_order = ["менеджер", "встреч_менеджер", "кальянный_мастер", "бартендер", "официант", "помощник"]
    role_labels = {
        "менеджер": "📋 Менеджеры",
        "встреч_менеджер": "🤝 Хостес",
        "кальянный_мастер": "💨 Кальянные",
        "бартендер": "🍹 Бар",
        "официант": "🍽 Официанты",
        "помощник": "🙋 Помощники",
    }

    by_role = {}
    for s in active:
        by_role.setdefault(s["role"], []).append(s["fullname"])

    for role in role_order:
        if role not in by_role:
            continue
        text += f"{role_labels[role]}:\n"
        for name in by_role[role]:
            amount = breakdown.get(name, 0)
            text += f"  {name} — {amount:,} ₽\n"
        text += "\n"

    real_total = sum(breakdown.values())
    text += f"Итого распределено: {real_total:,} ₽"

    await callback.message.edit_text(text, reply_markup=calc_confirm_keyboard())
    await state.set_state(TipsState.confirming_calc)
    await callback.answer()

@router.callback_query(lambda c: c.data == "tips_cancel")
async def tips_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Ввод чаевых отменён.")
    await callback.answer()

@router.callback_query(lambda c: c.data == "tips_confirm")
async def tips_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_str = data["date_str"]
    total = data["total"]
    breakdown = data["breakdown"]
    active_staff = data["active_staff"]

    role_map = {s["fullname"]: s["role"] for s in active_staff}

    await save_tip_entry(date_str, total, breakdown)
    ok = await write_tips_to_sheet(date_str, total, breakdown, role_map)

    await state.clear()
    status = "✅ Записано в таблицу" if ok else "⚠️ Ошибка записи в таблицу, но в JSON сохранено"
    await callback.message.edit_text(f"💰 Чаевые за {date_str} сохранены!\n{status}")
    await callback.answer()

    # Уведомляем сотрудников
    bot = callback.bot
    db = await load_db()
    for uid, info in db.items():
        fullname = info.get("fullname", "")
        if fullname in breakdown:
            amount = breakdown[fullname]
            try:
                await bot.send_message(
                    int(uid),
                    f"💰 Чаевые за {date_str}:\nТвоя сумма: {amount:,} ₽\nВсего за смену: {total:,} ₽"
                )
            except Exception:
                pass

# ------------------------------------------------------------------
# Мои чаевые (просмотр)
# ------------------------------------------------------------------
class MyTipsState(StatesGroup):
    choosing_day = State()

@router.message(F.text == "💰 Мои чаевые")
async def my_tips_entry(message: Message, role: str = None, state: FSMContext = None):
    await state.clear()
    fullname = await get_fullname_by_username(message.from_user.username or "")
    if not fullname:
        await message.answer("Ты не найден в базе. Напиши /start")
        return

    tips = await load_tips()
    now = datetime.now()

    week_start = now - timedelta(days=now.weekday())
    week_total = 0
    month_total = 0
    all_total = 0
    day_entries = []

    for date_str, entry in tips.items():
        try:
            parts = date_str.split(".")
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            full_year = 2000 + y if y < 100 else y
            dt = datetime(full_year, m, d)
        except Exception:
            continue

        amount = entry.get("breakdown", {}).get(fullname, 0)
        if not amount:
            continue

        all_total += amount
        if dt.month == now.month and dt.year == now.year:
            month_total += amount
        if dt >= week_start.replace(hour=0, minute=0, second=0):
            week_total += amount
        day_entries.append((dt, date_str, amount))

    text = (
        f"💰 Мои чаевые\n\n"
        f"За эту неделю:  {week_total:,} ₽\n"
        f"За этот месяц: {month_total:,} ₽\n"
        f"За всё время:  {all_total:,} ₽"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 Посмотреть по дням", callback_data="mytips_days")]
    ]) if day_entries else None

    await state.update_data(fullname=fullname, day_entries=[
        (dt.isoformat(), ds, amt) for dt, ds, amt in sorted(day_entries, reverse=True)
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(lambda c: c.data == "mytips_days")
async def my_tips_by_day(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entries = data.get("day_entries", [])
    if not entries:
        await callback.answer("Нет данных.", show_alert=True)
        return
    text = "📆 Чаевые по дням:\n\n"
    for iso, date_str, amount in entries:
        text += f"{date_str} — {amount:,} ₽\n"
    await callback.message.answer(text)
    await callback.answer()