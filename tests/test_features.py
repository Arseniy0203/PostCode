"""Тесты рекомендаций, жизненного цикла алертов, сводки по инициативам,
«зависших» задач, сценариев early_finish / hire_role, «что если нанять» и фасада."""

from __future__ import annotations

import copy

import pytest

from backend.recalculation.baseline import save_baseline
from backend.recalculation.pipeline import fact_from_rows, run_quarter
from backend.recalculation.recalc import apply_sprint_fact
from backend.recalculation.scenarios import build_fact, simulate
from backend.recalculation.what_if import hiring_impact
from backend.risk.alerts import (
    Alert,
    annotate_lifecycle,
    check_kpi,
    check_resource_deficit,
    check_single_point,
    check_stale,
    summarize_by_prodf,
)
from tests.test_recalc import CAPACITY, DEPS, ENGINEERS, _plan, fake_planner


def _t(tid, team, decision="included", start=1, end=1, hh=None, status="ToDo", **kw):
    return {"task_id": tid, "prodf_id": kw.pop("prodf", "PRODF-1"), "team_id": team,
            "status": status, "decision": decision, "planned_start_sprint": start,
            "planned_end_sprint": end, "estimated_hh": hh or {}, **kw}


# --- рекомендации со свободной ёмкостью ---------------------------------------


def test_orange_resource_compares_demand_with_whole_company_fund():
    current = [
        _t("DB-202", "Team-Platform", hh={"Разработчик Java": 200}),
        _t("MP-102", "Team-K", hh={"Разработчик Java": 60}),
    ]
    engineers = [
        {"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java", "capacity_rate": "1.0"},
        {"engineer_id": "ENG-424", "team_id": "Team-Alpha", "role": "Разработчик Java", "capacity_rate": "1.0"},
    ]
    [alert] = check_resource_deficit(save_baseline({"current": current}), engineers)
    assert (alert.category, alert.team_id, alert.deficit_hh) == ("resource", "Team-Platform", 100)
    assert "Team-Platform 200 ч, Team-K 60 ч" in alert.description
    assert "фонд всех сотрудников этой роли в компании — 160 ч" in alert.description


def test_no_resource_alert_when_company_pool_covers_demand():
    current = [_t("MOB-7013", "Team-L", hh={"Разработчик Android": 30})]
    engineers = [{"engineer_id": "ENG-417", "team_id": "Team-Platform", "role": "Разработчик Android",
                  "capacity_rate": "1.0"}]
    assert check_resource_deficit(save_baseline({"current": current}), engineers) == []


def test_missing_role_alert_is_hiring_category():
    current = [_t("SRV-4051", "Team-Delta", decision="postponed", start=None, end=None,
                  hh={"Руководитель проекта": 20})]
    [alert] = check_resource_deficit(save_baseline({"current": current}), [])
    assert (alert.type, alert.category) == ("orange", "hiring")


# --- жизненный цикл ------------------------------------------------------------


def _alert(task_id, sprint, type_="red", category="critical"):
    return Alert(type=type_, category=category, task_id=task_id, prodf_id_at_risk="PRODF-1",
                 description="d", sprint_detected=sprint)


def test_lifecycle_new_ongoing_resolved():
    first, _ = annotate_lifecycle([_alert("A", 1)], None, 1)
    assert (first[0].status, first[0].since_sprint) == ("new", 1)

    second, resolved = annotate_lifecycle([_alert("A", 2), _alert("B", 2)], [first[0].to_dict()], 2)
    assert [(a.task_id, a.status, a.since_sprint) for a in second] == [("A", "ongoing", 1), ("B", "new", 2)]
    assert resolved == []

    third, resolved = annotate_lifecycle([_alert("B", 3)], [a.to_dict() for a in second], 3)
    assert third[0].status == "ongoing"
    assert [(r["task_id"], r["status"], r["resolved_sprint"]) for r in resolved] == [("A", "resolved", 3)]


def test_recalc_marks_alert_lifecycle_across_sprints():
    plan = _plan()
    delay = {"tasks": {"A": {"status": "InProgress", "spent_hh": {"Разработчик Java": 10}}}}
    apply_sprint_fact(plan, delay, dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY,
                      planner=fake_planner)
    yellow = [a for a in plan["alerts"] if a.type == "yellow"]
    assert yellow and yellow[0].status == "new"
    done = {"tasks": {"A": {"status": "Done", "spent_hh": {"Разработчик Java": 70}}}}
    apply_sprint_fact(plan, done, dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY,
                      planner=fake_planner)
    # A закрыта с опозданием, но B и C всё ещё позже baseline — сдвиг цепочки не исчез.
    [still] = [a for a in plan["alerts"] if a.type == "yellow"]
    assert still.status == "ongoing" and "закрыта с опозданием" in still.description
    # Когда цепочка догнала/закрылась, алерт решается.
    rest = {"tasks": {t: {"status": "Done", "spent_hh": {"Разработчик Java": 80}} for t in ("B", "C")}}
    apply_sprint_fact(plan, rest, dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY,
                      planner=fake_planner)
    assert any(r["category"] == "cascade" for r in plan["resolved_alerts"])


