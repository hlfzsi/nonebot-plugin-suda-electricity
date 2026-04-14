import random

from .utils import send_low_balance_alert
from ..db import dormitory_repo
from ..suda import query_electricity, CaptchaRequiredError
from ..scheduler import DormitoryCheckDueEvent, scheduler_observer_registry
from ..utils import logger


async def scheduler_observer(event: DormitoryCheckDueEvent) -> None:
    dormitory = event.dormitory.dormitory
    dormitory_detail = await dormitory_repo.get_detail(dormitory.dormitory_key)
    if not dormitory_detail:
        logger.error(
            f"无法获取宿舍详情，跳过本次调度，dormitory_key={dormitory.dormitory_key}"
        )
        return
    if not dormitory_detail.users:
        logger.warning(
            f"宿舍没有绑定用户，跳过本次调度，dormitory_key={dormitory.dormitory_key}"
        )
        return
    user = random.choice(dormitory_detail.users)
    try:
        result = await query_electricity(user.user_name, user.password)
    except CaptchaRequiredError:
        logger.error(
            f"登录失败，可能需要验证码，暂不支持验证码登录，跳过本次调度，dormitory_key={dormitory.dormitory_key}"
        )
        return
    except Exception as e:
        logger.error(
            f"登录失败，发生错误：{e}，跳过本次调度，dormitory_key={dormitory.dormitory_key}"
        )
        import traceback

        traceback.print_exc()
        return

    if float(result.dormitory.balance) <= dormitory_detail.dormitory.balance_threshold:
        await send_low_balance_alert(result, user)
    else:
        logger.debug(
            f"宿舍电费充足，无需提醒，dormitory_key={dormitory.dormitory_key}, balance={result.dormitory.balance}"
        )


scheduler_observer_registry.register(scheduler_observer)
