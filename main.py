import asyncio
import json
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from middleware.role_check import RoleMiddleware
from database import get_role, get_roles, set_user_role, load_db, register_user
from keyboards import main_menu, schedule_menu, motivation_menu, knowledge_menu, roles_menu
from utils import has_access
from constants import ALL_ROLES
from backup_db import scheduled_backup

from dotenv import load_dotenv
import os
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(RoleMiddleware())

from handlers.schedule import router as schedule_router
dp.include_router(schedule_router)

from handlers.motivation import router as motivation_router
dp.include_router(motivation_router)

from handlers.admin import router as admin_router
dp.include_router(admin_router)

from handlers.tips import router as tips_router
dp.include_router(tips_router)

from handlers.tracker import router as tracker_router
dp.include_router(tracker_router)

# ─── FSM ────────────────────────────────────────────────────

class Registration(StatesGroup):
    waiting_for_name = State()
    confirming_name = State()

class AddRoleState(StatesGroup):
    waiting_user = State()
    waiting_role = State()

class RemoveRoleState(StatesGroup):
    waiting_user = State()
    waiting_role = State()

class BugReportState(StatesGroup):
    waiting_text = State()

# ─── Старт ──────────────────────────────────────────────────

@dp.message(Command("start"))
async def start(message: Message, role: str = None, state: FSMContext = None):
    db = await load_db()
    user = db.get(str(message.from_user.id))
    user_exists = user and user.get("fullname")

    if not user_exists:
        sent = await message.answer("Привет! Как тебя зовут?\nВведи фамилию и имя (например: Иванов Иван)")
        if state:
            await state.update_data(last_bot_msg_id=sent.message_id, last_bot_chat_id=message.chat.id)
        await state.set_state(Registration.waiting_for_name)
        return

    if role is None:
        await message.answer(
            "Привет, " + user["fullname"] + "!\n"
            "Твоя заявка на рассмотрении. Ожидай подтверждения."
        )
        return

    roles_list = await get_roles(message.from_user.id)
    roles_text = ", ".join([ALL_ROLES.get(r, r) for r in roles_list])
    await message.answer(
        "Привет, " + user["fullname"] + "!\n"
        "Твои роли: " + roles_text,
        reply_markup=main_menu(role, roles=roles_list)
    )

@dp.message(Registration.waiting_for_name)
async def process_name(message: Message, state: FSMContext = None):
    fullname = message.text.strip()
    if len(fullname.split()) < 2:
        sent = await message.answer("Введи фамилию И имя через пробел.\nНапример: Иванов Иван")
        if state:
            await state.update_data(last_bot_msg_id=sent.message_id, last_bot_chat_id=message.chat.id)
        return
    await state.update_data(fullname=fullname)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Верно", callback_data="confirm_name")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="change_name")]
    ])
    sent = await message.answer("Твоё имя: " + fullname + "\nВсё верно?", reply_markup=keyboard)
    if state:
        await state.update_data(last_bot_msg_id=sent.message_id, last_bot_chat_id=message.chat.id)
    await state.set_state(Registration.confirming_name)

@dp.callback_query(lambda c: c.data == "confirm_name")
async def confirm_name(callback: CallbackQuery, state: FSMContext = None):
    data = await state.get_data()
    fullname = data["fullname"]
    username = callback.from_user.username or ""
    await register_user(callback.from_user.id, fullname, username)
    await state.clear()

    db = await load_db()
    for uid, info in db.items():
        role = info.get("role")
        if role in ["сказочный_полковник", "менеджер", "owner"]:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="approve_" + str(callback.from_user.id))],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data="reject_" + str(callback.from_user.id))]
            ])
            try:
                await bot.send_message(
                    int(uid),
                    "Новый сотрудник хочет зарегистрироваться:\n"
                    "Имя: " + fullname + "\n"
                    "Username: @" + username,
                    reply_markup=keyboard
                )
            except:
                pass

    await callback.message.answer(
        "Отлично, " + fullname + "! Твоя заявка отправлена.\nОжидай подтверждения."
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "change_name")
async def change_name(callback: CallbackQuery, state: FSMContext = None):
    await callback.message.answer("Введи фамилию и имя заново:")
    await state.set_state(Registration.waiting_for_name)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("approve_"))
