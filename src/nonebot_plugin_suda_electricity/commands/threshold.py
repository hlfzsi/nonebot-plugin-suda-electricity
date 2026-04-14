__all__ = ["threshold_command", "threshold_matcher"]

from nonebot_plugin_alconna import Alconna, AlconnaMatch, Args, Match, on_alconna
from nonebot_plugin_uninfo import Uninfo

from ..db import dormitory_repo, dormitory_user_repo

threshold_command = Alconna("/sd threshold", Args["balance_threshold", float, None])
threshold_matcher = on_alconna(threshold_command, block=True)


@threshold_matcher.handle()
async def handle_threshold(
    session: Uninfo,
    balance_threshold: Match[str] = AlconnaMatch("balance_threshold"),
) -> None:
    if balance_threshold.result is None:
        await threshold_matcher.send("请提供电费阈值，例如：/sd threshold 20")
        return

    try:
        balance_threshold_value = float(balance_threshold.result)
    except ValueError:
        await threshold_matcher.send("电费阈值必须是一个数字，例如：/sd threshold 20")
        return

    binding = await dormitory_user_repo.get_binding_by_user_id(session.user.id)
    if not binding:
        await threshold_matcher.send(
            "你还没有绑定学号哦！请先使用 /sd login 命令绑定学号"
        )
        return

    dormitory, _ = binding

    try:
        updated_dormitory = await dormitory_repo.update_threshold(
            dormitory_key=dormitory.dormitory_key,
            balance_threshold=balance_threshold_value,
        )
    except ValueError:
        await threshold_matcher.send("电费阈值不能小于 0")
        return

    if not updated_dormitory:
        await threshold_matcher.send("更新电费阈值失败，请稍后重试")
        return

    await threshold_matcher.send(
        f"电费提醒阈值已更新为 {updated_dormitory.balance_threshold} 元"
    )
