"""Единый доступ к полям задачи плана для пересчёта, алертов и KPI-обвязки.

Планировщик, парсер и контракт пока называют поля по-разному
(planned_end_sprint / planned_end, prodf_id / «Номер инициативы»),
поэтому все чтения идут через эти функции.
"""

from __future__ import annotations

from typing import Any

from backend.explainability.reasons import QUARTER_SPRINTS

# Больше этого числа номер спринта быть не может — значит, это дата Excel.
MAX_SPRINT = 60

__all__ = [
    "QUARTER_SPRINTS",
    "MAX_SPRINT",
    "as_sprint",
    "field",
    "task_id",
    "prodf_id",
    "team_id",
    "is_done",
    "is_included",
    "start_sprint",
    "end_sprint",
    "planned_end_sprint",
    "forecast_end_sprint",
    "actual_end_sprint",
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


def as_sprint(value: Any) -> int | None:
    """Приводит значение к номеру спринта или возвращает None.

    В сырых задачах planned_end/actual_end содержат даты Excel (46097.45),
    datetime или строки, а из pandas приходят numpy-числа. Номером спринта
    считаем только целое в диапазоне 1..MAX_SPRINT: иначе дата 46097
    проедет как «спринт 46097» и задача будет выглядеть завершённой
    далеко за пределами квартала.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
        if number != float(value):
            return None
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= MAX_SPRINT else None


def _sprint_value(task: Any, *names: str) -> int | None:
    for name in names:
        number = as_sprint(field(task, name))
        if number is not None:
            return number
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


def planned_end_sprint(task: Any) -> int | None:
    """Плановый спринт окончания (из baseline) — знаменатель Say/Do."""
    return _sprint_value(task, "planned_end_sprint", "planned_end")


def forecast_end_sprint(task: Any) -> int | None:
    """Прогнозный спринт окончания, без подмены плановым."""
    return _sprint_value(task, "forecast_end_sprint", "forecast_end")


def actual_end_sprint(task: Any) -> int | None:
    """Фактический спринт завершения — числитель Say/Do и факт для KPI."""
    return _sprint_value(task, "actual_end_sprint", "actual_end")


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


def link_dependencies(tasks: list[dict[str, Any]], dependencies: Any) -> list[dict[str, Any]]:
    """Проставляет задачам плана blocked_by / blocks — для карточки задачи.

    Связи на задачи вне плана (призраки) не показываются: их уже нет в графе.
    """
    ids = {task_id(t) for t in tasks}
    blocked_by: dict[str, list[str]] = {}
    blocks: dict[str, list[str]] = {}
    for dep in dependencies or []:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        if a in ids and b in ids and a != b:
            blocked_by.setdefault(b, []).append(a)
            blocks.setdefault(a, []).append(b)
    for task in tasks:
        tid = task_id(task)
        task["blocked_by"] = sorted(set(blocked_by.get(tid, [])))
        task["blocks"] = sorted(set(blocks.get(tid, [])))
    return tasks
