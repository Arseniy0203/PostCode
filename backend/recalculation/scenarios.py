# Ответственный: Человек 2 (recalculation + risk)
"""Демо-сценарии факта по спринтам.

Датасет содержит только срез на Неделе 0, фактов внутри квартала нет,
поэтому факт генерируется из самого плана. Задача и инженер для сценария
выбираются по структуре плана, а не по зашитым id — сценарии работают на
любом датасете того же формата.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.recalculation.plan_fields import (
    end_sprint,
    field,
    is_done,
    is_included,
    start_sprint,
    task_id,
)
from backend.recalculation.recalc import Planner, apply_sprint_fact
from backend.risk.alerts import downstream, dependency_graph

SCENARIOS: dict[str, str] = {
    "on_track": "Всё по плану: каждая задача закрывается в свой плановый спринт.",
    "key_task_delay": (
        "Задержка ключевой задачи: задача, от которой зависит больше всего других, "
        "в спринте-триггере сделана только наполовину."
    ),
    "engineer_leave": (
        "Уход инженера: самый загруженный по плану инженер недоступен "
        "со следующего спринта после триггера."
    ),
}


def _allocation(task: Any, sprint: int) -> dict[str, float]:
    for key, hours in (field(task, "allocation", default={}) or {}).items():
        if int(key) == sprint:
            return dict(hours)
    return {}


def _active(task: Any, sprint: int) -> bool:
    end = end_sprint(task)
    start = start_sprint(task) or end
    return (not is_done(task) and is_included(task) and end is not None
            and start is not None and start <= sprint <= end)


def planned_fact(plan: dict[str, Any], sprint: int, engineers: list[dict[str, Any]]) -> dict[str, Any]:
    """Факт «ровно по плану»: часы из allocation, Done — в спринт окончания."""
    role_of = {e["engineer_id"]: e.get("role", "") for e in engineers}
    tasks: dict[str, Any] = {}
    for task in plan["current"]:
        if not _active(task, sprint):
            continue
        spent: dict[str, float] = defaultdict(float)
        for eng_id, hours in _allocation(task, sprint).items():
            spent[role_of.get(eng_id, eng_id)] += float(hours)
        tasks[task_id(task)] = {
            "status": "Done" if end_sprint(task) == sprint else "InProgress",
            "spent_hh": dict(spent),
        }
    return {"tasks": tasks}


def pick_key_task(plan: dict[str, Any], sprint: int, dependencies: list[dict[str, Any]]) -> str | None:
    """Активная в спринте задача с самой длинной цепочкой зависимых задач."""
    active = [t for t in plan["current"] if _active(t, sprint)]
    if not active:
        return None
    children, _ = dependency_graph(dependencies, {task_id(t) for t in plan["current"]})

    def weight(task: Any) -> tuple[int, float]:
        return len(downstream(children, task_id(task))), float(field(task, "rung", default=0) or 0)

    return task_id(max(active, key=weight))


def pick_busiest_engineer(plan: dict[str, Any], from_sprint: int) -> str | None:
    load: dict[str, float] = defaultdict(float)
    for task in plan["current"]:
        if is_done(task):
            continue
        for key, hours in (field(task, "allocation", default={}) or {}).items():
            if int(key) >= from_sprint:
                for eng_id, h in dict(hours).items():
                    load[eng_id] += float(h)
    return max(load, key=load.get) if load else None


def build_fact(
    plan: dict[str, Any],
    scenario: str,
    sprint: int,
    *,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    trigger_sprint: int = 1,
) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"Неизвестный сценарий {scenario!r}; есть: {', '.join(SCENARIOS)}")
    fact = planned_fact(plan, sprint, engineers)
    if sprint != trigger_sprint:
        return fact

    if scenario == "key_task_delay":
        tid = pick_key_task(plan, sprint, dependencies)
        if tid:
            entry = fact["tasks"].setdefault(tid, {"spent_hh": {}})
            entry["status"] = "InProgress"
            entry["spent_hh"] = {r: h / 2 for r, h in entry["spent_hh"].items()}
            fact["scenario_target"] = tid
    elif scenario == "engineer_leave":
        eng_id = pick_busiest_engineer(plan, sprint + 1)
        if eng_id:
            fact["engineers_unavailable"] = [eng_id]
            fact["scenario_target"] = eng_id
    return fact


def simulate(
    plan: dict[str, Any],
    scenario: str,
    until_sprint: int,
    *,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    capacity: dict[str, Any],
    trigger_sprint: int = 1,
    planner: Planner | None = None,
) -> dict[str, Any]:
    """Прогоняет сценарий спринт за спринтом до until_sprint включительно."""
    while plan.get("current_sprint", 0) < until_sprint:
        sprint = plan.get("current_sprint", 0) + 1
        fact = build_fact(plan, scenario, sprint, dependencies=dependencies,
                          engineers=engineers, trigger_sprint=trigger_sprint)
        apply_sprint_fact(plan, fact, sprint, dependencies=dependencies,
                          engineers=engineers, capacity=capacity, planner=planner)
    return plan
