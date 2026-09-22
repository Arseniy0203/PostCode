# Ответственный: Человек 2 (recalculation + risk)
"""Отчёт о качестве исходных данных.

Ответ экспертов: задачи-призраки и прочие ошибки таблиц не «чиним», а
валидируем и выносим наружу. Работает на сырых данных, до планирования.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from backend.recalculation.plan_fields import normalize_role, prodf_id, task_id
from backend.risk.alerts import Alert

TOTAL_ROW = normalize_role("Итого по ролям")


def _hours(value: Any) -> float:
    try:
        return float(str(value).replace(",", ".")) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _info(tid: str, prodf: str, description: str, recommendation: str,
          affected: Iterable[str] = ()) -> Alert:
    return Alert(
        type="info",
        category="data_quality",
        task_id=tid,
        prodf_id_at_risk=prodf,
        description=description,
        sprint_detected=0,
        recommendation=recommendation,
        affected_task_ids=tuple(affected) or (tid,),
    )


def _cycles(edges: set[tuple[str, str]]) -> list[list[str]]:
    """Простые циклы графа зависимостей (включая самоссылки), каждый — один раз."""
    graph: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(edges):
        graph[a].append(b)
    found: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    def walk(start: str, node: str, path: list[str]) -> None:
        for nxt in graph.get(node, []):
            if nxt == start:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key)
                    found.append(list(path))
            elif nxt not in path and nxt > start:
                walk(start, nxt, [*path, nxt])

    for start in sorted(graph):
        walk(start, start, [start])
    return found


def check_data_quality(
    tasks: list[dict[str, Any]],
    estimates: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]] | None = None,
    team_history: list[dict[str, Any]] | None = None,
) -> list[Alert]:
    """Задачи-призраки, дубли связей, расхождение estimated_hh с калькулятором,
    незавершённые задачи без сметы, команды без истории velocity и без инженеров.

    Args:
        tasks: задачи из Tasks.
        estimates: калькулятор сметы в длинном формате {task_id, role, hours}.
        dependencies: ВСЕ связи, включая отброшенные при парсинге.
        engineers: профили инженеров (для проверки команд без людей).
        team_history: история спринтов (для проверки команд без velocity).
    """
    by_id = {task_id(t): t for t in tasks}
    alerts: list[Alert] = []
    open_tasks = [t for t in tasks if str(t.get("status", "")).casefold() != "done"]
    teams = sorted({str(t.get("team_id")) for t in open_tasks if t.get("team_id")})

    if team_history is not None:
        with_history = {str(h.get("team_id")) for h in team_history}
        for team in teams:
            if team not in with_history:
                ids = sorted(task_id(t) for t in open_tasks if t.get("team_id") == team)
                alerts.append(_info(
                    ids[0], prodf_id(by_id[ids[0]]),
                    f"У {team} нет истории спринтов в Team_history: ёмкость 0 SP, "
                    f"все её задачи ({', '.join(ids)}) будут отложены.",
                    f"Добавить velocity {team} за прошлые спринты.",
                    affected=ids,
                ))
    if engineers is not None:
        staffed = {str(e.get("team_id")) for e in engineers}
        for team in teams:
            if team not in staffed:
                ids = sorted(task_id(t) for t in open_tasks if t.get("team_id") == team)
                alerts.append(_info(
                    ids[0], prodf_id(by_id[ids[0]]),
                    f"У {team} нет ни одного инженера в Engineers_profiles, а задач у неё "
                    f"{len(ids)}: {', '.join(ids)}.",
                    f"Проверить штатное расписание {team}; до этого задачи выполняются "
                    f"только инженерами других команд или откладываются.",
                    affected=ids,
                ))

    def prodf_of(*ids: str) -> str:
        return next((prodf_id(by_id[i]) for i in ids if i in by_id), "")

    ghost_pairs: set[tuple[str, str]] = set()
    for dep in dependencies:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        ghosts = [x for x in (a, b) if x not in by_id]
        if ghosts and (a, b) not in ghost_pairs:
            # Повтор той же связи — это отдельная находка «дубль», ниже.
            ghost_pairs.add((a, b))
            alerts.append(_info(
                a, prodf_of(a, b),
                f"Связь {a} → {b} ({dep.get('dependency_type', '—')}) ссылается на "
                + (f"задачу-призрак {ghosts[0]}: её нет" if len(ghosts) == 1
                   else f"задачи-призраки {', '.join(ghosts)}: их нет")
                + " в таблице Tasks. Связь исключена из планирования.",
                "Уточнить у владельца данных, существует ли задача, и поправить Task_dependencies.",
                affected=(a, b),
            ))

    known = {(d.get("blocking_task_id"), d.get("blocked_task_id")) for d in dependencies
             if d.get("blocking_task_id") in by_id and d.get("blocked_task_id") in by_id}
    for cycle in _cycles(known):
        alerts.append(_info(
            cycle[0], prodf_of(*cycle),
            ("Задача ссылается сама на себя: " if len(cycle) == 1 else "Цикл зависимостей: ")
            + " → ".join([*cycle, cycle[0]]) + ". Такие задачи никогда не смогут начаться.",
            "Разорвать цикл в Task_dependencies (уточнить у владельца данных, какая связь лишняя).",
            affected=cycle,
        ))

    pairs = Counter((d.get("blocking_task_id"), d.get("blocked_task_id")) for d in dependencies)
    for (a, b), count in pairs.items():
        if count > 1:
            types = sorted({d.get("dependency_type", "—") for d in dependencies
                            if (d.get("blocking_task_id"), d.get("blocked_task_id")) == (a, b)})
            alerts.append(_info(
                a, prodf_of(a, b),
                f"Связь {a} → {b} указана {count} раза с типами: {', '.join(types)}.",
                "Оставить одну связь; для планирования все типы равнозначны.",
                affected=(a, b),
            ))

    calc: dict[str, float] = defaultdict(float)
    for row in estimates:
        if normalize_role(row.get("role", "")) != TOTAL_ROW:
            calc[str(row.get("task_id"))] += _hours(row.get("hours"))

    for tid in sorted(set(calc) - set(by_id)):
        alerts.append(_info(
            tid, "",
            f"В калькуляторе сметы есть задача {tid} ({calc[tid]:g} ч), которой нет в Tasks.",
            "Проверить, не потеряна ли задача при выгрузке; в планировании не участвует.",
        ))

    from backend.recalculation.recalc import canonical_status

    for task in tasks:
        status = task.get("status")
        if status is not None and str(status).strip() and not canonical_status(status)[1]:
            alerts.append(_info(
                task_id(task), prodf_id(task),
                f"У {task_id(task)} статус «{status}» не распознан (ожидались ToDo / InProgress / Done).",
                "Исправить статус в датасете; до этого задача планируется как незавершённая.",
            ))

    for task in open_tasks:
        tid = task_id(task)
        sp = task.get("estimation_sp")
        # поле есть (колонка обязательна в Tasks), но пустое или не число
        if "estimation_sp" in task and (not isinstance(sp, (int, float)) or not sp > 0):
            alerts.append(_info(
                tid, prodf_id(task),
                f"У незавершённой задачи {tid} нет оценки в Story Points (пусто или не число).",
                "Оценить задачу в SP: пока лимит ёмкости команды по SP к ней не применяется.",
            ))
        if calc.get(tid, 0) <= 0:
            alerts.append(_info(
                tid, prodf_id(task),
                f"У незавершённой задачи {tid} нет оценки в калькуляторе сметы.",
                "Оценить задачу по ролям; до этого она откладывается с причиной «Не оценивалась».",
            ))

    for tid, task in by_id.items():
        declared = task.get("estimated_hh")
        # Пустая колонка (0 / NaN) — не расхождение, а отсутствие значения
        if not isinstance(declared, (int, float)) or not declared > 0 or tid not in calc:
            continue
        if abs(float(declared) - calc[tid]) > 0.01:
            alerts.append(_info(
                tid, prodf_id(task),
                f"{tid}: estimated_hh в Tasks = {float(declared):g} ч, а сумма калькулятора сметы "
                f"по ролям = {calc[tid]:g} ч. Значение в Tasks некорректно.",
                f"Перезаполнить estimated_hh значением калькулятора ({calc[tid]:g} ч) — "
                f"калькулятор является целевой сметой.",
            ))
    return alerts
