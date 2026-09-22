"""«Если запустить всё сразу» против плана: перегрузка есть, в плане — нет."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.recalculation.pipeline import week0_from_excel

MASTER = Path(__file__).resolve().parents[1] / "data" / "raw" / \
    "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx"


@pytest.fixture(scope="module")
def overload():
    if not MASTER.exists():
        pytest.skip("нет датасета")
    return week0_from_excel(MASTER.read_bytes())["overload"]


def test_all_at_once_overloads_every_team(overload):
    summary = overload["summary"]
    assert summary["teams_overloaded"] == len(overload["teams"]) == 6
    assert summary["max_team"]["team_id"] == "Team-Platform"
    assert summary["max_team"]["all_at_once_pct"] > 500


def test_plan_never_exceeds_team_capacity(overload):
    assert overload["summary"]["plan_peak_pct"] <= 100
    assert all((row["pct"] or 0) <= 100 for row in overload["plan_load"])


def test_roles_without_people_and_names_from_estimates(overload):
    summary = overload["summary"]
    assert "Руководитель проекта" in summary["roles_without_people"]
    names = {r["role"] for r in overload["roles"]}
    assert "ДевОпс" in names and "Девопс" not in names
    devops = next(r for r in overload["roles"] if r["role"] == "ДевОпс")
    assert devops["fund_sprint_hh"] == 80 and devops["all_at_once_pct"] > 100


def test_strict_rule_takes_fewer_tasks(overload):
    summary = overload["summary"]
    assert summary["strict_tasks"] < summary["plan_tasks"] == 36
