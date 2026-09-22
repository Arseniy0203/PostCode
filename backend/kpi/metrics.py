# Ответственный: Человек 3 (starmap + explainability + KPI)
"""KPI квартального плана.

Формулы — дословно из условия задачи:
    Процент выполнения квартального плана (PI Predictability) =
        число инициатив, завершённых в течение 12 недель /
        число инициатив, включённых в первоначальный план × 100%
    Выполнение плана спринта (Say/Do Ratio) =
        фактически выполненные SP / первоначально запланированные SP × 100%

Знаменатель всегда берётся из Plan.baseline (Неделя 0), поэтому пересчёт
current его не меняет (см. contracts/plan_schema.md).

Функции принимают списки PlannedTask как dict или как dataclass-объекты.
Если одна задача представлена несколькими строками (срезы по спринтам),
строки схлопываются по task_id.

Сроки читаются через backend/recalculation/plan_fields.py — единый для проекта
доступ к полям задачи. Это поля-НОМЕРА СПРИНТОВ (planned_end_sprint,
actual_end_sprint, forecast_end_sprint), которые пишет planning/scheduler.py.
Одноимённые поля без суффикса в сыром датасете содержат даты Excel, поэтому
значения вне диапазона номеров спринтов отбрасываются.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from backend.explainability.reasons import QUARTER_SPRINTS
from backend.recalculation.plan_fields import (
    actual_end_sprint,
    field,
    forecast_end_sprint,
    is_done,
    planned_end_sprint,
    prodf_id,
    task_id,
)

PI_NORM = (80.0, 100.0)
SAY_DO_NORM = (90.0, 105.0)

Mode = Literal["fact", "forecast"]
# committed  — считаем только задачи, взятые в квартал (обещание команды);
# initiative — считаем все задачи инициативы из baseline, включая перенесённые
#              (обещание бизнесу: цель достигнута целиком или нет).
Scope = Literal["committed", "initiative"]

# Статус метрики для UI и алертов. Значение value остаётся числом (0.0 без данных),
# а по статусу видно, можно ли его сравнивать с нормой:
#   ok / out_of_norm — есть что сравнивать;
#   no_data — знаменатель пуст (на спринт ничего не планировалось, план пуст);
#   future  — спринт ещё не наступил, факта быть не может.
STATUS_LABELS = {
    "ok": "в норме",
    "out_of_norm": "вне нормы",
    "no_data": "нет данных",
    "future": "спринт ещё не наступил",
}


def _status(value: float, norm: tuple[float, float], has_data: bool) -> str:
    if not has_data:
        return "no_data"
    return "ok" if norm[0] <= value <= norm[1] else "out_of_norm"


def _cancelled(task: dict[str, Any] | None) -> bool:
    """Отменена заказчиком: цель сняли, а не провалили.

    Знаменатель PI по формуле условия зафиксирован на Неделе 0, поэтому такие
    инициативы остаются в нём, но выводятся отдельным списком cancelled_prodf.
    """
    if task is None or is_done(task):
        return False
    status = str(task["status"] or "").strip().casefold()
    return task["decision"] == "cancelled" or status in ("cancelled", "canceled", "отменена")


def _collapse(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """task_id -> сводная запись задачи (строки-срезы одной задачи схлопываются).

    Все поля читаются через recalculation/plan_fields, чтобы правило чтения
    (planned_end_sprint против planned_end, prodf_id против «Номер инициативы»)
    было в проекте одно.
    """
    tasks: dict[str, dict[str, Any]] = {}
    for row in rows or ():
        tid = task_id(row)
        if not tid:
            continue
        sprint = field(row, "sprint_number", default=0)
        rec = tasks.setdefault(tid, {
            "task_id": tid,
            "prodf_id": None,
            "estimation_sp": 0.0,
            "decision": None,
            "status": None,
            "planned_end": None,
            "actual_end": None,
            "forecast_end": None,
            "_sprint": None,
        })
        rec["prodf_id"] = prodf_id(row) or rec["prodf_id"]
        rec["estimation_sp"] = float(
            field(row, "estimation_sp", default=rec["estimation_sp"]) or 0
        )
        for key, read in (
            ("planned_end", planned_end_sprint),
            ("actual_end", actual_end_sprint),
            ("forecast_end", forecast_end_sprint),
        ):
            value = read(row)
            if value is not None and (rec[key] is None or value > rec[key]):
                rec[key] = value
        # Статус и решение — из самого свежего среза.
        if rec["_sprint"] is None or sprint >= rec["_sprint"]:
            rec["_sprint"] = sprint
            rec["status"] = field(row, "status", default=rec["status"])
            rec["decision"] = field(row, "decision", default=rec["decision"])
    return tasks


def _included(task: dict[str, Any]) -> bool:
    # Задача без явного решения в baseline считается включённой.
    return task["decision"] in (None, "included")


def _pi_details(baseline: Any, current: Any, mode: Mode,
                scope: Scope = "committed") -> dict[str, Any]:
    if mode not in ("fact", "forecast"):
        raise ValueError(f"mode должен быть 'fact' или 'forecast', получен {mode!r}")
    if scope not in ("committed", "initiative"):
        raise ValueError(
            f"scope должен быть 'committed' или 'initiative', получен {scope!r}"
        )
    base = _collapse(baseline)
    cur = _collapse(current)

    # Знаменатель одинаков для обоих срезов: инициативы, которые взяли в квартал.
    committed: dict[str, list[str]] = {}
    all_tasks: dict[str, list[str]] = {}
    for task in base.values():
        # Done на Неделе 0 — факт прошлых периодов, в обещание квартала не входит
        # (ответ экспертов; планировщик помечает такие задачи included в спринт 0).
        if is_done(task):
            continue
        initiative = task["prodf_id"]
        if initiative is None:
            continue
        all_tasks.setdefault(initiative, []).append(task["task_id"])
        if _included(task):
            committed.setdefault(initiative, []).append(task["task_id"])

    checked = committed if scope == "committed" else {
        initiative: all_tasks[initiative] for initiative in committed
    }

    completed, not_completed, cancelled = [], {}, []
    for initiative, task_ids in sorted(checked.items()):
        if all(_cancelled(cur.get(t)) for t in task_ids):
            cancelled.append(initiative)
        blocking = [t for t in task_ids if not _task_completes(cur.get(t), mode)]
        if blocking:
            not_completed[initiative] = sorted(blocking)
        else:
            completed.append(initiative)

    denominator = len(committed)
    value = len(completed) / denominator * 100 if denominator else 0.0
    status = _status(value, PI_NORM, bool(denominator))
    return {
        "mode": mode,
        "scope": scope,
        "value": value,
        "numerator": len(completed),
        "denominator": denominator,
        "completed_prodf": completed,
        "not_completed_prodf": not_completed,
        "cancelled_prodf": cancelled,
        "baseline_scope": {k: sorted(v) for k, v in sorted(checked.items())},
        "in_norm": status == "ok",
        "status": status,
        "status_label": STATUS_LABELS[status],
    }


def _task_completes(task: dict[str, Any] | None, mode: Mode) -> bool:
    if task is None:
        return False
    if is_done(task):
        # Done без даты окончания считаем завершённым: статус — это факт.
        return task["actual_end"] is None or task["actual_end"] <= QUARTER_SPRINTS
    if mode == "fact" or task["decision"] in ("postponed", "cancelled"):
        return False
    end = task["forecast_end"] if task["forecast_end"] is not None else task["planned_end"]
    return end is not None and end <= QUARTER_SPRINTS


def pi_predictability(
    baseline: Any,
    current: Any,
    mode: Mode = "fact",
    scope: Scope = "committed",
) -> float:
    """Процент выполнения квартального плана (PI Predictability), %.

    Формула: число инициатив PRODF, завершённых в течение 12 недель /
             число инициатив, включённых в первоначальный план × 100%.

    Знаменатель в обоих срезах один: PRODF, у которых в baseline есть задачи
    с decision="included", то есть инициативы, взятые в квартал.

    Различается числитель:
        scope="committed"  — инициатива завершена, если закрыты её задачи,
            взятые в квартал. Отвечает на вопрос «выполнили ли то, что обещали
            на Неделе 0»;
        scope="initiative" — инициатива завершена, только если закрыты ВСЕ её
            задачи из baseline, включая перенесённые. Отвечает на вопрос
            заказчика «достигнута ли цель целиком».

    Завершённость задачи:
        mode="fact"     — status == Done и actual_end <= 6-го спринта;
        mode="forecast" — Done, либо прогноз (forecast_end, иначе planned_end)
                          <= 6-го спринта и задача не перенесена/не отменена.

    Args:
        baseline: неизменяемый список PlannedTask на Неделю 0 (Plan.baseline).
        current: актуальный список PlannedTask (Plan.current).
        mode: "fact" — по фактическим результатам, "forecast" — по прогнозу.
        scope: "committed" — по взятым в квартал задачам, "initiative" — по всей
            инициативе.

    Returns:
        Значение в процентах; 0.0, если в baseline нет включённых инициатив.
    """
    return _pi_details(baseline, current, mode, scope)["value"]


def _say_do_details(baseline: Any, current: Any, sprint_n: int,
                    current_sprint: int | None = None) -> dict[str, Any]:
    planned = [t for t in _collapse(baseline).values()
               if _included(t) and t["planned_end"] == sprint_n]
    done = [t for t in _collapse(current).values()
            if is_done(t) and t["actual_end"] == sprint_n]
    planned_sp = sum(t["estimation_sp"] for t in planned)
    done_sp = sum(t["estimation_sp"] for t in done)
    value = done_sp / planned_sp * 100 if planned_sp else 0.0
    if current_sprint is not None and sprint_n > current_sprint:
        status = "future"
    else:
        status = _status(value, SAY_DO_NORM, bool(planned_sp))
    return {
        "sprint": sprint_n,
        "value": value,
        "done_sp": done_sp,
        "planned_sp": planned_sp,
        "done_tasks": sorted(t["task_id"] for t in done),
        "planned_tasks": sorted(t["task_id"] for t in planned),
        "in_norm": status == "ok",
        "status": status,
        "status_label": STATUS_LABELS[status],
    }


def say_do_ratio(baseline: Any, current: Any, sprint_n: int) -> float:
    """Выполнение плана спринта (Say/Do Ratio), %.

    Формула: фактически выполненные SP в спринте sprint_n (из current) /
             первоначально запланированные SP на этот спринт (ИЗ BASELINE,
             а не из current) × 100%.

    Задача засчитывается в спринт своего окончания: план — по planned_end
    в baseline, факт — по actual_end задач со статусом Done в current.

    Args:
        baseline: неизменяемый список PlannedTask на Неделю 0 (Plan.baseline).
        current: актуальный список PlannedTask (Plan.current), источник факта.
        sprint_n: номер спринта.

    Returns:
        Значение в процентах; 0.0, если на спринт ничего не планировалось.
    """
    return _say_do_details(baseline, current, sprint_n)["value"]


def kpi_breakdown(
    baseline: Any,
    current: Any,
    sprints: Iterable[int] = range(1, QUARTER_SPRINTS + 1),
    current_sprint: int | None = None,
) -> dict[str, Any]:
    """Расшифровка KPI: что вошло в числитель и знаменатель каждой метрики.

    Returns:
        {
            "pi_predictability": {"fact": {...}, "forecast": {...},
                                  "fact_initiative": {...},
                                  "forecast_initiative": {...}},
            "say_do": [{"sprint", "value", "done_sp", "planned_sp",
                        "done_tasks", "planned_tasks", "in_norm",
                        "status", "status_label"}, ...],
            "current_sprint": номер последнего прошедшего спринта или None,
            "norms": {"pi_predictability": (80, 100), "say_do": (90, 105)},
        }
    """
    return {
        "pi_predictability": {
            "fact": _pi_details(baseline, current, "fact"),
            "forecast": _pi_details(baseline, current, "forecast"),
            "fact_initiative": _pi_details(baseline, current, "fact", "initiative"),
            "forecast_initiative": _pi_details(
                baseline, current, "forecast", "initiative"),
        },
        "say_do": [_say_do_details(baseline, current, n, current_sprint) for n in sprints],
        "current_sprint": current_sprint,
        "norms": {"pi_predictability": PI_NORM, "say_do": SAY_DO_NORM},
    }
