"""«Что если нанять»: какой найм вернёт в квартал больше всего задач и инициатив.

Ответ экспертов: подсвечивать, кого нужно нанять. Кандидаты — все роли, из-за
которых задачи отложены: и те, которых нет в компании, и те, что есть, но
заняты. Каждый вариант проверяется честно: план Недели 0 строится заново тем
же шагом plan_tasks с нанятым инженером и сравнивается с исходным.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Callable

from backend.recalculation.plan_fields import (
    field,
    is_done,
    is_included,
    normalize_role,
    prodf_id,
    remaining_hh,
    task_id,
    team_id,
)
from backend.recalculation.quarter import HIRE_LEAD_SPRINTS, average_ramp, ready_sprint
from backend.recalculation.recalc import is_cancelled, plan_tasks
from backend.risk.alerts import SPRINT_HOURS, role_supply

Planner = Callable[..., dict[str, Any]]


def _tasks(n: int) -> str:
    n100 = abs(n) % 100
    if 11 <= n100 <= 14:
        return "задач"
    return {1: "задача", 2: "задачи", 3: "задачи", 4: "задачи"}.get(n100 % 10, "задач")


def _full(task: dict[str, Any]) -> bool:
    """Включена и идёт до полной цели (без часов ролей, которых нет в компании)."""
    return is_included(task) and not field(task, "unstaffed_hh")


def plan_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Что взято в квартал: задачи, SP и инициативы, у которых включены все открытые задачи.

    full_tasks — включённые задачи с полной целью; partial_tasks — включённые с
    частичной целью (часть ролей без людей); prodf_full — инициативы, у которых
    все открытые задачи идут до полной цели.
    """
    open_tasks = [t for t in tasks if not is_done(t) and not is_cancelled(t)]
    included = [t for t in open_tasks if is_included(t)]
    by_prodf: dict[str, list[bool]] = defaultdict(list)
    full_by_prodf: dict[str, list[bool]] = defaultdict(list)
    for task in open_tasks:
        by_prodf[prodf_id(task)].append(is_included(task))
        full_by_prodf[prodf_id(task)].append(_full(task))
    return {
        "tasks": sorted(task_id(t) for t in included),
        "full_tasks": sorted(task_id(t) for t in included if _full(t)),
        "partial_tasks": sorted(task_id(t) for t in included if not _full(t)),
        "sp": round(sum(float(t.get("estimation_sp") or 0) for t in included), 1),
        "prodf_complete": sorted(p for p, flags in by_prodf.items() if flags and all(flags)),
        "prodf_full": sorted(p for p, flags in full_by_prodf.items() if flags and all(flags)),
        "open_tasks": len(open_tasks),
    }