async def approve_user(callback: CallbackQuery, state: FSMContext = None):
    user_id = int(callback.data[8:])
    db = await load_db()
    user = db.get(str(user_id))
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="role_" + str(user_id) + "_" + role)]
        for role, label in ALL_ROLES.items()
        if role != "owner"
    ])
    await callback.message.answer("Выбери роль для " + user["fullname"] + ":", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("role_") and len(c.data.split("_")) >= 3)
async def assign_role(callback: CallbackQuery, state: FSMContext = None):
    parts = callback.data[5:].split("_")
    user_id = int(parts[0])
    role = "_".join(parts[1:])
    db = await load_db()
    user = db.get(str(user_id))
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return

    existing_roles = await get_roles(user_id)
    if len(existing_roles) >= 3:
        await callback.message.answer(
            "У сотрудника " + user["fullname"] + " уже 3 роли!\n"
            "Сначала удалите одну через 👑 Управление ролями"
        )
        await callback.answer()
        return

    await set_user_role(user_id, user["fullname"], role, user.get("username", ""))

    if role in ["официант", "бартендер", "менеджер", "встреч_менеджер", "помощник", "кальянный_мастер"]:
        from handlers.schedule import add_to_staff_sheet
        await add_to_staff_sheet(user["fullname"], role)

    await callback.message.answer("Роль " + ALL_ROLES.get(role, role) + " назначена " + user["fullname"])
    try:
        new_role = await get_role(user_id)
        new_roles = await get_roles(user_id)
        await bot.send_message(
            user_id,
            "Тебе назначена роль: " + ALL_ROLES.get(role, role),
            reply_markup=main_menu(new_role, roles=new_roles)
        )
    except:
        pass
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_user(callback: CallbackQuery, state: FSMContext = None):
    user_id = int(callback.data[7:])
    db = await load_db()
    user = db.get(str(user_id))
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return
    await callback.message.answer("Заявка " + user["fullname"] + " отклонена.")
    try:
        await bot.send_message(user_id, "К сожалению твоя заявка была отклонена.")
    except:
        pass
    await callback.answer()

# ─── Главное меню — кнопки ───────────────────────────────────

@dp.message(F.text == "📅 Мой график")
async def my_schedule(message: Message, role: str = None, state: FSMContext = None):
    from handlers.schedule import find_current_month_sheet, get_spreadsheet, find_employee_row
    from database import get_active_sheet
    username = message.from_user.username or ""
    db = await load_db()
    fullname = None
    for uid, info in db.items():
        if info.get("username", "").lower() == username.lower():
            fullname = info.get("fullname")
            break
    if not fullname:
        await message.answer("Ты не найден в базе. Напиши /start")
        return
    active_sheet = await get_active_sheet("schedule")
    sheet_name = active_sheet if active_sheet else await find_current_month_sheet()
    if not sheet_name:
        await message.answer("Лист для текущего месяца не найден.")
        return
    try:
        spreadsheet = await get_spreadsheet()
        worksheet = await spreadsheet.worksheet(sheet_name)
        row = await find_employee_row(worksheet, fullname)
        if not row:
            await message.answer("Ты не найден в таблице графика.")
            return
        header = await worksheet.row_values(2)
        row_values = await worksheet.row_values(row)
        text = "Твой график:\n\n"
        has_shifts = False
        for i, val in enumerate(row_values[1:], 1):
            if val and i < len(header):
                text += header[i] + " — " + val + "\n"
                has_shifts = True
        if not has_shifts:
            await message.answer("У тебя пока нет смен.")
        else:
            await message.answer(text)
    except Exception as e:
        await message.answer("Ошибка: " + str(e))

@dp.message(F.text == "💰 Мои чаевые")
async def my_tips(message: Message, role: str = None, state: FSMContext = None):
    from handlers.tips import my_tips_entry
    await my_tips_entry(message, role=role, state=state)

@dp.message(F.text == "📚 База знаний")
async def knowledge_base(message: Message, role: str = None, state: FSMContext = None):
    await message.answer("База знаний — в разработке.")

