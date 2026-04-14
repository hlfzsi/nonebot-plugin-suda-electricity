__all__ = ["subscribe_command", "subscribe_matcher"]

from nonebot_plugin_alconna import Alconna, on_alconna
from nonebot_plugin_uninfo import Uninfo

from ..db import dormitory_user_repo
from ..utils import extract_session_info

subscribe_command = Alconna("/sd subscribe")
subscribe_matcher = on_alconna(subscribe_command, block=True, aliases={"/sd sub"})


@subscribe_matcher.handle()
async def handle_subscribe(session: Uninfo) -> None:
    user = await dormitory_user_repo.get_by_user_id(session.user.id)
    if not user:
        await subscribe_matcher.send(
            "你还没有绑定学号哦！请先使用 /sd login 命令绑定学号"
        )
        return

    session_info = extract_session_info(session)
    subscribe_id = session_info["group_id"] or session_info["user_id"]
    subscribe_type = "group" if session_info["group_id"] else "private"

    updated_user = await dormitory_user_repo.update_subscription_target(
        user_name=user.user_name,
        user_id=session.user.id,
        subscribe_type=subscribe_type,
        subscribe_id=subscribe_id,
    )
    if not updated_user:
        await subscribe_matcher.send("更新订阅目标失败，请稍后重试")
        return

    target_label = "当前群聊" if subscribe_type == "group" else "当前私聊"
    await subscribe_matcher.send(f"订阅成功，电费不足提醒已绑定到{target_label}")
