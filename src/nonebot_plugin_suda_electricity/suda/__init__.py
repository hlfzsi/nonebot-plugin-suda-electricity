"""对外导出苏大电费查询的主要模型与服务。"""

from .cas import CaptchaRequiredError, CasCodeResult, CasLoginState
from .http_pool import HttpClientPool
from .models import (
    AverageElectricity,
    DormitoryProfile,
    ElectricityQueryResult,
    ElectricityStats,
    GatewayEnvelope,
    GatewayResponse,
    UserIdentity,
)
from .service import SudaElectricityService, close_shared_client_pools, query_electricity

__all__ = [
    "AverageElectricity",
    "CaptchaRequiredError",
    "CasCodeResult",
    "CasLoginState",
    "DormitoryProfile",
    "ElectricityQueryResult",
    "ElectricityStats",
    "GatewayEnvelope",
    "GatewayResponse",
    "HttpClientPool",
    "SudaElectricityService",
    "UserIdentity",
    "close_shared_client_pools",
    "query_electricity",
]
