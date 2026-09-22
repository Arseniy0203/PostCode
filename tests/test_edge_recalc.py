# Ответственный: Человек 2 (recalculation + risk)
"""Краевые случаи: «роль есть, но заняты», SP-переросток, цепочки блокировок,
отмена/переоценка/новые задачи/откат, задачи без сметы, отпуск, план найма,
итоги квартала, качество данных и «ни одной отложенной задачи без объяснения»."""

from __future__ import annotations

import copy

import pytest

from backend.recalculation.baseline import save_baseline
from backend.recalculation.pipeline import build_week0, load_dataset, run_quarter
from backend.recalculation.recalc import apply_sprint_fact, plan_tasks, rollback
from backend.recalculation.scenarios import simulate
from backend.recalculation.summary import quarter_summary
from backend.recalculation.what_if import hiring_roadmap
from backend.risk.alerts import check_blocked, check_resource_deficit
from backend.risk.data_quality import check_data_quality
from tests.test_features import role_aware_planner
from tests.test_recalc import CAPACITY, DEPS, ENGINEERS, KW, _get, _plan, fake_planner


def _t(tid, team, decision="postponed", hh=None, **kw):
    return {"task_id": tid, "prodf_id": kw.pop("prodf", "PRODF-1"), "team_id": team,
            "status": kw.pop("status", "ToDo"), "decision": decision,
            "estimated_hh": hh or {}, **kw}


# --- «роль есть, но все заняты» -----------------------------------------------


def test_hiring_alert_only_for_roles_absent_in_whole_company():
    current = [_t("SRV-4051", "Team-Delta", hh={"Разработчик Java": 100, "Руководитель проекта": 10})]
    engineers = [{"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java",
                  "capacity_rate": 1.0}]
    [alert] = check_resource_deficit(save_baseline({"current": current}), engineers)
    assert (alert.category, alert.role) == ("hiring", "Руководитель проекта")
    assert "В компании нет ни одного инженера роли «Руководитель проекта»" in alert.description
    assert "Java" not in alert.description, "Java в компании есть — планировщик возьмёт её из пула"


def test_hire_when_role_exists_but_everyone_is_busy():
    busy = {s: {"ENG-414": 80} for s in range(1, 7)}
    current = [
        _t("SRV-4051", "Team-Delta", hh={"Разработчик Java": 100}),
        _t("MP-102", "Team-K", decision="included", planned_start_sprint=1, planned_end_sprint=6,
           hh={"Разработчик Java": 480}, allocation=busy),
    ]
    engineers = [{"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java",
                  "capacity_rate": 1.0}]
    plan = save_baseline({"current": current})
    alert = next(a for a in check_resource_deficit(plan, engineers) if a.category == "capacity")
    assert "«Разработчик Java» — в компании 1 чел." in alert.recommendation
    assert "все заняты" in alert.recommendation


def test_borrow_when_other_team_has_free_hours_for_whole_need():
    current = [_t("SRV-4051", "Team-Delta", hh={"Разработчик Java": 100})]
    engineers = [{"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java",
                  "capacity_rate": 1.0}]
    [alert] = check_resource_deficit(save_baseline({"current": current}), engineers)
    assert alert.recommendation.startswith("Привлечь из других команд: «Разработчик Java» — ENG-414")
    assert "единственный носитель роли" in alert.recommendation


def test_partial_borrow_starts_when_person_can_switch():
    current = [_t("SRV-4051", "Team-Delta", hh={"Разработчик Java": 600})]
    engineers = [{"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java",
                  "capacity_rate": 1.0}]
    [alert] = check_resource_deficit(save_baseline({"current": current}), engineers)
    # переключение ≈ 1 спринт: человек доступен со 2-го по 6-й спринт → 400 ч, а не 480
    assert "свободно только 400 ч из нужных 600 ч" in alert.recommendation
    assert "частично: ENG-414 из Team-K" in alert.recommendation


def test_same_engineer_is_not_offered_to_two_teams():
    current = [_t("A-1", "Team-Delta", hh={"Архитектор решений": 300}, rung=90),
               _t("B-1", "Team-Beta", hh={"Архитектор решений": 300}, rung=80)]
    engineers = [{"engineer_id": "ENG-418", "team_id": "Team-Platform", "role": "Архитектор решений",
                  "capacity_rate": 1.0}]
    alerts = {a.task_id: a for a in check_resource_deficit(save_baseline({"current": current}), engineers)}
    assert "ENG-418 из Team-Platform (300 ч" in alerts["A-1"].recommendation
    assert "свободно только 100 ч из нужных 300 ч" in alerts["B-1"].recommendation


# --- отложена, хотя роли есть ---------------------------------------------------


