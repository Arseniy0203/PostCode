# Ответственный: Человек 2 (recalculation + risk)
"""Единый доступ к полям задачи плана для пересчёта, алертов и KPI-обвязки.

Планировщик, парсер и контракт пока называют поля по-разному
(planned_end_sprint / planned_end, prodf_id / «Номер инициативы»),
поэтому все чтения идут через эти функции.
"""

from __future__ import annotations

from typing import Any

from backend.explainability.reasons import QUARTER_SPRINTS

__all__ = [
    "QUARTER_SPRINTS",
    "field",
    "task_id",
    "prodf_id",
    "team_id",
    "is_done",
    "is_included",
    "start_sprint",
    "end_sprint",
    "normalize_role",
    "remaining_hh",
    "current_sprint",
]


def field(task: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = task.get(name) if isinstance(task, dict) else getattr(task, name, None)
        if value is not None:
            return value
    return default


def _sprint_value(task: Any, *names: str) -> int | None:
    # planned_end в сырых задачах — дата-строка из Excel, а не номер спринта.
    for name in names:
        value = field(task, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def task_id(task: Any) -> str:
    return str(field(task, "task_id", default=""))


def prodf_id(task: Any) -> str:
    return str(field(task, "prodf_id", "Номер инициативы", default=""))


def team_id(task: Any) -> str:
    return str(field(task, "team_id", default=""))


def is_done(task: Any) -> bool:
    return str(field(task, "status", default="")).casefold() == "done"


def is_included(task: Any) -> bool:
    return field(task, "decision") == "included"


def start_sprint(task: Any) -> int | None:
    return _sprint_value(task, "planned_start_sprint", "planned_start")


def end_sprint(task: Any) -> int | None:
    """Прогнозный спринт окончания; если прогноза нет — плановый."""
    return _sprint_value(
        task, "forecast_end_sprint", "forecast_end", "planned_end_sprint", "planned_end"
    )


def normalize_role(role: str) -> str:
    """Ключ роли: «ДевОпс»/«Девопс», «iOS»/«IOS», «Big Data»/«BigData» совпадают."""
    return "".join(str(role).split()).casefold()


def remaining_hh(task: Any) -> dict[str, tuple[str, float]]:
    """Остаток часов по ролям: {ключ_роли: (название роли, часы)}.

    Остаток = смета (estimated_hh) − потрачено (spent_hh), не меньше нуля.
    """
    estimated = field(task, "estimated_hh", default={}) or {}
    spent = field(task, "spent_hh", default={}) or {}
    if not isinstance(estimated, dict):
        return {}
    spent_by_key = {normalize_role(r): float(h or 0) for r, h in dict(spent).items()}
    result: dict[str, tuple[str, float]] = {}
    for role, hours in estimated.items():
        key = normalize_role(role)
        left = float(hours or 0) - spent_by_key.get(key, 0.0)
        if left > 0:
            name, prev = result.get(key, (role, 0.0))
            result[key] = (name, prev + left)
    return result


def current_sprint(plan: Any) -> int:
    return int(field(plan, "current_sprint", default=0) or 0)
