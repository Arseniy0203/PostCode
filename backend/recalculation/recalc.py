# Ответственный: Человек 2 (recalculation + risk)
"""Пересчёт плана по факту выполнения спринта.

Формат факта за спринт:
    {
        "tasks": {task_id: {
            "status": "Done" | "InProgress" | "Cancelled" | "Deferred",
            "spent_hh": {роль: часы, потраченные В ЭТОМ спринте},
            "estimated_hh": {роль: часы},     # переоценка: новая полная смета
            "defer": True | False,            # перенести в следующий квартал / вернуть
            "note": "причина отмены / переоценки / переноса",
        }},
        "tasks_added": [{task_id, prodf_id, team_id, estimation_sp, estimated_hh, rung}],
        "engineers_unavailable": [engineer_id, ...],   # со следующего спринта
        "engineers_returned": [engineer_id, ...],      # вернулись из отпуска/болезни
        "engineers_hired": [{engineer_id, team_id, role, capacity_rate, ...}],
    }

Факт принимается строго по одному спринту и по порядку (1, 2, ...): система
не может увидеть будущее, а повторная подача того же спринта отклоняется.
Ошибочный факт отменяется через rollback.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from backend.explainability import reasons as R
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
from backend.risk.alerts import (
    SPRINT_HOURS,
    Alert,
    annotate_lifecycle,
    collect_alerts,
)

Planner = Callable[..., dict[str, Any]]

PLANNING_FIELDS = (
    "decision",
    "reason_code",
    "reason_label",
    "reason_detail",
    "target_option",
    "target_label",
    "reason_facts",
    "planned_start_sprint",
    "planned_end_sprint",
    "forecast_end_sprint",
    "allocation",
    "assigned_engineers",
)
_SERVICE_COLUMNS = {normalize_role("task_id"), normalize_role("Исходное время")}
_TOTAL_ROW = normalize_role("Итого по ролям")
_CANCELLED = {"cancelled", "canceled", "отменено", "отменена"}
_DEFERRED = {"deferred", "перенесено", "перенесена"}


class SprintOrderError(ValueError):
    """Факт подан не за следующий по порядку спринт."""


def is_cancelled(task: Any) -> bool:
    return (str(field(task, "status", default="")).casefold() in _CANCELLED
            or field(task, "decision") == "cancelled")


def is_deferred(task: Any) -> bool:
    """Задача перенесена в следующий квартал решением руководителя."""
    return bool(field(task, "deferred", default=False))


def defer_reason(task: Any) -> dict[str, Any]:
    note = field(task, "defer_note", default="") or "решение руководителя"
    progress = ""
    estimated = _estimated_total(task)
    spent = sum(float(h or 0) for h in (field(task, "spent_hh", default={}) or {}).values())
    if estimated and spent:
        progress = f" Выполнено {min(spent / estimated, 1):.0%}, остаток переходит в следующий квартал."
    target = R.TargetOption.FUNC_PARTIAL if spent else R.TargetOption.NOT_IN_QUARTER
    return R.make_reason(
        R.ReasonCode.PLAN_EXCEEDED,
        f"{task_id(task)} перенесена в следующий квартал: {note}.{progress}",
        decision="postponed", target=target,
        facts={"task_id": task_id(task), "note": note, "manual": True},
    ).to_dict()


def mark_deferred(tasks: list[dict[str, Any]], defer: dict[str, str] | list[str] | None,
                  sprint: int = 0) -> list[str]:
    """Помечает задачи перенесёнными; возвращает id, которых нет среди задач."""
    notes = dict.fromkeys(defer, "") if isinstance(defer, list) else dict(defer or {})
    by_id = {task_id(t): t for t in tasks}
    for tid, note in notes.items():
        if tid in by_id:
            by_id[tid].update(deferred=True, deferred_sprint=sprint, defer_note=note or None)
    return [tid for tid in notes if tid not in by_id]


def _estimated_total(task: Any) -> float:
    estimated = field(task, "estimated_hh", default={}) or {}
    return sum(float(h or 0) for h in estimated.values()) if isinstance(estimated, dict) else 0.0


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


def _tail_input(task: dict[str, Any]) -> dict[str, Any]:
    """Незавершённая задача для планировщика: остаток часов и пропорциональный остаток SP."""
    item = {k: v for k, v in task.items() if k not in PLANNING_FIELDS}
    left = remaining_hh(task)
    item["estimated_hh"] = {name: hours for name, hours in left.values()}
    total = _estimated_total(task)
    if total > 0:
        sp = float(task.get("estimation_sp") or 0)
        item["estimation_sp"] = round(sp * sum(h for _, h in left.values()) / total, 2)
    return item


def plan_tasks(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    planner: Planner | None = None,
    start_sprint: int = 1,
) -> list[dict[str, Any]]:
    """Один шаг планирования — общий для Недели 0 и пересчёта.

    Done-задачи фиксируются (locked); отменённые и перенесённые руководителем
    в планировщик не идут, поэтому зависящие от них задачи не могут начаться;
    задачи без сметы откладываются с причиной «Не оценивалась»; задачи в работе
    планируются на остаток часов. Возвращает задачи в исходном порядке с
    обновлёнными полями планирования.
    """
    locked, to_planner, unestimated = [], [], set()
    for task in tasks:
        if is_cancelled(task) or (is_deferred(task) and not is_done(task)):
            continue
        if is_done(task):
            locked.append(task_id(task))
            to_planner.append(task)
        elif _estimated_total(task) <= 0:
            unestimated.add(task_id(task))
        else:
            to_planner.append(_tail_input(task))

    result = (planner or _default_planner())(
        to_planner, capacity, dependencies, engineers,
        estimates=None, start_sprint=start_sprint, locked_tasks=locked,
    )
    planned = {task_id(t): t for t in result.get("current") or []}

    merged_tasks = []
    for task in tasks:
        tid = task_id(task)
        merged = copy.deepcopy(task)
        if tid in unestimated:
            merged.update({k: None for k in PLANNING_FIELDS})
            merged.update(R.not_estimated(tid).to_dict())
        elif is_deferred(task) and not is_done(task) and not is_cancelled(task):
            merged.update({k: None for k in PLANNING_FIELDS})
            merged.update(defer_reason(task))
        elif not is_done(task) and not is_cancelled(task) and tid in planned:
            for key in PLANNING_FIELDS:
                if key in planned[tid] or key in merged:
                    merged[key] = copy.deepcopy(planned[tid].get(key))
        merged_tasks.append(merged)
    return merged_tasks


def _apply_task_fact(task: dict[str, Any], fact: dict[str, Any], sprint: int) -> None:
    spent = dict(task.get("spent_hh") or {})
    for role, hours in (fact.get("spent_hh") or {}).items():
        spent[role] = spent.get(role, 0.0) + float(hours or 0)
    task["spent_hh"] = spent

    if fact.get("estimated_hh"):
        task.setdefault("estimated_hh_initial", copy.deepcopy(task.get("estimated_hh") or {}))
        task["estimated_hh"] = {role: float(h) for role, h in fact["estimated_hh"].items()}
        task["reestimated_sprint"] = sprint

    status = fact.get("status")
    if status and str(status).casefold() in _DEFERRED:
        fact = {**fact, "defer": True}
        status = None
    if fact.get("defer") is True:
        task.update(deferred=True, deferred_sprint=sprint, defer_note=fact.get("note"))
    elif fact.get("defer") is False and task.get("deferred"):
        task.update(deferred=False, returned_sprint=sprint)
    if status:
        task["status"] = status
    if is_cancelled(task):
        task["status"] = "Cancelled"
        note = fact.get("note") or "заказчик отменил задачу"
        task.update({k: None for k in PLANNING_FIELDS})
        task.update(R.requirements_changed(task_id(task), note).to_dict())
        task["cancelled_sprint"] = sprint
    elif is_done(task):
        task["actual_end_sprint"] = sprint
        task["forecast_end_sprint"] = sprint
        # Часы, заложенные на будущие спринты, освобождаются для хвоста.
        task["allocation"] = {
            s: hours for s, hours in (task.get("allocation") or {}).items() if int(s) <= sprint
        }
    if task.get("actual_start_sprint") is None and (is_done(task) or fact.get("spent_hh")):
        task["actual_start_sprint"] = sprint


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
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "added",
                "detail": f"{tid} добавлена в {sprint_label(sprint)}: решение — "
                          f"{field(task, 'decision')}. {field(task, 'reason_detail', default='')}".strip(),
            })
        elif is_cancelled(task) and not is_cancelled(old_task):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "cancelled",
                "detail": f"{tid} отменена в {sprint_label(sprint)}. {field(task, 'reason_detail', default='')}",
            })
        elif is_deferred(task) and not is_deferred(old_task):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "deferred",
                "detail": field(task, "reason_detail", default=f"{tid} перенесена в следующий квартал."),
            })
        elif is_deferred(old_task) and not is_deferred(task):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "returned",
                "detail": f"{tid} возвращена в квартал: решение — {field(task, 'decision')}.",
            })
        elif is_done(task) and not is_done(old_task):
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "done",
                "detail": f"{tid} закрыта в {sprint_label(sprint)}.",
            })
        elif field(task, "reestimated_sprint") == sprint:
            changes.append({
                "task_id": tid, "prodf_id": prodf_id(task), "kind": "reestimated",
                "detail": (
                    f"{tid} переоценена: было {_estimated_total(old_task):g} ч, "
                    f"стало {_estimated_total(task):g} ч."
                ),
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


def active_resources(
    engineers: list[dict[str, Any]],
    capacity: dict[str, Any],
    unavailable: set[str],
    hired: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Инженеры и ёмкость с учётом ушедших/отсутствующих и нанятых."""
    active = [e for e in [*engineers, *hired.values()] if e.get("engineer_id") not in unavailable]
    active_capacity = copy.deepcopy(capacity)
    engineer_hh = active_capacity.setdefault("engineer_hh", {})
    for eng_id, eng in hired.items():
        engineer_hh[eng_id] = SPRINT_HOURS * float(eng.get("capacity_rate") or 1.0)
    for eng_id in unavailable:
        engineer_hh[eng_id] = 0.0
    return active, active_capacity


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

    Done-задачи фиксируются (actual_end_sprint), отменённые выводятся из плана,
    остальные планируются заново с sprint + 1 на остаток часов. baseline не
    меняется. В plan пишутся current, current_sprint, alerts, warnings и
    снапшот в history.

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
        elif is_done(task) or is_cancelled(task):
            warnings.append(f"Факт по {tid} пропущен: задача уже закрыта или отменена.")
        else:
            _apply_task_fact(task, task_fact, sprint)

    for new_task in fact.get("tasks_added") or []:
        tid = task_id(new_task)
        if not tid or tid in tasks:
            warnings.append(f"Новая задача {tid or '<без id>'} пропущена: пустой или повторяющийся id.")
            continue
        item = {"status": "ToDo", "spent_hh": {}, "estimated_hh": {}, **copy.deepcopy(new_task)}
        item["added_sprint"] = sprint
        tasks[tid] = item

    unavailable = (set(plan.get("engineers_unavailable") or []) | set(fact.get("engineers_unavailable") or [])) \
        - set(fact.get("engineers_returned") or [])
    hired = {e["engineer_id"]: dict(e) for e in plan.get("engineers_hired") or []}
    for eng in fact.get("engineers_hired") or []:
        hired.setdefault(eng["engineer_id"], dict(eng))
    active_engineers, active_capacity = active_resources(engineers, capacity, unavailable, hired)

    current = plan_tasks(list(tasks.values()), active_capacity, dependencies, active_engineers,
                         planner=planner, start_sprint=sprint + 1)

    plan["current"] = current
    plan["current_sprint"] = sprint
    plan["engineers_unavailable"] = sorted(unavailable)
    plan["engineers_hired"] = list(hired.values())
    plan["capacity"] = active_capacity
    history = plan.setdefault("history", [])
    last_alerts = history[-1].get("alerts") if history else None
    alerts, resolved = annotate_lifecycle(
        collect_alerts(plan, dependencies, active_engineers, reference=previous), last_alerts, sprint
    )
    plan["alerts"] = alerts
    plan["resolved_alerts"] = resolved
    plan["warnings"] = warnings
    history.append({
        "sprint": sprint,
        "fact": copy.deepcopy(fact),
        "current": copy.deepcopy(current),
        "changes": _changes(previous, current, dependencies, sprint),
        "alerts": [a.to_dict() for a in alerts],
        "resolved_alerts": resolved,
        "warnings": list(warnings),
        "engineers_unavailable": sorted(unavailable),
        "engineers_hired": copy.deepcopy(list(hired.values())),
        "capacity": copy.deepcopy(active_capacity),
    })
    return plan


def rollback(plan: dict[str, Any], to_sprint: int) -> dict[str, Any]:
    """Откатывает план к состоянию после спринта to_sprint (0 — Неделя 0).

    Нужен, если факт введён с ошибкой: снапшоты уже лежат в history.
    baseline не меняется.
    """
    history = plan.get("history") or []
    if not 0 <= to_sprint < len(history):
        raise ValueError(f"Нет снапшота спринта {to_sprint}: доступны 0…{len(history) - 1}")
    entry = history[to_sprint]
    plan["history"] = history[: to_sprint + 1]
    plan["current"] = copy.deepcopy(entry["current"])
    plan["current_sprint"] = to_sprint
    plan["engineers_unavailable"] = list(entry.get("engineers_unavailable") or [])
    plan["engineers_hired"] = copy.deepcopy(entry.get("engineers_hired") or [])
    if entry.get("capacity") is not None:
        plan["capacity"] = copy.deepcopy(entry["capacity"])
    plan["alerts"] = [Alert.from_dict(a) for a in entry.get("alerts") or []]
    plan["resolved_alerts"] = list(entry.get("resolved_alerts") or [])
    plan["warnings"] = list(entry.get("warnings") or [])
    return plan