@dp.message(F.text == "🏆 Моя мотивация")
async def my_motivation(message: Message, role: str = None, state: FSMContext = None):
    from handlers.motivation import my_motivation_handler
    await my_motivation_handler(message, state=state)

@dp.message(F.text == "📅 Управление графиком")
async def manage_schedule(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
    else:
        await message.answer("Управление графиком:", reply_markup=schedule_menu())

@dp.message(F.text == "💰 Чаевые")
async def tips_button(message: Message, role: str = None, roles: list = None, state: FSMContext = None):
    from handlers.tips import tips_entry
    await tips_entry(message, role=role, roles=roles, state=state)

@dp.message(F.text == "🏆 Управление мотивацией")
async def manage_motivation(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    from handlers.motivation import motivation_start
    await motivation_start(message, role=role, state=state, bot=bot)

@dp.message(F.text == "👤 Добавить сотрудника")
async def add_employee(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    await message.answer(
        "Чтобы добавить сотрудника — попроси его написать боту /start.\n"
        "После регистрации ты получишь уведомление и сможешь выбрать его роль."
    )

@dp.message(F.text == "📖 Управление базой знаний")
async def manage_knowledge(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    await message.answer("Управление базой знаний:", reply_markup=knowledge_menu())

@dp.message(F.text == "📊 Аналитика")
async def analytics(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    await message.answer("Аналитика — в разработке.")

@dp.message(F.text == "🔙 Назад")
async def go_back(message: Message, role: str = None, state: FSMContext = None):
    if role:
        roles_list = await get_roles(message.from_user.id)
        await message.answer("Главное меню:", reply_markup=main_menu(role, roles=roles_list))
    else:
        await message.answer("Главное меню:")

# ─── Кнопки графика ─────────────────────────────────────────

@dp.message(F.text == "📆 Добавить смены на месяц")
async def btn_newshift(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    from handlers.schedule import new_shift_start
    await new_shift_start(message, role=role, state=state)

@dp.message(F.text == "➕ Добавить смену")
async def btn_addshift(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    from handlers.schedule import add_single_shift_start
    await add_single_shift_start(message, role=role, state=state)

@dp.message(F.text == "❌ Убрать смену")
async def btn_delshift(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    from handlers.schedule import del_shift_start
    await del_shift_start(message, state=state)

@dp.message(F.text == "📋 Управление графиком")
async def btn_schedules(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if not has_access(role, ["менеджер", "сказочный_полковник"], roles_list):
        await message.answer("Нет доступа.")
        return
    from handlers.schedule import manage_schedules_entry
    await manage_schedules_entry(message, role=role, state=state)

# ─── Управление ролями (только owner) ───────────────────────

@dp.message(F.text == "👑 Управление ролями")
async def manage_roles(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if "owner" not in roles_list:
        await message.answer("Нет доступа.")
        return
    await message.answer("Управление ролями:", reply_markup=roles_menu())

@dp.message(F.text == "📋 Роли сотрудников")
async def list_roles(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if "owner" not in roles_list:
        await message.answer("Нет доступа.")
        return
    db = await load_db()
    if not db:
        await message.answer("Нет сотрудников.")
        return
    text = "Роли сотрудников:\n\n"
    for uid, info in db.items():
        fullname = info.get("fullname", "Неизвестно")
        roles_raw = info.get("roles", "[]")
        if isinstance(roles_raw, str):
            try:
                roles = json.loads(roles_raw)
            except:
                roles = [info.get("role")] if info.get("role") else []
        else:
            roles = roles_raw if roles_raw else []
        roles_text = ", ".join([ALL_ROLES.get(r, r) for r in roles if r])
        text += "• " + fullname + " — " + (roles_text or "нет роли") + "\n"
    await message.answer(text)

@dp.message(F.text == "➕ Добавить роль сотруднику")
async def add_role_start(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if "owner" not in roles_list:
        await message.answer("Нет доступа.")
        return
    db = await load_db()
    employees = [(uid, info["fullname"]) for uid, info in db.items() if info.get("fullname")]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=fullname, callback_data="ar_user_" + uid)]
        for uid, fullname in employees
    ])
    await message.answer("Выбери сотрудника:", reply_markup=keyboard)
    await state.set_state(AddRoleState.waiting_user)

@dp.callback_query(lambda c: c.data.startswith("ar_user_"))
async def add_role_get_user(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.data[8:]
    db = await load_db()
    user = db.get(uid)
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return
    existing_roles = await get_roles(int(uid))
    if len(existing_roles) >= 3:
        await callback.message.answer("У " + user["fullname"] + " уже 3 роли!\nСначала удалите одну.")
        await callback.answer()
        return
    await state.update_data(target_uid=uid, target_fullname=user["fullname"])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="ar_role_" + r)]
        for r, label in ALL_ROLES.items()
        if r not in existing_roles
    ])
    await callback.message.answer(
        "Сотрудник: " + user["fullname"] + "\nВыбери роль для добавления:",
        reply_markup=keyboard
    )
    await callback.answer()
    await state.set_state(AddRoleState.waiting_role)

@dp.callback_query(lambda c: c.data.startswith("ar_role_"))
async def add_role_confirm(callback: CallbackQuery, state: FSMContext = None):
    role = callback.data[8:]
    data = await state.get_data()
    uid = data["target_uid"]
    fullname = data["target_fullname"]
    db = await load_db()
    user = db.get(uid)
    await set_user_role(int(uid), fullname, role, user.get("username", ""))
    await callback.message.answer("Роль " + ALL_ROLES.get(role, role) + " добавлена " + fullname)
    try:
        new_role = await get_role(int(uid))
        new_roles = await get_roles(int(uid))
        await bot.send_message(int(uid), "Тебе добавлена роль: " + ALL_ROLES.get(role, role), reply_markup=main_menu(new_role, roles=new_roles))
    except:
        pass
    await state.clear()
    await callback.answer()

@dp.message(F.text == "➖ Убрать роль сотруднику")
async def remove_role_start(message: Message, role: str = None, state: FSMContext = None):
    roles_list = await get_roles(message.from_user.id)
    if "owner" not in roles_list:
        await message.answer("Нет доступа.")
        return
    db = await load_db()
    employees = [(uid, info["fullname"]) for uid, info in db.items() if info.get("fullname")]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=fullname, callback_data="rr_user_" + uid)]
        for uid, fullname in employees
    ])
    await message.answer("Выбери сотрудника:", reply_markup=keyboard)
    await state.set_state(RemoveRoleState.waiting_user)

@dp.callback_query(lambda c: c.data.startswith("rr_user_"))
async def remove_role_get_user(callback: CallbackQuery, state: FSMContext = None):
    uid = callback.data[8:]
    db = await load_db()
    user = db.get(uid)
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return
    existing_roles = await get_roles(int(uid))
    if not existing_roles:
        await callback.message.answer("У " + user["fullname"] + " нет ролей.")
        await callback.answer()
        return
    await state.update_data(target_uid=uid, target_fullname=user["fullname"])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ALL_ROLES.get(r, r), callback_data="rr_role_" + r)]
        for r in existing_roles
    ])
    await callback.message.answer(
        "Сотрудник: " + user["fullname"] + "\nКакую роль убрать?",
        reply_markup=keyboard
    )
    await callback.answer()
    await state.set_state(RemoveRoleState.waiting_role)

