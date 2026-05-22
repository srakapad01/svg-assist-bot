import pytest
import json
import os
import tempfile
from database import load_db, save_db, get_user, set_user_role, register_user, get_role, get_roles
from unittest.mock import patch

# Фикстура для временного файла БД
@pytest.fixture
def temp_db_file():
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    # Подменяем DB_FILE глобально (через патч)
    with patch("database.DB_FILE", tmp_path):
        # Очищаем перед каждым тестом
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        yield tmp_path
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

@pytest.mark.asyncio
async def test_save_and_load_db(temp_db_file):
    data = {"1": {"name": "Test", "role": "admin"}}
    await save_db(data)
    loaded = await load_db()
    assert loaded == data

@pytest.mark.asyncio
async def test_get_user_existing(temp_db_file):
    await save_db({"42": {"fullname": "John Doe", "role": "официант"}})
    user = await get_user(42)
    assert user["fullname"] == "John Doe"
    assert user["role"] == "официант"

@pytest.mark.asyncio
async def test_get_user_not_exists(temp_db_file):
    user = await get_user(999)
    assert user is None

@pytest.mark.asyncio
async def test_register_user_new(temp_db_file):
    await register_user(123, "Иванов Иван", "ivan_username")
    user = await get_user(123)
    assert user["fullname"] == "Иванов Иван"
    assert user["username"] == "ivan_username"
    assert user["role"] is None
    assert user["roles"] == []

@pytest.mark.asyncio
async def test_register_user_updates_existing(temp_db_file):
    await register_user(123, "Старое имя", "old_user")
    await register_user(123, "Новое имя", "new_user")
    user = await get_user(123)
    assert user["fullname"] == "Новое имя"
    assert user["username"] == "new_user"

@pytest.mark.asyncio
async def test_set_user_role_new_user(temp_db_file):
    await set_user_role(1, "Петров Петр", "менеджер", "petrov")
    user = await get_user(1)
    assert user["role"] == "менеджер"
    assert user["roles"] == ["менеджер"]
    assert user["name"] == "Петров Петр"

@pytest.mark.asyncio
async def test_set_user_role_add_role_to_existing(temp_db_file):
    await set_user_role(2, "Сидоров Сидр", "официант", "sidor")
    await set_user_role(2, "Сидоров Сидр", "бартендер", "sidor")
    user = await get_user(2)
    # Должно быть две роли, первая — официант (основная)
    assert user["roles"] == ["официант", "бартендер"]
    assert user["role"] == "официант"

@pytest.mark.asyncio
async def test_set_user_role_limit_3_roles(temp_db_file):
    await set_user_role(3, "Многоролевой", "роль1", "user3")
    await set_user_role(3, "Многоролевой", "роль2", "user3")
    await set_user_role(3, "Многоролевой", "роль3", "user3")
    await set_user_role(3, "Многоролевой", "роль4", "user3")
    user = await get_user(3)
    # Должны остаться только последние три: роль2, роль3, роль4 (первая ушла)
    assert user["roles"] == ["роль2", "роль3", "роль4"]
    assert user["role"] == "роль2"

@pytest.mark.asyncio
async def test_get_role(temp_db_file):
    await set_user_role(4, "Тестер", "официант", "tester")
    role = await get_role(4)
    assert role == "официант"
    # Несуществующий пользователь
    role2 = await get_role(999)
    assert role2 is None

@pytest.mark.asyncio
async def test_get_roles(temp_db_file):
    await set_user_role(5, "Мультироль", "официант", "multi")
    await set_user_role(5, "Мультироль", "бартендер", "multi")
    roles = await get_roles(5)
    assert roles == ["официант", "бартендер"]
    # Проверка на одного пользователя без ролей
    await register_user(6, "Безрольный", "norole")
    roles2 = await get_roles(6)
    assert roles2 == []