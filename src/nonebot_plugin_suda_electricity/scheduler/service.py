__all__ = ["DormitorySchedulerService"]

import asyncio
import contextlib
import time
from typing import Awaitable, Callable, Protocol

from ..config import Config
from ..db import Dormitory, DormitoryDetail
from ..utils import logger
from .models import DormitoryCheckDueEvent, SchedulerDispatchReport
from .observer import DormitoryScheduleObserverRegistry
from .schedule import compute_next_check_at


class DormitoryScheduleRepository(Protocol):
    async def list_due_details(self, *, now: int, limit: int) -> list[DormitoryDetail]: ...

    async def update_check_schedule(
        self,
        *,
        dormitory_key: str,
        last_check_at: int,
        next_check_at: int,
    ) -> Dormitory | None: ...


class DormitorySchedulerService:
    def __init__(
        self,
        *,
        dormitory_repository: DormitoryScheduleRepository,
        observer_registry: DormitoryScheduleObserverRegistry,
        config: Config,
        now_provider: Callable[[], int] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._dormitory_repository = dormitory_repository
        self._observer_registry = observer_registry
        self._config = config
        self._now_provider = now_provider or (lambda: int(time.time()))
        self._sleep_func = sleep_func or asyncio.sleep
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_forever(),
            name="suda-dormitory-scheduler",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return

        self._stop_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def run_once(self) -> SchedulerDispatchReport:
        if self._run_lock.locked():
            return SchedulerDispatchReport(skipped=True)

        async with self._run_lock:
            now = self._now_provider()
            due_dormitories = await self._dormitory_repository.list_due_details(
                now=now,
                limit=self._config.suda_scheduler_due_limit,
            )
            report = SchedulerDispatchReport(
                checked_dormitories=len(due_dormitories),
            )

            for dormitory_detail in due_dormitories:
                next_check_at = compute_next_check_at(
                    from_timestamp=now,
                    interval_hours=self._config.suda_scheduler_interval_hours,
                )
                updated = await self._dormitory_repository.update_check_schedule(
                    dormitory_key=dormitory_detail.dormitory.dormitory_key,
                    last_check_at=now,
                    next_check_at=next_check_at,
                )
                if updated is None:
                    continue

                event = DormitoryCheckDueEvent(
                    dormitory=dormitory_detail,
                    dispatched_at=now,
                    next_check_at=next_check_at,
                )
                dispatch_result = await self._observer_registry.notify(event)
                report.dispatched_events += 1
                report.observer_calls += dispatch_result.observer_calls
                report.observer_failures += dispatch_result.observer_failures

            return report

    async def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dormitory scheduler tick failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.suda_scheduler_tick_seconds,
                )
            except TimeoutError:
                continue

