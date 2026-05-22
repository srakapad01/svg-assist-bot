# database.py – SQLite версия
import asyncio
import json
import aiosqlite
import os
from typing import Optional, List, Dict, Any

DB_PATH = "bot_database.db"
_connection_pool = None

async def get_connection():
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = await aiosqlite.connect(DB_PATH)
        _connection_pool.row_factory = aiosqlite.Row
    return _connection_pool

async def init_db():
    conn = await get_connection()
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            fullname TEXT NOT NULL,
            name TEXT,
            role TEXT,
            roles TEXT,
            username TEXT
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS motivation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL,
            emoji TEXT NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            comment TEXT,
            manager TEXT NOT NULL,
            date TEXT NOT NULL
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS tips (
            date TEXT PRIMARY KEY,
            total INTEGER NOT NULL,
            breakdown TEXT NOT NULL,   -- JSON
            recorded_at TEXT NOT NULL
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            name TEXT PRIMARY KEY,
            work INTEGER NOT NULL,
            rest INTEGER NOT NULL
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    await conn.commit()

    await conn.execute('''
        CREATE TABLE IF NOT EXISTS global_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_fixed INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS employee_blocks (
            user_id INTEGER,
            block_id INTEGER,
            is_signed INTEGER DEFAULT 0,
            signed_at TEXT,
            signed_by INTEGER,
            PRIMARY KEY (user_id, block_id)
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS block_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            block_id INTEGER,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            changed_by INTEGER,
            changed_at TEXT
        )
    ''')
    await conn.commit()

# ----- Пользователи -----

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = await get_connection()
    async with conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        if row:
            return dict(row)
    return None

async def set_user_role(user_id: int, name: str, role: str, username: str = ""):
    conn = await get_connection()
    # Получаем текущие роли
    cur = await conn.execute("SELECT roles FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    if row and row["roles"]:
        roles_list = json.loads(row["roles"])
    else:
        roles_list = []
    if role not in roles_list:
        if len(roles_list) >= 3:
            roles_list.pop(0)
        roles_list.append(role)
    roles_json = json.dumps(roles_list)
    first_role = roles_list[0] if roles_list else None

    await conn.execute('''
        INSERT OR REPLACE INTO users (user_id, fullname, name, role, roles, username)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, name, name, first_role, roles_json, username))
    await conn.commit()

async def get_role(user_id: int) -> Optional[str]:
    user = await get_user(user_id)
    if user:
        return user.get("role")
    return None

async def get_roles(user_id: int) -> List[str]:
    user = await get_user(user_id)
    if user and user.get("roles"):
        return json.loads(user["roles"])
    return []

async def register_user(user_id: int, fullname: str, username: str):
    conn = await get_connection()
    await conn.execute('''
        INSERT OR REPLACE INTO users (user_id, fullname, name, username, role, roles)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, fullname, fullname, username, None, "[]"))
    await conn.commit()

async def load_db():
    """Для обратной совместимости – возвращает словарь всех пользователей"""
    conn = await get_connection()
    users = {}
    async with conn.execute("SELECT * FROM users") as cursor:
        async for row in cursor:
            users[str(row["user_id"])] = dict(row)
    return users

async def save_db(data):
    """Не используется, но оставлено для совместимости"""
    pass

# ----- Мотивация -----

async def load_motivation():
    conn = await get_connection()
    rows = []
    async with conn.execute("SELECT * FROM motivation") as cursor:
        async for row in cursor:
            rows.append(dict(row))
    return rows

async def save_motivation(data):
    conn = await get_connection()
    await conn.execute("DELETE FROM motivation")
    for record in data:
        await conn.execute('''
            INSERT INTO motivation (employee, emoji, type, amount, comment, manager, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (record["employee"], record["emoji"], record["type"], record["amount"],
              record.get("comment", ""), record["manager"], record["date"]))
    await conn.commit()

async def add_motivation_record(employee: str, emoji: str, comment: str, manager: str, type_name: str, amount: int):
    conn = await get_connection()
    from datetime import datetime
    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    await conn.execute('''
        INSERT INTO motivation (employee, emoji, type, amount, comment, manager, date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (employee, emoji, type_name, amount, comment, manager, date))
    await conn.commit()

# ----- Чаевые -----

async def load_tips():
    conn = await get_connection()
    tips = {}
    async with conn.execute("SELECT * FROM tips") as cursor:
        async for row in cursor:
            tips[row["date"]] = {
                "total": row["total"],
                "breakdown": json.loads(row["breakdown"]),
                "recorded_at": row["recorded_at"]
            }
    return tips

async def save_tips(data):
    conn = await get_connection()
    await conn.execute("DELETE FROM tips")
    for date_str, entry in data.items():
        await conn.execute('''
            INSERT INTO tips (date, total, breakdown, recorded_at)
            VALUES (?, ?, ?, ?)
        ''', (date_str, entry["total"], json.dumps(entry["breakdown"]), entry["recorded_at"]))
    await conn.commit()

async def save_tip_entry(date_str: str, total: int, breakdown: dict):
    from datetime import datetime
    tips = await load_tips()
    tips[date_str] = {
        "total": total,
        "breakdown": breakdown,
        "recorded_at": datetime.now().isoformat()
    }
    await save_tips(tips)

# ----- Типы графиков (schedules) -----

async def load_schedules():
    conn = await get_connection()
    schedules = {}
    async with conn.execute("SELECT * FROM schedules") as cursor:
        async for row in cursor:
            schedules[row["name"]] = {"work": row["work"], "rest": row["rest"]}
    return schedules

async def save_schedules(data):
    conn = await get_connection()
    await conn.execute("DELETE FROM schedules")
    for name, info in data.items():
        await conn.execute("INSERT INTO schedules (name, work, rest) VALUES (?, ?, ?)",
                           (name, info["work"], info["rest"]))
    await conn.commit()

# ----- Настройки -----

async def get_active_sheet(sheet_type: str):
    conn = await get_connection()
    async with conn.execute("SELECT value FROM settings WHERE key = ?", (sheet_type,)) as cursor:
        row = await cursor.fetchone()
        return row["value"] if row else None

async def set_active_sheet(sheet_type: str, sheet_name: str):
    conn = await get_connection()
    await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                       (sheet_type, sheet_name))
    await conn.commit()

# ---------- Трекер (блоки знаний) ----------
async def get_all_fixed_blocks():
    conn = await get_connection()
    rows = []
    async with conn.execute("SELECT * FROM global_blocks WHERE is_fixed = 1 ORDER BY id") as cursor:
        async for row in cursor:
            rows.append(dict(row))
    return rows

async def get_all_dynamic_blocks():
    conn = await get_connection()
    rows = []
    async with conn.execute("SELECT * FROM global_blocks WHERE is_fixed = 0 ORDER BY id") as cursor:
        async for row in cursor:
            rows.append(dict(row))
    return rows

async def get_employee_blocks_map(user_id: int):
    conn = await get_connection()
    result = {}
    async with conn.execute("SELECT id FROM global_blocks") as cursor:
        all_blocks = await cursor.fetchall()
    for (bid,) in all_blocks:
        res = await conn.execute("SELECT is_signed FROM employee_blocks WHERE user_id = ? AND block_id = ?", (user_id, bid))
        row = await res.fetchone()
        result[bid] = row[0] if row else 0
    return result

async def set_employee_block(user_id: int, block_id: int, is_signed: int, signed_by: int):
    conn = await get_connection()
    # Получаем текущее значение is_signed (если запись существует)
    res = await conn.execute("SELECT is_signed FROM employee_blocks WHERE user_id = ? AND block_id = ?", (user_id, block_id))
    row = await res.fetchone()
    old_val = row[0] if row else 0
    # Вставляем или обновляем
    await conn.execute('''
        INSERT OR REPLACE INTO employee_blocks (user_id, block_id, is_signed, signed_at, signed_by)
        VALUES (?, ?, ?, datetime('now'), ?)
    ''', (user_id, block_id, is_signed, signed_by))
    # Логируем изменение
    await conn.execute('''
        INSERT INTO block_log (user_id, block_id, action, old_value, new_value, changed_by, changed_at)
        VALUES (?, ?, 'update', ?, ?, ?, datetime('now'))
    ''', (user_id, block_id, str(old_val), str(is_signed), signed_by))
    await conn.commit()

async def is_kitchen_signed(user_id: int) -> bool:
    conn = await get_connection()
    async with conn.execute("SELECT id FROM global_blocks WHERE sort_order = 1 AND is_fixed = 1") as cursor:
        kitchen_block_ids = [row[0] async for row in cursor]
    if not kitchen_block_ids:
        return False
    for block_id in kitchen_block_ids:
        res = await conn.execute("SELECT is_signed FROM employee_blocks WHERE user_id = ? AND block_id = ?", (user_id, block_id))
        row = await res.fetchone()
        if not row or row[0] == 0:
            return False
    return True

async def get_employee_max_fixed_level(user_id: int) -> float:
    kitchen_ok = await is_kitchen_signed(user_id)
    conn = await get_connection()
    async with conn.execute("SELECT id FROM global_blocks WHERE name = 'Авторский бар' AND is_fixed = 1") as cur:
        row = await cur.fetchone()
        ab_id = row[0] if row else None
    async with conn.execute("SELECT id FROM global_blocks WHERE name = 'Винный шкаф' AND is_fixed = 1") as cur:
        row = await cur.fetchone()
        ws_id = row[0] if row else None
    if not ab_id or not ws_id:
        return 0.5
    res_ab = await conn.execute("SELECT is_signed FROM employee_blocks WHERE user_id = ? AND block_id = ?", (user_id, ab_id))
    ab_signed = (await res_ab.fetchone())[0] if res_ab.rowcount else 0
    res_ws = await conn.execute("SELECT is_signed FROM employee_blocks WHERE user_id = ? AND block_id = ?", (user_id, ws_id))
    ws_signed = (await res_ws.fetchone())[0] if res_ws.rowcount else 0

    if kitchen_ok and ab_signed and ws_signed:
        return 1.0
    elif kitchen_ok and ab_signed:
        return 0.75
    else:
        return 0.5

async def get_employee_coefficient(user_id: int) -> float:
    base = await get_employee_max_fixed_level(user_id)
    dyn_blocks = await get_all_dynamic_blocks()
    if not dyn_blocks:
        return base
    emp_map = await get_employee_blocks_map(user_id)
    any_unsigned = any(emp_map.get(b["id"], 0) == 0 for b in dyn_blocks)
    if any_unsigned:
        if base == 1.0:
            return 0.75
        elif base == 0.75:
            return 0.5
        else:
            return 0.5
    return base

async def add_dynamic_block(name: str, created_by: int):
    conn = await get_connection()
    await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES (?, 0, 0)", (name,))
    block_id = (await conn.execute("SELECT last_insert_rowid()")).fetchone()[0]
    db = await load_db()
    tracker_roles = ["официант", "бартендер", "помощник", "встреч_менеджер"]
    for uid, info in db.items():
        role = info.get("role")
        if role in tracker_roles:
            await conn.execute('''
                INSERT OR IGNORE INTO employee_blocks (user_id, block_id, is_signed, signed_at, signed_by)
                VALUES (?, ?, 0, NULL, ?)
            ''', (int(uid), block_id, created_by))
    await conn.execute('''
        INSERT INTO block_log (user_id, block_id, action, old_value, new_value, changed_by, changed_at)
        VALUES (NULL, ?, 'global_add', NULL, ?, ?, datetime('now'))
    ''', (block_id, name, created_by))
    await conn.commit()

async def update_dynamic_block(block_id: int, new_name: str, changed_by: int):
    conn = await get_connection()
    old = await conn.execute("SELECT name FROM global_blocks WHERE id = ?", (block_id,))
    old_name = (await old.fetchone())[0]
    await conn.execute("UPDATE global_blocks SET name = ? WHERE id = ?", (new_name, block_id))
    await conn.execute('''
        INSERT INTO block_log (user_id, block_id, action, old_value, new_value, changed_by, changed_at)
        VALUES (NULL, ?, 'global_edit', ?, ?, ?, datetime('now'))
    ''', (block_id, old_name, new_name, changed_by))
    await conn.commit()

async def delete_dynamic_block(block_id: int, deleted_by: int):
    conn = await get_connection()
    cur = await conn.execute("SELECT name FROM global_blocks WHERE id = ?", (block_id,))
    name_row = await cur.fetchone()
    block_name = name_row[0] if name_row else "unknown"
    await conn.execute("DELETE FROM employee_blocks WHERE block_id = ?", (block_id,))
    await conn.execute("DELETE FROM global_blocks WHERE id = ?", (block_id,))
    await conn.execute('''
        INSERT INTO block_log (user_id, block_id, action, old_value, new_value, changed_by, changed_at)
        VALUES (NULL, ?, 'global_delete', ?, NULL, ?, datetime('now'))
    ''', (block_id, block_name, deleted_by))
    await conn.commit()

async def remove_role(user_id: int, role_to_remove: str):
    conn = await get_connection()
    cur = await conn.execute("SELECT roles FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    if not row:
        return
    roles = json.loads(row["roles"]) if row["roles"] else []
    if role_to_remove in roles:
        roles.remove(role_to_remove)
    first_role = roles[0] if roles else None
    await conn.execute(
        "UPDATE users SET roles = ?, role = ? WHERE user_id = ?",
        (json.dumps(roles), first_role, user_id)
    )
    await conn.commit()