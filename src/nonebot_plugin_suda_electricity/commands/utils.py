__all__ = ["format_electricity_query_result", "send_low_balance_alert"]


from nonebot_plugin_alconna import UniMessage, Target

from ..suda import ElectricityQueryResult
from ..db import DormitoryUser
from ..utils import logger


def format_electricity_query_result(result: ElectricityQueryResult) -> str:
    lines = []
    lines.append("查询结果：")
    lines.append(f"宿舍：{result.dormitory.dormitory_name}")
    lines.append(f"剩余电量：{result.dormitory.left_electricity} 度")
    lines.append(f"剩余金额：{result.dormitory.balance} 元")
    return "\n".join(lines)


async def send_low_balance_alert(
    result: ElectricityQueryResult, user: DormitoryUser
) -> None:
    msg = UniMessage()
    msg.at(user.user_id).text(" 宿舍电费不足提醒, 是时候开冲了！\n\n")
    msg.text(format_electricity_query_result(result))
    logger.info(
        f"发送宿舍电费不足提醒，user_id={user.user_id}, dormitory_name={result.dormitory.dormitory_name}"
    )

    target = Target(id=user.subscribe_id, private=(user.subscribe_type == "private"))
    await msg.send(target=target)
