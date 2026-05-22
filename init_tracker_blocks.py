import asyncio
from database import get_connection

async def init_blocks():
    conn = await get_connection()
    
    # Проверим, есть ли уже блоки
    async with conn.execute("SELECT name FROM global_blocks WHERE is_fixed = 1") as cur:
        existing = [row[0] async for row in cur]
    
    print("Существующие фиксированные блоки:", existing)
    
    # Список блоков, которые должны быть (без Сервиса, так как он не влияет, но пусть будет)
    # Сервис добавляется при инициализации, но не влияет на коэффициент.
    # Убедимся, что кухонные подблоки, авторский бар и винный шкаф присутствуют.
    
    # Если блоков нет, создадим их (это уже должно было произойти при первом запуске)
    if not existing:
        print("Таблица global_blocks пуста, добавляем начальные блоки...")
        await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Сервис', 1, 0)")
        kitchen_subs = ["Закуски", "Салаты", "Горячие блюда", "Супы", "Десерты", "Азия", "Стейки", "Гарниры", "Соуса"]
        for sub in kitchen_subs:
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES (?, 1, 1)", (sub,))
        await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Авторский бар', 1, 2)")
        await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Винный шкаф', 1, 3)")
        await conn.commit()
        print("Блоки добавлены.")
    else:
        # Проверим, есть ли авторский бар и винный шкаф
        names = [name.lower() for name in existing]
        if 'авторский бар' not in names:
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Авторский бар', 1, 2)")
        if 'винный шкаф' not in names:
            await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES ('Винный шкаф', 1, 3)")
        # Проверим, что все подблоки кухни на месте
        needed_subs = ["Закуски", "Салаты", "Горячие блюда", "Супы", "Десерты", "Азия", "Стейки", "Гарниры", "Соуса"]
        for sub in needed_subs:
            if sub not in existing:
                await conn.execute("INSERT INTO global_blocks (name, is_fixed, sort_order) VALUES (?, 1, 1)", (sub,))
        await conn.commit()
        print("Недостающие блоки добавлены.")
    
    # Теперь создадим записи employee_blocks для всех существующих пользователей
    # (чтобы у каждого были записи для всех блоков, даже если они не сданы)
    await conn.execute("""
        INSERT OR IGNORE INTO employee_blocks (user_id, block_id, is_signed, signed_at, signed_by)
        SELECT u.user_id, g.id, 0, NULL, 0
        FROM users u
        CROSS JOIN global_blocks g
        WHERE g.is_fixed = 1 OR g.is_fixed = 0
    """)
    await conn.commit()
    print("Записи employee_blocks для всех пользователей и блоков созданы (is_signed=0).")
    
    # Выведем для проверки
    async with conn.execute("SELECT COUNT(*) FROM employee_blocks") as cur:
        count = (await cur.fetchone())[0]
    print(f"Всего записей в employee_blocks: {count}")

if __name__ == "__main__":
    asyncio.run(init_blocks())