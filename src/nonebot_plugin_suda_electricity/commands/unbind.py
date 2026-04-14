__all__ = ["unbind_command", "unbind_matcher"]

from nonebot_plugin_alconna import Alconna, on_alconna
from nonebot_plugin_uninfo import Uninfo

from ..db import dormitory_user_repo

unbind_command = Alconna("/sd logout")
unbind_matcher = on_alconna(unbind_command, block=True)


@unbind_matcher.handle()
async def handle_unbind(session: Uninfo) -> None:
    user = await dormitory_user_repo.get_by_user_id(session.user.id)
    if not user:
        await unbind_matcher.send("你还没有绑定学号哦！")
        return
    await dormitory_user_repo.unbind(user_id=user.user_id)
    await unbind_matcher.send("解绑成功！")