def test_capacity_alert_for_task_postponed_with_all_roles_staffed():
    busy = {s: {"ENG-403": 80} for s in range(1, 7)}
    current = [
        _t("AI-302", "Team-L", hh={"Разработчик Java": 100}, reason_detail="Не хватило часов"),
        _t("ONK-2914", "Team-L", decision="included", planned_start_sprint=1, planned_end_sprint=6,
           hh={"Разработчик Java": 480}, allocation=busy),
    ]
    engineers = [{"engineer_id": "ENG-403", "team_id": "Team-L", "role": "Разработчик Java",
                  "capacity_rate": 1.0}]
    alerts = check_resource_deficit(save_baseline({"current": current}), engineers)
    capacity = next(a for a in alerts if a.category == "capacity")
    assert capacity.task_id == "AI-302" and "все нужные роли в компании есть" in capacity.description
    assert "свободно только 0 ч" in capacity.recommendation


def test_oversized_task_reported_with_split_advice():
    plan = save_baseline({"current": [_t("DB-202", "Team-Platform", estimation_sp=8,
                                         hh={"Разработчик .net": 40})]})
    plan["capacity"] = {"team_sp": {"Team-Platform": 7.2}}
    engineers = [{"engineer_id": "ENG-420", "team_id": "Team-Platform", "role": "Разработчик .net",
                  "capacity_rate": 1.0}]
    [alert] = check_resource_deficit(plan, engineers)
    assert alert.category == "capacity" and "8 SP" in alert.description and "7.2 SP" in alert.description
    assert "Разбить DB-202 на 2 части" in alert.recommendation


# --- цепочки блокировок ---------------------------------------------------------


def test_blocked_alert_shows_chain_to_root_cause():
    current = [
        _t("DB-202", "Team-Platform", reason_detail="нет Руководителя проекта"),
        _t("MP-102", "Team-K"),
        _t("MP-103", "Team-K"),
    ]
    deps = [{"blocking_task_id": "DB-202", "blocked_task_id": "MP-102"},
            {"blocking_task_id": "MP-102", "blocked_task_id": "MP-103"}]
    alerts = {a.task_id: a for a in check_blocked(save_baseline({"current": current}), deps)}
    assert set(alerts) == {"MP-102", "MP-103"}
    assert "ждёт MP-102 → DB-202" in alerts["MP-103"].description
    assert "нет Руководителя проекта" in alerts["MP-103"].description


# --- отмена, переоценка, новые задачи, откат ----------------------------------


def test_cancelled_task_leaves_plan_and_blocks_dependents():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"A": {"status": "Cancelled", "note": "заказчик снял"}}}, **KW)
    a, b = _get(plan, "A"), _get(plan, "B")
    assert (a["status"], a["decision"], a["reason_code"]) == ("Cancelled", "cancelled", "REQUIREMENTS_CHANGED")
    assert b["decision"] == "postponed"
    red = next(x for x in plan["alerts"] if x.type == "red" and x.task_id == "A")
    assert "отменена" in red.description
    assert any(c["kind"] == "cancelled" for c in plan["history"][-1]["changes"])


def test_reestimate_replaces_estimate_keeps_baseline():
    plan = _plan()
    fact = {"tasks": {"C": {"estimated_hh": {"Разработчик Java": 240}}}}
    apply_sprint_fact(plan, fact, **KW)
    c = _get(plan, "C")
    assert c["estimated_hh"] == {"Разработчик Java": 240}
    assert c["estimated_hh_initial"] == {"Разработчик Java": 80}
    assert next(t for t in plan["baseline"] if t["task_id"] == "C")["estimated_hh"] == {"Разработчик Java": 80}
    assert any(ch["kind"] == "reestimated" for ch in plan["history"][-1]["changes"])


def test_added_task_is_planned_but_not_in_baseline():
    plan = _plan()
    new = {"task_id": "NEW-1", "prodf_id": "PRODF-9", "team_id": "Team-X", "estimation_sp": 3,
           "estimated_hh": {"Разработчик Java": 40}}
    apply_sprint_fact(plan, {"tasks_added": [new, dict(new)]}, **KW)
    assert _get(plan, "NEW-1")["decision"] == "included"
    assert "NEW-1" not in {t["task_id"] for t in plan["baseline"]}
    assert len(plan["warnings"]) == 1
    assert any(c["kind"] == "added" for c in plan["history"][-1]["changes"])


def test_rollback_restores_state_and_allows_reapply():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"A": {"status": "Done"}}}, **KW)
    after_one = copy.deepcopy(plan["current"])
    apply_sprint_fact(plan, {"engineers_unavailable": ["ENG-1"]}, **KW)
    rollback(plan, 1)
    assert plan["current_sprint"] == 1 and plan["current"] == after_one
    assert plan["engineers_unavailable"] == [] and len(plan["history"]) == 2
    apply_sprint_fact(plan, {}, 2, **KW)
    with pytest.raises(ValueError):
        rollback(plan, 9)


# --- задачи без сметы -----------------------------------------------------------


