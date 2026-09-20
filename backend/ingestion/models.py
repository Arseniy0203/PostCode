from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

@dataclass
class Task:
    task_id: str
    parent_id: str | None
    prodf_id: str
    team_id: str
    status: str
    rung: str
    estimation_sp: float
    estimated_hh: dict[str, float] = field(default_factory=dict)

@dataclass
class PlannedTask(Task):
    planned_start: int | None = None
    planned_end: int | None = None
    actual_start: int | None = None
    actual_end: int | None = None
    sprint_number: int | None = None
    decision: Literal["included", "postponed", "cancelled"] | None = None
    reason_code: str | None = None
    reason_detail: str | None = None

@dataclass
class Plan:
    baseline: list[PlannedTask] = field(default_factory=list)
    current: list[PlannedTask] = field(default_factory=list)

@dataclass
class Dependency:
    blocking_task_id: str
    blocked_task_id: str
    dependency_type: str

@dataclass
class Engineer:
    engineer_id: str
    team_id: str
    role: str
    grade: str
    capacity_rate: float
    skills_declared: list[str] = field(default_factory=list)

@dataclass
class TeamHistorySnapshot:
    team_id: str
    sprint_number: int
    velocity_sp: float