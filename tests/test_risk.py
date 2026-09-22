"""Тесты baseline и алертов на реальной цепочке из датасета:
DB-201 → DB-202 → {DB-203, MP-102}, MP-101 → MP-102 → MP-103."""

from __future__ import annotations

import copy

import pytest

from backend.recalculation.baseline import BaselineAlreadySavedError, save_baseline
from backend.risk.alerts import (
    check_cascade,
    check_critical,
    check_resource_deficit,
    collect_alerts,
)

DEPS = [
    {"blocking_task_id": "DB-201", "blocked_task_id": "DB-202", "dependency_type": "is required for"},
    {"blocking_task_id": "DB-202", "blocked_task_id": "DB-203", "dependency_type": "has to be done before"},
    {"blocking_task_id": "DB-202", "blocked_task_id": "MP-102", "dependency_type": "depends on"},
    {"blocking_task_id": "MP-101", "blocked_task_id": "MP-102", "dependency_type": "has to be done before"},
    {"blocking_task_id": "MP-102", "blocked_task_id": "MP-103", "dependency_type": "has to be done before"},
]

ENGINEERS = [
    {"engineer_id": "ENG-420", "team_id": "Team-Platform", "role": "Разработчик .net", "capacity_rate": "1.0"},
    {"engineer_id": "ENG-419", "team_id": "Team-Platform", "role": "Девопс", "capacity_rate": "0.5"},
    {"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java", "capacity_rate": "1.0"},
]


def _t(tid, prodf, team, start, end, status="ToDo", decision="included", hh=None, **kw):
    return {"task_id": tid, "prodf_id": prodf, "team_id": team, "status": status,
            "decision": decision, "planned_start_sprint": start, "planned_end_sprint": end,
            "estimated_hh": hh or {}, **kw}


def _plan():
    current = [
        _t("DB-201", "PRODF-7124", "Team-Platform", None, None, status="Done"),
        _t("MP-101", "PRODF-7123", "Team-L", None, None, status="Done"),
        _t("DB-202", "PRODF-7124", "Team-Platform", 1, 2, status="InProgress",
           hh={"Разработчик .net": 130, "ДевОпс": 32}, spent_hh={"Разработчик .net": 40}),
        _t("DB-203", "PRODF-7124", "Team-Platform", 3, 3, hh={"ДевОпс": 40}),
        _t("MP-102", "PRODF-7123", "Team-K", 3, 4, status="InProgress"),
        _t("MP-103", "PRODF-7123", "Team-K", 5, 5),
    ]
    return save_baseline({"current": current, "baseline": current})


def _task(plan, tid):
    return next(t for t in plan["current"] if t["task_id"] == tid)


# --- baseline ------------------------------------------------------------------


def test_baseline_is_independent_copy_of_current():
    plan = _plan()
    _task(plan, "DB-202")["planned_end_sprint"] = 5
    assert next(t for t in plan["baseline"] if t["task_id"] == "DB-202")["planned_end_sprint"] == 2


def test_baseline_cannot_be_saved_twice():
    plan = _plan()
    with pytest.raises(BaselineAlreadySavedError):
        save_baseline(plan)


# --- red -----------------------------------------------------------------------


def test_no_alerts_when_plan_matches_baseline():
    plan = _plan()
    assert check_critical(plan) == []
    assert check_cascade(plan, DEPS) == []


def test_red_when_forecast_beyond_quarter():
    plan = _plan()
    _task(plan, "MP-103")["forecast_end_sprint"] = 7
    [alert] = check_critical(plan)
    assert (alert.type, alert.task_id, alert.prodf_id_at_risk) == ("red", "MP-103", "PRODF-7123")
    assert alert.recommendation


def test_red_when_baseline_task_dropped_from_plan():
    plan = _plan()
    _task(plan, "DB-203").update(decision="postponed", reason_detail="нет ДевОпс")
    [alert] = check_critical(plan)
    assert alert.task_id == "DB-203" and "нет ДевОпс" in alert.description


def test_task_postponed_at_week0_is_not_red():
    current = [_t("AI-303", "PRODF-7125", "Team-L", None, None, decision="postponed")]
    assert check_critical(save_baseline({"current": current})) == []


def test_done_task_never_alerts():
    plan = _plan()
    _task(plan, "DB-201")["forecast_end_sprint"] = 9
    assert check_critical(plan) == []


# --- yellow --------------------------------------------------------------------


def test_yellow_cascade_reports_root_and_whole_chain():
    plan = _plan()
    _task(plan, "DB-202")["forecast_end_sprint"] = 3
    _task(plan, "DB-203")["forecast_end_sprint"] = 4
    _task(plan, "MP-102")["forecast_end_sprint"] = 5
    _task(plan, "MP-103")["forecast_end_sprint"] = 6

    [alert] = check_cascade(plan, DEPS)
    assert alert.type == "yellow" and alert.task_id == "DB-202"
    assert set(alert.affected_task_ids) == {"DB-202", "DB-203", "MP-102", "MP-103"}
    assert set(alert.affected_prodf_ids) == {"PRODF-7124", "PRODF-7123"}


