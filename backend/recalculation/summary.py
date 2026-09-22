# Ответственный: Человек 2 (recalculation + risk)
"""Итоги квартала: что сделано, что переходит в следующий квартал и почему.

До 6-го спринта — прогноз итогов по текущему плану, после — факт. Для
незавершённых задач предлагается итог из справочника «Варианты выбора цели»,
для закрытых — результат из «Результатов работ при закрытии задачи».
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.explainability.reasons import ClosingResult, TargetOption
from backend.recalculation.plan_fields import (
    QUARTER_SPRINTS,
    current_sprint,
    end_sprint,
    field,
    is_done,
    is_included,
    prodf_id,
    task_id,
)
from backend.recalculation.quarter import sprint_label
from backend.recalculation.recalc import is_cancelled, is_deferred
from backend.risk.alerts import work_progress


def _outcome(task: Any, final: bool) -> dict[str, Any]:
    tid = task_id(task)
    progress = work_progress(task)
    target = field(task, "target_label", default=None)
    partial = bool(field(task, "unstaffed_hh", default=None))
    if is_done(task):
        detail = f"закрыта в {sprint_label(field(task, 'actual_end_sprint'))}"
        if partial and target:
            detail += f" с частичной целью «{target}»"
        return {"task_id": tid, "outcome": "done", "label": ClosingResult.ACHIEVED.label,
                "detail": detail, "partial": partial}
    if is_cancelled(task):
        return {"task_id": tid, "outcome": "cancelled", "label": TargetOption.CANCELLED_BY_CUSTOMER.label,
                "detail": field(task, "reason_detail", default="") or "отменена"}
    if is_deferred(task):
        label = (TargetOption.FUNC_PARTIAL if progress else TargetOption.NOT_IN_QUARTER).label
        return {"task_id": tid, "outcome": "deferred", "label": label,
                "detail": field(task, "reason_detail", default="") or "перенесена в следующий квартал"}
    end = end_sprint(task)
    if not final and is_included(task) and end is not None and end <= QUARTER_SPRINTS:
        return {"task_id": tid, "outcome": "on_plan", "label": target or "в плане квартала",
                "detail": f"прогноз окончания — {sprint_label(end)}"
                          + (" (частичная цель: часть ролей без людей)" if partial else ""),
                "partial": partial}
    if progress > 0:
        return {"task_id": tid, "outcome": "carry_over", "label": TargetOption.FUNC_PARTIAL.label,
                "detail": f"выполнено {progress:.0%}, остаток переходит в следующий квартал"}
    return {"task_id": tid, "outcome": "not_in_quarter", "label": TargetOption.NOT_IN_QUARTER.label,
            "detail": field(task, "reason_detail", default="") or "не взята в квартал"}


def quarter_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """Итоги по задачам и инициативам (Done до Недели 0 не учитываются)."""
    final = current_sprint(plan) >= QUARTER_SPRINTS
    closed_before = {task_id(t) for t in plan.get("baseline") or () if is_done(t)}
    rows = [_outcome(t, final) for t in plan.get("current") or [] if task_id(t) not in closed_before]
    by_task = {task_id(t): t for t in plan.get("current") or []}

    counts: dict[str, int] = defaultdict(int)
    initiatives: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    partial_by_prodf: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        counts[row["outcome"]] += 1
        initiatives[prodf_id(by_task[row["task_id"]])][row["outcome"]].append(row["task_id"])
        if row.get("partial"):
            partial_by_prodf[prodf_id(by_task[row["task_id"]])].append(row["task_id"])

    prodf_rows = []
    for prodf, groups in sorted(initiatives.items()):
        # Отменённое заказчиком — не часть цели: инициатива может быть выполнена без него.
        total = sum(len(v) for k, v in groups.items() if k != "cancelled")
        finished = len(groups.get("done", [])) + (0 if final else len(groups.get("on_plan", [])))
        if total == 0:
            status = "отменена"
        elif finished == total and partial_by_prodf.get(prodf):
            status = ("выполнена с частичной целью" if final
                      else "укладывается в квартал с частичной целью")
        elif finished == total:
            status = "выполнена" if final else "укладывается в квартал"
        elif finished or groups.get("carry_over"):
            status = "частично"
        else:
            status = "не в квартале"
        prodf_rows.append({"prodf_id": prodf, "status": status, "tasks": dict(groups),
                           "partial_tasks": sorted(partial_by_prodf.get(prodf, [])),
                           "done_share": round(finished / total, 2) if total else 0.0})
    return {
        "mode": "final" if final else "forecast",
        "sprint": current_sprint(plan),
        "counts": dict(counts),
        "tasks": rows,
        "initiatives": prodf_rows,
    }
