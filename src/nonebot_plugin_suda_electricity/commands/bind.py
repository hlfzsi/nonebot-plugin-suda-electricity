__all__ = ["bind_command", "bind_matcher"]

from nonebot_plugin_alconna import Alconna, AlconnaMatch, Args, Match, on_alconna
from nonebot_plugin_uninfo import Uninfo

from .utils import format_electricity_query_result
from ..db import dormitory_user_repo
from ..scheduler import compute_initial_check_at
from ..suda import query_electricity, CaptchaRequiredError
from ..utils import extract_session_info

bind_command = Alconna("/sd login", Args["user_name", str, None]["password", str, None])
bind_matcher = on_alconna(bind_command, block=True)


@bind_matcher.handle()
async def handle_bind(
    session: Uninfo,
    user_name: Match[str] = AlconnaMatch("user_name"),
    password: Match[str] = AlconnaMatch("password"),
) -> None:
    if not user_name.result or not password.result:
        await bind_matcher.send(
            "请提供用户名和密码，例如：/sd login 用户名 密码 , 建议在私聊中发送"
        )
        return

    try:
        result = await query_electricity(user_name.result, password.result)
    except CaptchaRequiredError:
        await bind_matcher.send("登录失败，可能需要验证码，暂不支持验证码登录")
        return
    except Exception as e:
        await bind_matcher.send(f"登录失败，发生错误：{e}")
        import traceback

        traceback.print_exc()
        return

    session_info = extract_session_info(session)

    await dormitory_user_repo.bind(
        user_id=session.user.id,
        dormitory=result.dormitory,
        user_name=user_name.result,
        password=password.result,
        initial_next_check_at=compute_initial_check_at(),
        subscribe_id=session_info["group_id"] or session_info["user_id"],
        subscribe_type="group" if session_info["group_id"] else "private",
    )

    await bind_matcher.send(
        "绑定成功！" + "\n" + format_electricity_query_result(result)
    )