# --- сводка по инициативам -----------------------------------------------------


def test_summary_by_prodf_takes_worst_status():
    alerts = [
        _alert("A", 1, "orange", "resource"),
        _alert("B", 1, "red", "critical"),
        Alert(type="yellow", category="cascade", task_id="C", prodf_id_at_risk="PRODF-2",
              description="d", sprint_detected=1, affected_prodf_ids=("PRODF-2", "PRODF-1"),
              recommendation="поднять приоритет C"),
    ]
    rows = summarize_by_prodf(alerts)
    assert [r["prodf_id"] for r in rows] == ["PRODF-1", "PRODF-2"]
    assert rows[0]["worst"] == "red" and rows[0]["causes"] == ["B"]
    assert rows[0]["counts"] == {"red": 1, "yellow": 1, "orange": 1, "info": 0}
    assert rows[1]["recommendations"] == ["поднять приоритет C"]


# --- зависшие задачи -----------------------------------------------------------


def test_stale_at_week0_by_old_start_date():
    current = [
        _t("KP-3898", "Team-Platform", status="InProgress", actual_start="2025-12-02 09:40:00"),
        _t("NEW-1", "Team-Platform", status="InProgress", actual_start="2026-06-20 09:00:00"),
    ]
    [alert] = check_stale(save_baseline({"current": current}))
    assert alert.task_id == "KP-3898" and "02.12.2025" in alert.description


def test_stale_when_no_progress_two_sprints():
    plan = save_baseline({"current": [_t("X", "Team-K", status="InProgress", spent_hh={"Аналитик": 10})]})
    plan["history"].append({"sprint": 1, "current": copy.deepcopy(plan["current"])})
    plan["current_sprint"] = 2
    [alert] = check_stale(plan)
    assert "нет прироста часов 2 спринта подряд" in alert.description

    plan["current"][0]["spent_hh"] = {"Аналитик": 30}
    assert check_stale(plan) == []


# --- сценарии early_finish и hire_role -----------------------------------------


def test_early_finish_pulls_chain_forward():
    plan = simulate(_plan(), "early_finish", 2, dependencies=DEPS, engineers=ENGINEERS,
                    capacity=CAPACITY, planner=fake_planner, trigger_sprint=2)
    assert plan["history"][2]["fact"]["scenario_target"] == "B"
    c = next(t for t in plan["current"] if t["task_id"] == "C")
    assert c["forecast_end_sprint"] == 3
    change = next(ch for ch in plan["history"][2]["changes"] if ch["task_id"] == "C")
    assert change["delta"] == -1


def test_hire_role_picks_role_with_most_postponed_hours_and_planner_gets_engineer():
    current = [
        _t("T1", "Team-Delta", decision="postponed", start=None, end=None,
           hh={"Руководитель проекта": 50, "Разработчик 1С": 10}),
        _t("T2", "Team-Delta", decision="postponed", start=None, end=None,
           hh={"Разработчик 1С": 20}),
    ]
    plan = save_baseline({"current": current})
    fact = build_fact(plan, "hire_role", 1, dependencies=[], engineers=[])
    [hire] = fact["engineers_hired"]
    assert (hire["team_id"], hire["role"]) == ("Team-Delta", "Руководитель проекта")

    seen = {}

    def spy(tasks, capacity, dependencies, engineers, **kwargs):
        seen["engineers"], seen["capacity"] = engineers, capacity
        return fake_planner(tasks, capacity, dependencies, engineers, **kwargs)

    apply_sprint_fact(plan, fact, dependencies=[], engineers=[], capacity={"engineer_hh": {}}, planner=spy)
    assert [e["engineer_id"] for e in seen["engineers"]] == [hire["engineer_id"]]
    # выходит во 2-м спринте: 50 % → 75 % → 100 % ×3, в среднем 68 ч за спринт
    assert seen["capacity"]["engineer_hh"][hire["engineer_id"]] == 68.0
    assert plan["engineers_hired"] == [{**hire, "joined_sprint": 2}]


def test_manual_target_overrides_auto_pick():
    fact = build_fact(_plan(), "key_task_delay", 1, dependencies=DEPS, engineers=ENGINEERS, target="D")
    assert fact["scenario_target"] == "D" and fact["tasks"]["D"]["status"] == "InProgress"


# --- KPI-алерты и единственный носитель роли -----------------------------------


def _kpi_plan(done_sp_task_status):
    current = [
        _t("A", "Team-X", start=1, end=1, estimation_sp=10, prodf="P1"),
        _t("B", "Team-X", start=1, end=1, estimation_sp=10, prodf="P2"),
        _t("OLD", "Team-X", status="Done", start=0, end=0, prodf="P3"),
    ]
    plan = save_baseline({"current": current})
    plan["current_sprint"] = 1
    plan["current"][0].update(status="Done", actual_end_sprint=1)
    plan["current"][1].update(status=done_sp_task_status, forecast_end_sprint=7)
    return plan


