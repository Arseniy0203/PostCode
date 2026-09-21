# Ответственный: Человек 3 (starmap + explainability)
"""Тесты звёздной карты: профили, Bus Factor и связь риска с планом."""

from __future__ import annotations

import pytest

from backend.starmap.bus_factor import (
    absence_impact,
    bus_factor,
    bus_factors,
    find_missing_competencies,
    find_single_points_of_failure,
    link_risk_to_tasks,
    starmap_summary,
)
from backend.starmap.competencies import canonical_tech, split_skills
from backend.starmap.profiles import build_engineer_profiles, build_team_profiles


@pytest.fixture
def engineers():
    """Мини-срез Engineers_profiles, повторяющий особенности датасета."""
    return [
        # Инженер на 0.5 ставки в двух командах — две строки, один человек.
        {"engineer_id": "ENG-405", "team_id": "Team-L", "role": "Дизайнер",
         "grade": "Senior", "capacity_rate": "0.5",
         "skills_declared": "Figma, UX/UI"},
        {"engineer_id": "ENG-405", "team_id": "Team-Alpha", "role": "Дизайнер",
         "grade": "Senior", "capacity_rate": "0.5",
         "skills_declared": "Figma, UX/UI"},
        {"engineer_id": "ENG-420", "team_id": "Team-Platform",
         "role": "Разработчик .net", "grade": "Senior", "capacity_rate": "1.0",
         "skills_declared": "C#, .NET Core, CI/CD (GitLab, Jenkins)"},
        {"engineer_id": "ENG-401", "team_id": "Team-L", "role": "Разработчик Java",
         "grade": "Middle", "capacity_rate": "1.0",
         "skills_declared": "Java, Apache Kafka"},
        {"engineer_id": "ENG-414", "team_id": "Team-K", "role": "Разработчик Java",
         "grade": "Senior", "capacity_rate": "1.0", "skills_declared": "Java, Kafka"},
        # Разное написание роли: «Девопс» в профиле, «ДевОпс» в смете.
        {"engineer_id": "ENG-419", "team_id": "Team-Platform", "role": "Девопс",
         "grade": "Senior", "capacity_rate": "1.0", "skills_declared": "Docker"},
    ]


@pytest.fixture
def plan():
    return {"current": [
        {"task_id": "DB-202", "prodf_id": "PRODF-7124", "team_id": "Team-Platform",
         "status": "InProgress", "decision": "included", "estimated_hh": 324,
         "planned_start_sprint": 2, "planned_end_sprint": 3},
        {"task_id": "MOB-7011", "prodf_id": "PRODF-7121", "team_id": "Team-L",
         "status": "ToDo", "decision": "included", "estimated_hh": 165,
         "planned_start_sprint": 1, "planned_end_sprint": 1},
        {"task_id": "OLD-1", "prodf_id": "PRODF-7100", "team_id": "Team-L",
         "status": "Done", "estimated_hh": 10},
    ]}


@pytest.fixture
def estimates():
    return [
        {"task_id": "DB-202", "role": "Разработчик .net", "hours": 130},
        {"task_id": "DB-202", "role": "ДевОпс", "hours": 32},
        {"task_id": "DB-202", "role": "Руководитель проекта", "hours": 20},
        {"task_id": "MOB-7011", "role": "Дизайнер", "hours": 35},
        {"task_id": "MOB-7011", "role": "Разработчик Java", "hours": 40},
        {"task_id": "OLD-1", "role": "Разработчик .net", "hours": 50},
    ]


def test_split_skills_keeps_brackets() -> None:
    parts = split_skills("CI/CD (GitLab, Jenkins), Docker")
    assert parts == ["CI/CD (GitLab, Jenkins)", "Docker"]


def test_kafka_aliases_are_one_technology(engineers) -> None:
    assert canonical_tech("Apache Kafka") == canonical_tech("Kafka")
    assert bus_factor("Apache Kafka", engineers) == 2


def test_part_time_engineer_counted_once(engineers) -> None:
    # ENG-405 записан двумя строками, но человек один.
    assert bus_factor("Дизайнер", engineers) == 1
    assert bus_factor("Figma", engineers) == 1


