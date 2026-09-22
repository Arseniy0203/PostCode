"""Звёздная карта: Bus Factor внутри команд, «нанять» и «обучить» (ответы экспертов п. 8–9)."""

from __future__ import annotations

import pytest

from backend.recalculation.pipeline import run_quarter
from backend.starmap.competencies import canonical_tech
from backend.starmap.recommendations import (
    hire_recommendations,
    team_bus_factor,
    train_candidates,
    train_recommendations,
)


@pytest.fixture(scope="module")
def report():
    return run_quarter(until_sprint=0)


def _eng(eid, team, role, skills, grade="Middle", rate=1.0):
    return {"engineer_id": eid, "team_id": team, "role": role, "grade": grade,
            "capacity_rate": rate, "skills_declared": skills}


ENGINEERS = [
    _eng("E-DEV", "A", "Девопс", "Docker, Kubernetes, CI/CD (GitLab, Jenkins)", "Senior"),
    _eng("E-JAVA", "B", "Разработчик Java", "Java, Docker, Kubernetes"),
    _eng("E-QA", "B", "Тестировщик", "SQL, Postman"),
    _eng("E-LEAD", "A", "Руководитель BigData", "Agile/Scrum, Роадмапы", "Senior"),
]
PLAN = {"current": [
    {"task_id": "T1", "team_id": "B", "status": "ToDo", "decision": "included",
     "estimated_hh": {"ДевОпс": 20, "Разработчик Java": 40, "Руководитель проекта": 10}},
]}


def test_ci_cd_spellings_are_one_skill():
    assert canonical_tech("CI/CD (GitLab, Jenkins)") == canonical_tech("GitLab CI") == "CI/CD"


def test_team_bus_factor_marks_borrowed_missing_and_single():
    rows = {r["competency"]: r for r in team_bus_factor(PLAN, ENGINEERS)["B"]["rows"]}
    assert rows["ДевОпс"]["status"] == "borrowed" and rows["ДевОпс"]["elsewhere"] == ["E-DEV"]
    assert rows["Руководитель проекта"]["status"] == "missing"
    assert rows["Разработчик Java"]["status"] == "single"


def test_train_candidate_has_core_stack():
    best = train_candidates("ДевОпс", ENGINEERS)[0]
    assert best["engineer_id"] == "E-JAVA" and "Docker" in best["overlap"]


def test_one_shared_trivial_skill_is_not_enough():
    assert not [c for c in train_candidates("ДевОпс", ENGINEERS) if c["engineer_id"] == "E-QA"]


def test_hire_suggests_related_role_to_train():
    [hire] = hire_recommendations(PLAN, ENGINEERS)
    assert hire["role"] == "Руководитель проекта"
    assert hire["train_instead"][0]["engineer_id"] == "E-LEAD"


def test_train_recommendation_for_single_owner():
    recs = {r["role"]: r for r in train_recommendations(PLAN, ENGINEERS)}
    assert recs["ДевОпс"]["candidate"]["engineer_id"] == "E-JAVA"


def test_real_data_starmap_in_report(report):
    star = report["starmap"]
    assert "Руководитель проекта" in star["missing_competencies"]
    assert set(star["team_bus_factor"]) >= {"Team-Alpha", "Team-K", "Team-L"}
    trains = {r["role"]: r["candidate"]["engineer_id"] for r in star["recommendations"]["train"]}
    assert trains.get("ДевОпс") == "ENG-429"
    # Absence impact: только нужные плану компетенции (без TOGAF/ARKit) и кандидаты на замену.
    impact = star["absence_impact"]["ENG-419"]
    assert impact["tasks_at_risk"] and impact["backup_candidates"]
    assert all(c in star["bus_factors"] for c in impact["sole_competencies"])


def test_strict_comparison_shows_value_of_partial_targets(report):
    strict = report["strict_comparison"]
    assert len(report["summary"]["baseline"]["tasks"]) > len(strict["tasks"]) + 15
