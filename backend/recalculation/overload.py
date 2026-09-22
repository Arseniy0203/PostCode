"""«Если запустить всё сразу» против плана планировщика.

Подсказка организаторов: датасет намеренно перегружен — если запустить все задачи
одновременно, команды перегрузятся и по Story Points, и по часам. Здесь это
показано числами: нагрузка первого спринта при одновременном старте всего
бэклога, потребность ролей против фонда квартала — и то, как план разложил ту же
работу (загрузка спринтов не выше 100%, роли без людей — частичной целью).
"""

from __future__ import annotations

from typing import Any

from backend.recalculation.plan_fields import (
    QUARTER_SPRINTS,
    field,
    is_done,
    normalize_role,
    remaining_hh,
    task_id,
)
from backend.recalculation.what_if import plan_summary

HOURS_PER_SPRINT = 80.0
SP_TOLERANCE = 0.03   # sp_by_sprint в плане округлены до 0.01 — сумма может «перелезть» на сотые


def _pct(used: float, cap: float) -> int | None:
    """Загрузка в %; превышение в пределах допуска округления — это 100%."""
    if not cap:
        return None
    if cap < used <= cap + SP_TOLERANCE:
        used = cap
    return round(100 * used / cap)


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _role_funds(engineers: list[dict[str, Any]]) -> dict[str, tuple[str, float, set[str]]]:
    """Фонд часов роли за спринт: {ключ: (название, часы, инженеры)}; парттаймер — сумма долей."""
    funds: dict[str, tuple[str, float, set[str]]] = {}
    for eng in engineers:
        key = normalize_role(eng.get("role"))
        if not key:
            continue
        name, hours, people = funds.get(key, (eng.get("role"), 0.0, set()))
        people.add(eng.get("engineer_id"))
        funds[key] = (name, hours + HOURS_PER_SPRINT * _num(eng.get("capacity_rate") or 1), people)
    return funds


def overload_report(data: dict[str, Any], plan: dict[str, Any],
                    strict: dict[str, Any] | None = None) -> dict[str, Any]:
    """Сравнение «всё сразу в спринте 1» с планом Недели 0.

    Args:
        data: датасет конвейера (tasks, engineers, capacity).
        plan: план Недели 0 (baseline).
        strict: plan_summary по строгому правилу «нет роли — не берём» (необязательно).

    Returns:
        {"teams": [...], "roles": [...], "plan_load": [...], "summary": {...}}
    """
    open_tasks = [t for t in data["tasks"] if not is_done(t)]
    team_cap = (data.get("capacity") or {}).get("team_sp") or {}

    # Команды: весь бэклог стартует в спринте 1 против ёмкости спринта и квартала
    backlog_sp: dict[str, float] = {}
    for task in open_tasks:
        team = field(task, "team_id")
        backlog_sp[team] = backlog_sp.get(team, 0.0) + _num(field(task, "estimation_sp"))

    # План: SP, списанные в каждый спринт (sp_by_sprint), против ёмкости
    plan_sp: dict[str, dict[int, float]] = {}
    for task in plan.get("baseline") or []:
        if field(task, "decision") != "included":
            continue
        team = field(task, "team_id")
        for sprint, sp in (field(task, "sp_by_sprint", default={}) or {}).items():
            row = plan_sp.setdefault(team, {})
            row[int(sprint)] = row.get(int(sprint), 0.0) + _num(sp)

    teams = []
    plan_load = []
    for team in sorted(set(backlog_sp) | set(team_cap)):
        cap = _num(team_cap.get(team))
        need = backlog_sp.get(team, 0.0)
        per_sprint = plan_sp.get(team, {})
        peak = max(per_sprint.values(), default=0.0)
        teams.append({
            "team_id": team,
            "capacity_sp": round(cap, 1),
            "backlog_sp": round(need, 1),
            "all_at_once_pct": round(100 * need / cap) if cap else None,
            "quarter_capacity_sp": round(cap * QUARTER_SPRINTS, 1),
            "plan_peak_pct": _pct(peak, cap),
        })
        for sprint in range(1, QUARTER_SPRINTS + 1):
            used = per_sprint.get(sprint, 0.0)
            plan_load.append({"team_id": team, "sprint": sprint, "sp": round(used, 1),
                              "pct": _pct(used, cap)})

    # Роли: остаток часов бэклога против фонда за спринт и за квартал
    funds = _role_funds(data.get("engineers") or [])
    need_hh: dict[str, tuple[str, float, set[str]]] = {}
    for task in open_tasks:
        for key, (name, hours) in remaining_hh(task).items():
            prev_name, prev, tasks = need_hh.get(key, (name, 0.0, set()))
            tasks.add(task_id(task))
            need_hh[key] = (prev_name, prev + hours, tasks)
    roles = []
    for key, (name, hours, tasks) in need_hh.items():
        _, fund, people = funds.get(key, (name, 0.0, set()))
        roles.append({
            "role": name,   # название из сметы, как во всём интерфейсе
            "need_hh": round(hours),
            "tasks": len(tasks),
            "fund_sprint_hh": round(fund),
            "fund_quarter_hh": round(fund * QUARTER_SPRINTS),
            "people": len(people),
            "all_at_once_pct": round(100 * hours / fund) if fund else None,
            "quarter_pct": round(100 * hours / (fund * QUARTER_SPRINTS)) if fund else None,
        })
    roles.sort(key=lambda r: (r["fund_sprint_hh"] > 0, -(r["all_at_once_pct"] or 0), -r["need_hh"]))

    ours = plan_summary(list(plan.get("baseline") or []))
    over = [t for t in teams if (t["all_at_once_pct"] or 0) > 100]
    return {
        "teams": teams,
        "roles": roles,
        "plan_load": plan_load,
        "summary": {
            "open_tasks": len(open_tasks),
            "teams_overloaded": len(over),
            "max_team": max(teams, key=lambda t: t["all_at_once_pct"] or 0, default=None),
            "roles_without_people": [r["role"] for r in roles if not r["fund_sprint_hh"]],
            "roles_over_quarter": [r["role"] for r in roles if (r["quarter_pct"] or 0) > 100],
            "plan_tasks": len(ours.get("tasks") or []),
            "plan_full": len(ours.get("full_tasks") or []),
            "plan_partial": len(ours.get("partial_tasks") or []),
            "plan_peak_pct": max((t["plan_peak_pct"] or 0 for t in teams), default=0),
            "strict_tasks": len((strict or {}).get("tasks") or []) if strict else None,
        },
    }