def test_role_spelling_normalized(engineers) -> None:
    assert bus_factor("ДевОпс", engineers) == bus_factor("Девопс", engineers) == 1


def test_bus_factor_by_team(engineers) -> None:
    assert bus_factor("Разработчик Java", engineers) == 2
    assert bus_factor("Разработчик Java", engineers, team_id="Team-L") == 1
    assert bus_factor("Разработчик Java", engineers, team_id="Team-K") == 1


def test_unknown_competency_is_zero(engineers) -> None:
    assert bus_factor("Разработчик 1С", engineers) == 0


def test_engineer_profile_merges_teams(engineers) -> None:
    profile = build_engineer_profiles(engineers)["ENG-405"]
    assert profile["teams"] == {"Team-L": 0.5, "Team-Alpha": 0.5}
    assert profile["capacity_rate"] == 1.0
    assert profile["hours_per_sprint"] == 80.0
    assert profile["is_shared"] is True


def test_team_profile_lists_roles_and_capacity(engineers) -> None:
    teams = build_team_profiles(engineers)
    assert teams["Team-L"]["fte"] == 1.5
    assert teams["Team-L"]["hours_per_sprint"] == 120.0
    assert "ENG-405" in teams["Team-L"]["shared_engineers"]
    assert teams["Team-Platform"]["roles"]["Разработчик .net"] == ["ENG-420"]


def test_bus_factor_one_linked_to_task(plan, engineers, estimates) -> None:
    """Эталон ТЗ: компетенция-одиночка связывается с конкретной задачей плана."""
    spof = find_single_points_of_failure(engineers, plan, estimates=estimates)
    assert "Разработчик .net" in spof and "C#" in spof

    links = link_risk_to_tasks(plan, spof, engineers, estimates)
    net_tasks = links["Разработчик .net"]
    assert [t["task_id"] for t in net_tasks] == ["DB-202"]
    assert net_tasks[0]["prodf_id"] == "PRODF-7124"
    assert net_tasks[0]["hours"] == 130
    assert net_tasks[0]["owner_engineer_ids"] == ["ENG-420"]


def test_done_tasks_are_not_at_risk(plan, engineers, estimates) -> None:
    links = link_risk_to_tasks(plan, ["Разработчик .net"], engineers, estimates)
    assert "OLD-1" not in [t["task_id"] for t in links["Разработчик .net"]]


def test_missing_competency_has_zero_bus_factor(plan, engineers, estimates) -> None:
    missing = find_missing_competencies(plan, engineers, estimates)
    assert "Руководитель проекта" in missing
    assert bus_factors(engineers, plan, estimates=estimates)["Руководитель проекта"] == 0


def test_shared_java_is_not_single_point(plan, engineers, estimates) -> None:
    # Java-разработчиков двое, значит MOB-7011 по этой роли не в зоне риска.
    spof = find_single_points_of_failure(engineers, plan, estimates=estimates)
    assert "Разработчик Java" not in spof


def test_absence_impact_names_tasks_and_goals(plan, engineers, estimates) -> None:
    impact = absence_impact("ENG-420", plan, engineers, estimates)
    assert impact["role"] == "Разработчик .net"
    assert impact["teams"] == ["Team-Platform"]
    assert "C#" in impact["sole_competencies"]
    assert [t["task_id"] for t in impact["tasks_at_risk"]] == ["DB-202"]
    assert impact["prodf_at_risk"] == ["PRODF-7124"]


def test_summary_matches_frontend_contract(plan, engineers, estimates) -> None:
    summary = starmap_summary(plan, engineers, estimates)
    assert {"engineers", "bus_factors", "linked_tasks"} <= set(summary)
    assert all(isinstance(v, int) for v in summary["bus_factors"].values())
    # Фронт ищет носителя по skills_declared профиля — имена должны совпадать.
    skills = {s for e in summary["engineers"] for s in e["skills_declared"]}
    assert {s for s, bf in summary["bus_factors"].items() if bf} & skills
