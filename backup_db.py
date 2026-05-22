import asyncio
import aiosqlite
import shutil
import os
from datetime import datetime

DB_PATH = "bot_database.db"
BACKUP_FOLDER = "backups"

async def create_backup():
    """Создаёт копию базы данных в папке backups с датой в имени"""
    if not os.path.exists(BACKUP_FOLDER):
        os.makedirs(BACKUP_FOLDER)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = os.path.join(BACKUP_FOLDER, f"bot_database_{timestamp}.db")
    
    # Создаём резервную копию через aiosqlite (без блокировки)
    async with aiosqlite.connect(DB_PATH) as src:
        async with aiosqlite.connect(backup_path) as dst:
            await src.backup(dst)
    
    print(f"Бэкап создан: {backup_path}")
    
    # Удаляем старые бэкапы (оставляем только последние 7 дней)
    await delete_old_backups(days=7)

async def delete_old_backups(days: int):
    """Удаляет бэкапы старше указанного количества дней"""
    now = datetime.now()
    for filename in os.listdir(BACKUP_FOLDER):
        if filename.startswith("bot_database_") and filename.endswith(".db"):
            filepath = os.path.join(BACKUP_FOLDER, filename)
            try:
                timestamp_str = filename.replace("bot_database_", "").replace(".db", "")
                file_date = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                if (now - file_date).days > days:
                    os.remove(filepath)
                    print(f"Удалён старый бэкап: {filename}")
            except Exception as e:
                print(f"Ошибка при удалении {filename}: {e}")

async def scheduled_backup(interval_hours: int = 24):
    """Запускает фоновую задачу для регулярного бэкапа"""
    while True:
        await create_backup()
        await asyncio.sleep(interval_hours * 3600)

if __name__ == "__main__":
    asyncio.run(create_backup())