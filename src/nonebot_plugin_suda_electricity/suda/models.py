"""定义苏大电费查询流程使用的数据模型。"""

from typing import Annotated, Any, ClassVar, Generic, Literal, TypeVar

import orjson
from pydantic import BaseModel, Field, StringConstraints, computed_field

ContentModelT = TypeVar("ContentModelT", bound=BaseModel)
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
GatewayCommandName = Literal[
    "authRedirectReq",
    "loginReq",
    "aveElecReq",
    "elecCostQueryReq",
]


def dump_json(data: Any) -> str:
    """将对象序列化为 UTF-8 JSON 字符串。"""

    return orjson.dumps(data).decode("utf-8")


class GatewayCommand(BaseModel):
    """所有电费网关命令对象的基类。"""

    command_name: ClassVar[Any]

    @computed_field(alias="classname", return_type=str)
    @property
    def classname(self) -> str:
        return self.command_name

    def as_form_fields(self) -> dict[str, str]:
        """将命令对象转换为网关要求的表单字段。"""

        return {"strTemp": dump_json(self.model_dump(by_alias=True, exclude_none=True))}


class AuthRedirectCommand(GatewayCommand):
    command_name: ClassVar[Literal["authRedirectReq"]] = "authRedirectReq"
    code: NonEmptyStr


class LoginCommand(GatewayCommand):
    command_name: ClassVar[Literal["loginReq"]] = "loginReq"
    uxid: NonEmptyStr
    user_name: NonEmptyStr = Field(alias="userName")


class AverageElectricityCommand(GatewayCommand):
    command_name: ClassVar[Literal["aveElecReq"]] = "aveElecReq"
    uxid: NonEmptyStr
    user_name: NonEmptyStr = Field(alias="userName")


class ElectricityStatsCommand(GatewayCommand):
    command_name: ClassVar[Literal["elecCostQueryReq"]] = "elecCostQueryReq"
    campus_code: NonEmptyStr = Field(alias="xqbm")
    building_code: NonEmptyStr = Field(alias="gylbm")
    room_code: NonEmptyStr = Field(alias="fjbm")


class GatewayEnvelope(BaseModel):
    """描述网关返回的外层信封结构。"""

    code: int | str | None = None
    message: str = ""
    content_raw: str = Field(default="", alias="content")

    @computed_field(return_type=bool)
    @property
    def business_ok(self) -> bool:
        return self.code in {None, 0, "0"}


class GatewayResponse(BaseModel, Generic[ContentModelT]):
    """统一封装 HTTP 状态、业务状态和解析结果。"""

    ok: bool
    status: int
    raw_text: str
    payload: GatewayEnvelope | None = None
    content: ContentModelT | None = None
    error: str | None = None


class UserIdentity(BaseModel):
    """表示网关识别出的用户身份信息。"""

    account: NonEmptyStr
    name: NonEmptyStr
    user_type: str = Field(default="", alias="userType")


class DormitoryProfile(BaseModel):
    """表示宿舍与账户余额相关信息。"""

    user_type: str = Field(..., alias="userType")
    dorm_room_id: str = Field(..., alias="dkRoomId")
    campus_code: str = Field(..., alias="xqbm")
    building_code: str = Field(..., alias="gylbm")
    room_code: str = Field(..., alias="fjbm")
    campus_name: str = Field(..., alias="xqmc")
    building_name: str = Field(..., alias="gylmc")
    room_name: str = Field(..., alias="fjmc")
    left_electricity: str = Field(..., alias="leftElec")
    balance: str = Field(..., alias="balance")
    left_electricity_aircon: str = Field(..., alias="leftElecK")
    balance_aircon: str = Field(..., alias="balanceK")
    subsidy_electricity: str = Field(..., alias="leftBzElec")
    subsidy_balance: str = Field(..., alias="balanceBz")
    average_electricity: str = Field(..., alias="avrElec")
    is_merged: bool = Field(..., alias="isMerge")

    @property
    def dormitory_name(self) -> str:
        return f"{self.campus_name}-{self.building_name}-{self.room_name}"


class AverageElectricity(BaseModel):
    """表示平均用电量查询结果。"""

    average_electricity: str = Field(..., alias="avrElec")


class ElectricityStats(BaseModel):
    """表示剩余电量及统计指标。"""

    left_electricity: str = Field(..., alias="leftElec")
    left_amount: str = Field(..., alias="leftAmount")
    left_days: str = Field(..., alias="leftDays")
    highest_daily_amount: str = Field(..., alias="highestDailyAmount")
    lowest_daily_amount: str = Field(..., alias="lowestDailyAmount")
    average_daily_amount: str = Field(..., alias="averageDailyAmount")
    highest_monthly_amount: str = Field(..., alias="highestMonthlyAmount")
    lowest_monthly_amount: str = Field(..., alias="lowestMonthlyAmount")
    average_monthly_amount: str = Field(..., alias="averageMonthlyAmount")


class ElectricityQueryResult(BaseModel):
    """汇总一次完整查询链路的最终结果。"""

    code: str
    final_url: str
    identity: UserIdentity
    dormitory: DormitoryProfile
    stats: ElectricityStats
    auth_response: GatewayResponse[UserIdentity]
    login_response: GatewayResponse[DormitoryProfile]
    average_response: GatewayResponse[AverageElectricity]
    stats_response: GatewayResponse[ElectricityStats]
