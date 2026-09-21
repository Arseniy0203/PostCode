# Ответственный: Человек 2 (recalculation + risk)
"""Пересчёт плана по факту выполнения спринта.

Формат факта за спринт:
    {
        "tasks": {task_id: {"status": "Done" | "InProgress",
                            "spent_hh": {роль: часы, потраченные В ЭТОМ спринте}}},
        "engineers_unavailable": [engineer_id, ...],   # с следующего спринта
    }

Факт принимается строго по одному спринту и по порядку (1, 2, ...): система
не может увидеть будущее, а повторная подача того же спринта отклоняется.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from backend.explainability.reasons import explain_shift
from backend.recalculation.plan_fields import (
    QUARTER_SPRINTS,
    current_sprint,
    end_sprint,
    field,
    is_done,
    is_included,
    normalize_role,
    prodf_id,
    remaining_hh,
    task_id,
)
from backend.recalculation.quarter import sprint_label
from backend.risk.alerts import collect_alerts

Planner = Callable[..., dict[str, Any]]

PLANNING_FIELDS = (
    "decision",
    "reason_code",
    "reason_detail",
    "planned_start_sprint",
    "planned_end_sprint",
    "forecast_end_sprint",
    "allocation",
    "assigned_engineers",
)
_SERVICE_COLUMNS = {normalize_role("task_id"), normalize_role("Исходное время")}
_TOTAL_ROW = normalize_role("Итого по ролям")


class SprintOrderError(ValueError):
    """Факт подан не за следующий по порядку спринт."""


def prepare_tasks(
    tasks: list[dict[str, Any]],
    estimates: list[dict[str, Any]],
    spent_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Готовит задачи к планированию и пересчёту.

    estimated_hh заменяется разбивкой по ролям из калькулятора сметы (ответ
    экспертов: калькулятор — целевая смета), spent_hh — факт по ролям из
    Spent_time_roles для задач в работе.
    """
    by_task: dict[str, dict[str, float]] = {}
    for row in estimates:
        hours = float(row.get("hours") or 0)
        if hours > 0 and normalize_role(row.get("role", "")) != _TOTAL_ROW:
            roles = by_task.setdefault(str(row.get("task_id")), {})
            roles[row["role"]] = roles.get(row["role"], 0.0) + hours

    spent_by_task: dict[str, dict[str, float]] = {}
    for row in spent_rows or []:
        spent_by_task[str(row.get("task_id"))] = {
            role: float(hours)
            for role, hours in row.items()
            if normalize_role(role) not in _SERVICE_COLUMNS and hours
        }

    prepared = []
    for task in tasks:
        item = copy.deepcopy(task)
        tid = task_id(task)
        item["estimated_hh"] = dict(by_task.get(tid, {}))
        item["spent_hh"] = dict(spent_by_task.get(tid, {}))
        prepared.append(item)
    return prepared


def _default_planner() -> Planner:
    from backend.planning.scheduler import build_baseline_plan

    return build_baseline_plan


def _apply_task_fact(task: dict[str, Any], fact: dict[str, Any], sprint: int) -> None:
    spent = dict(task.get("spent_hh") or {})
    for role, hours in (fact.get("spent_hh") or {}).items():
        spent[role] = spent.get(role, 0.0) + float(hours or 0)
    task["spent_hh"] = spent

    status = fact.get("status")
    if status:
        task["status"] = status
    if is_done(task):
        task["actual_end_sprint"] = sprint
        task["forecast_end_sprint"] = sprint
        # Часы, заложенные на будущие спринты, освобождаются для хвоста.
        task["allocation"] = {
            s: hours for s, hours in (task.get("allocation") or {}).items() if int(s) <= sprint
        }
    if task.get("actual_start_sprint") is None and (is_done(task) or fact.get("spent_hh")):
        task["actual_start_sprint"] = sprint


def _tail_input(task: dict[str, Any]) -> dict[str, Any]:
    """Незавершённая задача для планировщика: остаток часов и пропорциональный остаток SP."""
    item = {k: v for k, v in task.items() if k not in PLANNING_FIELDS}
    left = remaining_hh(task)
    item["estimated_hh"] = {name: hours for name, hours in left.values()}
    total = sum(float(h or 0) for h in (task.get("estimated_hh") or {}).values())
    if total > 0:
        sp = float(task.get("estimation_sp") or 0)
        item["estimation_sp"] = round(sp * sum(h for _, h in left.values()) / total, 2)
    return item


