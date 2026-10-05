"""Единая точка входа для UI: данные → план Недели 0 → пересчёт → отчёт.

    from backend.recalculation.pipeline import run_quarter
    report = run_quarter("key_task_delay", until_sprint=3)

UI не собирает конвейер руками: всё, что нужно страницам плана, рисков и
«что если», лежит в одном словаре report.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from backend.recalculation.baseline import save_baseline
from backend.recalculation.next_quarter import next_quarter_backlog
from backend.recalculation.plan_fields import link_dependencies, normalize_role
from backend.recalculation.recalc import (
    Planner,
    to_hours,
    apply_sprint_fact,
    mark_deferred,
    plan_tasks,
    prepare_tasks,
)
from backend.recalculation.scenarios import SCENARIOS, simulate
from backend.recalculation.summary import quarter_summary
from backend.recalculation.what_if import hiring_impact, hiring_roadmap, plan_summary
from backend.risk.alerts import Alert, annotate_lifecycle, collect_alerts, summarize_by_prodf
from backend.risk.data_quality import check_data_quality

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"
_FACT_SERVICE = {normalize_role(c) for c in ("task_id", "status", "статус", "Исходное время")}


def load_dataset(data_dir: Path | str = DATA_DIR) -> dict[str, Any]:
    """Читает data/processed/*.json и готовит задачи и ёмкость к планированию."""
    root = Path(data_dir)
    if not (root / "tasks.json").exists():
        raise FileNotFoundError(f"Не найден {root / 'tasks.json'}: проверьте путь к data/processed")

    def load(name: str, default: Any = None) -> Any:
        path = root / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    parsed = {name: load(name, []) for name in ("tasks", "estimates", "engineers_profiles",
                                                "team_history", "task_dependencies",
                                                "spent_time_roles")}
    excluded = [row["dependency"] for row in load("excluded_dependencies_log", []) or []]
    return dataset_from_parsed(parsed, excluded)


def dataset_from_parsed(parsed: dict[str, Any], excluded: list[dict[str, Any]] | None = None
                        ) -> dict[str, Any]:
    """Данные к планированию из таблиц датасета (parse_excel или data/processed)."""
    from backend.planning.capacity import engineer_capacity_hh, team_capacity_sp

    raw_tasks = parsed.get("tasks") or []
    estimates = parsed.get("estimates") or []
    engineers = parsed.get("engineers_profiles") or []
    history = parsed.get("team_history") or []
    dependencies = parsed.get("task_dependencies") or []
    excluded = list(excluded or [])
    return {
        "raw_tasks": raw_tasks,
        "tasks": prepare_tasks(raw_tasks, estimates, parsed.get("spent_time_roles") or []),
        "estimates": estimates,
        "engineers": engineers,
        "dependencies": dependencies,
        "all_dependencies": [*dependencies, *excluded],
        "team_history": history,
        "capacity": {
            "team_sp": {t: team_capacity_sp(t, history) for t in sorted({h["team_id"] for h in history})},
            "engineer_hh": {e["engineer_id"]: engineer_capacity_hh(e["engineer_id"], 1, engineers)
                            for e in engineers},
        },
    }


def build_week0(data: dict[str, Any], planner: Planner | None = None,
                defer: dict[str, str] | list[str] | None = None) -> dict[str, Any]:
    """План Недели 0: baseline, алерты на старте и отчёт о качестве данных.

    Тем же шагом plan_tasks, что и пересчёт: задачи в работе планируются на
    остаток часов, задачи без сметы откладываются с причиной «Не оценивалась».
    defer — задачи, которые руководитель решил сразу перенести в следующий
    квартал (id или {id: комментарий]}): так до фиксации плана видно, что
    это освобождает для остальных задач.
    """
    tasks = copy.deepcopy(data["tasks"])
    unknown = mark_deferred(tasks, defer)
    current = plan_tasks(tasks, data["capacity"], data["dependencies"], data["engineers"],
                         planner=planner, start_sprint=1)
    plan = save_baseline({"current": link_dependencies(current, data["dependencies"])})
    plan["warnings"] = [f"Перенос {tid} пропущен: такой задачи нет." for tid in unknown]
    plan["capacity"] = data["capacity"]
    plan["history"][0].update(engineers_unavailable=[], engineers_hired=[], capacity=data["capacity"])
    alerts, _ = annotate_lifecycle(collect_alerts(plan, data["dependencies"], data["engineers"]), None, 0)
    plan["alerts"] = alerts
    plan["resolved_alerts"] = []
    plan["history"][0]["alerts"] = [a.to_dict() for a in alerts]
    plan["data_quality"] = check_data_quality(
        data["raw_tasks"], data["estimates"], data["all_dependencies"],
        engineers=data["engineers"], team_history=data["team_history"],
    )
    return plan


def _kpi(plan: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from backend.kpi.metrics import kpi_breakdown

        return kpi_breakdown(plan["baseline"], plan["current"],
                             current_sprint=int(plan.get("current_sprint") or 0))
    except Exception as exc:  # KPI — модуль другой команды; отчёт не должен падать из-за него
        return {"error": f"KPI недоступны: {exc}"}


def _as_alert(item: Any) -> Alert:
    return item if isinstance(item, Alert) else Alert.from_dict(item)


def _jsonable(value: Any) -> Any:
    """Копия структуры, которую можно отдать в json.dumps / Streamlit / API."""
    if isinstance(value, Alert):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        items = [_jsonable(v) for v in value]
        return sorted(items) if isinstance(value, set) else items
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value


def _staff(plan: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Штат на текущий спринт: без ушедших/отсутствующих, с нанятыми."""
    gone = set(plan.get("engineers_unavailable") or [])
    return [e for e in [*data["engineers"], *(plan.get("engineers_hired") or [])]
            if e.get("engineer_id") not in gone]


def _starmap(plan: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    try:
        from backend.starmap.bus_factor import starmap_summary

        return starmap_summary(plan, _staff(plan, data), data["estimates"])
    except Exception as exc:  # модуль другой команды: отчёт не должен падать из-за него
        return {"error": f"Звёздная карта недоступна: {exc}"}


def _strict_comparison(data: dict[str, Any]) -> dict[str, Any]:
    """Сколько взяли бы в квартал по строгому правилу «нет роли — задачу не берём»."""
    from functools import partial

    from backend.planning.scheduler import build_baseline_plan

    strict = plan_tasks(copy.deepcopy(data["tasks"]), data["capacity"], data["dependencies"],
                        data["engineers"], planner=partial(build_baseline_plan, missing_roles="postpone"))
    return plan_summary(strict)


def _sprints() -> list[dict[str, Any]]:
    from backend.recalculation.quarter import QUARTER_SPRINTS, sprint_dates, sprint_label

    return [{"sprint": n, "label": sprint_label(n), "start": sprint_dates(n)[0],
             "end": sprint_dates(n)[1]} for n in range(1, QUARTER_SPRINTS + 1)]


def build_report(plan: dict[str, Any], data: dict[str, Any], planner: Planner | None = None,
                 with_hiring: bool = True) -> dict[str, Any]:
    """Весь отчёт для UI; результат целиком сериализуется в JSON."""
    alerts = [_as_alert(a) for a in plan.get("alerts") or []]
    return _jsonable({
        "plan": plan,
        "sprint": plan.get("current_sprint", 0),
        "summary": {"baseline": plan_summary(list(plan["baseline"])),
                    "current": plan_summary(plan["current"])},
        "alerts": alerts,
        "resolved_alerts": plan.get("resolved_alerts") or [],
        "by_prodf": summarize_by_prodf(alerts),
        "history": plan.get("history") or [],
        "warnings": plan.get("warnings") or [],
        "data_quality": [_as_alert(a) for a in plan.get("data_quality") or []],
        "hiring": (hiring_impact(data["tasks"], data["capacity"], data["dependencies"],
                                 data["engineers"], planner=planner) if with_hiring else None),
        "hiring_roadmap": (hiring_roadmap(data["tasks"], data["capacity"], data["dependencies"],
                                          data["engineers"], planner=planner) if with_hiring else None),
        "quarter_summary": quarter_summary(plan),
        "next_quarter": next_quarter_backlog(plan, data["engineers"], data["dependencies"]),
        "kpi": _kpi(plan),
        "scenarios": dict(SCENARIOS),
        "starmap": _starmap(plan, data),
        "strict_comparison": _strict_comparison(data) if with_hiring else None,
        "sprints": _sprints(),
        "dependencies": data["dependencies"],
    })


def run_quarter(
    scenario: str = "on_track",
    until_sprint: int = 0,
    *,
    trigger_sprint: int = 1,
    target: Any = None,
    data_dir: Path | str = DATA_DIR,
    planner: Planner | None = None,
    with_hiring: bool = True,
    defer: dict[str, str] | list[str] | None = None,
    missing_roles: str = "partial",
    facts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """План Недели 0 и прогон сценария до until_sprint (0 — только Неделя 0).

    defer — задачи, которые руководитель переносит в следующий квартал ещё на
    Неделе 0 (id или {id: комментарий}).
    missing_roles — правило для ролей без людей: "partial" (частичная цель из
    «Вариантов выбора цели», по умолчанию) или "postpone" (строго переносить).
    facts — загруженные факты следующих спринтов (load_fact_excel), применяются
    по очереди после сценария.
    """
    if planner is None and missing_roles != "partial":
        from functools import partial

        from backend.planning.scheduler import build_baseline_plan

        planner = partial(build_baseline_plan, missing_roles=missing_roles)
    data = load_dataset(data_dir)
    plan = build_week0(data, planner, defer=defer)
    if until_sprint:
        simulate(plan, scenario, until_sprint, dependencies=data["dependencies"],
                 engineers=data["engineers"], capacity=data["capacity"],
                 trigger_sprint=trigger_sprint, target=target, planner=planner)
    for fact in facts or ():
        apply_uploaded_fact(plan, data, fact, planner)
    return build_report(plan, data, planner, with_hiring)


def fact_template(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Шаблон факта следующего спринта: задачи, которые в нём идут, и часы по плану.

    Руководитель скачивает таблицу, правит статус и часы и загружает обратно
    (формат load_fact_excel: task_id | status | <роль>: часы за спринт).
    """
    plan = report["plan"]
    sprint = int(plan.get("current_sprint") or 0) + 1
    role_of: dict[str, str] = {}
    for eng in load_dataset().get("engineers", []):
        role_of.setdefault(str(eng.get("engineer_id")), str(eng.get("role")))
    for eng in plan.get("engineers_hired") or []:
        role_of.setdefault(str(eng.get("engineer_id")), str(eng.get("role")))
    rows = []
    for task in plan.get("current") or []:
        if task.get("status") == "Done" or task.get("decision") != "included":
            continue
        hours = (task.get("allocation") or {}).get(str(sprint)) or (task.get("allocation") or {}).get(sprint)
        closes = task.get("forecast_end_sprint") == sprint
        if not hours and not closes:
            continue
        hours = hours or {}
        row: dict[str, Any] = {"task_id": task["task_id"],
                               "status": "Done" if task.get("forecast_end_sprint") == sprint else "InProgress"}
        for eng_id, h in hours.items():
            role = role_of.get(str(eng_id), str(eng_id))
            row[role] = round(row.get(role, 0) + float(h), 2)
        rows.append(row)
    return rows


def fact_from_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Факт спринта из таблицы: task_id | status | <роль>: часы за спринт | ..."""
    tasks: dict[str, Any] = {}
    for row in rows:
        tid = str(row.get("task_id") or "").strip()
        if not tid:
            continue
        status = row.get("status") or row.get("статус")
        spent = {}
        for column, value in row.items():
            if normalize_role(column) in _FACT_SERVICE:
                continue
            try:
                hours = float(value)
            except (TypeError, ValueError):
                continue
            if hours > 0:
                spent[column] = hours
        entry: dict[str, Any] = {"spent_hh": spent}
        if status and str(status).strip():
            entry["status"] = str(status).strip()
        tasks[tid] = entry
    return {"tasks": tasks}


def load_fact_excel(path: Path | str) -> dict[str, Any]:
    import pandas as pd

    frame = pd.read_excel(path).dropna(how="all")
    return fact_from_rows(frame.to_dict(orient="records"))


def apply_uploaded_fact(plan: dict[str, Any], data: dict[str, Any], fact: dict[str, Any],
                        planner: Planner | None = None) -> dict[str, Any]:
    """Применяет загруженный пользователем факт за следующий спринт."""
    return apply_sprint_fact(plan, fact, dependencies=data["dependencies"], engineers=data["engineers"],
                             capacity=data["capacity"], planner=planner)



# --- Загрузка датасета и факта из Excel (поток UI: «загрузить файл спринта») ----


def _parse_excel_bytes(data: bytes | str | Path) -> dict[str, Any]:
    import io

    from backend.ingestion.parse_dataset import parse_excel

    return parse_excel(str(data) if isinstance(data, (str, Path)) else io.BytesIO(data))


def week0_from_excel(data: bytes | str | Path, planner: Planner | None = None,
                     defer: dict[str, str] | list[str] | None = None) -> dict[str, Any]:
    """План Недели 0 из загруженного Excel-датасета — тем же конвейером, что run_quarter.

    Задачи в работе планируются на остаток (смета − Spent_time_roles), ёмкость
    команды — средний velocity × 0.8, фантомные связи уходят в отчёт о качестве
    данных. Возвращает ключи, которые ждёт frontend/app.py (parsed, clean_deps,
    plan, alerts, starmap) и полный отчёт report.

    defer — задачи, которые руководитель вручную снял с квартала (id или
    {id: комментарий}): они не планируются, а их ёмкость достаётся остальным.
    """
    from backend.ingestion.parse_dataset import filter_orphan_dependencies

    parsed = _parse_excel_bytes(data)
    clean, excluded = filter_orphan_dependencies(parsed["task_dependencies"], parsed["tasks"])
    parsed = {**parsed, "task_dependencies": clean}
    dataset = dataset_from_parsed(parsed, [row["dependency"] for row in excluded]
                                  + list(parsed.get("duplicate_dependencies") or []))
    plan = build_week0(dataset, planner, defer=defer)
    report = build_report(plan, dataset, planner)
    from backend.recalculation.overload import overload_report
    overload = _jsonable(overload_report(dataset, plan, report.get("strict_comparison")))
    return {"parsed": parsed, "clean_deps": clean, "dataset": dataset, "plan": plan,
            "alerts": report["alerts"], "starmap": report["starmap"], "report": report,
            "overload": overload}


def fact_from_dataset_excel(data: bytes | str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Факт спринта из Excel в формате исходного датасета (как в demo_scenarios).

    В таком файле Spent_time_roles — НАКОПЛЕННЫЕ часы с начала работ, а пересчёт
    ждёт часы за спринт: из накопленного вычитается то, что план уже учёл
    (spent_hh задачи). Статус передаётся, только если он изменился.
    """
    parsed = _parse_excel_bytes(data)
    by_id = {str(t.get("task_id")): t for t in plan.get("current") or []}
    cumulative: dict[str, dict[str, float]] = {}
    for row in parsed.get("spent_time_roles") or []:
        tid = str(row.get("task_id") or "").strip()
        cumulative[tid] = {k: to_hours(v) for k, v in row.items()
                           if normalize_role(k) not in _FACT_SERVICE and to_hours(v) > 0}
    tasks: dict[str, Any] = {}
    for row in parsed.get("tasks") or []:
        tid = str(row.get("task_id") or "").strip()
        task = by_id.get(tid)
        if not tid or task is None:
            continue
        done_before = {normalize_role(r): to_hours(h) for r, h in (task.get("spent_hh") or {}).items()}
        delta = {}
        for role, hours in cumulative.get(tid, {}).items():
            extra = hours - done_before.get(normalize_role(role), 0.0)
            if extra > 0.05:
                delta[role] = round(extra, 2)
        status = str(row.get("status") or "").strip()
        changed = status and status.casefold() != str(task.get("status") or "").casefold()
        if delta or changed:
            entry: dict[str, Any] = {"spent_hh": delta}
            if status:
                entry["status"] = status
            tasks[tid] = entry
    return {"tasks": tasks}