@dp.callback_query(lambda c: c.data.startswith("rr_role_"))
async def remove_role_confirm(callback: CallbackQuery, state: FSMContext = None):
    role_to_remove = callback.data[8:]
    data = await state.get_data()
    uid = data["target_uid"]
    fullname = data["target_fullname"]
    from database import remove_role
    await remove_role(int(uid), role_to_remove)
    await callback.message.answer("Роль " + ALL_ROLES.get(role_to_remove, role_to_remove) + " убрана у " + fullname)
    try:
        new_role = await get_role(int(uid))
        new_roles = await get_roles(int(uid))
        await bot.send_message(int(uid), "Роль " + ALL_ROLES.get(role_to_remove, role_to_remove) + " была убрана.", reply_markup=main_menu(new_role, roles=new_roles))
    except:
        pass
    await state.clear()
    await callback.answer()

# ─── Служебные ──────────────────────────────────────────────

@dp.message(Command("setadmin"))
async def set_admin(message: Message, state: FSMContext = None):
    if message.from_user.id != OWNER_ID:
        await message.answer("Нет прав.")
        return
    db = await load_db()
    fullname = db.get(str(message.from_user.id), {}).get("fullname", message.from_user.first_name)
    await set_user_role(message.from_user.id, fullname, "owner")
    await message.answer("Ты назначен Owner!", reply_markup=main_menu("owner"))

