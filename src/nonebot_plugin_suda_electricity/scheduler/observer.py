__all__ = [
    "DormitoryScheduleObserver",
    "ObserverDispatchResult",
    "DormitoryScheduleObserverRegistry",
]

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..utils import logger
from .models import DormitoryCheckDueEvent

DormitoryScheduleObserver = Callable[[DormitoryCheckDueEvent], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class ObserverDispatchResult:
    observer_calls: int = 0
    observer_failures: int = 0


class DormitoryScheduleObserverRegistry:
    def __init__(self) -> None:
        self._observers: list[DormitoryScheduleObserver] = []

    def register(
        self, observer: DormitoryScheduleObserver
    ) -> DormitoryScheduleObserver:
        if observer not in self._observers:
            self._observers.append(observer)
        return observer

    def unregister(self, observer: DormitoryScheduleObserver) -> None:
        if observer in self._observers:
            self._observers.remove(observer)

    def list(self) -> tuple[DormitoryScheduleObserver, ...]:
        return tuple(self._observers)

    async def notify(self, event: DormitoryCheckDueEvent) -> ObserverDispatchResult:
        observers = self.list()
        failures = 0

        for observer in observers:
            try:
                await observer(event)
            except Exception:
                failures += 1
                logger.exception("Dormitory scheduler observer failed")

        return ObserverDispatchResult(
            observer_calls=len(observers),
            observer_failures=failures,
        )

