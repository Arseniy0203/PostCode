# Ответственный: Человек 3 (starmap + explainability)
"""Bus Factor компетенций и связь риска с задачами текущего плана.

Bus Factor компетенции — число инженеров, которые ею владеют. Единица означает,
что знание держится на одном человеке и его отсутствие останавливает работу.

Считаем на двух уровнях (см. competencies.py):
    role — роль из сметы задач. Через неё Bus Factor связывается с планом:
        в задачах есть часы по ролям, а технологий в них нет;
    tech — заявленная технология из профиля инженера.

Инженеры, разделённые между командами, считаются один раз: Bus Factor меряет
людей, а не строки в таблице.
"""

from __future__ import annotations

from typing import Any, Iterable

from backend.recalculation.plan_fields import (
    end_sprint,
    field,
    is_done,
    prodf_id,
    remaining_hh,
    start_sprint,
    task_id,
)
from backend.starmap.competencies import (
    canonical_tech,
    core_tech_for_role,
    role_key,
    tech_key,
)
from backend.starmap.profiles import normalize_engineers

BUS_FACTOR_METHOD = (
    "Bus Factor = число инженеров, владеющих компетенцией. Инженеры с частичной "
    "ставкой в нескольких командах считаются один раз. Компетенции берутся на двух "
    "уровнях: роль из сметы задач и заявленная технология из профиля. Названия "
    "приводятся к единому виду («Девопс» и «ДевОпс», «Kafka» и «Apache Kafka» — "
    "одно и то же). Bus Factor = 1 означает единственного носителя, Bus Factor = 0 — "
    "что компетенция нужна плану, но в компании её нет."
)

Level = str  # "role" | "tech"


