# Ответственный: Человек 3 (starmap + explainability)
"""Сборка профилей команд и инженеров для звёздной карты компетенций.

Инженер с частичной ставкой записан в датасете несколькими строками — по одной
на каждую команду (ENG-405, ENG-406, ENG-419, ENG-426 разделены пополам).
Здесь такие строки схлопываются в один профиль: иначе Bus Factor посчитает
одного человека за двоих, а доступные часы удвоятся.
"""

from __future__ import annotations

from typing import Any, Iterable

from backend.starmap.competencies import (
    canonical_tech,
    role_display_names,
    role_key,
    split_skills,
    tech_key,
)

# Один инженер на полной ставке даёт 80 ч за двухнедельный спринт
# (Focus Factor уже учтён, см. онбординг).
SPRINT_HOURS = 80.0


def _as_dict(engineer: Any) -> dict[str, Any]:
    if isinstance(engineer, dict):
        return engineer
    return {
        name: getattr(engineer, name)
        for name in ("engineer_id", "team_id", "role", "grade",
                     "capacity_rate", "skills_declared")
        if hasattr(engineer, name)
    }


def normalize_engineers(engineers: Iterable[Any]) -> list[dict[str, Any]]:
    """Схлопывает строки Engineers_profiles в профили инженеров.

    Args:
        engineers: строки Engineers_profiles (dict или dataclass Engineer).

    Returns:
        Список профилей: engineer_id, role, role_key, grade, teams
        {команда: ставка}, capacity_rate (сумма ставок), hours_per_sprint,
        skills_declared (канонические технологии) и skill_keys.
    """
    rows = [_as_dict(e) for e in engineers or ()]
    display = role_display_names(rows)
    profiles: dict[str, dict[str, Any]] = {}

    for row in rows:
        engineer_id = row.get("engineer_id")
        if not engineer_id:
            continue
        role = " ".join(str(row.get("role") or "").split())
        key = role_key(role)
        profile = profiles.setdefault(engineer_id, {
            "engineer_id": engineer_id,
            "role": display.get(key, role),
            "role_key": key,
            "grade": row.get("grade"),
            "teams": {},
            "capacity_rate": 0.0,
            "skills_declared": [],
            "skill_keys": set(),
        })
        team = row.get("team_id")
        rate = float(row.get("capacity_rate") or 0)
        if team is not None and team not in profile["teams"]:
            profile["teams"][team] = rate
            profile["capacity_rate"] += rate
        for skill in split_skills(row.get("skills_declared")):
            canonical = canonical_tech(skill)
            if tech_key(canonical) not in profile["skill_keys"]:
                profile["skill_keys"].add(tech_key(canonical))
                profile["skills_declared"].append(canonical)

    for profile in profiles.values():
        profile["hours_per_sprint"] = round(SPRINT_HOURS * profile["capacity_rate"], 2)
        profile["hours_by_team"] = {
            team: round(SPRINT_HOURS * rate, 2) for team, rate in profile["teams"].items()
        }
        profile["is_shared"] = len(profile["teams"]) > 1
        profile["skills_declared"].sort()

    return sorted(profiles.values(), key=lambda p: p["engineer_id"])


def build_engineer_profiles(engineers: Iterable[Any]) -> dict[str, Any]:
    """Профили инженеров для карты: engineer_id -> профиль.

    Профиль показывает роль, грейд, распределение между командами и доступные
    часы на спринт — это требование условия («распределение специалистов между
    командами и их доступность»).
    """
    return {p["engineer_id"]: p for p in normalize_engineers(engineers)}


def build_team_profiles(engineers: Iterable[Any]) -> dict[str, Any]:
    """Профили команд: team_id -> состав, роли, технологии и ёмкость в часах."""
    profiles = normalize_engineers(engineers)
    teams: dict[str, Any] = {}

    for profile in profiles:
        for team, rate in profile["teams"].items():
            team_profile = teams.setdefault(team, {
                "team_id": team,
                "engineers": [],
                "shared_engineers": [],
                "fte": 0.0,
                "hours_per_sprint": 0.0,
                "roles": {},
                "techs": {},
            })
            team_profile["engineers"].append(profile["engineer_id"])
            if profile["is_shared"]:
                team_profile["shared_engineers"].append(profile["engineer_id"])
            team_profile["fte"] += rate
            team_profile["hours_per_sprint"] += SPRINT_HOURS * rate
            team_profile["roles"].setdefault(profile["role"], []).append(
                profile["engineer_id"]
            )
            for skill in profile["skills_declared"]:
                team_profile["techs"].setdefault(skill, []).append(
                    profile["engineer_id"]
                )

    for team_profile in teams.values():
        team_profile["fte"] = round(team_profile["fte"], 2)
        team_profile["hours_per_sprint"] = round(team_profile["hours_per_sprint"], 2)
        team_profile["engineers"].sort()
        team_profile["shared_engineers"].sort()

    return dict(sorted(teams.items()))
