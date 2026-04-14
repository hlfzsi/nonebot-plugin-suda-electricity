"""编排 CAS 登录与宿舍电费查询的高层服务。"""

from typing import Self, TypeVar

from pydantic import BaseModel

from .cas import (
    DEFAULT_LOGIN_URL,
    CaptchaRequiredError,
    CasClient,
    CasCodeResult,
    CasLoginState,
)
from .gateway import DEFAULT_TIMEOUT, JSON_COMMAND_URL, SmartElectricGateway
from .http_pool import DEFAULT_POOL_SIZE, HttpClientLease, HttpClientPool
from .models import (
    ElectricityQueryResult,
    GatewayResponse,
)

ContentModelT = TypeVar("ContentModelT", bound=BaseModel)
_SHARED_CLIENT_POOLS: dict[tuple[int, int], HttpClientPool] = {}


def _get_shared_client_pool(timeout: int, max_pool_size: int) -> HttpClientPool:
    """返回供快捷查询函数复用的共享 HTTP Client 池。"""

    key = (timeout, max_pool_size)
    pool = _SHARED_CLIENT_POOLS.get(key)
    if pool is None:
        pool = HttpClientPool(timeout=timeout, max_size=max_pool_size)
        _SHARED_CLIENT_POOLS[key] = pool
    return pool


async def close_shared_client_pools() -> None:
    """关闭快捷查询函数持有的所有共享连接池。"""

    pools = list(_SHARED_CLIENT_POOLS.values())
    _SHARED_CLIENT_POOLS.clear()
    for pool in pools:
        await pool.aclose()


