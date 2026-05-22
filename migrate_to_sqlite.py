import asyncio
import json
import os
import aiosqlite
from database import get_connection, save_motivation, save_tips, save_schedules

async def migrate():
    print("Миграция начата...")
    # Подключаемся к новой БД (создаст таблицы)
    await get_connection()
    
    # Перенос users.json
    if os.path.exists("users.json"):
        with open("users.json", "r", encoding="utf-8") as f:
            users = json.load(f)
        conn = await get_connection()
        for uid, info in users.items():
            roles_json = json.dumps(info.get("roles", []))
            await conn.execute('''
                INSERT OR REPLACE INTO users (user_id, fullname, name, role, roles, username)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (int(uid), info.get("fullname", ""), info.get("name", ""),
                  info.get("role"), roles_json, info.get("username", "")))
        await conn.commit()
        print(f"Перенесено {len(users)} пользователей.")
    
    # Перенос motivation.json
    if os.path.exists("motivation.json"):
        with open("motivation.json", "r", encoding="utf-8") as f:
            motivation = json.load(f)
        await save_motivation(motivation)
        print(f"Перенесено {len(motivation)} записей мотивации.")
    
    # Перенос tips.json
    if os.path.exists("tips.json"):
        with open("tips.json", "r", encoding="utf-8") as f:
            tips = json.load(f)
        await save_tips(tips)
        print(f"Перенесено {len(tips)} записей чаевых.")
    
    # Перенос schedules.json
    if os.path.exists("schedules.json"):
        with open("schedules.json", "r", encoding="utf-8") as f:
            schedules = json.load(f)
        await save_schedules(schedules)
        print(f"Перенесено {len(schedules)} типов графиков.")
    
    # Перенос settings.json
    if os.path.exists("settings.json"):
        with open("settings.json", "r", encoding="utf-8") as f:
            settings = json.load(f)
        conn = await get_connection()
        for key, value in settings.items():
            await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                               (key, value))
        await conn.commit()
        print(f"Перенесено {len(settings)} настроек.")
    
    print("Миграция завершена!")

if __name__ == "__main__":
    asyncio.run(migrate())