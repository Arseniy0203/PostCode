# Ответственный: Человек 1 (ingestion + planning)
"""Датаклассы доменных сущностей ingestion-слоя.

Поля соответствуют contracts/task_schema.md. Только объявления полей
с type hints, без логики и валидации.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
class Dependency:
    task_id: str
    depends_on_task_id: str


@dataclass
class Engineer:
    engineer_id: str
    team_id: str
    capacity_rate: float
    skills_declared: list[str] = field(default_factory=list)


@dataclass
class TeamHistorySnapshot:
    team_id: str
    sprint_number: int
    velocity_sp: float
