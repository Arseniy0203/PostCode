# Ответственный: Человек 2 (recalculation + risk)
"""Единая точка входа для UI: данные → план Недели 0 → пересчёт → отчёт.

    from backend.recalculation.pipeline import run_quarter
    report = run_quarter("key_task_delay", until_sprint=3)

UI не собирает конвейер руками: всё, что нужно страницам плана, рисков и
«что если», лежит в одном словаре report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from backend.recalculation.baseline import save_baseline
from backend.recalculation.plan_fields import normalize_role
from backend.recalculation.recalc import Planner, apply_sprint_fact, plan_tasks, prepare_tasks
from backend.recalculation.scenarios import SCENARIOS, simulate
from backend.recalculation.summary import quarter_summary
from backend.recalculation.what_if import hiring_impact, hiring_roadmap, plan_summary
from backend.risk.alerts import annotate_lifecycle, collect_alerts, summarize_by_prodf
from backend.risk.data_quality import check_data_quality

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"
_FACT_SERVICE = {normalize_role(c) for c in ("task_id", "status", "статус", "Исходное время")}


def load_dataset(data_dir: Path | str = DATA_DIR) -> dict[str, Any]:
    """Читает data/processed/*.json и готовит задачи и ёмкость к планированию."""
    from backend.planning.capacity import engineer_capacity_hh, team_capacity_sp

    root = Path(data_dir)

    def load(name: str, default: Any = None) -> Any:
        path = root / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    raw_tasks = load("tasks", [])
    estimates = load("estimates", [])
    engineers = load("engineers_profiles", [])
    history = load("team_history", [])
    dependencies = load("task_dependencies", [])
    excluded = [row["dependency"] for row in load("excluded_dependencies_log", []) or []]
    return {
        "raw_tasks": raw_tasks,
        "tasks": prepare_tasks(raw_tasks, estimates, load("spent_time_roles", [])),
        "estimates": estimates,
        "engineers": engineers,
        "dependencies": dependencies,
        "all_dependencies": [*dependencies, *excluded],
        "team_history": history,
        "capacity": {
            "team_sp": {t: team_capacity_sp(t, history) for t in {h["team_id"] for h in history}},
            "engineer_hh": {e["engineer_id"]: engineer_capacity_hh(e["engineer_id"], 1, engineers)
                            for e in engineers},
        },
    }


def build_week0(data: dict[str, Any], planner: Planner | None = None) -> dict[str, Any]:
    """План Недели 0: baseline, алерты на старте и отчёт о качестве данных.

    Тем же шагом plan_tasks, что и пересчёт: задачи в работе планируются на
    остаток часов, задачи без сметы откладываются с причиной «Не оценивалась».
    """
    current = plan_tasks(data["tasks"], data["capacity"], data["dependencies"], data["engineers"],
                         planner=planner, start_sprint=1)
    plan = save_baseline({"current": current})
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

        return kpi_breakdown(plan["baseline"], plan["current"])
    except Exception as exc:  # KPI — модуль другой команды; отчёт не должен падать из-за него
        return {"error": f"KPI недоступны: {exc}"}


def build_report(plan: dict[str, Any], data: dict[str, Any], planner: Planner | None = None,
                 with_hiring: bool = True) -> dict[str, Any]:
    alerts = plan.get("alerts") or []
    return {
        "plan": plan,
        "sprint": plan.get("current_sprint", 0),
        "summary": {"baseline": plan_summary(list(plan["baseline"])),
                    "current": plan_summary(plan["current"])},
        "alerts": [a.to_dict() for a in alerts],
        "resolved_alerts": plan.get("resolved_alerts") or [],
        "by_prodf": summarize_by_prodf(alerts),
        "history": plan.get("history") or [],
        "warnings": plan.get("warnings") or [],
        "data_quality": [a.to_dict() for a in plan.get("data_quality") or []],
        "hiring": (hiring_impact(data["tasks"], data["capacity"], data["dependencies"],
                                 data["engineers"], planner=planner) if with_hiring else None),
        "hiring_roadmap": (hiring_roadmap(data["tasks"], data["capacity"], data["dependencies"],
                                          data["engineers"], planner=planner) if with_hiring else None),
        "quarter_summary": quarter_summary(plan),
        "kpi": _kpi(plan),
        "scenarios": dict(SCENARIOS),
    }


def run_quarter(
    scenario: str = "on_track",
    until_sprint: int = 0,
    *,
    trigger_sprint: int = 1,
    target: Any = None,
    data_dir: Path | str = DATA_DIR,
    planner: Planner | None = None,
    with_hiring: bool = True,
) -> dict[str, Any]:
    """План Недели 0 и прогон сценария до until_sprint (0 — только Неделя 0)."""
    data = load_dataset(data_dir)
    plan = build_week0(data, planner)
    if until_sprint:
        simulate(plan, scenario, until_sprint, dependencies=data["dependencies"],
                 engineers=data["engineers"], capacity=data["capacity"],
                 trigger_sprint=trigger_sprint, target=target, planner=planner)
    return build_report(plan, data, planner, with_hiring)


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