class SudaElectricityService:
    """串联 CAS 与电费网关的统一服务入口。

    默认情况下，服务会从 ``HttpClientPool`` 中借出一个 ``AsyncClient``，
    让同一条查询管道在整个生命周期内复用同一个连接池、Cookie 容器和会话头。
    """

    def __init__(
        self,
        login_url: str | None = None,
        base_url: str = JSON_COMMAND_URL,
        timeout: int = DEFAULT_TIMEOUT,
        client_pool: HttpClientPool | None = None,
        max_pool_size: int = DEFAULT_POOL_SIZE,
        cas_client: CasClient | None = None,
        gateway: SmartElectricGateway | None = None,
    ) -> None:
        """初始化苏大电费查询服务。

        Args:
            login_url: 自定义 CAS 登录入口。
            base_url: 电费网关命令接口地址。
            timeout: HTTP 请求超时时间，单位为秒。
            client_pool: 可选的共享 HTTP Client 池。
            max_pool_size: 未显式传入连接池时，内部池允许创建的最大 Client 数。
            cas_client: 外部提供的 CAS 客户端。若提供，则会优先复用其底层 Client。
            gateway: 外部提供的网关客户端。若提供，则会优先复用其底层 Client。

        Raises:
            ValueError: 同时传入的 ``cas_client`` 与 ``gateway`` 未共享同一个底层 Client。
        """

        self.login_url = login_url or DEFAULT_LOGIN_URL
        self.base_url = base_url
        self.timeout = timeout
        self._external_clients = cas_client is not None or gateway is not None
        self._owns_client_pool = client_pool is None and not self._external_clients
        self._client_pool = None
        self._active_lease: HttpClientLease | None = None
        self.cas_client: CasClient | None = None
        self.gateway: SmartElectricGateway | None = None

        if self._external_clients:
            self.cas_client, self.gateway = self._wire_external_clients(
                cas_client=cas_client,
                gateway=gateway,
            )
        else:
            self._client_pool = client_pool or HttpClientPool(
                timeout=timeout,
                max_size=max_pool_size,
            )

        self._pending_login_state: CasLoginState | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """释放当前活跃管道，并在需要时关闭连接池。"""

        self._pending_login_state = None
        if self.cas_client is not None:
            await self.cas_client.aclose()
        if self.gateway is not None:
            await self.gateway.aclose()
        await self._release_pipeline()
        if self._client_pool is not None and self._owns_client_pool:
            await self._client_pool.aclose()

    def reset(self) -> None:
        """重置当前活跃会话中的 Cookie 与暂存状态。"""

        if self.cas_client is not None:
            self.cas_client.reset()
        if self.gateway is not None:
            self.gateway.reset()
        self._pending_login_state = None

    @property
    def active_client(self):
        """返回当前活跃管道绑定的底层 HTTP Client。"""

        return None if self.cas_client is None else self.cas_client._client

    @property
    def active_user_agent(self) -> str:
        """返回当前活跃管道正在使用的 User-Agent。"""

        client = self.active_client
        if client is None:
            return ""
        return str(client.headers.get("User-Agent", ""))

    async def prepare_login(self) -> CasLoginState:
        """准备登录流程，并在需要时拉取验证码信息。"""

        cas_client, _ = await self._restart_pipeline()
        self._pending_login_state = await cas_client.prepare_login()
        return self._pending_login_state

    async def fetch_code(
        self,
        username: str,
        password: str,
        captcha: str | None = None,
    ) -> CasCodeResult:
        """获取用于后续网关鉴权的授权码。"""

        login_state = self._pending_login_state or await self.prepare_login()
        cas_client, _ = await self._ensure_pipeline()
        try:
            result = await cas_client.fetch_code(
                username=username,
                password=password,
                captcha=captcha,
                state=login_state,
            )
        except CaptchaRequiredError:
            self._pending_login_state = login_state
            raise

        self._pending_login_state = None
        return result

    async def query_electricity(
        self,
        username: str,
        password: str,
        captcha: str | None = None,
    ) -> ElectricityQueryResult:
        """执行完整的电费查询链路。

        Args:
            username: 学号或统一身份认证账号。
            password: 统一身份认证密码。
            captcha: 可选验证码。

        Returns:
            包含用户身份、宿舍信息与电费统计的聚合结果。
        """

        try:
            code_result = await self.fetch_code(
                username=username,
                password=password,
                captcha=captcha,
            )
            if not code_result.ok:
                raise RuntimeError(
                    "failed to fetch code: "
                    f"{code_result.error_code} {code_result.error_message}".strip()
                )

            _, gateway = await self._ensure_pipeline()
            auth_response = await gateway.auth_redirect(code_result.code)
            identity = self._require_content(auth_response, "authRedirectReq")

            login_response = await gateway.login(identity.account, identity.name)
            dormitory = self._require_content(login_response, "loginReq")

            average_response = await gateway.average_electricity(
                identity.account,
                identity.name,
            )
            average = self._require_content(average_response, "aveElecReq")
            dormitory = dormitory.model_copy(
                update={"average_electricity": average.average_electricity}
            )

            stats_response = await gateway.electricity_stats(
                dormitory.campus_code,
                dormitory.building_code,
                dormitory.room_code,
            )
            stats = self._require_content(stats_response, "elecCostQueryReq")

            return ElectricityQueryResult(
                code=code_result.code,
                final_url=code_result.final_url,
                identity=identity,
                dormitory=dormitory,
                stats=stats,
                auth_response=auth_response,
                login_response=login_response,
                average_response=average_response,
                stats_response=stats_response,
            )
        except CaptchaRequiredError:
            raise
        except Exception:
            await self._release_pipeline()
            raise
        finally:
            if self._pending_login_state is None:
                await self._release_pipeline()

    @staticmethod
    def _require_content(
        response: GatewayResponse[ContentModelT],
        step_name: str,
    ) -> ContentModelT:
        """确保网关响应中包含可用的业务内容。"""

        if not response.ok or response.content is None:
            detail = response.error or response.raw_text or f"status={response.status}"
            raise RuntimeError(f"{step_name} failed: {detail}")
        return response.content

    def _wire_external_clients(
        self,
        cas_client: CasClient | None,
        gateway: SmartElectricGateway | None,
    ) -> tuple[CasClient, SmartElectricGateway]:
        """将外部注入的客户端对齐为共享同一底层 Client 的组合。"""

        if cas_client is not None and gateway is not None:
            if cas_client._client is not gateway._client:
                raise ValueError(
                    "cas_client and gateway must share the same underlying client"
                )
            return cas_client, gateway

        if cas_client is not None:
            return cas_client, SmartElectricGateway(
                base_url=self.base_url,
                timeout=self.timeout,
                client=cas_client._client,
            )

        if gateway is None:
            raise ValueError("either cas_client or gateway must be provided")

        return (
            CasClient(
                login_url=self.login_url,
                timeout=self.timeout,
                client=gateway._client,
            ),
            gateway,
        )

    async def _restart_pipeline(self) -> tuple[CasClient, SmartElectricGateway]:
        """丢弃旧管道并开启一条新的独占查询管道。"""

        if not self._external_clients:
            await self._release_pipeline()
        else:
            self.reset()
        return await self._ensure_pipeline()

    async def _ensure_pipeline(self) -> tuple[CasClient, SmartElectricGateway]:
        """确保当前存在一条可复用的活跃查询管道。"""

        if self.cas_client is not None and self.gateway is not None:
            return self.cas_client, self.gateway

        if self._client_pool is None:
            raise RuntimeError("http client pool is not initialized")

        self._active_lease = await self._client_pool.acquire()
        shared_client = self._active_lease.client
        self.cas_client = CasClient(
            login_url=self.login_url,
            timeout=self.timeout,
            client=shared_client,
        )
        self.gateway = SmartElectricGateway(
            base_url=self.base_url,
            timeout=self.timeout,
            client=shared_client,
        )
        return self.cas_client, self.gateway

    async def _release_pipeline(self) -> None:
        """归还当前池化 Client，并清空派生客户端。"""

        self._pending_login_state = None
        if self._external_clients:
            return

        if self._active_lease is not None:
            await self._active_lease.release()
            self._active_lease = None

        self.cas_client = None
        self.gateway = None


async def query_electricity(
    username: str,
    password: str,
    captcha: str | None = None,
    *,
    login_url: str | None = None,
    base_url: str = JSON_COMMAND_URL,
    timeout: int = DEFAULT_TIMEOUT,
    max_pool_size: int = DEFAULT_POOL_SIZE,
) -> ElectricityQueryResult:
    """以便捷函数形式执行一次完整电费查询。"""

    pool = _get_shared_client_pool(timeout=timeout, max_pool_size=max_pool_size)
    async with SudaElectricityService(
        login_url=login_url,
        base_url=base_url,
        timeout=timeout,
        client_pool=pool,
        max_pool_size=max_pool_size,
    ) as service:
        return await service.query_electricity(
            username=username,
            password=password,
            captcha=captcha,
        )
