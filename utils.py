from database import get_roles, load_db

def has_access(role: str, required_roles: list, roles: list = None) -> bool:
    # Если текущая роль – владелец или среди всех ролей есть владелец
    if role == "owner" or (roles and "owner" in roles):
        return True
    if role in required_roles:
        return True
    if roles:
        return any(r in required_roles for r in roles)
    return False

async def get_fullname_by_user_id(user_id: int):
    from database import get_user
    user = await get_user(user_id)
    return user.get("fullname") if user else None

async def get_fullname_by_username(username: str):
    db = await load_db()
    for uid, info in db.items():
        if info.get("username", "").lower() == username.lower():
            return info.get("fullname")
    return None

async def get_user_by_fullname(fullname: str):
    db = await load_db()
    for uid, info in db.items():
        if info.get("fullname", "").lower() == fullname.lower():
            return {"id": uid, "fullname": info["fullname"], "roles": info.get("roles", [])}
    return None