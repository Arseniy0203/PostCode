"""Поток UI «загрузить датасет → загрузить факт спринта»: Excel → план → пересчёт."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from backend.recalculation.pipeline import (
    apply_uploaded_fact,
    fact_from_dataset_excel,
    week0_from_excel,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "raw" / "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx"
DEMO = ROOT / "demo_scenarios"

pytestmark = pytest.mark.skipif(not DATASET.exists(), reason="нет актуального датасета")


def _replay(scenario: str):
    state = week0_from_excel(DATASET.read_bytes())
    plan, data = state["plan"], state["dataset"]
    per_sprint = []
    for sprint in range(1, 7):
        fact = fact_from_dataset_excel(DEMO / scenario / f"Sprint_{sprint}_Fact.xlsx", plan)
        apply_uploaded_fact(plan, data, fact)
        per_sprint.append(Counter(a.type for a in plan["alerts"]))
    return plan, per_sprint


def test_week0_from_excel_uses_remaining_hours():
    state = week0_from_excel(DATASET)
    tasks = {t["task_id"]: t for t in state["plan"]["current"]}
    in_progress = [t for t in tasks.values() if t["status"] == "InProgress"]
    assert in_progress and all(t["spent_hh"] for t in in_progress)
    assert state["report"]["kpi"] and state["starmap"]["team_bus_factor"]


def test_cumulative_hours_become_hours_of_the_sprint():
    state = week0_from_excel(DATASET)
    plan = state["plan"]
    fact = fact_from_dataset_excel(DEMO / "Scenario_1_Ideal" / "Sprint_1_Fact.xlsx", plan)
    by_id = {t["task_id"]: t for t in plan["current"]}
    for tid, entry in fact["tasks"].items():
        already = sum((by_id[tid].get("spent_hh") or {}).values())
        # За спринт не может прийти больше, чем 80 ч × все инженеры задачи, и уж точно
        # не весь накопленный с начала работ объём заново.
        assert sum(entry["spent_hh"].values()) < already + 800


@pytest.mark.skipif(not DEMO.exists(), reason="нет demo_scenarios")
def test_demo_ideal_is_clean_and_cancel_is_red():
    plan, sprints = _replay("Scenario_1_Ideal")
    assert all(c["red"] == 0 and c["yellow"] == 0 for c in sprints)
    assert not [c for h in plan["history"][1:] for c in h["changes"] if c["kind"] == "shift"]

    _, cancel = _replay("Scenario_3_Canceled_Task")
    assert cancel[0]["red"] >= 1

    _, delay = _replay("Scenario_2_Cascade_Delay")
    assert delay[0]["yellow"] >= 1
