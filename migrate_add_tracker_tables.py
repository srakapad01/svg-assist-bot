import asyncio
import aiosqlite
import os

DB_PATH = "bot_database.db"

async def migrate():
    if not os.path.exists(DB_PATH):
        print("База данных не найдена")
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        # Добавляем таблицы, если их нет
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
                user_id INTEGER NOT NULL,
                block_id INTEGER NOT NULL,
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
        # Заполняем фиксированные блоки, если таблица пуста
        cur = await conn.execute("SELECT COUNT(*) FROM global_blocks")
        count = (await cur.fetchone())[0]
        if count == 0:
            # Сервис
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Сервис', 1, 0)")
            # Подблоки кухни
            kitchen_subs = ["Закуски", "Салаты", "Горячие блюда", "Супы", "Десерты", "Азия", "Стейки", "Гарниры", "Соуса"]
            for sub in kitchen_subs:
                await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES (?, 1, 1)", (sub,))
            # Авторский бар
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Авторский бар', 1, 2)")
            # Винный шкаф
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Винный шкаф', 1, 3)")
            await conn.commit()
            print("Фиксированные блоки добавлены.")
        else:
            print("Таблицы уже существуют, блоки не добавлены.")
        
        # Также нужно убедиться, что для существующих пользователей, которые участвуют в трекере, созданы записи в employee_blocks? Это можно сделать позже через админку.
        print("Миграция завершена.")

if __name__ == "__main__":
    asyncio.run(migrate())