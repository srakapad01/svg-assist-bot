from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from settings import get_active_sheet, set_active_sheet
from google.oauth2.service_account import Credentials
import gspread
from utils import has_access

router = Router()

SCHEDULE_SPREADSHEET_ID = "1qXt0kKVhTQpi3MJaVCinZG8a0pxZISxUuLNvU-kXkcY"
MOTIVATION_SPREADSHEET_ID = "1UAeDQksHXDMmoUwXYjRyVe5Ic1Pza48cHT2u3LEuEnA"

def get_sheets(spreadsheet_id: str):
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    import json as _json
credentials_json = os.getenv("GOOGLE_CREDENTIALS")
if credentials_json:
    creds = Credentials.from_service_account_info(_json.loads(credentials_json), scopes=SCOPES)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(spreadsheet_id)
    return [ws.title for ws in spreadsheet.worksheets()]

class AdminState(StatesGroup):
    waiting_schedule_sheet = State()
    waiting_motivation_sheet = State()

@router.message(lambda m: m.text == "⚙️ Настройки")
async def admin_settings(message: Message, role: str = None):
    from utils import has_access
    if not has_access(role, ["сказочный_полковник"]):
        await message.answer("Нет доступа.")
        return
    schedule_sheet = get_active_sheet("schedule") or "не выбран"
    motivation_sheet = get_active_sheet("motivation") or "не выбран"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Лист графика: " + schedule_sheet, callback_data="set_schedule_sheet")],
        [InlineKeyboardButton(text="🏆 Лист мотивации: " + motivation_sheet, callback_data="set_motivation_sheet")],
    ])
    await message.answer("Настройки активных листов:", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "set_schedule_sheet")
async def set_schedule_sheet(callback: CallbackQuery, state: FSMContext):
    try:
        sheets = get_sheets(SCHEDULE_SPREADSHEET_ID)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=sheet, callback_data="sched_sheet_" + str(i))]
            for i, sheet in enumerate(sheets)
        ])
        await state.update_data(sheets=sheets)
        await callback.message.answer("Выбери активный лист для графика:", reply_markup=keyboard)
        await state.set_state(AdminState.waiting_schedule_sheet)
    except Exception as e:
        await callback.message.answer("Ошибка: " + str(e))
    await callback.answer()

@router.callback_query(lambda c: c.data == "set_motivation_sheet")
async def set_motivation_sheet(callback: CallbackQuery, state: FSMContext):
    try:
        sheets = get_sheets(MOTIVATION_SPREADSHEET_ID)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=sheet, callback_data="motiv_sheet_" + str(i))]
            for i, sheet in enumerate(sheets)
        ])
        await state.update_data(sheets=sheets)
        await callback.message.answer("Выбери активный лист для мотивации:", reply_markup=keyboard)
        await state.set_state(AdminState.waiting_motivation_sheet)
    except Exception as e:
        await callback.message.answer("Ошибка: " + str(e))
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("sched_sheet_"))
async def confirm_schedule_sheet(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[12:])
    data = await state.get_data()
    sheets = data.get("sheets", [])
    sheet_name = sheets[idx]
    set_active_sheet("schedule", sheet_name)
    await callback.message.answer("Активный лист графика: " + sheet_name + "\n\nСинхронизирую штат...")
    
    from handlers.schedule import sync_staff_to_schedule
    success = sync_staff_to_schedule(sheet_name)
    
    if success:
        await callback.message.answer("✅ Штат успешно перенесён в график!")
    else:
        await callback.message.answer("⚠️ Лист выбран, но синхронизация не удалась. Проверь структуру таблицы.")
    
    await state.clear()
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("motiv_sheet_"))
async def confirm_motivation_sheet(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[12:])
    data = await state.get_data()
    sheets = data.get("sheets", [])
    sheet_name = sheets[idx]
    set_active_sheet("motivation", sheet_name)
    await callback.message.answer("Активный лист мотивации: " + sheet_name + "\n\nСинхронизирую штат...")

    from handlers.motivation import sync_staff_to_motivation
    success = sync_staff_to_motivation(sheet_name)

    if success:
        await callback.message.answer("✅ Штат успешно перенесён в таблицу мотивации!")
    else:
        await callback.message.answer("⚠️ Лист выбран, но синхронизация не удалась. Проверь структуру таблицы.")

    await state.clear()
    await callback.answer()

# ─── Синхронизация ШТАТ → Мотивация ─────────────────────────

STAFF_TO_MOTIVATION_MAP = {
    "Бартендеры":        "Бартендеры",
    "Встреч. Менеджеры": "Встреч. менеджеры",
    "Официанты":         "Официанты",
    "Помощники":         "Помощники",
    "Кальянные мастера": "Кальяные мастера",
}

def sync_staff_to_motivation(motivation_sheet_name: str):
    try:
        from handlers.schedule import get_spreadsheet as get_schedule_spreadsheet
        staff_ws = get_schedule_spreadsheet().worksheet("ШТАТ")
        motivation_ws = get_motivation_spreadsheet().worksheet(motivation_sheet_name)

        staff_header = staff_ws.row_values(1)
        staff_data = {}
        for i, dept_name in enumerate(staff_header):
            if dept_name.strip():
                col_values = staff_ws.col_values(i + 1)[1:]
                staff_data[dept_name.strip()] = [v for v in col_values if v.strip()]

        col1 = motivation_ws.col_values(1)
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
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row, 1),
                    "values": [[""]]
                })
            for j, emp in enumerate(employees):
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(group_row + 1 + j, 1),
                    "values": [[emp]]
                })

        if updates:
            motivation_ws.batch_update(updates)

        print("Штат синхронизирован с мотивацией: " + motivation_sheet_name)
        return True

    except Exception as e:
        print("Ошибка синхронизации мотивации:", e)
        return False