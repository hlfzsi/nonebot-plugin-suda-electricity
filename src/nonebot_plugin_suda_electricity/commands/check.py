__all__ = ["check_command", "check_matcher"]

from nonebot_plugin_alconna import Alconna, on_alconna
from nonebot_plugin_uninfo import Uninfo

from .utils import format_electricity_query_result, send_low_balance_alert
from ..db import dormitory_user_repo, dormitory_repo
from ..suda import query_electricity, CaptchaRequiredError

check_command = Alconna("/sd check")
check_matcher = on_alconna(check_command, block=True)


@check_matcher.handle()
async def handle_check(session: Uninfo) -> None:
    user = await dormitory_user_repo.get_by_user_id(session.user.id)
    if not user:
        await check_matcher.send("你还没有绑定宿舍哦！请先使用 /sd login 命令绑定宿舍")
        return
    try:
        result = await query_electricity(user.user_name, user.password)
    except CaptchaRequiredError:
        await check_matcher.send("登录失败，可能需要验证码，暂不支持验证码登录")
        return
    except Exception as e:
        await check_matcher.send(f"登录失败，发生错误：{e}")
        return

    dormitory = await dormitory_repo.get(user.dormitory_key)
    if not dormitory:
        await check_matcher.send("无法获取宿舍信息，请稍后再试")
        return

    if float(result.dormitory.balance) <= dormitory.balance_threshold:
        await send_low_balance_alert(result, user)
        return
    else:
        await check_matcher.send(format_electricity_query_result(result))
        return