def competency_index(engineers: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Индекс компетенций: ключ -> {name, level, engineers, teams}.

    Ключ роли и ключ технологии не пересекаются, потому что уровень входит
    в ключ: "role:разработчик.net", "tech:c#".
    """
    index: dict[str, dict[str, Any]] = {}

    def add(level: Level, key: str, name: str, profile: dict[str, Any]) -> None:
        entry = index.setdefault(f"{level}:{key}", {
            "name": name, "level": level, "engineers": set(), "teams": set(),
        })
        entry["engineers"].add(profile["engineer_id"])
        entry["teams"].update(profile["teams"])

    for profile in normalize_engineers(engineers):
        if profile["role"]:
            add("role", profile["role_key"], profile["role"], profile)
        for skill in profile["skills_declared"]:
            add("tech", tech_key(skill), skill, profile)
    return index


def bus_factor(
    competency: str,
    engineers: Iterable[Any],
    *,
    team_id: str | None = None,
    level: Level | None = None,
) -> int:
    """Считает Bus Factor компетенции.

    Args:
        competency: название роли или технологии в любом написании.
        engineers: строки Engineers_profiles.
        team_id: если задан, считаются только инженеры этой команды.
        level: "role" или "tech"; по умолчанию ищется на обоих уровнях.

    Returns:
        Число инженеров-носителей; 0, если носителей нет.
    """
    index = competency_index(engineers)
    levels = (level,) if level else ("role", "tech")
    profiles = {p["engineer_id"]: p for p in normalize_engineers(engineers)}

    owners: set[str] = set()
    for lvl in levels:
        key = role_key(competency) if lvl == "role" else tech_key(canonical_tech(competency))
        entry = index.get(f"{lvl}:{key}")
        if entry:
            owners |= entry["engineers"]
    if team_id is not None:
        owners = {e for e in owners if team_id in profiles[e]["teams"]}
    return len(owners)


def _plan_tasks(plan: Any) -> list[Any]:
    """Незавершённые задачи текущего плана (Plan.current или простой список)."""
    rows = plan
    if isinstance(plan, dict):
        rows = plan.get("current") or plan.get("baseline") or []
    elif hasattr(plan, "current"):
        rows = plan.current
    tasks = []
    for row in rows or ():
        if not task_id(row) or is_done(row):
            continue
        if field(row, "decision") == "cancelled":
            continue
        tasks.append(row)
    return tasks


def _add_role(roles: dict[str, tuple[str, float]], role: Any, hours: Any) -> None:
    key = role_key(role)
    value = float(hours or 0)
    if not key or "итого" in key or value <= 0:
        return
    name, before = roles.get(key, (" ".join(str(role).split()), 0.0))
    roles[key] = (name, before + value)


def estimates_index(estimates: Iterable[Any] | None) -> dict[str, dict[str, tuple[str, float]]]:
    """Смета в виде task_id -> {ключ роли: (название, часы)}.

    Планировщик кладёт в задачу estimated_hh одним числом, поэтому часы по
    ролям берём из Estimates — иначе связать задачу с компетенцией нечем.
    """
    index: dict[str, dict[str, tuple[str, float]]] = {}
    for row in estimates or ():
        tid = field(row, "task_id")
        if not tid:
            continue
        _add_role(index.setdefault(str(tid), {}), field(row, "role"), field(row, "hours"))
    return index


def _task_roles(
    task: Any,
    estimates: dict[str, dict[str, tuple[str, float]]] | None = None,
    engineer_roles: dict[str, str] | None = None,
) -> dict[str, tuple[str, float]]:
    """Роли задачи: ключ -> (название, часы).

    Источники по убыванию точности: estimated_hh словарём, смета Estimates,
    фактическое распределение часов по инженерам (allocation).
    """
    roles: dict[str, tuple[str, float]] = {}
    estimated = field(task, "estimated_hh", default={}) or {}
    if isinstance(estimated, dict):
        # Для задачи в работе важен остаток (смета − факт), а не вся смета.
        for name, hours in remaining_hh(task).values():
            _add_role(roles, name, hours)
        if not roles:
            for role, hours in estimated.items():
                _add_role(roles, role, hours)
    if roles:
        return roles

    from_estimates = (estimates or {}).get(task_id(task))
    if from_estimates:
        return dict(from_estimates)

    allocation = field(task, "allocation", default={}) or {}
    if isinstance(allocation, dict) and engineer_roles:
        for per_sprint in allocation.values():
            for who, hours in (per_sprint or {}).items():
                _add_role(roles, engineer_roles.get(who, who), hours)
    return roles


def _engineer_roles(engineers: Iterable[Any]) -> dict[str, str]:
    return {p["engineer_id"]: p["role"] for p in normalize_engineers(engineers or ())}


def required_competencies(
    plan: Any,
    engineers: Iterable[Any],
    estimates: Iterable[Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Компетенции, которые нужны незавершённым задачам плана.

    Роли берутся из сметы задач, технологии — ключевой стек этих ролей
    (ROLE_CORE_TECH). Именно они «критические»: без них бэклог встанет.
    Остальные 100+ технологий из профилей карту только засоряют.
    """
    required: dict[str, dict[str, Any]] = {}
    index = estimates_index(estimates)
    roles_by_engineer = _engineer_roles(engineers)
    for task in _plan_tasks(plan):
        for key, (name, hours) in _task_roles(task, index, roles_by_engineer).items():
            entry = required.setdefault(f"role:{key}", {
                "name": name, "level": "role", "hours": 0.0, "tasks": set(),
            })
            entry["hours"] += hours
            entry["tasks"].add(task_id(task))
            for tech in core_tech_for_role(name):
                tech_entry = required.setdefault(f"tech:{tech_key(tech)}", {
                    "name": canonical_tech(tech), "level": "tech",
                    "hours": 0.0, "tasks": set(),
                })
                tech_entry["hours"] += hours
                tech_entry["tasks"].add(task_id(task))
    return required


def bus_factors(
    engineers: Iterable[Any],
    plan: Any = None,
    *,
    only_required: bool = False,
    estimates: Iterable[Any] | None = None,
) -> dict[str, int]:
    """Bus Factor по всем компетенциям: название -> число носителей.

    Args:
        engineers: строки Engineers_profiles.
        plan: текущий план; нужен, чтобы показать компетенции с Bus Factor = 0
            (план их требует, носителей нет) и отфильтровать лишнее.
        only_required: оставить только компетенции, нужные задачам плана.
    """
    index = competency_index(engineers)
    required = (required_competencies(plan, engineers, estimates)
                if plan is not None else {})
    result: dict[str, int] = {}

    for key, entry in index.items():
        if only_required and key not in required:
            continue
        result[entry["name"]] = len(entry["engineers"])
    for key, entry in required.items():
        if key not in index:
            result[entry["name"]] = 0  # компетенция нужна плану, носителей нет
    return dict(sorted(result.items(), key=lambda item: (item[1], item[0])))


def find_single_points_of_failure(
    engineers: Iterable[Any],
    plan: Any = None,
    *,
    only_required: bool = True,
    estimates: Iterable[Any] | None = None,
) -> list[str]:
    """Компетенции с Bus Factor = 1.

    Если передан план, по умолчанию возвращаются только компетенции, которые
    ему нужны: иначе список превращается в 87 технологий из профилей, среди
    которых Jira и TOGAF.
    """
    factors = bus_factors(engineers, plan, estimates=estimates,
                          only_required=only_required and plan is not None)
    return [name for name, count in factors.items() if count == 1]


def find_missing_competencies(
    plan: Any,
    engineers: Iterable[Any],
    estimates: Iterable[Any] | None = None,
) -> list[str]:
    """Компетенции с Bus Factor = 0: план их требует, а носителей нет."""
    index = competency_index(engineers)
    return sorted(
        entry["name"]
        for key, entry in required_competencies(plan, engineers, estimates).items()
        if key not in index
    )


def link_risk_to_tasks(
    plan: Any,
    competencies: Iterable[str],
    engineers: Iterable[Any] | None = None,
    estimates: Iterable[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Связывает компетенции-одиночки с задачами текущего плана.

    Риск виден не отдельной табличкой, а в связке с планом: по каждой
    компетенции возвращаются задачи, которые на ней держатся, с командой,
    часами, спринтами, целью PRODF и носителем компетенции.

    Args:
        plan: текущий Plan (используется Plan.current).
        competencies: компетенции с Bus Factor = 1 (результат
            find_single_points_of_failure).
        engineers: строки Engineers_profiles; без них носитель не определяется.

    Returns:
        Словарь competency -> список задач под риском.
    """
    index = competency_index(engineers or ())
    profiles = normalize_engineers(engineers or ())
    estimate_rows = estimates_index(estimates)
    roles_by_engineer = _engineer_roles(engineers or ())
    result: dict[str, list[dict[str, Any]]] = {}

    for competency in competencies:
        entry = (index.get(f"role:{role_key(competency)}")
                 or index.get(f"tech:{tech_key(canonical_tech(competency))}"))
        owners = sorted(entry["engineers"]) if entry else []
        # Для технологии задачи ищем по ролям её носителей: задачи знают роли.
        owner_roles = {role_key(p["role"])
                       for p in profiles if p["engineer_id"] in owners}
        if entry and entry["level"] == "role":
            owner_roles = {role_key(entry["name"])}

        linked: list[dict[str, Any]] = []
        for task in _plan_tasks(plan):
            roles = _task_roles(task, estimate_rows, roles_by_engineer)
            assigned = field(task, "assigned_engineers", default=[]) or []
            matched = [key for key in roles if key in owner_roles]
            if not matched and not (owners and set(owners) & set(assigned)):
                continue
            role_name, hours = (roles[matched[0]] if matched else ("", 0.0))
            linked.append({
                "task_id": task_id(task),
                "prodf_id": prodf_id(task),
                "decision": field(task, "decision", default="included") or "included",
                "team_id": field(task, "team_id", default=""),
                "role": role_name,
                "hours": hours,
                "planned_start_sprint": start_sprint(task),
                "planned_end_sprint": end_sprint(task),
                "owner_engineer_ids": owners,
            })
        if linked:
            result[competency] = sorted(linked, key=lambda t: t["task_id"])
    return result


def absence_impact(
    engineer_id: str,
    plan: Any,
    engineers: Iterable[Any],
    estimates: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Что встанет, если этого инженера не будет.

    Прямой ответ на требование условия: показать, где отсутствие одного
    сотрудника создаёт риск для выполнения работ.
    """
    from backend.starmap.recommendations import train_candidates

    index = competency_index(engineers)
    required = required_competencies(plan, engineers, estimates) if plan is not None else {}
    # Только то, что нужно плану: TOGAF и ARKit в профиле — не риск для квартала.
    lost = sorted(
        entry["name"] for key, entry in index.items()
        if entry["engineers"] == {engineer_id} and (not required or key in required)
    )
    linked = link_risk_to_tasks(plan, lost, engineers, estimates)
    tasks = {t["task_id"]: t for items in linked.values() for t in items}
    in_plan = [t for t in tasks.values() if t.get("decision") != "postponed"]
    profile = next((p for p in normalize_engineers(engineers)
                    if p["engineer_id"] == engineer_id), None)
    backups = train_candidates(profile["role"], engineers) if profile else []
    return {
        "engineer_id": engineer_id,
        "role": profile["role"] if profile else "",
        "teams": sorted(profile["teams"]) if profile else [],
        "sole_competencies": lost,
        "tasks_at_risk": sorted(in_plan, key=lambda t: t["task_id"]),
        "postponed_tasks": sorted((t for t in tasks.values() if t.get("decision") == "postponed"),
                                  key=lambda t: t["task_id"]),
        "hours_at_risk": round(sum(float(t.get("hours") or 0) for t in in_plan), 1),
        "prodf_at_risk": sorted({t["prodf_id"] for t in in_plan if t["prodf_id"]}),
        "backup_candidates": backups,
    }


def starmap_summary(
    plan: Any,
    engineers: Iterable[Any],
    estimates: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Данные звёздной карты одним объектом — для фронта и API.

    Ключи engineers / bus_factors / linked_tasks совпадают с тем, что ожидает
    frontend/views/star_map_view.py.
    """
    from backend.starmap.profiles import build_engineer_profiles, build_team_profiles
    from backend.starmap.recommendations import (
        hire_recommendations,
        team_bus_factor,
        train_recommendations,
    )

    profiles = normalize_engineers(engineers)
    required = (required_competencies(plan, engineers, estimates)
                if plan is not None else {})
    factors = bus_factors(engineers, plan, estimates=estimates,
                          only_required=plan is not None)
    spof = [name for name, count in factors.items() if count == 1]
    missing = (find_missing_competencies(plan, engineers, estimates)
               if plan is not None else [])

    linked = link_risk_to_tasks(plan, spof, engineers, estimates)
    index = competency_index(engineers)
    competencies = [
        {
            "competency": name,
            "engineers_count": count,
            "bus_factor": count,
            "is_risk": count <= 1,
            "linked_task_ids": ([t["task_id"] for t in linked.get(name, [])] or sorted(
                next((set(e["tasks"]) for e in required.values() if e["name"] == name), set()))),
            "engineer_ids": sorted(
                next((e["engineers"] for e in index.values() if e["name"] == name), set())
            ),
        }
        for name, count in factors.items()
    ]

    return {
        "competencies": competencies,
        "engineers": profiles,
        "bus_factors": factors,
        "linked_tasks": linked,
        "single_points_of_failure": spof,
        "missing_competencies": missing,
        "absence_impact": {
            engineer["engineer_id"]: absence_impact(
                engineer["engineer_id"], plan, engineers, estimates)
            for engineer in profiles
            if any(name in spof for name in
                   [engineer["role"], *engineer["skills_declared"]])
        },
        "engineer_profiles": build_engineer_profiles(engineers),
        "team_profiles": build_team_profiles(engineers),
        "single_points_grouped": _group_by_role(spof, profiles),
        "team_bus_factor": team_bus_factor(plan, engineers) if plan is not None else {},
        "recommendations": {
            "hire": hire_recommendations(plan, engineers) if plan is not None else [],
            "train": train_recommendations(plan, engineers) if plan is not None else [],
        },
        "method": BUS_FACTOR_METHOD,
    }


def _group_by_role(spof: list[str], profiles: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Одиночки, сгруппированные по носителю: «Разработчик .net» — C#, .NET Core.

    Роль и её ключевой стек у единственного носителя — один и тот же риск;
    в UI его лучше показать одной строкой, а не тремя.
    """
    by_owner: dict[str, list[str]] = {}
    index = competency_index(profiles_to_rows(profiles))
    for name in spof:
        entry = next((e for e in index.values() if e["name"] == name), None)
        if not entry or len(entry["engineers"]) != 1:
            continue
        owner = next(iter(entry["engineers"]))
        by_owner.setdefault(owner, []).append(name)
    role_of = {p["engineer_id"]: p["role"] for p in profiles}
    grouped: dict[str, list[str]] = {}
    for owner, names in sorted(by_owner.items()):
        head = role_of.get(owner) or owner
        grouped[f"{head} ({owner})"] = sorted(n for n in names if n != head)
    return grouped


def profiles_to_rows(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Профили обратно в строки Engineers_profiles (по строке на команду)."""
    rows = []
    for p in profiles:
        for team, rate in (p.get("teams") or {"": 1.0}).items():
            rows.append({"engineer_id": p["engineer_id"], "team_id": team, "role": p["role"],
                         "grade": p.get("grade"), "capacity_rate": rate,
                         "skills_declared": list(p.get("skills_declared") or [])})
    return rows
