"""封装苏大宿舍电费网关的命令式调用。"""

from typing import Self, TypeVar

import httpx
import orjson
from pydantic import BaseModel

from .http_pool import build_http_client
from .models import (
    AuthRedirectCommand,
    AverageElectricity,
    AverageElectricityCommand,
    DormitoryProfile,
    ElectricityStats,
    ElectricityStatsCommand,
    GatewayCommand,
    GatewayEnvelope,
    GatewayResponse,
    LoginCommand,
    UserIdentity,
)

# 登录会自动跳转https
JSON_COMMAND_URL = "http://sder.hqglc.suda.edu.cn/sdzndb/jsp/http/json_command.action"
INDEX_URL = "http://sder.hqglc.suda.edu.cn/sdzndb/webResources/www/index.html"
DEFAULT_TIMEOUT = 15


ContentModelT = TypeVar("ContentModelT", bound=BaseModel)


def _build_http_client(timeout: float) -> httpx.AsyncClient:
    """为独立使用的宿舍电费网关创建默认 HTTP Client。"""

    return build_http_client(timeout)


class SmartElectricGateway:
    """面向电费网关的高层命令客户端。"""

    def __init__(
        self,
        base_url: str = JSON_COMMAND_URL,
        timeout: int = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._client = client or _build_http_client(timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """关闭当前持有的 HTTP Client。"""

        if self._owns_client:
            await self._client.aclose()

    def reset(self) -> None:
        """清理当前会话中的 Cookie。"""

        self._client.cookies.clear()

    async def post_command(
        self,
        command: GatewayCommand,
        content_model: type[ContentModelT],
    ) -> GatewayResponse[ContentModelT]:
        """向电费网关发送命令并解析结构化响应。

        Args:
            command: 要发送的网关命令对象。
            content_model: ``content`` 字段对应的 Pydantic 模型。

        Returns:
            统一封装后的网关响应对象。
        """

        try:
            response = await self._client.post(
                self.base_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "http://sder.hqglc.suda.edu.cn",
                    "Referer": INDEX_URL,
                },
                files={"strTemp": (None, command.as_form_fields()["strTemp"])},
            )
        except httpx.HTTPError as exc:
            return GatewayResponse(
                ok=False,
                status=0,
                raw_text="",
                payload=None,
                content=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        raw_text = response.text
        if not raw_text.strip():
            return GatewayResponse(
                ok=False,
                status=response.status_code,
                raw_text=raw_text,
                payload=None,
                content=None,
                error="empty response body",
            )

        try:
            payload_raw = orjson.loads(response.content)
        except orjson.JSONDecodeError as exc:
            return GatewayResponse(
                ok=False,
                status=response.status_code,
                raw_text=raw_text,
                payload=None,
                content=None,
                error=f"invalid JSON response: {exc}",
            )

        if not isinstance(payload_raw, dict):
            return GatewayResponse(
                ok=False,
                status=response.status_code,
                raw_text=raw_text,
                payload=None,
                content=None,
                error="gateway returned a non-object JSON payload",
            )

        envelope = GatewayEnvelope.model_validate(
            {
                "code": payload_raw.get("code"),
                "message": payload_raw.get("message") or "",
                "content": payload_raw.get("content") or "",
            }
        )
        content: ContentModelT | None = None
        if envelope.content_raw:
            try:
                content_raw = orjson.loads(envelope.content_raw)
            except orjson.JSONDecodeError as exc:
                return GatewayResponse(
                    ok=False,
                    status=response.status_code,
                    raw_text=raw_text,
                    payload=envelope,
                    content=None,
                    error=f"invalid gateway content JSON: {exc}",
                )

            if not isinstance(content_raw, dict):
                return GatewayResponse(
                    ok=False,
                    status=response.status_code,
                    raw_text=raw_text,
                    payload=envelope,
                    content=None,
                    error="gateway content payload is not an object",
                )

            try:
                content = content_model.model_validate(content_raw)
            except Exception as exc:
                return GatewayResponse(
                    ok=False,
                    status=response.status_code,
                    raw_text=raw_text,
                    payload=envelope,
                    content=None,
                    error=f"invalid {content_model.__name__} payload: {exc}",
                )

        error = (
            None
            if envelope.business_ok
            else envelope.message or "gateway business error"
        )
        return GatewayResponse(
            ok=response.is_success and envelope.business_ok and content is not None,
            status=response.status_code,
            raw_text=raw_text,
            payload=envelope,
            content=content,
            error=error,
        )

    async def auth_redirect(self, code: str) -> GatewayResponse[UserIdentity]:
        """调用鉴权跳转接口，换取用户身份信息。"""

        return await self.post_command(AuthRedirectCommand(code=code), UserIdentity)

    async def login(
        self, uxid: str, user_name: str
    ) -> GatewayResponse[DormitoryProfile]:
        """登录宿舍电费系统并获取宿舍档案。"""

        return await self.post_command(
            LoginCommand(uxid=uxid, userName=user_name),
            DormitoryProfile,
        )

    async def average_electricity(
        self,
        uxid: str,
        user_name: str,
    ) -> GatewayResponse[AverageElectricity]:
        """查询宿舍的平均用电量。"""

        return await self.post_command(
            AverageElectricityCommand(uxid=uxid, userName=user_name),
            AverageElectricity,
        )

    async def electricity_stats(
        self,
        campus_code: str,
        building_code: str,
        room_code: str,
    ) -> GatewayResponse[ElectricityStats]:
        """查询宿舍当前剩余电量与统计信息。"""

        return await self.post_command(
            ElectricityStatsCommand(
                xqbm=campus_code.replace(" ", ""),
                gylbm=building_code,
                fjbm=room_code,
            ),
            ElectricityStats,
        )
