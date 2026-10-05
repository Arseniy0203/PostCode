"""Overview charts must not present a future forecast as observed fact."""

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("altair")

from frontend.views.dashboard_view import completion_series


def test_completion_series_respects_commitments_and_fact_horizon():
    baseline = [
        {"task_id": "A", "decision": "included", "planned_end_sprint": 1},
        {"task_id": "B", "decision": "included", "planned_end_sprint": 2},
    ]
    plan = {
        "baseline": baseline,
        "current": [
            {**baseline[0], "status": "Done", "actual_end_sprint": 2},
            {**baseline[1], "decision": "cancelled"},
            {"task_id": "NEW", "decision": "included", "status": "Done", "actual_end_sprint": 1},
        ],
        "current_sprint": 2,
    }
    values = {(r["Спринт"], r["Срез"]): r["Задачи"] for r in completion_series(plan)}
    assert values[(2, "Исходный план")] == 2
    assert values[(2, "Текущий прогноз")] == 1
    assert values[(1, "Факт")] == 0
    assert values[(2, "Факт")] == 1
    assert all((s, "Факт") not in values for s in range(3, 7))


def test_baseline_has_no_observed_fact():
    rows = completion_series({"baseline": [], "current": [], "current_sprint": 0})
    assert len(rows) == 12
    assert all(r["Срез"] != "Факт" and r["Задачи"] == 0 for r in rows)
