"""为苏大电费查询流程提供可复用的 HTTP Client 池。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fake_useragent import UserAgent

DEFAULT_POOL_SIZE = 4
_USER_AGENT_FACTORY = UserAgent()


def build_user_agent() -> str:
    """生成一个伪装的浏览器 User-Agent。

    Returns:
        由 ``fake_useragent`` 随机产生的 User-Agent 字符串。
    """

    return _USER_AGENT_FACTORY.random


def build_http_client(timeout: float) -> httpx.AsyncClient:
    """构造默认配置的异步 HTTP Client。

    Args:
        timeout: 请求超时时间，单位为秒。

    Returns:
        已设置超时、自动跟随重定向和随机 User-Agent 的 ``AsyncClient``。
    """

    return httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": build_user_agent()},
        timeout=httpx.Timeout(timeout),
    )


@dataclass(slots=True)
class HttpClientLease:
    """表示一次从池中借出的独占 Client。"""

    pool: "HttpClientPool"
    client: httpx.AsyncClient
    released: bool = False

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, *_: object) -> None:
        await self.release()

    async def release(self) -> None:
        """归还当前借出的 Client。"""

        if self.released:
            return
        self.released = True
        await self.pool._release(self.client)


class HttpClientPool:
    """管理可复用的异步 HTTP Client。

    同一时刻，同一个 ``AsyncClient`` 只会借给一条查询管道使用。
    管道结束后，池会清理 Cookie 并回收该 Client。
    """

    def __init__(
        self,
        timeout: float,
        max_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        """初始化 HTTP Client 池。

        Args:
            timeout: 新建 Client 时使用的超时时间，单位为秒。
            max_size: 池允许创建的最大 Client 数量。

        Raises:
            ValueError: ``max_size`` 小于 1。
        """

        if max_size < 1:
            raise ValueError("max_size must be greater than 0")

        self.timeout = timeout
        self.max_size = max_size
        self._available: list[httpx.AsyncClient] = []
        self._condition = asyncio.Condition()
        self._all_clients: set[httpx.AsyncClient] = set()
        self._created = 0
        self._closing = False

    async def acquire(self) -> HttpClientLease:
        """借出一个 Client 供单条管道独占使用。

        Returns:
            一个可显式释放的 ``HttpClientLease``。

        Raises:
            RuntimeError: 连接池已经关闭。
        """

        return HttpClientLease(pool=self, client=await self._checkout())

    @asynccontextmanager
    async def session(self) -> AsyncIterator[httpx.AsyncClient]:
        """以异步上下文方式借用一个 Client。"""

        lease = await self.acquire()
        try:
            yield lease.client
        finally:
            await lease.release()

    async def aclose(self) -> None:
        """关闭池中所有空闲 Client，并在后续归还时关闭活跃 Client。"""

        async with self._condition:
            self._closing = True
            clients_to_close = self._available
            self._available = []
            self._condition.notify_all()

        for client in clients_to_close:
            await client.aclose()
            self._all_clients.discard(client)

        self._created = len(self._all_clients)

    async def _checkout(self) -> httpx.AsyncClient:
        async with self._condition:
            if self._closing:
                raise RuntimeError("http client pool is closed")

            while True:
                if self._available:
                    client = self._available.pop()
                    client.headers["User-Agent"] = build_user_agent()
                    return client

                if self._created < self.max_size:
                    client = build_http_client(self.timeout)
                    self._all_clients.add(client)
                    self._created += 1
                    return client

                await self._condition.wait()
                if self._closing:
                    raise RuntimeError("http client pool is closed")

    async def _release(self, client: httpx.AsyncClient) -> None:
        client.cookies.clear()
        should_close = False
        async with self._condition:
            if client not in self._all_clients:
                return

            if self._closing:
                self._all_clients.discard(client)
                self._created = len(self._all_clients)
                self._condition.notify_all()
                should_close = True
            else:
                self._available.append(client)
                self._condition.notify(1)

        if should_close:
            await client.aclose()
