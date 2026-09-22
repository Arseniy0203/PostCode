# Ответственный: Человек 3 (starmap + explainability)
"""Bus Factor внутри команд и кадровые рекомендации звёздной карты.

Ответы экспертов: главное в звёздной карте — Bus Factor, считать его нужно
внутри конкретной команды (п. 9); плюсом будет подсветка «нужно нанять
сотрудника с ролью X» и «желательно обучить специалиста Y технологии Z» (п. 8).

team_bus_factor — матрица «команда × нужная ей компетенция»: сколько часов
    бэклога команды держится на роли/технологии, кто её носители в команде и
    кто — в других командах;
hire_recommendations — роли, которых нет ни у одного инженера компании;
train_candidates — кого обучить: у кандидата уже есть часть ключевого стека
    роли или навыков единственного носителя (дублёр учится быстрее новичка).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from backend.recalculation.plan_fields import (
    field,
    is_done,
    remaining_hh,
    task_id,
    team_id,
)
from backend.starmap.competencies import core_tech_for_role, role_key, tech_key
from backend.starmap.profiles import normalize_engineers

# Роли, у которых «соседняя» роль с тем же первым словом — разумный кандидат
# на обучение (руководитель BigData → руководитель проекта). Для «разработчик»
# это не работает: Java-разработчик не становится 1С-разработчиком за квартал.
_RELATED_PREFIXES = ("руководитель", "аналитик", "архитектор", "тестировщик")
_GRADE_ORDER = {"Senior": 0, "Middle": 1, "Junior": 2}


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _open_tasks(plan: Any) -> list[Any]:
    rows = plan.get("current") if isinstance(plan, dict) else plan
    return [t for t in rows or () if task_id(t) and not is_done(t)
            and field(t, "decision") != "cancelled" and not field(t, "deferred")]


def _demand(plan: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """команда → ключ роли → {name, hours, tasks}: остаток часов бэклога команды."""
    demand: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for task in _open_tasks(plan):
        for key, (name, hours) in remaining_hh(task).items():
            if hours <= 0:
                continue
            entry = demand[team_id(task)].setdefault(key, {"name": name, "hours": 0.0, "tasks": set()})
            entry["hours"] += hours
            entry["tasks"].add(task_id(task))
    return demand


def team_bus_factor(plan: Any, engineers: Iterable[Any]) -> dict[str, Any]:
    """Bus Factor внутри каждой команды по ролям и их ключевому стеку.

    Returns:
        {team_id: {"rows": [...], "risks": N, "headline": str}}; строка —
        competency, level (role/tech), role (для tech — чья это технология),
        hours, tasks, in_team, elsewhere, bus_factor, status, status_label.
        status: missing (нет в компании), borrowed (в команде нет, есть в других),
        single (один носитель в команде), ok.
    """
    profiles = normalize_engineers(engineers)
    result: dict[str, Any] = {}
    for team, roles in sorted(_demand(plan).items()):
        rows = []
        for key, need in sorted(roles.items(), key=lambda kv: -kv[1]["hours"]):
            holders = [p for p in profiles if p["role_key"] == key]
            rows.append(_row(need["name"], "role", need["name"], need, holders, team))
            for tech in core_tech_for_role(need["name"]):
                holders = [p for p in profiles if tech_key(tech) in p["skill_keys"]]
                rows.append(_row(tech, "tech", need["name"], need, holders, team))
        risks = [r for r in rows if r["status"] != "ok" and r["level"] == "role"]
        result[team] = {
            "rows": rows,
            "risks": len(risks),
            "headline": (f"{team}: {len(risks)} {_plural(len(risks), 'роль', 'роли', 'ролей')} с Bus Factor ≤ 1 "
                         f"в команде — " + ", ".join(f"«{r['competency']}» ({r['status_label']})"
                                                     for r in risks[:4])
                         if risks else f"{team}: у всех нужных ролей в команде ≥ 2 носителей"),
        }
    return result


def _row(name, level, role, need, holders, team):
    in_team = sorted(p["engineer_id"] for p in holders if team in p["teams"])
    elsewhere = sorted(p["engineer_id"] for p in holders if team not in p["teams"])
    if not holders:
        status, label = "missing", "нет в компании — нанять"
    elif not in_team:
        status, label = "borrowed", "в команде нет — зависим от других команд"
    elif len(in_team) == 1:
        status, label = "single", f"один носитель в команде ({in_team[0]})"
    else:
        status, label = "ok", "в норме"
    return {"competency": name, "level": level, "role": role,
            "hours": round(need["hours"], 1), "tasks": sorted(need["tasks"]),
            "in_team": in_team, "elsewhere": elsewhere, "bus_factor": len(in_team),
            "status": status, "status_label": label}


def hire_recommendations(plan: Any, engineers: Iterable[Any]) -> list[dict[str, Any]]:
    """«Нужно нанять сотрудника с ролью X»: роли бэклога без единого носителя."""
    profiles = normalize_engineers(engineers)
    have = {p["role_key"] for p in profiles}
    roles: dict[str, dict[str, Any]] = {}
    for team, by_role in _demand(plan).items():
        for key, need in by_role.items():
            if key in have:
                continue
            entry = roles.setdefault(key, {"role": need["name"], "hours": 0.0, "tasks": set(),
                                           "teams": defaultdict(float)})
            entry["hours"] += need["hours"]
            entry["tasks"] |= need["tasks"]
            entry["teams"][team] += need["hours"]
    result = []
    for entry in sorted(roles.values(), key=lambda e: -e["hours"]):
        teams = dict(sorted(entry["teams"].items(), key=lambda kv: -kv[1]))
        top = next(iter(teams))
        candidates = train_candidates(entry["role"], engineers)
        result.append({
            "role": entry["role"],
            "hours": round(entry["hours"], 1),
            "tasks": sorted(entry["tasks"]),
            "teams": {t: round(h, 1) for t, h in teams.items()},
            "train_instead": candidates[:1],
            "headline": (f"Нанять «{entry['role']}»: {entry['hours']:g} ч бэклога, "
                         f"{len(entry['tasks'])} {_plural(len(entry['tasks']), 'задача', 'задачи', 'задач')}; "
                         f"больше всего нужен в {top} "
                         f"({teams[top]:g} ч)"
                         + (f". Быстрее — обучить {candidates[0]['engineer_id']} "
                            f"({candidates[0]['role']}): {candidates[0]['why']}"
                            if candidates else "")),
        })
    return result


def train_candidates(role: str, engineers: Iterable[Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Кого обучить роли role, чтобы у неё появился дублёр.

    Кандидат уже знает часть ключевого стека роли (ROLE_CORE_TECH) или навыков
    её единственного носителя; для руководителей/аналитиков/архитекторов/
    тестировщиков подходит и соседняя роль с тем же первым словом. Носители
    роли кандидатами не считаются. Сортировка: пересечение навыков, грейд.
    """
    profiles = normalize_engineers(engineers)
    key = role_key(role)
    owners = [p for p in profiles if p["role_key"] == key]
    core = {tech_key(t): t for t in core_tech_for_role(role)}
    target = dict(core)
    for owner in owners:
        for skill in owner["skills_declared"]:
            target.setdefault(tech_key(skill), skill)
    words = str(role).split()
    prefix = role_key(words[0]) if words else ""
    related = prefix in _RELATED_PREFIXES

    candidates = []
    for p in profiles:
        if p["role_key"] == key:
            continue
        overlap = sorted(target[k] for k in p["skill_keys"] if k in target)
        core_hits = [k for k in p["skill_keys"] if k in core]
        same_family = related and p["role_key"].startswith(prefix)
        # Один общий навык вроде SQLite — не основание: нужен ключевой стек роли,
        # смежная роль или хотя бы два общих навыка с носителем.
        if not core_hits and not same_family and len(overlap) < 2:
            continue
        why = []
        if overlap:
            why.append("уже знает " + ", ".join(overlap[:4]))
        if same_family:
            why.append(f"смежная роль «{p['role']}»")
        candidates.append({
            "engineer_id": p["engineer_id"],
            "role": p["role"],
            "grade": p["grade"],
            "teams": sorted(p["teams"]),
            "overlap": overlap,
            "score": len(core_hits) * 3 + len(overlap) + (3 if same_family else 0),
            "why": "; ".join(why),
        })
    candidates.sort(key=lambda c: (-c["score"], _GRADE_ORDER.get(c["grade"], 3), c["engineer_id"]))
    return candidates[:limit]


