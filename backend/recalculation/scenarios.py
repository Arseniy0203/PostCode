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
    normalize_role,
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
        "Задержка ключевой задачи: задача, от которой зависит больше всего задач плана, "
        "в спринте-триггере не продвинулась (или сделана наполовину, если должна была "
        "закрыться) — видно, как сдвигается цепочка."
    ),
    "engineer_leave": (
        "Уход инженера: уходит единственный в компании носитель роли, у которого есть "
        "работа в плане (риск Bus Factor = 1); если таких нет — самый загруженный."
    ),
    "early_finish": (
        "Закрыли раньше срока: самая длинная из идущих задач закрыта в спринте-триггере, "
        "освободившаяся ёмкость уходит на хвост."
    ),
    "hire_role": (
        "Найм: нанят инженер роли, которой нет в компании и из-за которой отложено "
        "больше всего часов; выходит со следующего спринта, первые спринты — адаптация."
    ),
    "vacation": (
        "Отпуск: единственный носитель роли (или самый загруженный инженер) "
        "отсутствует 2 спринта после триггера и возвращается."
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
    """Задача для задержки: каскад должен быть виден.

    Приоритет — задача, от которой зависит больше всего задач, стоящих в плане
    (сдвиг отложенных задач никто не заметит), затем — задача, которая по плану
    закрывается в этом спринте, затем приоритет инициативы.
    """
    active = [t for t in plan["current"] if _active(t, sprint)]
    if not active:
        return None
    by_id = {task_id(t): t for t in plan["current"]}
    children, _ = dependency_graph(dependencies, set(by_id))

    def weight(task: Any) -> tuple[int, int, float]:
        planned_chain = [c for c in downstream(children, task_id(task))
                         if is_included(by_id[c]) and not is_done(by_id[c])]
        return (len(planned_chain), int(end_sprint(task) == sprint),
                float(field(task, "rung", default=0) or 0))

    return task_id(max(active, key=weight))


def _load_by_engineer(plan: dict[str, Any], from_sprint: int) -> dict[str, float]:
    load: dict[str, float] = defaultdict(float)
    for task in plan["current"]:
        if is_done(task):
            continue
        for key, hours in (field(task, "allocation", default={}) or {}).items():
            if int(key) >= from_sprint:
                for eng_id, h in dict(hours).items():
                    load[eng_id] += float(h)
    return load


def pick_busiest_engineer(plan: dict[str, Any], from_sprint: int,
                          engineers: list[dict[str, Any]] | None = None) -> str | None:
    """Кого «убрать» в сценариях ухода/отпуска.

    Сначала — единственный в компании носитель роли, у которого есть работа
    в плане: без него задачи встают, и это видно (риск Bus Factor = 1). Если
    таких нет — самый загруженный инженер.
    """
    load = _load_by_engineer(plan, from_sprint)
    if engineers:
        people: dict[str, set[str]] = defaultdict(set)
        for eng in engineers:
            people[normalize_role(eng.get("role", ""))].add(str(eng.get("engineer_id")))
        sole = {next(iter(ids)) for ids in people.values() if len(ids) == 1}
        loaded_sole = {e: h for e, h in load.items() if e in sole and h > 0}
        if loaded_sole:
            return max(loaded_sole, key=loaded_sole.get)
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
    """(команда, роль) для найма: роль, которой нет во всей компании и из-за
    которой отложено больше всего часов, — в команду, где их больше всего.

    Планировщик берёт людей из общего пула, поэтому нанимать роль, которая
    в компании уже есть, почти бесполезно; такая роль берётся, только если
    отсутствующих ролей нет.
    """
    staff = [*engineers, *(plan.get("engineers_hired") or [])]
    company = {normalize_role(e.get("role", "")) for e in staff}
    staffed = role_supply(staff)
    absent: dict[tuple[str, str], float] = defaultdict(float)
    lost: dict[tuple[str, str], float] = defaultdict(float)
    names: dict[str, str] = {}
    for task in plan["current"]:
        if is_done(task) or field(task, "deferred"):
            continue
        # Отложенные задачи и задачи с частичной целью (часы ролей без людей).
        if field(task, "decision") != "postponed" and not field(task, "unstaffed_hh"):
            continue
        for key, (name, hours) in remaining_hh(task).items():
            if field(task, "decision") != "postponed" and key in company:
                continue
            names.setdefault(key, name)
            if key not in company:
                absent[(team_id(task), key)] += hours
            elif staffed.get((team_id(task), key), 0) == 0:
                lost[(team_id(task), key)] += hours
    pool = absent or lost
    if not pool:
        return None
    by_role: dict[str, float] = defaultdict(float)
    for (_, key), hours in pool.items():
        by_role[key] += hours
    role = max(by_role, key=by_role.get)
    team = max((k for k in pool if k[1] == role), key=pool.get)[0]
    return team, names[role]


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
            task = next((t for t in plan["current"] if task_id(t) == tid), None)
            entry = fact["tasks"].setdefault(tid, {"spent_hh": {}})
            entry["status"] = "InProgress"
            # Если задача и так не должна была закрыться в этом спринте, «половина»
            # — это ровно план; задержка тогда означает, что работа не продвинулась.
            ends_now = task is not None and end_sprint(task) == sprint
            entry["spent_hh"] = {r: h / 2 for r, h in entry["spent_hh"].items()} if ends_now else {}
            fact["scenario_target"] = tid
    elif scenario in ("engineer_leave", "vacation"):
        eng_id = target or pick_busiest_engineer(plan, sprint + 1, engineers)
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
