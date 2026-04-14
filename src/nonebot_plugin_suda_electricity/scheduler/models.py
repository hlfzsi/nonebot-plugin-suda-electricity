__all__ = ["DormitoryCheckDueEvent", "SchedulerDispatchReport"]

from dataclasses import dataclass

from ..db import DormitoryDetail


@dataclass(slots=True, frozen=True)
class DormitoryCheckDueEvent:
    dormitory: DormitoryDetail
    dispatched_at: int
    next_check_at: int


@dataclass(slots=True)
class SchedulerDispatchReport:
    checked_dormitories: int = 0
    dispatched_events: int = 0
    observer_calls: int = 0
    observer_failures: int = 0
    skipped: bool = False