def train_recommendations(plan: Any, engineers: Iterable[Any]) -> list[dict[str, Any]]:
    """«Желательно обучить специалиста Y технологии Z» для ролей с Bus Factor = 1."""
    profiles = normalize_engineers(engineers)
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in profiles:
        by_role[p["role_key"]].append(p)
    demand: dict[str, dict[str, Any]] = {}
    for by_key in _demand(plan).values():
        for key, need in by_key.items():
            entry = demand.setdefault(key, {"name": need["name"], "hours": 0.0, "tasks": set()})
            entry["hours"] += need["hours"]
            entry["tasks"] |= need["tasks"]
    result = []
    for key, need in sorted(demand.items(), key=lambda kv: -kv[1]["hours"]):
        owners = by_role.get(key, [])
        if len(owners) != 1:
            continue
        owner = owners[0]
        need = {**need, "name": owner["role"]}   # написание роли — как у людей
        candidates = train_candidates(need["name"], engineers)
        if not candidates:
            continue
        best = candidates[0]
        tech = ", ".join(core_tech_for_role(need["name"])) or f"задачам роли «{need['name']}»"
        result.append({
            "role": need["name"],
            "owner_id": owner["engineer_id"],
            "hours": round(need["hours"], 1),
            "tasks": sorted(need["tasks"]),
            "candidate": best,
            "alternatives": candidates[1:],
            "headline": (f"Обучить {best['engineer_id']} ({best['role']}, "
                         f"{', '.join(best['teams'])}) технологиям роли «{need['name']}» — {tech}: "
                         f"{best['why']}. Сейчас {len(need['tasks'])} "
                         f"{_plural(len(need['tasks']), 'задача', 'задачи', 'задач')} ({need['hours']:g} ч) "
                         f"{_plural(len(need['tasks']), 'держится', 'держатся', 'держатся')} "
                         f"только на {owner['engineer_id']}."),
        })
    return result
