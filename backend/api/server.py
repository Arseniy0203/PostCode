"""Тонкий API-слой поверх run_quarter — для интеграции с внешними системами.

Streamlit-приложение вызывает backend напрямую как библиотеку; API нужен, если
отчёт забирает другой сервис. Функции работают и без FastAPI; если он
установлен (pip install fastapi uvicorn), поднимается HTTP-сервер:

    uvicorn backend.api.server:app --reload
    GET /plan?scenario=key_task_delay&until_sprint=3
    GET /alerts?until_sprint=3
    GET /starmap
"""

from __future__ import annotations

from typing import Any

from backend.recalculation.pipeline import run_quarter


def get_plan(scenario: str = "on_track", until_sprint: int = 0) -> dict[str, Any]:
    """План (baseline + current), итоги квартала и KPI."""
    report = run_quarter(scenario, until_sprint, with_hiring=False)
    return {key: report[key] for key in ("sprint", "plan", "summary", "quarter_summary", "kpi", "sprints")}


def get_alerts(scenario: str = "on_track", until_sprint: int = 0) -> dict[str, Any]:
    """Алерты текущего пересчёта, решённые алерты и сводка по инициативам."""
    report = run_quarter(scenario, until_sprint, with_hiring=False)
    return {key: report[key] for key in ("sprint", "alerts", "resolved_alerts", "by_prodf", "data_quality")}


def get_starmap(scenario: str = "on_track", until_sprint: int = 0) -> dict[str, Any]:
    """Звёздная карта: Bus Factor по командам и компании, рекомендации нанять/обучить."""
    return run_quarter(scenario, until_sprint, with_hiring=False)["starmap"]


try:  # FastAPI — необязательная зависимость
    from fastapi import FastAPI
except ImportError:  # pragma: no cover
    app = None
else:
    app = FastAPI(title="ПочтаТех PI-планирование")
    app.get("/plan")(get_plan)
    app.get("/alerts")(get_alerts)
    app.get("/starmap")(get_starmap)
