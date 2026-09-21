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
    remaining_hh,
    start_sprint,
    task_id,
    team_id,
)
from backend.recalculation.recalc import Planner, apply_sprint_fact
from backend.risk.alerts import role_supply, dependency_graph, downstream

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
    "early_finish": (
        "Закрыли раньше срока: самая длинная из идущих задач закрыта в спринте-триггере, "
        "освободившаяся ёмкость уходит на хвост."
    ),
    "hire_role": (
        "Найм: в команду нанят инженер роли, из-за отсутствия которой перенесено "
        "больше всего часов; выходит со следующего спринта после триггера."
    ),
    "vacation": (
        "Отпуск: самый загруженный по плану инженер отсутствует 2 спринта после "
        "триггера и возвращается."
    ),
    "defer_task": (
        "Перенос в следующий квартал: руководитель переносит ещё не начатую задачу "
        "с самым низким приоритетом, освободившаяся ёмкость уходит на остальные."
    ),
}
VACATION_SPRINTS = 2


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
    role_of = {e["engineer_id"]: e.get("role", "")
               for e in [*engineers, *(plan.get("engineers_hired") or [])]}
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


def pick_early_finish(plan: dict[str, Any], sprint: int) -> str | None:
    """Идущая задача, до планового конца которой дальше всего."""
    candidates = [t for t in plan["current"] if _active(t, sprint) and end_sprint(t) > sprint]
    if not candidates:
        return None
    return task_id(max(candidates, key=lambda t: (end_sprint(t), float(field(t, "rung", default=0) or 0))))


def pick_defer(plan: dict[str, Any], sprint: int) -> str | None:
    """Включённая, ещё не начатая задача с самым низким приоритетом (rung)."""
    candidates = [t for t in plan["current"]
                  if not is_done(t) and is_included(t) and (start_sprint(t) or 0) > sprint
                  and not field(t, "spent_hh")]
    if not candidates:
        return None
    return task_id(min(candidates, key=lambda t: (float(field(t, "rung", default=0) or 0), task_id(t))))


def pick_hire(plan: dict[str, Any], engineers: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(команда, роль), из-за отсутствия которой перенесено больше всего часов."""
    staffed = role_supply([*engineers, *(plan.get("engineers_hired") or [])])
    lost: dict[tuple[str, str], float] = defaultdict(float)
    names: dict[str, str] = {}
    for task in plan["current"]:
        if is_done(task) or field(task, "decision") != "postponed":
            continue
        for key, (name, hours) in remaining_hh(task).items():
            if staffed.get((team_id(task), key), 0) == 0:
                lost[(team_id(task), key)] += hours
                names.setdefault(key, name)
    if not lost:
        return None
    team, key = max(lost, key=lost.get)
    return team, names[key]


def hired_engineer(team: str, role: str, plan: dict[str, Any]) -> dict[str, Any]:
    number = len(plan.get("engineers_hired") or []) + 1
    return {"engineer_id": f"HIRE-{number:02d}", "team_id": team, "role": role,
            "grade": "Middle", "capacity_rate": 1.0, "skills_declared": [], "is_hired": True}


def build_fact(
    plan: dict[str, Any],
    scenario: str,
    sprint: int,
    *,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    trigger_sprint: int = 1,
    target: Any = None,
) -> dict[str, Any]:
    """Факт спринта по сценарию.

    target — ручной выбор цели вместо автоматического: task_id для
    key_task_delay / early_finish, engineer_id для engineer_leave / vacation,
    {"team_id", "role"} для hire_role.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"Неизвестный сценарий {scenario!r}; есть: {', '.join(SCENARIOS)}")
    fact = planned_fact(plan, sprint, engineers)
    if scenario == "vacation" and sprint == trigger_sprint + VACATION_SPRINTS:
        away = next((h["fact"].get("scenario_target") for h in plan.get("history") or []
                     if h.get("sprint") == trigger_sprint and h.get("fact")), None)
        if away:
            fact["engineers_returned"] = [away]
            fact["scenario_target"] = away
        return fact
    if sprint != trigger_sprint:
        return fact

    if scenario == "key_task_delay":
        tid = target or pick_key_task(plan, sprint, dependencies)
        if tid:
            entry = fact["tasks"].setdefault(tid, {"spent_hh": {}})
            entry["status"] = "InProgress"
            entry["spent_hh"] = {r: h / 2 for r, h in entry["spent_hh"].items()}
            fact["scenario_target"] = tid
    elif scenario in ("engineer_leave", "vacation"):
        eng_id = target or pick_busiest_engineer(plan, sprint + 1)
        if eng_id:
            fact["engineers_unavailable"] = [eng_id]
            fact["scenario_target"] = eng_id
    elif scenario == "early_finish":
        tid = target or pick_early_finish(plan, sprint)
        task = next((t for t in plan["current"] if task_id(t) == tid), None)
        if task is not None and not is_done(task):
            fact["tasks"][tid] = {
                "status": "Done",
                "spent_hh": {name: hours for name, hours in remaining_hh(task).values()},
            }
            fact["scenario_target"] = tid
    elif scenario == "defer_task":
        tid = target or pick_defer(plan, sprint)
        if tid:
            entry = fact["tasks"].setdefault(tid, {"spent_hh": {}})
            entry.update(defer=True, note="решение руководителя: освободить ёмкость для приоритетных задач")
            entry.pop("status", None)
            fact["scenario_target"] = tid
    elif scenario == "hire_role":
        pick = (target["team_id"], target["role"]) if target else pick_hire(plan, engineers)
        if pick:
            hire = hired_engineer(*pick, plan)
            fact["engineers_hired"] = [hire]
            fact["scenario_target"] = f"{hire['role']} → {hire['team_id']}"
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
    target: Any = None,
    planner: Planner | None = None,
) -> dict[str, Any]:
    """Прогоняет сценарий спринт за спринтом до until_sprint включительно."""
    while plan.get("current_sprint", 0) < until_sprint:
        sprint = plan.get("current_sprint", 0) + 1
        fact = build_fact(plan, scenario, sprint, dependencies=dependencies, engineers=engineers,
                          trigger_sprint=trigger_sprint, target=target)
        apply_sprint_fact(plan, fact, sprint, dependencies=dependencies,
                          engineers=engineers, capacity=capacity, planner=planner)
    return plan