def test_unestimated_task_postponed_as_not_estimated():
    tasks = [{"task_id": "X", "prodf_id": "P", "team_id": "Team-X", "status": "ToDo",
              "estimation_sp": 3, "estimated_hh": {}},
             {"task_id": "Y", "prodf_id": "P", "team_id": "Team-X", "status": "ToDo",
              "estimation_sp": 3, "estimated_hh": {"Разработчик Java": 40}}]
    planned = {t["task_id"]: t for t in plan_tasks(tasks, CAPACITY, [], ENGINEERS, planner=fake_planner)}
    assert (planned["X"]["decision"], planned["X"]["reason_code"]) == ("postponed", "NOT_ESTIMATED")
    assert planned["Y"]["decision"] == "included"


# --- отпуск ---------------------------------------------------------------------


def test_vacation_engineer_returns_after_two_sprints():
    plan = _plan()
    for task in plan["current"]:
        for sprint, hours in (task.get("allocation") or {}).items():
            task["allocation"][sprint] = {"ENG-1": sum(hours.values())}
    engineers = [{"engineer_id": "ENG-1", "team_id": "Team-X", "role": "Разработчик Java"}]
    simulate(plan, "vacation", 3, dependencies=DEPS, engineers=engineers, capacity=CAPACITY,
             planner=fake_planner)
    assert plan["history"][1]["engineers_unavailable"] == ["ENG-1"]
    assert plan["history"][3]["engineers_unavailable"] == []


# --- план найма -----------------------------------------------------------------


def test_hiring_roadmap_orders_hires_by_gain_and_stops():
    tasks = [
        {"task_id": "T1", "prodf_id": "P1", "team_id": "Team-X", "status": "ToDo",
         "estimation_sp": 5, "estimated_hh": {"PM": 10, "Java": 10}},
        {"task_id": "T2", "prodf_id": "P2", "team_id": "Team-X", "status": "ToDo",
         "estimation_sp": 5, "estimated_hh": {"PM": 10, "Java": 10}},
        {"task_id": "T3", "prodf_id": "P3", "team_id": "Team-X", "status": "ToDo",
         "estimation_sp": 5, "estimated_hh": {"1С": 10}},
    ]
    engineers = [{"engineer_id": "E1", "team_id": "Team-X", "role": "Java", "capacity_rate": 1.0}]
    steps = hiring_roadmap(tasks, {"engineer_hh": {"E1": 80}}, [], engineers, planner=role_aware_planner)
    assert [(s["role"], s["tasks_gained"]) for s in steps] == [("PM", ["T1", "T2"]), ("1С", ["T3"])]
    assert steps[-1]["total_tasks"] == 3


# --- итоги квартала -------------------------------------------------------------


def test_quarter_summary_final_with_carry_over():
    plan = _plan()
    simulate(plan, "on_track", 3, dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY,
             planner=fake_planner)
    apply_sprint_fact(plan, {"tasks": {"C": {"status": "InProgress", "spent_hh": {"Разработчик Java": 20}}}},
                      **KW)
    apply_sprint_fact(plan, {}, **KW)
    apply_sprint_fact(plan, {}, **KW)
    summary = quarter_summary(plan)
    outcome = {r["task_id"]: r["outcome"] for r in summary["tasks"]}
    assert summary["mode"] == "final"
    assert outcome["A"] == "done" and outcome["C"] == "carry_over"
    assert "OLD" not in outcome, "закрытые до Недели 0 не входят в итоги квартала"
    c_row = next(r for r in summary["tasks"] if r["task_id"] == "C")
    assert c_row["label"].startswith("3. Функционал реализован частично")


def test_quarter_summary_forecast_mode_before_end():
    summary = quarter_summary(_plan())
    assert summary["mode"] == "forecast"
    assert {r["outcome"] for r in summary["tasks"]} == {"on_plan"}


# --- качество данных ------------------------------------------------------------


def test_data_quality_team_without_history_or_engineers_and_unestimated():
    tasks = [{"task_id": "Z-1", "prodf_id": "P", "team_id": "Team-Z", "status": "ToDo"}]
    alerts = check_data_quality(tasks, [], [], engineers=[], team_history=[])
    text = " | ".join(a.description for a in alerts)
    assert "нет истории спринтов" in text
    assert "нет ни одного инженера" in text
    assert "нет оценки в калькуляторе" in text


# --- реальный датасет -----------------------------------------------------------


def test_no_silent_postponements_on_real_dataset():
    data = load_dataset()
    for plan in (build_week0(data),):
        explained = {t for a in plan["alerts"] for t in a.affected_task_ids}
        silent = [t["task_id"] for t in plan["current"]
                  if t["status"] != "Done" and t["decision"] != "included"
                  and t["task_id"] not in explained]
        assert silent == []


def test_run_quarter_has_roadmap_and_summary():
    report = run_quarter("vacation", until_sprint=3)
    step = report["hiring_roadmap"][0]
    assert step["gain_tasks"] + step["gain_full"] > 0
    assert report["quarter_summary"]["mode"] == "forecast"
