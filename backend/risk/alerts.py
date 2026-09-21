# Ответственный: Человек 2 (recalculation + risk)
"""Обнаружение рисков по текущему плану и построение алертов.

Три типа из условия (contracts/alert_schema.md):
    red    — прогноз окончания задачи за пределами 6-го спринта (12 недель);
    yellow — задача задерживается и сдвигает цепочку зависимых задач;
    orange — потребность роли на следующий спринт больше фонда часов команды.
Дополнительно (type="info", не смешиваются с обязательными):
    overrun      — по роли потрачено больше сметы (ранний сигнал будущего red);
    data_quality — ошибки исходных таблиц (см. backend/risk/data_quality.py).
Каждый алерт содержит prodf_id_at_risk и рекомендацию, что можно сделать.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal

from backend.explainability.reasons import explain_shift
from backend.recalculation.plan_fields import (
    QUARTER_SPRINTS,
    current_sprint,
    end_sprint,
    field,
    is_done,
    is_included,
    normalize_role,
    prodf_id,
    remaining_hh,
    start_sprint,
    task_id,
    team_id,
)
from backend.recalculation.quarter import sprint_label

SPRINT_HOURS = 80.0
AlertType = Literal["red", "yellow", "orange", "info"]
_SEVERITY = {"red": 0, "yellow": 1, "orange": 2, "info": 3}


@dataclass(frozen=True)
class Alert:
    type: AlertType
    task_id: str
    prodf_id_at_risk: str
    description: str
    sprint_detected: int
    recommendation: str = ""
    category: str = ""
    team_id: str | None = None
    role: str | None = None
    deficit_hh: float | None = None
    affected_task_ids: tuple[str, ...] = ()
    affected_prodf_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["affected_task_ids"] = list(self.affected_task_ids)
        data["affected_prodf_ids"] = list(self.affected_prodf_ids)
        return data


def _fmt(hours: float) -> str:
    return f"{hours:g}" if float(hours).is_integer() else f"{hours:.1f}"


def _open_tasks(plan: Any) -> list[Any]:
    return [t for t in field(plan, "current", default=[]) or [] if not is_done(t)]


# --- red ---------------------------------------------------------------------


def check_critical(plan: Any) -> list[Alert]:
    """Красный: прогноз окончания > 6-го спринта или задача выпала из плана.

    Задача, которая была в baseline, а в current перенесена, — тоже срыв
    обещания на Неделе 0. Задачи, отложенные планировщиком сразу на Неделе 0,
    алертом не считаются: это решение, а не отклонение.
    """
    sprint = current_sprint(plan)
    in_baseline = {
        task_id(t) for t in field(plan, "baseline", default=()) or () if is_included(t)
    }
    alerts: list[Alert] = []
    for task in _open_tasks(plan):
        tid, prodf, team = task_id(task), prodf_id(task), team_id(task)
        end = end_sprint(task)
        if is_included(task):
            if end is None or end <= QUARTER_SPRINTS:
                continue
            description = (
                f"Прогноз окончания {tid} — {sprint_label(end)}, это позже "
                f"{sprint_label(QUARTER_SPRINTS)}. Инициатива {prodf} "
                f"не будет выполнена в квартале."
            )
        elif tid in in_baseline:
            reason = field(task, "reason_detail", default="причина не указана")
            description = (
                f"{tid} была в плане Недели 0, но при пересчёте выпала из квартала "
                f"({reason}). Инициатива {prodf} под угрозой срыва."
            )
        else:
            continue
        alerts.append(Alert(
            type="red",
            category="critical",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=description,
            sprint_detected=sprint,
            recommendation=(
                f"Согласовать с заказчиком {prodf} частичную цель из «Вариантов выбора "
                f"цели» или перенос; проверить, можно ли усилить команду {team}."
            ),
            team_id=team,
            affected_task_ids=(tid,),
            affected_prodf_ids=(prodf,),
        ))
    return alerts


# --- yellow ------------------------------------------------------------------


def dependency_graph(dependencies: Iterable[dict[str, Any]], known: set[str]):
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for dep in dependencies:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        if a in known and b in known and b not in children[a]:
            children[a].append(b)
            parents[b].append(a)
    return children, parents


def downstream(children: dict[str, list[str]], root: str) -> list[str]:
    seen, order, queue = {root}, [], deque([root])
    while queue:
        for child in children.get(queue.popleft(), []):
            if child not in seen:
                seen.add(child)
                order.append(child)
                queue.append(child)
    return order


def check_cascade(
    plan: Any,
    dependencies: list[dict[str, Any]],
    reference: Iterable[Any] | None = None,
) -> list[Alert]:
    """Жёлтый: задача задержалась и сдвигает цепочку зависимых задач.

    Сдвиг считается относительно reference (прошлый снапшот current; по
    умолчанию — baseline). Алерт ставится только на исходную задачу цепочки:
    если сдвинулась и её блокирующая задача, это следствие, а не причина.
    Все три типа связей трактуются как «A блокирует B».
    """
    sprint = current_sprint(plan)
    ref = reference if reference is not None else field(plan, "baseline", default=()) or ()
    ref_end = {task_id(t): end_sprint(t) for t in ref}
    current = {task_id(t): t for t in field(plan, "current", default=[]) or []}
    children, parents = dependency_graph(dependencies, set(current))

    shifted: dict[str, tuple[int, int]] = {}
    for tid, task in current.items():
        old, new = ref_end.get(tid), end_sprint(task)
        if not is_done(task) and is_included(task) and old and new and new > old:
            shifted[tid] = (old, new)

    alerts: list[Alert] = []
    for tid, (old, new) in shifted.items():
        if any(p in shifted for p in parents.get(tid, [])):
            continue
        chain = [c for c in downstream(children, tid) if not is_done(current[c])]
        if not chain:
            continue
        task = current[tid]
        prodf, team = prodf_id(task), team_id(task)
        shift = explain_shift(
            tid, old, new, prodf_id=prodf, cause_text="задача не уложилась в плановый спринт"
        )
        prodfs = tuple(dict.fromkeys([prodf, *(prodf_id(current[c]) for c in chain)]))
        alerts.append(Alert(
            type="yellow",
            category="cascade",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=f"{shift.detail} Сдвигается цепочка зависимых задач: {', '.join(chain)}.",
            sprint_detected=sprint,
            recommendation=(
                f"Поднять приоритет {tid} в команде {team}: она блокирует "
                f"{len(chain)} задач(и) в {len(prodfs)} инициатив(ах)."
            ),
            team_id=team,
            affected_task_ids=(tid, *chain),
            affected_prodf_ids=prodfs,
        ))
    return alerts


# --- orange ------------------------------------------------------------------


def _supply(engineers: Iterable[dict[str, Any]]) -> dict[tuple[str, str], float]:
    # Строка профиля = доля инженера в конкретной команде (парттайм 0.5 → 40 ч).
    supply: dict[tuple[str, str], float] = defaultdict(float)
    for eng in engineers:
        key = (str(eng.get("team_id")), normalize_role(eng.get("role", "")))
        supply[key] += SPRINT_HOURS * float(eng.get("capacity_rate") or 0)
    return supply


def check_resource_deficit(
    plan: Any,
    engineers: list[dict[str, Any]],
    sprint: int | None = None,
) -> list[Alert]:
    """Оранжевый: потребность роли на следующий спринт > фонда часов команды.

    Потребность задачи в спринте = остаток часов роли / оставшиеся спринты
    задачи (равномерно). Отдельно: роль, которой в команде нет совсем, а задачи
    из-за неё перенесены, — алерт с рекомендацией нанять.
    """
    detected = current_sprint(plan)
    target = sprint if sprint is not None else detected + 1
    supply = _supply(engineers)

    demand: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    missing: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    role_name: dict[str, str] = {}
    prodf_of: dict[str, str] = {}

    for task in _open_tasks(plan):
        tid, team = task_id(task), team_id(task)
        prodf_of[tid] = prodf_id(task)
        left = remaining_hh(task)
        if is_included(task):
            end = end_sprint(task)
            start = max(start_sprint(task) or target, target)
            if end is None or not start <= target <= end:
                continue
            for key, (name, hours) in left.items():
                role_name.setdefault(key, name)
                demand[(team, key)][tid] += hours / (end - start + 1)
        elif field(task, "decision") == "postponed":
            for key, (name, hours) in left.items():
                if supply.get((team, key), 0) == 0:
                    role_name.setdefault(key, name)
                    missing[(team, key)][tid] += hours

    alerts: list[Alert] = []
    if target <= QUARTER_SPRINTS:
        for (team, key), by_task in demand.items():
            need, have = sum(by_task.values()), supply.get((team, key), 0.0)
            if need <= have:
                continue
            alerts.append(_orange(
                team, role_name[key], by_task, prodf_of, detected,
                deficit=need - have,
                description=(
                    f"{sprint_label(target).capitalize()}: роли «{role_name[key]}» в {team} нужно "
                    f"{_fmt(round(need, 1))} ч, доступно {_fmt(have)} ч "
                    f"(дефицит {_fmt(round(need - have, 1))} ч)."
                ),
                recommendation=(
                    f"Перераспределить задачи роли «{role_name[key]}» на следующие спринты, "
                    f"привлечь инженера этой роли из другой команды или нанять."
                    if have else
                    f"В {team} нет ни одного инженера роли «{role_name[key]}»: нанять "
                    f"или временно привлечь из другой команды."
                ),
            ))

    # Один алерт на команду, а не на каждую пару «команда × роль»: иначе
    # на реальном датасете их десятки и начальник перестаёт их читать.
    by_team: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for (team, key), by_task in missing.items():
        by_team[team][key] = by_task
    for team, roles in by_team.items():
        hours = {key: sum(t.values()) for key, t in roles.items()}
        ordered = sorted(hours, key=lambda k: -hours[k])
        tasks_hh: dict[str, float] = defaultdict(float)
        for by_task in roles.values():
            for tid, h in by_task.items():
                tasks_hh[tid] += h
        listing = ", ".join(f"«{role_name[k]}» ({_fmt(round(hours[k], 1))} ч)" for k in ordered)
        alerts.append(_orange(
            team, role_name[ordered[0]], tasks_hh, prodf_of, detected,
            deficit=sum(hours.values()),
            description=(
                f"В {team} нет ни одного инженера ролей: {listing}. Из-за этого перенесены "
                f"задачи: {', '.join(sorted(tasks_hh))}."
            ),
            recommendation=(
                f"Нанять в {team} или обучить сотрудников команды, в первую очередь "
                f"«{role_name[ordered[0]]}»; до этого задачи остаются перенесёнными."
            ),
        ))
    return alerts


def _orange(team, role, by_task, prodf_of, detected, *, deficit, description, recommendation):
    ordered = sorted(by_task, key=lambda t: -by_task[t])
    prodfs = tuple(dict.fromkeys(prodf_of[t] for t in ordered))
    return Alert(
        type="orange",
        category="resource",
        task_id=ordered[0],
        prodf_id_at_risk=prodfs[0],
        description=description,
        sprint_detected=detected,
        recommendation=recommendation,
        team_id=team,
        role=role,
        deficit_hh=round(deficit, 1),
        affected_task_ids=tuple(ordered),
        affected_prodf_ids=prodfs,
    )


# --- info: перерасход сметы ----------------------------------------------------


def check_overrun(plan: Any) -> list[Alert]:
    """Инфо: по роли уже потрачено больше, чем заложено в калькуляторе сметы."""
    sprint = current_sprint(plan)
    alerts: list[Alert] = []
    for task in _open_tasks(plan):
        estimated = field(task, "estimated_hh", default={}) or {}
        spent = field(task, "spent_hh", default={}) or {}
        if not isinstance(estimated, dict):
            continue
        planned = {normalize_role(r): (r, float(h or 0)) for r, h in estimated.items()}
        over = []
        for role, hours in dict(spent).items():
            name, budget = planned.get(normalize_role(role), (role, 0.0))
            if float(hours or 0) > budget:
                over.append(f"«{name}»: {_fmt(float(hours))} ч из {_fmt(budget)} ч")
        if not over:
            continue
        tid, prodf, team = task_id(task), prodf_id(task), team_id(task)
        alerts.append(Alert(
            type="info",
            category="overrun",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=f"Перерасход сметы по {tid}: {'; '.join(over)}.",
            sprint_detected=sprint,
            recommendation=(
                f"Переоценить остаток {tid} с командой {team}: если оценка занижена, "
                f"прогноз окончания и инициатива {prodf} под риском."
            ),
            team_id=team,
            affected_task_ids=(tid,),
            affected_prodf_ids=(prodf,),
        ))
    return alerts


def collect_alerts(
    plan: Any,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    reference: Iterable[Any] | None = None,
) -> list[Alert]:
    """Все алерты по плану: red → yellow → orange → info, затем по инициативе."""
    alerts = [
        *check_critical(plan),
        *check_cascade(plan, dependencies, reference),
        *check_resource_deficit(plan, engineers),
        *check_overrun(plan),
    ]
    return sorted(alerts, key=lambda a: (_SEVERITY[a.type], a.prodf_id_at_risk, a.task_id))