def test_kpi_alerts_for_low_say_do_and_pi_forecast():
    alerts = check_kpi(_kpi_plan("InProgress"))
    texts = " | ".join(a.description for a in alerts)
    assert "Say/Do спринта 1: 50%" in texts
    assert "Прогноз PI Predictability 50%" in texts and "P2" in texts
    assert "P3" not in texts, "Done до Недели 0 не входит в обещание квартала"


def test_no_kpi_alerts_when_in_norm():
    plan = _kpi_plan("Done")
    plan["current"][1].update(actual_end_sprint=1)
    assert check_kpi(plan) == []


def test_single_point_groups_tasks_by_sole_engineer():
    current = [
        _t("DB-202", "Team-Platform", hh={"Разработчик .net": 90}),
        _t("DB-203", "Team-Platform", hh={"Разработчик .net": 10, "Аналитик": 5}),
    ]
    engineers = [
        {"engineer_id": "ENG-420", "team_id": "Team-Platform", "role": "Разработчик .net"},
        {"engineer_id": "ENG-404", "team_id": "Team-L", "role": "Аналитик"},
        {"engineer_id": "ENG-425", "team_id": "Team-Alpha", "role": "Аналитик"},
    ]
    [alert] = check_single_point(save_baseline({"current": current}), engineers)
    assert alert.category == "single_point" and alert.affected_task_ids == ("DB-202", "DB-203")
    assert "ENG-420" in alert.description


# --- «что если нанять» ---------------------------------------------------------


def role_aware_planner(tasks, capacity, dependencies, engineers, **kwargs):
    staffed = {(e["team_id"], e["role"]) for e in engineers}
    current = []
    for task in tasks:
        ok = all((task["team_id"], role) in staffed for role in task["estimated_hh"])
        current.append({**task, "decision": "included" if ok else "postponed"})
    return {"current": current}


def test_hiring_impact_ranks_hires_and_bundles():
    tasks = [
        {"task_id": "T1", "prodf_id": "P1", "team_id": "Team-X", "status": "ToDo",
         "estimation_sp": 5, "estimated_hh": {"Java": 80}},
        {"task_id": "T2", "prodf_id": "P2", "team_id": "Team-X", "status": "ToDo",
         "estimation_sp": 8, "estimated_hh": {"PM": 40, "Java": 40}},
        {"task_id": "T3", "prodf_id": "P3", "team_id": "Team-Y", "status": "ToDo",
         "estimation_sp": 3, "estimated_hh": {"PM": 20, "1С": 20}},
    ]
    engineers = [{"engineer_id": "E1", "team_id": "Team-X", "role": "Java", "capacity_rate": 1.0}]
    result = hiring_impact(tasks, {"engineer_hh": {"E1": 80}}, [], engineers, planner=role_aware_planner)

    assert result["baseline"]["tasks"] == ["T1"]
    best = result["options"][0]
    assert (best["team_id"], best["roles"], best["tasks_gained"]) == ("Team-X", ["PM"], ["T2"])
    assert best["prodf_gained"] == ["P2"]
    bundle = next(o for o in result["options"] if o["team_id"] == "Team-Y")
    assert bundle["kind"] == "team_bundle" and bundle["tasks_gained"] == ["T3"]
    assert all(o["gain_tasks"] > 0 for o in result["options"])
    assert result["ceiling"]["gain_tasks"] == 2


# --- загрузка факта и фасад ----------------------------------------------------


def test_fact_from_rows_parses_table():
    rows = [
        {"task_id": "DB-202", "status": "InProgress", "Разработчик .net": 40, "Аналитик": 0,
         "Исходное время": 324, "комментарий": "текст"},
        {"task_id": "MP-102", "status": "Done", "Разработчик Java": "12.5"},
        {"task_id": None, "status": "Done"},
    ]
    fact = fact_from_rows(rows)
    assert fact["tasks"] == {
        "DB-202": {"status": "InProgress", "spent_hh": {"Разработчик .net": 40.0}},
        "MP-102": {"status": "Done", "spent_hh": {"Разработчик Java": 12.5}},
    }


def test_run_quarter_on_real_dataset_returns_full_report():
    report = run_quarter("hire_role", until_sprint=2)
    assert report["sprint"] == 2 and len(report["history"]) == 3
    for key in ("summary", "alerts", "by_prodf", "data_quality", "hiring", "kpi", "scenarios"):
        assert key in report
    assert report["data_quality"], "в датасете есть задачи-призраки — отчёт не может быть пустым"
    assert all(a["prodf_id_at_risk"] or a["category"] == "data_quality" for a in report["alerts"])
    assert list(report["plan"]["baseline"]) == report["history"][0]["current"]
