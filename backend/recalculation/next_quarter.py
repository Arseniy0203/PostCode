# Ответственный: Человек 2 (recalculation + risk)
"""Бэклог следующего квартала: что переносится, почему и что подготовить к старту.

Начальник получает не только список того, что не влезло, но и план подготовки:
кого нанять и до какой даты начать поиск, какие задачи разбить, что оценить.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from typing import Any, Iterable

from backend.explainability.reasons import TargetOption
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
    team_id,
)
from backend.recalculation.quarter import (
    HIRE_LEAD_SPRINTS,
    QUARTER_START,
    SPRINT_DAYS,
    next_quarter_start,
    sprint_dates,
)
from backend.recalculation.recalc import is_cancelled, is_deferred
from backend.risk.alerts import _plural, blocked_by_postponed, work_progress

KIND_LABEL = {
    "manual": "перенесена решением руководителя",
    "no_role": "в компании нет нужной роли",
    "oversized": "задача больше ёмкости команды за спринт",
    "blocked": "ждёт перенесённую задачу",
    "not_estimated": "не оценена",
    "capacity": "не хватило ёмкости команды",
    "carry_over": "не успели закончить",
    "partial": "частичная цель: доделать работу ролей, которых нет в компании",
}


def _team_sp(plan: dict[str, Any]) -> dict[str, float]:
    return (field(plan, "capacity", default={}) or {}).get("team_sp") or {}


def next_quarter_backlog(
    plan: dict[str, Any],
    engineers: list[dict[str, Any]],
    dependencies: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Задачи, уходящие в следующий квартал, и действия для подготовки к нему."""
    start = next_quarter_start()
    hire_by = start - timedelta(days=HIRE_LEAD_SPRINTS * SPRINT_DAYS)
    now = current_sprint(plan)
    today = QUARTER_START if now == 0 else sprint_dates(now)[1]
    final = now >= QUARTER_SPRINTS

    gone = set(plan.get("engineers_unavailable") or [])
    staff = [e for e in [*engineers, *(plan.get("engineers_hired") or [])]
             if e.get("engineer_id") not in gone]
    people: dict[str, set[str]] = defaultdict(set)
    for eng in staff:
        people[normalize_role(eng.get("role", ""))].add(str(eng.get("engineer_id")))
    blocked = blocked_by_postponed(plan, list(dependencies))
    team_sp = _team_sp(plan)
    by_id = {task_id(t): t for t in plan.get("current") or []}

    rows: list[dict[str, Any]] = []
    kinds: dict[str, str] = {}
    hire: dict[str, dict[str, Any]] = {}
    for task in plan.get("current") or []:
        if is_done(task) or is_cancelled(task):
            continue
        end = end_sprint(task)
        tid, team, left = task_id(task), team_id(task), remaining_hh(task)
        absent = [name for key, (name, _) in left.items() if not people.get(key)]
        unstaffed = {normalize_role(r) for r in field(task, "unstaffed_hh", default={}) or {}}
        on_time = is_included(task) and not final and end is not None and end <= QUARTER_SPRINTS
        if on_time and not unstaffed:
            continue
        sp = float(field(task, "estimation_sp", default=0) or 0)
        if on_time:
            # Идёт с частичной целью: в следующий квартал — только недостающие часы ролей.
            kind = "partial"
            left = {k: v for k, v in left.items() if k in unstaffed}
            absent = [name for key, (name, _) in left.items() if not people.get(key)]
            sp = 0.0
        elif is_included(task):
            kind = "carry_over"
        elif is_deferred(task):
            kind = "manual"
        elif field(task, "reason_code") == "NOT_ESTIMATED":
            kind = "not_estimated"
        elif tid in blocked:
            kind = "blocked"
        elif absent:
            kind = "no_role"
        elif team in team_sp and sp * (1 - work_progress(task)) > float(team_sp[team] or 0):
            kind = "oversized"
        else:
            kind = "capacity"
        kinds[tid] = kind
        progress = work_progress(task)
        rows.append({
            "task_id": tid,
            "prodf_id": prodf_id(task),
            "team_id": team,
            "kind": kind,
            "reason": KIND_LABEL[kind],
            "detail": (f"ждёт {' → '.join(blocked[tid])}" if kind == "blocked"
                       else ", ".join(f"«{n}»" for n, _ in left.values()) if kind == "partial"
                       else ", ".join(f"«{r}»" for r in absent) if kind == "no_role"
                       else field(task, "reason_detail", default="") or ""),
            "target_label": (field(task, "target_label", default=None) if kind == "partial"
                             else (TargetOption.FUNC_PARTIAL if progress
                                   else TargetOption.NOT_IN_QUARTER).label),
            "progress": round(progress, 2),
            "sp": sp,
            "hours_left": round(sum(h for _, h in left.values()), 1),
        })
        for name in absent:
            entry = hire.setdefault(normalize_role(name), {"role": name, "hours": 0.0, "tasks": []})
            entry["hours"] += next(h for key, (n, h) in left.items() if n == name)
            entry["tasks"].append(tid)

    # Задачи, заблокированные цепочкой, разблокируются вместе с корнем.
    for tid, chain in blocked.items():
        root = chain[-1]
        if kinds.get(root) != "no_role":
            continue
        for key, (name, _) in remaining_hh(by_id[root]).items():
            if not people.get(key) and tid not in hire[key]["tasks"]:
                hire[key].setdefault("also_unblocks", []).append(tid)

    late = today > hire_by
    prepare: list[dict[str, Any]] = []
    for entry in sorted(hire.values(), key=lambda e: -(len(e["tasks"]) + len(e.get("also_unblocks", [])))):
        count = len(entry["tasks"]) + len(entry.get("also_unblocks", []))
        when = ("начать поиск немедленно — срок уже прошёл" if late
                else f"начать поиск не позже {hire_by:%d.%m.%Y}")
        prepare.append({
            "action": "hire",
            "role": entry["role"],
            "hours": round(entry["hours"], 1),
            "tasks": sorted(entry["tasks"]),
            "also_unblocks": sorted(entry.get("also_unblocks", [])),
            "start_search_by": hire_by.isoformat(),
            "headline": (f"Нанять «{entry['role']}» к {start:%d.%m.%Y}: {when} "
                         f"(найм ≈ {HIRE_LEAD_SPRINTS * SPRINT_DAYS // 7} недель) — "
                         f"нужен для {count} {_plural(count, 'задачи', 'задач', 'задач')}, "
                         f"{entry['hours']:g} ч."),
        })
    for row in rows:
        if row["kind"] == "oversized":
            cap = float(team_sp.get(row["team_id"]) or 0)
            parts = max(2, math.ceil(row["sp"] / cap)) if cap else 2
            prepare.append({"action": "split", "tasks": [row["task_id"]],
                            "headline": f"Разбить {row['task_id']} ({row['sp']:g} SP) на {parts} части "
                                        f"по ≤{cap:g} SP до старта квартала."})
        elif row["kind"] == "not_estimated":
            prepare.append({"action": "estimate", "tasks": [row["task_id"]],
                            "headline": f"Оценить {row['task_id']} по ролям в калькуляторе сметы."})

    by_prodf: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_prodf[row["prodf_id"]].append(row["task_id"])
    return {
        "next_quarter_start": start.isoformat(),
        "mode": "final" if final else "forecast",
        "tasks": rows,
        "counts": {kind: sum(1 for r in rows if r["kind"] == kind) for kind in KIND_LABEL
                   if any(r["kind"] == kind for r in rows)},
        "sp_total": round(sum(r["sp"] for r in rows), 1),
        "hours_total": round(sum(r["hours_left"] for r in rows), 1),
        "by_prodf": {p: sorted(ids) for p, ids in sorted(by_prodf.items())},
        "prepare": prepare,
    }
