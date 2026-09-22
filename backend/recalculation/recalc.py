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
    link_dependencies,
    normalize_role,
    prodf_id,
    remaining_hh,
    task_id,
)
from backend.recalculation.quarter import average_ramp, sprint_label
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
    "sp_by_sprint",
    "unstaffed_hh",
    "recommendation",
    "score",
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
    notes = dict(defer) if isinstance(defer, dict) else dict.fromkeys(defer or (), "")
    by_id = {task_id(t): t for t in tasks}
    for tid, note in notes.items():
        if tid in by_id:
            by_id[tid].update(deferred=True, deferred_sprint=sprint, defer_note=note or None)
    return [tid for tid in notes if tid not in by_id]


_STATUS_SYNONYMS = {
    **dict.fromkeys(("done", "выполнено", "выполнена", "готово", "готова", "закрыта", "закрыто",
                     "завершена", "завершено", "closed", "resolved"), "Done"),
    **dict.fromkeys(("inprogress", "in progress", "в работе", "вработе"), "InProgress"),
    **dict.fromkeys(("todo", "to do", "новая", "к выполнению"), "ToDo"),
}


def to_hours(value: Any) -> float:
    """Число часов из ячейки: 12.5, "12,5", " 12 ", None, NaN → число (или 0)."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if value == value else 0.0
    try:
        return float(str(value).strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def canonical_status(status: Any) -> tuple[str | None, bool]:
    """(статус, распознан ли) — синонимы «Выполнено»/«Готово»/«Closed» → Done и т. п."""
    if status is None or not str(status).strip():
        return None, True
    key = " ".join(str(status).split()).casefold()
    if key in _CANCELLED:
        return "Cancelled", True
    if key in _DEFERRED:
        return "Deferred", True
    if key in _STATUS_SYNONYMS:
        return _STATUS_SYNONYMS[key], True
    return None, False


def _canonical_roles(hours: dict[str, Any], reference: dict[str, Any]) -> dict[str, float]:
    """Приводит названия ролей к написанию из сметы задачи («Девопс» → «ДевОпс»)."""
    names = {normalize_role(r): r for r in (reference if isinstance(reference, dict) else {})}
    result: dict[str, float] = {}
    for role, value in hours.items():
        name = names.get(normalize_role(role), role)
        result[name] = result.get(name, 0.0) + to_hours(value)
    return result


def humanize_roles(text: str, tasks: list[dict[str, Any]]) -> str:
    """Заменяет служебные ключи ролей («руководительпроекта») их названиями из сметы."""
    if not text:
        return text
    names: dict[str, str] = {}
    for task in tasks:
        for role in (field(task, "estimated_hh", default={}) or {}):
            names.setdefault(normalize_role(role), role)
    for key in sorted(names, key=len, reverse=True):
        if key in text and key != names[key]:
            text = text.replace(key, names[key])
    return text


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
        hours = to_hours(row.get("hours"))
        if hours > 0 and normalize_role(row.get("role", "")) != _TOTAL_ROW:
            roles = by_task.setdefault(str(row.get("task_id")), {})
            roles[row["role"]] = roles.get(row["role"], 0.0) + hours

    spent_by_task: dict[str, dict[str, float]] = {}
    for row in spent_rows or []:
        spent_by_task[str(row.get("task_id"))] = {
            role: to_hours(hours)
            for role, hours in row.items()
            if normalize_role(role) not in _SERVICE_COLUMNS and to_hours(hours) > 0
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
    # Полная смета — чтобы доля ролей без людей считалась от всей задачи, а не от остатка.
    item["estimate_total_hh"] = total
    # Место в очереди прошлого плана: при пересчёте взятые обязательства не
    # перетасовываются, новые задачи заполняют оставшуюся ёмкость.
    rank = (task.get("reason_facts") or {}).get("rank") if is_included(task) else None
    if rank:
        item["plan_rank"] = rank
    if total > 0:
        # SP списываются только на часы, которые реально выполняются: без ролей,
        # вынесенных в дефицит (частичная цель). Иначе ставка SP за час при
        # каждом пересчёте росла бы и план «уплывал» даже без отклонений.
        unstaffed = {normalize_role(r) for r in task.get("unstaffed_hh") or {}}
        estimate = {normalize_role(r): to_hours(h) for r, h in (task.get("estimated_hh") or {}).items()}
        work_total = total - sum(h for k, h in estimate.items() if k in unstaffed)
        work_left = sum(h for k, (_, h) in left.items() if k not in unstaffed)
        if work_total <= 0:
            work_total, work_left = total, sum(h for _, h in left.values())
        sp = float(task.get("estimation_sp") or 0)
        item["estimation_sp"] = round(sp * work_left / work_total, 4)
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
            if merged.get("reason_detail"):
                merged["reason_detail"] = humanize_roles(merged["reason_detail"], tasks)
        merged_tasks.append(merged)
    return merged_tasks


def close_quarter(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """После 6-го спринта планировать некуда: незакрытые задачи переходят в
    следующий квартал с честной причиной (а не «ждёт смежную команду»)."""
    closed = []
    for task in tasks:
        item = copy.deepcopy(task)
        if not is_done(item) and not is_cancelled(item) and not is_deferred(item) and is_included(item):
            estimated = _estimated_total(item)
            spent = sum(to_hours(h) for h in (item.get("spent_hh") or {}).values())
            share = f" Выполнено {min(spent / estimated, 1):.0%}." if estimated and spent else ""
            item.update({k: None for k in PLANNING_FIELDS})
            item.update(R.make_reason(
                R.ReasonCode.PLAN_EXCEEDED,
                f"Квартал завершён, {task_id(item)} не закрыта.{share} Остаток переходит в следующий квартал.",
                decision="postponed",
                target=R.TargetOption.FUNC_PARTIAL if spent else R.TargetOption.NOT_IN_QUARTER,
                facts={"task_id": task_id(item), "carry_over": True},
            ).to_dict())
        closed.append(item)
    return closed


def _apply_task_fact(task: dict[str, Any], fact: dict[str, Any], sprint: int) -> list[str]:
    warnings: list[str] = []
    tid = task_id(task)
    if fact.get("estimated_hh"):
        new_estimate = fact["estimated_hh"]
        if isinstance(new_estimate, dict):
            task.setdefault("estimated_hh_initial", copy.deepcopy(task.get("estimated_hh") or {}))
            task["estimated_hh"] = {role: to_hours(h) for role, h in new_estimate.items()}
            task["reestimated_sprint"] = sprint
        else:
            warnings.append(f"Переоценка {tid} пропущена: смета должна быть по ролям (роль: часы).")

    reference = task.get("estimated_hh") or {}
    spent = _canonical_roles(task.get("spent_hh") or {}, reference)
    for role, hours in _canonical_roles(fact.get("spent_hh") or {}, reference).items():
        spent[role] = spent.get(role, 0.0) + hours
    task["spent_hh"] = spent

    status, known = canonical_status(fact.get("status"))
    if not known:
        warnings.append(f"Статус «{fact.get('status')}» у {tid} не распознан — статус не изменён "
                        f"(допустимо: Done, InProgress, ToDo, Cancelled, Deferred).")
    if status == "Deferred":
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
    return warnings


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
    first_sprint: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Инженеры и ёмкость с учётом ушедших/отсутствующих и нанятых.

    Недоступный инженер остаётся в списке с нулевой ставкой: иначе и
    планировщик, и алерты решили бы, что роли «нет в компании», хотя человек
    есть, просто сейчас недоступен. Нанятый выходит на полную продуктивность
    не сразу (ONBOARDING_RAMP): его ёмкость — средняя на спринтах first_sprint…6.
    """
    active = [dict(e, capacity_rate=0.0, unavailable=True) if e.get("engineer_id") in unavailable else e
              for e in [*engineers, *hired.values()]]
    active_capacity = copy.deepcopy(capacity)
    engineer_hh = active_capacity.setdefault("engineer_hh", {})
    for eng_id, eng in hired.items():
        ramp = average_ramp(int(eng.get("joined_sprint") or first_sprint), first_sprint)
        engineer_hh[eng_id] = round(SPRINT_HOURS * float(eng.get("capacity_rate") or 1.0) * ramp, 1)
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
    for new_task in fact.get("tasks_added") or []:
        tid = task_id(new_task)
        if not tid or tid in tasks:
            warnings.append(f"Новая задача {tid or 'без id'} пропущена: пустой или повторяющийся id.")
            continue
        item = {"status": "ToDo", "spent_hh": {}, "estimated_hh": {}, **copy.deepcopy(new_task)}
        if not isinstance(item["estimated_hh"], dict):
            warnings.append(f"У новой задачи {tid} смета не по ролям — задача помечена «Не оценивалась».")
            item["estimated_hh"] = {}
        item["added_sprint"] = sprint
        tasks[tid] = item

    for tid, task_fact in (fact.get("tasks") or {}).items():
        task = tasks.get(tid)
        if task is None:
            warnings.append(f"Факт по {tid} пропущен: такой задачи нет в плане.")
        elif is_done(task) or is_cancelled(task):
            warnings.append(f"Факт по {tid} пропущен: задача уже закрыта или отменена.")
        else:
            warnings.extend(_apply_task_fact(task, task_fact, sprint))

    unavailable = (set(plan.get("engineers_unavailable") or []) | set(fact.get("engineers_unavailable") or [])) \
        - set(fact.get("engineers_returned") or [])
    hired = {e["engineer_id"]: dict(e) for e in plan.get("engineers_hired") or []}
    for eng in fact.get("engineers_hired") or []:
        hired.setdefault(eng["engineer_id"], {"joined_sprint": sprint + 1, **eng})
    active_engineers, active_capacity = active_resources(
        engineers, capacity, unavailable, hired, first_sprint=sprint + 1
    )

    if sprint >= QUARTER_SPRINTS:
        current = close_quarter(list(tasks.values()))
    else:
        current = plan_tasks(list(tasks.values()), active_capacity, dependencies, active_engineers,
                             planner=planner, start_sprint=sprint + 1)

    plan["current"] = link_dependencies(current, dependencies)
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