def test_yellow_persists_while_shift_against_baseline_remains():
    plan = _plan()
    _task(plan, "DB-202")["forecast_end_sprint"] = 3
    previous = copy.deepcopy(plan["current"])
    [alert] = check_cascade(plan, DEPS, reference=previous)
    assert alert.task_id == "DB-202" and "С прошлого пересчёта" not in alert.description


def test_yellow_mentions_further_delay_since_previous_snapshot():
    plan = _plan()
    _task(plan, "DB-202")["forecast_end_sprint"] = 3
    previous = copy.deepcopy(plan["current"])
    _task(plan, "DB-202")["forecast_end_sprint"] = 4
    [alert] = check_cascade(plan, DEPS, reference=previous)
    assert "С прошлого пересчёта — ещё +1" in alert.description


def test_no_yellow_for_shifted_task_without_dependents():
    plan = _plan()
    _task(plan, "MP-103")["forecast_end_sprint"] = 6
    assert check_cascade(plan, DEPS) == []


def test_phantom_dependencies_are_ignored():
    plan = _plan()
    _task(plan, "DB-203")["forecast_end_sprint"] = 4
    deps = DEPS + [{"blocking_task_id": "DB-203", "blocked_task_id": "SRV-4019"}]
    assert check_cascade(plan, deps) == []


# --- orange --------------------------------------------------------------------


def test_orange_when_role_demand_exceeds_team_hours():
    plan = _plan()
    # DB-202: остаток .net 90 ч на спринты 1–2 → 45 ч/спринт при фонде 80 — дефицита нет.
    # ДевОпс 32 ч / 2 = 16 ч при фонде 40 (0.5 ставки) — дефицита нет.
    assert check_resource_deficit(plan, ENGINEERS) == []

    _task(plan, "DB-202")["estimated_hh"]["Разработчик .net"] = 250
    [alert] = check_resource_deficit(plan, ENGINEERS)
    assert (alert.type, alert.team_id, alert.role) == ("orange", "Team-Platform", "Разработчик .net")
    assert alert.deficit_hh == pytest.approx(105 - 80)


def test_role_names_are_normalized():
    plan = _plan()
    _task(plan, "DB-202")["estimated_hh"] = {"ДевОпс": 70}
    engineers = [{"team_id": "Team-Platform", "role": "Девопс", "capacity_rate": "1.0"}]
    assert check_resource_deficit(plan, engineers) == []


def test_part_time_engineer_counts_half_per_team():
    plan = _plan()
    _task(plan, "DB-202")["estimated_hh"] = {"ДевОпс": 100}
    [alert] = check_resource_deficit(plan, ENGINEERS)
    assert alert.deficit_hh == pytest.approx(50 - 40)


def test_orange_hire_for_postponed_task_with_missing_role():
    current = [_t("SRV-4051", "PRODF-7130", "Team-Delta", None, None, decision="postponed",
                  hh={"Руководитель проекта": 20})]
    [alert] = check_resource_deficit(save_baseline({"current": current}), ENGINEERS)
    assert alert.role == "Руководитель проекта" and alert.deficit_hh == 20
    assert "Нанять" in alert.recommendation


def test_missing_roles_one_alert_per_role():
    current = [
        _t("SRV-4051", "PRODF-7130", "Team-Delta", None, None, decision="postponed",
           hh={"Руководитель проекта": 20, "Разработчик 1С": 50}),
        _t("SRV-4052", "PRODF-7130", "Team-Delta", None, None, decision="postponed",
           hh={"Руководитель проекта": 15}),
    ]
    alerts = {a.role: a for a in check_resource_deficit(save_baseline({"current": current}), ENGINEERS)}
    assert set(alerts) == {"Руководитель проекта", "Разработчик 1С"}
    assert alerts["Руководитель проекта"].deficit_hh == 35
    assert set(alerts["Руководитель проекта"].affected_task_ids) == {"SRV-4051", "SRV-4052"}
    assert alerts["Разработчик 1С"].affected_task_ids == ("SRV-4051",)


def test_no_orange_after_last_sprint():
    plan = _plan()
    _task(plan, "DB-202")["estimated_hh"]["Разработчик .net"] = 900
    plan["current_sprint"] = 6
    assert check_resource_deficit(plan, ENGINEERS) == []


# --- сводка --------------------------------------------------------------------


def test_collect_alerts_sorted_by_severity_and_serializable():
    plan = _plan()
    _task(plan, "DB-202")["forecast_end_sprint"] = 3
    _task(plan, "MP-103")["forecast_end_sprint"] = 7
    _task(plan, "DB-202")["estimated_hh"]["Разработчик .net"] = 300
    alerts = collect_alerts(plan, DEPS, ENGINEERS)
    assert [a.type for a in alerts] == sorted(
        (a.type for a in alerts), key=["red", "yellow", "orange", "info"].index)
    assert {"red", "yellow", "orange"} <= {a.type for a in alerts}
    assert all(a.to_dict()["prodf_id_at_risk"] for a in alerts)
