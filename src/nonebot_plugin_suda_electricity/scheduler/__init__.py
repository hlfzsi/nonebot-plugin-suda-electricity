__all__ = [
    "DormitoryCheckDueEvent",
    "SchedulerDispatchReport",
    "DormitoryScheduleObserver",
    "ObserverDispatchResult",
    "DormitoryScheduleObserverRegistry",
    "DormitorySchedulerService",
    "compute_initial_check_at",
    "compute_next_check_at",
    "scheduler_observer_registry",
    "dormitory_scheduler",
    "start_scheduler",
    "stop_scheduler",
]

from ..config import APP_CONFIG
from ..db import dormitory_repo
from .models import DormitoryCheckDueEvent, SchedulerDispatchReport
from .observer import (
    DormitoryScheduleObserver,
    DormitoryScheduleObserverRegistry,
    ObserverDispatchResult,
)
from .schedule import compute_initial_check_at, compute_next_check_at
from .service import DormitorySchedulerService

scheduler_observer_registry = DormitoryScheduleObserverRegistry()
dormitory_scheduler = DormitorySchedulerService(
    dormitory_repository=dormitory_repo,
    observer_registry=scheduler_observer_registry,
    config=APP_CONFIG,
)


async def start_scheduler() -> None:
    await dormitory_scheduler.start()


async def stop_scheduler() -> None:
    await dormitory_scheduler.stop()