def _gain(base: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(задачи, вошедшие в квартал; задачи, доведённые до полной цели)."""
    gained = sorted(set(after["tasks"]) - set(base["tasks"]))
    upgraded = sorted(set(after.get("full_tasks", [])) - set(base.get("full_tasks", []))
                      - set(gained))
    return gained, upgraded


def _gain_text(gained: list[str], upgraded: list[str]) -> str:
    parts = []
    if gained:
        parts.append(f"+{len(gained)} {_tasks(len(gained))} в квартал")
    if upgraded:
        parts.append(f"+{len(upgraded)} {_tasks(len(upgraded))} до полной цели")
    return ", ".join(parts) or "без прироста"


def _lost_hours(planned: list[dict[str, Any]], engineers: list[dict[str, Any]]):
    """(команда, роль) → часы, которых не хватило: отложенные задачи и часы ролей
    без людей у задач с частичной целью; отдельно — роли, которых нет в команде."""
    staffed = role_supply(engineers)
    lost: dict[tuple[str, str], tuple[str, float]] = {}
    missing: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for task in planned:
        if is_done(task) or is_cancelled(task):
            continue
        left = remaining_hh(task)
        if is_included(task):
            unstaffed = {normalize_role(r) for r in field(task, "unstaffed_hh", default={}) or {}}
            if not unstaffed:
                continue
            left = {k: v for k, v in left.items() if k in unstaffed}
        elif field(task, "decision") != "postponed":
            continue
        team = team_id(task)
        for key, (name, hours) in left.items():
            old = lost.get((team, key), (name, 0.0))
            lost[(team, key)] = (old[0], old[1] + hours)
            if staffed.get((team, key), 0) == 0:
                prev = missing[team].get(key, (name, 0.0))
                missing[team][key] = (prev[0], prev[1] + hours)
    return lost, missing


def _best_team_per_role(lost) -> list[tuple[str, str]]:
    best: dict[str, tuple[str, str, float]] = {}
    for (team, key), (name, hours) in lost.items():
        if key not in best or hours > best[key][2]:
            best[key] = (team, name, hours)
    return [(team, name) for team, name, _ in best.values()]


# Решение о найме принимается на Неделе 0: человек выходит к спринту 4 (поиск)
# и первые спринты работает не в полную силу (адаптация) — как в рекомендациях алертов.
HIRE_JOINS_SPRINT = ready_sprint(0, HIRE_LEAD_SPRINTS)


def _hire(engineers, capacity, hires, prefix="HIRE"):
    """Добавляет нанятых: выход к HIRE_JOINS_SPRINT, затем адаптация.

    Планировщик принимает одну ёмкость на весь квартал, поэтому нанятому
    даётся средняя продуктивность по спринтам 1…6 (до выхода — ноль).
    """
    new_engineers = [*engineers]
    new_capacity = copy.deepcopy(capacity)
    hours = round(SPRINT_HOURS * average_ramp(HIRE_JOINS_SPRINT, 1), 1)
    for n, (team, role) in enumerate(hires, 1):
        eng_id = f"{prefix}-{n:02d}"
        new_engineers.append({"engineer_id": eng_id, "team_id": team, "role": role,
                              "grade": "Middle", "capacity_rate": 1.0, "skills_declared": []})
        new_capacity.setdefault("engineer_hh", {})[eng_id] = hours
    return new_engineers, new_capacity


def _run(tasks, capacity, dependencies, engineers, planner, hires):
    new_engineers, new_capacity = _hire(engineers, capacity, hires)
    planned = plan_tasks(copy.deepcopy(tasks), new_capacity, dependencies, new_engineers, planner=planner)
    return plan_summary(planned)


def _option(kind, team, roles, base, after):
    gained, upgraded = _gain(base, after)
    prodf = sorted(set(after.get("prodf_full", [])) - set(base.get("prodf_full", [])))
    who = (f"{len(roles)} инженеров недостающих ролей" if kind == "all"
           else ", ".join(f"«{r}»" for r in roles))
    sp_gain = round(after["sp"] - base["sp"], 1)
    headline = (
        f"Нанять в {team}: {who} → {_gain_text(gained, upgraded)}"
        + (f", +{sp_gain:g} SP" if sp_gain else "")
        + f" (решение на Неделе 0, выход к спринту {HIRE_JOINS_SPRINT}, адаптация)"
        + (f"; инициативы до полной цели: {', '.join(prodf)}" if prodf else "")
    )
    return {
        "kind": kind,
        "team_id": team,
        "roles": list(roles),
        "hires": len(roles),
        "tasks_gained": gained,
        "tasks_upgraded": upgraded,
        "gain_full": len(upgraded),
        "gain_tasks": len(gained),
        "gain_sp": round(after["sp"] - base["sp"], 1),
        "prodf_gained": prodf,
        "headline": headline,
    }


def hiring_impact(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    planner: Planner | None = None,
    top: int = 5,
) -> dict[str, Any]:
    """Ранжирует варианты найма по отдаче на одного нанятого.

    Варианты: один инженер роли (в команду, где из-за неё потеряно больше
    всего часов); весь набор недостающих ролей команды; «потолок» — закрыть
    все недостающие роли во всех командах.

    Returns:
        {"baseline": summary, "options": [...], "ceiling": option | None}
    """
    base_plan = plan_tasks(copy.deepcopy(tasks), capacity, dependencies, engineers, planner=planner)
    base = plan_summary(base_plan)
    lost, missing = _lost_hours(base_plan, engineers)

    options = []
    for team, name in _best_team_per_role(lost):
        after = _run(tasks, capacity, dependencies, engineers, planner, [(team, name)])
        options.append(_option("role", team, [name], base, after))
    for team, roles in missing.items():
        ordered = sorted(roles.values(), key=lambda r: -r[1])
        if len(ordered) > 1:
            hires = [(team, name) for name, _ in ordered]
            after = _run(tasks, capacity, dependencies, engineers, planner, hires)
            options.append(_option("team_bundle", team, [n for n, _ in ordered], base, after))

    ceiling = None
    if missing:
        hires = [(team, name) for team, roles in missing.items() for name, _ in roles.values()]
        after = _run(tasks, capacity, dependencies, engineers, planner, hires)
        ceiling = _option("all", "всех командах", [f"{r} ({t})" for t, r in hires], base, after)

    useful = [o for o in options if o["gain_tasks"] + o["gain_full"] > 0]
    # Сначала отдача на одного нанятого: 1 человек → +10 задач лучше, чем 11 человек → +23.
    useful.sort(key=lambda o: (-(o["gain_tasks"] + o["gain_full"]) / o["hires"],
                               -o["gain_tasks"] / o["hires"],
                               -len(o["prodf_gained"]) / o["hires"], -o["gain_sp"], o["hires"]))
    return {"baseline": base, "options": useful[:top], "ceiling": ceiling}


def hiring_roadmap(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    planner: Planner | None = None,
    max_hires: int = 5,
) -> list[dict[str, Any]]:
    """Жадный план найма: на каждом шаге — найм с наибольшим приростом задач.

    Отвечает на вопрос «кого нанимать первым, вторым…»: шаги учитывают уже
    нанятых. Останавливается, когда очередной найм ничего не даёт.
    """
    hires: list[tuple[str, str]] = []
    steps: list[dict[str, Any]] = []
    current_engineers, current_capacity = engineers, capacity
    planned = plan_tasks(copy.deepcopy(tasks), capacity, dependencies, engineers, planner=planner)
    base = plan_summary(planned)
    start = base
    for step in range(1, max_hires + 1):
        lost, _ = _lost_hours(planned, current_engineers)
        best = None
        for team, name in _best_team_per_role(lost):
            new_engineers, new_capacity = _hire(current_engineers, current_capacity, [(team, name)],
                                                prefix=f"HIRE{step}")
            trial = plan_tasks(copy.deepcopy(tasks), new_capacity, dependencies, new_engineers,
                               planner=planner)
            summary = plan_summary(trial)
            gained, upgraded = _gain(base, summary)
            key = (len(gained) + len(upgraded), len(gained), summary["sp"] - base["sp"])
            if key[0] > 0 and (best is None or key > best[0]):
                best = (key, team, name, summary, trial, new_engineers, new_capacity)
        if best is None:
            break
        (_, _, gain_sp), team, name, summary, planned, current_engineers, current_capacity = best
        gained, upgraded = _gain(base, summary)
        again = any(role == name for _, role in hires)
        hires.append((team, name))
        steps.append({
            "step": step,
            "team_id": team,
            "role": name,
            "tasks_gained": gained,
            "tasks_upgraded": upgraded,
            "gain_tasks": len(gained),
            "gain_full": len(upgraded),
            "gain_sp": round(gain_sp, 1),
            "total_tasks": len(summary["tasks"]),
            "total_full_tasks": len(summary["full_tasks"]),
            "total_gain_tasks": len(summary["tasks"]) - len(start["tasks"]),
            "headline": (f"{step}. Нанять {'ещё одного ' if again else ''}«{name}» в {team} → "
                         f"{_gain_text(gained, upgraded)} "
                         f"(в квартале {len(summary['tasks'])} из {summary['open_tasks']}, "
                         f"до полной цели {len(summary['full_tasks'])}; "
                         f"выход к спринту {HIRE_JOINS_SPRINT}, адаптация)"),
        })
        base = summary
    return steps