@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer("Твой ID: " + str(message.from_user.id))

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия.")
        return
    await state.clear()
    user_role = await get_role(message.from_user.id)
    user_roles = await get_roles(message.from_user.id)
    await message.answer("Действие отменено.", reply_markup=main_menu(user_role, roles=user_roles))

# ─── Отчёт об ошибке ────────────────────────────────────────

@dp.message(F.text == "🐛 Ошибка")
async def bug_report_start(message: Message, state: FSMContext = None):
    await state.set_state(BugReportState.waiting_text)
    await message.answer("Опиши ошибку — что делал и что пошло не так:")

@dp.message(BugReportState.waiting_text)
async def bug_report_send(message: Message, state: FSMContext = None):
    text = message.text.strip()
    db = await load_db()
    user = db.get(str(message.from_user.id), {})
    fullname = user.get("fullname", "Неизвестно")

    state_data = await state.get_data()
    actions = state_data.get("last_actions", [])
    actions_text = "\n".join([f"  {i+1}. {a}" for i, a in enumerate(actions)]) or "нет данных"

    report = (
        "🐛 Отчёт об ошибке\n\n"
        "От: " + fullname + " (@" + (message.from_user.username or "без username") + ")\n\n"
        "Описание:\n" + text + "\n\n"
        "Последние действия:\n" + actions_text
    )

    for uid, info in db.items():
        roles_raw = info.get("roles", "[]")
        try:
            roles = json.loads(roles_raw) if isinstance(roles_raw, str) else roles_raw
        except:
            roles = []
        if any(r in roles for r in ["owner", "сказочный_полковник"]):
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Принято", callback_data="bug_ack_" + str(message.from_user.id))]
                ])
                await bot.send_message(int(uid), report, reply_markup=kb)
            except:
                pass

    await state.clear()
    await message.answer("✅ Отчёт отправлен!")

@dp.callback_query(lambda c: c.data.startswith("bug_ack_"))
async def bug_ack(callback: CallbackQuery):
    reporter_id = int(callback.data[8:])
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Принято")
    try:
        await bot.send_message(reporter_id, "✅ Твой отчёт принят руководством!")
    except:
        pass

# ─── Запуск ─────────────────────────────────────────────────

async def check_staff_on_start():
    from handlers.schedule import add_to_staff_sheet, is_in_staff
    db = await load_db()
    for uid, info in db.items():
        if info.get("fullname"):
            roles = info.get("roles", [])
            if isinstance(roles, str):
                try:
                    roles = json.loads(roles)
                except:
                    roles = []
            for role in roles:
                if role and role != "owner":
                    if not await is_in_staff(info["fullname"]):
                        await add_to_staff_sheet(info["fullname"], role)
                        print("Добавлен в ШТАТ: " + info["fullname"] + " — " + role)

async def sync_staff_hourly():
    from handlers.schedule import sync_staff_to_schedule
    from handlers.motivation import sync_staff_to_motivation
    from database import get_active_sheet
    while True:
        await asyncio.sleep(3600)
        active_schedule = await get_active_sheet("schedule")
        if active_schedule:
            await sync_staff_to_schedule(active_schedule)
        active_motivation = await get_active_sheet("motivation")
        if active_motivation:
            await sync_staff_to_motivation(active_motivation)

async def main():
    print("Бот запущен!")
    from database import init_db
    await init_db()
    await check_staff_on_start()
    asyncio.create_task(sync_staff_hourly())
    asyncio.create_task(scheduled_backup(24))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())