def _changes(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    sprint: int,
) -> list[dict[str, Any]]:
    """Что изменилось в плане и почему — для блока «что поменялось» в UI."""
    before = {task_id(t): t for t in previous}
    blockers: dict[str, list[str]] = {}
    for dep in dependencies:
        blockers.setdefault(dep.get("blocked_task_id"), []).append(dep.get("blocking_task_id"))

    shifted: dict[str, tuple[int, int]] = {}
    for task in current:
        old_task = before.get(task_id(task))
        if old_task is None or is_done(task):
            continue
        old, new = end_sprint(old_task), end_sprint(task)
        if is_included(old_task) and is_included(task) and old and new and old != new:
            shifted[task_id(task)] = (old, new)

    changes: list[dict[str, Any]] = []
    for task in current:
        tid = task_id(task)
        old_task = before.get(tid)
        if old_task is None:
            continue
        if is_done(task) and not is_done(old_task):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "done",
                "detail": f"{tid} закрыта в {sprint_label(sprint)}.",
            })
        elif tid in shifted:
            old, new = shifted[tid]
            cause = next((b for b in blockers.get(tid, []) if b in shifted), None)
            if cause:
                text = None
            elif new < old:
                text = "освободилась ёмкость команды"
            elif old <= sprint:
                text = f"задача не закрыта в плановом спринте {old}"
            else:
                text = "при пересчёте хвоста изменилась доступная ёмкость"
            changes.append({"kind": "shift", **explain_shift(
                tid, old, new, prodf_id=prodf_id(task), cause_task_id=cause, cause_text=text
            ).to_dict()})
        elif field(old_task, "decision") != field(task, "decision"):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "decision",
                "old": field(old_task, "decision"), "new": field(task, "decision"),
                "detail": (
                    f"Решение по {tid}: {field(old_task, 'decision')} → {field(task, 'decision')}. "
                    f"{field(task, 'reason_detail', default='')}"
                ).strip(),
            })
    return changes


def apply_sprint_fact(
    plan: dict[str, Any],
    fact: dict[str, Any],
    sprint: int | None = None,
    *,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    capacity: dict[str, Any],
    planner: Planner | None = None,
) -> dict[str, Any]:
    """Применяет факт спринта и перепланирует незавершённый хвост.

    Done-задачи фиксируются (actual_end_sprint), остальные планируются заново
    с sprint + 1 на остаток часов. baseline не меняется. В plan пишутся
    current, current_sprint, alerts, warnings и снапшот в history.

    Raises:
        ValueError: baseline не зафиксирован или квартал закончился.
        SprintOrderError: подан не следующий по порядку спринт.
    """
    if not plan.get("baseline_locked"):
        raise ValueError("Сначала зафиксируйте baseline через save_baseline")
    expected = current_sprint(plan) + 1
    sprint = expected if sprint is None else sprint
    if sprint != expected:
        raise SprintOrderError(f"Ожидается факт за спринт {expected}, получен за {sprint}")
    if sprint > QUARTER_SPRINTS:
        raise ValueError(f"Квартал закончился: спринтов всего {QUARTER_SPRINTS}")

    previous = copy.deepcopy(plan["current"])
    tasks = {task_id(t): copy.deepcopy(t) for t in plan["current"]}
    warnings: list[str] = []
    for tid, task_fact in (fact.get("tasks") or {}).items():
        task = tasks.get(tid)
        if task is None:
            warnings.append(f"Факт по {tid} пропущен: такой задачи нет в плане.")
        elif is_done(task):
            warnings.append(f"Факт по {tid} пропущен: задача уже закрыта.")
        else:
            _apply_task_fact(task, task_fact, sprint)

    gone = set(fact.get("engineers_unavailable") or []) | set(plan.get("engineers_unavailable") or [])
    active_engineers = [e for e in engineers if e.get("engineer_id") not in gone]
    active_capacity = copy.deepcopy(capacity)
    for eng_id in gone:
        active_capacity.setdefault("engineer_hh", {})[eng_id] = 0.0

    locked = [tid for tid, t in tasks.items() if is_done(t)]
    tail = [t if is_done(t) else _tail_input(t) for t in tasks.values()]
    result = (planner or _default_planner())(
        tail,
        active_capacity,
        dependencies,
        active_engineers,
        estimates=None,
        start_sprint=sprint + 1,
        locked_tasks=locked,
    )
    planned = {task_id(t): t for t in result.get("current") or []}

    current = []
    for tid, task in tasks.items():
        merged = copy.deepcopy(task)
        if not is_done(task) and tid in planned:
            for key in PLANNING_FIELDS:
                merged[key] = copy.deepcopy(planned[tid].get(key))
        current.append(merged)

    plan["current"] = current
    plan["current_sprint"] = sprint
    plan["engineers_unavailable"] = sorted(gone)
    alerts = collect_alerts(plan, dependencies, active_engineers, reference=previous)
    plan["alerts"] = alerts
    plan["warnings"] = warnings
    plan.setdefault("history", []).append({
        "sprint": sprint,
        "fact": copy.deepcopy(fact),
        "current": copy.deepcopy(current),
        "changes": _changes(previous, current, dependencies, sprint),
        "alerts": [a.to_dict() for a in alerts],
        "warnings": list(warnings),
    })
    return plan
