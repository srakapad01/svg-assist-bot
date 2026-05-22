from aiogram import BaseMiddleware
from aiogram.types import Message
from database import get_role, get_roles

class RoleMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
            data["role"] = await get_role(user_id)
            data["roles"] = await get_roles(user_id)

            # Записываем действие в FSM state
            state = data.get("state")
            if state and event.text and not event.text.startswith("/"):
                try:
                    state_data = await state.get_data()
                    actions = state_data.get("last_actions", [])
                    actions.append(event.text)
                    actions = actions[-5:]  # только последние 5
                    await state.update_data(last_actions=actions)
                except:
                    pass

        return await handler(event, data)