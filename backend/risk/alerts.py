# Ответственный: Человек 2 (recalculation + risk)
"""Обнаружение рисков по текущему плану и построение алертов.

Три типа из условия (contracts/alert_schema.md):
    red    — прогноз окончания задачи за пределами 6-го спринта (12 недель);
    yellow — задача задерживается и сдвигает цепочку зависимых задач;
    orange/resource — потребность роли на следующий спринт больше фонда часов
                      всех сотрудников этой роли в компании (как в условии);
    orange/hiring   — в компании нет ни одного инженера роли, задачи отложены;
    orange/capacity — роли есть, но задача больше ёмкости спринта или часы заняты.
Дополнительно (type="info", не смешиваются с обязательными):
    overrun      — по роли потрачено больше сметы (ранний сигнал будущего red);
    stale        — задача в работе без движения;
    kpi          — прогноз PI Predictability или Say/Do спринта вне нормы;
    single_point — задачи держатся на единственном в компании инженере роли;
    blocked      — задача отложена из-за отложенной блокирующей задачи (с корневой причиной);
    data_quality — ошибки исходных таблиц (см. backend/risk/data_quality.py).
Каждый алерт содержит prodf_id_at_risk и рекомендацию, что можно сделать.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
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
from backend.recalculation.quarter import (
    BORROW_LEAD_SPRINTS,
    BORROW_RAMP,
    HIRE_LEAD_SPRINTS,
    ONBOARDING_RAMP,
    QUARTER_START,
    next_quarter_start,
    ramp_factor,
    ready_sprint,
    sprint_label,
)

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
    status: str = "new"
    since_sprint: int | None = None
    defer_task_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("affected_task_ids", "affected_prodf_ids", "defer_task_ids"):
            data[key] = list(getattr(self, key))
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alert":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in ("affected_task_ids", "affected_prodf_ids", "defer_task_ids"):
            known[key] = tuple(known.get(key) or ())
        return cls(**known)


def _pct(value: float) -> str:
    """89.7 → «89.7», 100.0 → «100»: у границы нормы округление не должно врать."""
    return f"{round(value, 1):g}"


def _fmt(hours: float) -> str:
    return f"{hours:g}" if float(hours).is_integer() else f"{hours:.1f}"


def _open_tasks(plan: Any) -> list[Any]:
    return [t for t in field(plan, "current", default=[]) or [] if not is_done(t)]


def _sp_left(task: Any) -> float:
    """Остаток SP пропорционально остатку часов (как его видит планировщик)."""
    sp = float(field(task, "estimation_sp", default=0) or 0)
    return sp * (1 - work_progress(task))


def work_progress(task: Any) -> float:
    """Доля выполненных работ по часам: потрачено / смета."""
    estimated = field(task, "estimated_hh", default={}) or {}
    spent = field(task, "spent_hh", default={}) or {}
    if not isinstance(estimated, dict):
        return 0.0
    total = sum(float(h or 0) for h in estimated.values())
    return min(1.0, sum(float(h or 0) for h in dict(spent).values()) / total) if total else 0.0


# --- red ---------------------------------------------------------------------


def check_critical(plan: Any, dependencies: list[dict[str, Any]] | None = None) -> list[Alert]:
    """Красный: прогноз окончания > 6-го спринта или задача выпала из плана.

    Задача, которая была в baseline, а в current перенесена, — тоже срыв
    обещания на Неделе 0. Задачи, отложенные планировщиком сразу на Неделе 0,
    алертом не считаются: это решение, а не отклонение. Если задача выпала
    из-за отложенной блокирующей задачи, рекомендация указывает на корень.
    """
    sprint = current_sprint(plan)
    in_baseline = {
        task_id(t) for t in field(plan, "baseline", default=()) or () if is_included(t)
    }
    chains = blocked_by_postponed(plan, dependencies or [])
    by_id = {task_id(t): t for t in field(plan, "current", default=[]) or []}
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
            done_share = work_progress(task)
            progress = f" Уже сделано {done_share:.0%} работ." if done_share else ""
            if field(task, "decision") == "cancelled":
                description = (
                    f"{tid} была в плане Недели 0, но отменена ({reason}).{progress} "
                    f"Инициатива {prodf} не будет выполнена в заявленном объёме."
                )
            elif field(task, "deferred"):
                description = (
                    f"{tid} была в плане Недели 0, но перенесена в следующий квартал "
                    f"решением руководителя ({field(task, 'defer_note') or 'без комментария'}). "
                    f"Инициатива {prodf} не будет выполнена в заявленном объёме."
                )
            else:
                description = (
                    f"{tid} была в плане Недели 0, но при пересчёте выпала из квартала "
                    f"({reason}).{progress} Инициатива {prodf} под угрозой срыва."
                )
        else:
            continue
        chain = chains.get(tid)
        if chain:
            root = by_id[chain[-1]]
            description += f" Корневая причина — {chain[-1]} ({team_id(root)}), цепочка: {' → '.join(chain)}."
            recommendation = (
                f"Решать причину у {chain[-1]} в {team_id(root)}: "
                f"{field(root, 'reason_detail', default='') or 'задача не в плане'}. "
                f"Параллельно согласовать с заказчиком {prodf} частичную цель или перенос."
            )
        else:
            recommendation = (
                f"Согласовать с заказчиком {prodf} частичную цель из «Вариантов выбора "
                f"цели» или перенос; проверить, можно ли усилить команду {team}."
            )
        alerts.append(Alert(
            type="red",
            category="critical",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=description,
            sprint_detected=sprint,
            recommendation=recommendation,
            team_id=team,
            affected_task_ids=(tid, *(chain or ())),
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

    Сдвиг считается относительно baseline: алерт держится, пока задача
    заканчивается позже, чем обещали на Неделе 0 (иначе он «решался» бы на
    следующем спринте, хотя сдвиг никуда не делся). reference (прошлый снапшот)
    нужен только для текста «ещё +N с прошлого пересчёта». Алерт ставится на
    исходную задачу цепочки: если сдвинулась и её блокирующая задача, это
    следствие, а не причина. Все три типа связей — «A блокирует B».
    """
    sprint = current_sprint(plan)
    base_end = {task_id(t): end_sprint(t) for t in field(plan, "baseline", default=()) or ()}
    prev_end = {task_id(t): end_sprint(t) for t in reference} if reference is not None else {}
    current = {task_id(t): t for t in field(plan, "current", default=[]) or []}
    children, parents = dependency_graph(dependencies, set(current))

    shifted: dict[str, tuple[int, int]] = {}
    for tid, task in current.items():
        old, new = base_end.get(tid), end_sprint(task)
        if not is_done(task) and is_included(task) and old and new and new > old:
            shifted[tid] = (old, new)

    def late_downstream(tid: str) -> list[str]:
        return [c for c in downstream(children, tid)
                if not is_done(current[c]) and is_included(current[c])
                and base_end.get(c) and (end_sprint(current[c]) or 0) > base_end[c]]

    # Задача закрыта с опозданием, а зависимые от неё всё ещё идут позже baseline:
    # сдвиг цепочки никуда не делся — алерт держится на исходной задаче.
    closed_late: set[str] = set()
    for tid, task in current.items():
        old, new = base_end.get(tid), field(task, "actual_end_sprint")
        if is_done(task) and old and new and new > old and late_downstream(tid):
            shifted[tid] = (old, new)
            closed_late.add(tid)

    alerts: list[Alert] = []
    for tid, (old, new) in shifted.items():
        if any(p in shifted for p in parents.get(tid, [])):
            continue
        chain = (late_downstream(tid) if tid in closed_late else
                 [c for c in downstream(children, tid)
                  if not is_done(current[c]) and is_included(current[c])])
        if not chain:
            continue
        task = current[tid]
        prodf, team = prodf_id(task), team_id(task)
        cause = ("задача закрыта с опозданием" if tid in closed_late
                 else "задача не уложилась в плановый спринт")
        shift = explain_shift(tid, old, new, prodf_id=prodf, cause_text=cause)
        prodfs = tuple(dict.fromkeys([prodf, *(prodf_id(current[c]) for c in chain)]))
        extra = ""
        if prev_end.get(tid) and new > prev_end[tid] and prev_end[tid] != old:
            extra = f" С прошлого пересчёта — ещё +{new - prev_end[tid]}."
        alerts.append(Alert(
            type="yellow",
            category="cascade",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=(f"{shift.detail}{extra} Сдвигается цепочка зависимых задач: "
                         f"{', '.join(chain)}."),
            sprint_detected=sprint,
            recommendation=(
                f"Ускорить зависимые задачи ({', '.join(chain)}) или согласовать с заказчиком "
                f"новый срок: {tid} уже закрыта, но цепочка отстаёт от baseline."
                if tid in closed_late else
                f"Поднять приоритет {tid} в команде {team}: она блокирует "
                f"{len(chain)} {_plural(len(chain), 'задачу', 'задачи', 'задач')} в "
                f"{len(prodfs)} {_plural(len(prodfs), 'инициативе', 'инициативах', 'инициативах')}."
            ),
            team_id=team,
            affected_task_ids=(tid, *chain),
            affected_prodf_ids=prodfs,
        ))
    return alerts


# --- orange ------------------------------------------------------------------


def role_supply(engineers: Iterable[dict[str, Any]]) -> dict[tuple[str, str], float]:
    # Строка профиля = доля инженера в конкретной команде (парттайм 0.5 → 40 ч).
    supply: dict[tuple[str, str], float] = defaultdict(float)
    for eng in engineers:
        key = (str(eng.get("team_id")), normalize_role(eng.get("role", "")))
        supply[key] += SPRINT_HOURS * float(eng.get("capacity_rate") or 0)
    return supply


def company_hours(engineers: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Фонд часов роли за спринт по всей компании (планировщик берёт людей из общего пула)."""
    total: dict[str, float] = defaultdict(float)
    for (_, key), hours in role_supply(engineers).items():
        total[key] += hours
    return total


def free_hours(plan: Any, engineers: Iterable[dict[str, Any]], sprint: int) -> dict[str, float]:
    """Свободные часы каждого инженера в спринте: фонд минус часы по allocation плана."""
    capacity: dict[str, float] = defaultdict(float)
    for eng in engineers:
        capacity[str(eng.get("engineer_id"))] += SPRINT_HOURS * float(eng.get("capacity_rate") or 0)
    used: dict[str, float] = defaultdict(float)
    for task in _open_tasks(plan):
        for key, hours in (field(task, "allocation", default={}) or {}).items():
            if int(key) == sprint and isinstance(hours, dict):
                for eng_id, h in hours.items():
                    used[str(eng_id)] += float(h or 0)
    return {e: c - used[e] for e, c in capacity.items() if c - used[e] > 0}


def _people_by_role(engineers: Iterable[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    """роль → {engineer_id: команды}; парттаймер с двумя строками — один человек."""
    people: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for eng in engineers:
        people[normalize_role(eng.get("role", ""))][str(eng.get("engineer_id"))].add(str(eng.get("team_id")))
    return people


class FreePool:
    """Свободные часы инженеров по спринтам first…last.

    Рекомендации «привлечь» расходуют часы из пула, поэтому одного и того же
    человека не предлагают нескольким командам на одни и те же часы.
    """

    def __init__(self, plan: Any, engineers: Iterable[dict[str, Any]], first: int,
                 last: int = QUARTER_SPRINTS):
        self.free = {s: free_hours(plan, engineers, s) for s in range(max(first, 1), last + 1)}

    def available(self, eng_id: str) -> float:
        return sum(free.get(eng_id, 0.0) for free in self.free.values())

    def take(self, eng_id: str, hours: float) -> float:
        taken = 0.0
        for free in self.free.values():
            part = min(hours - taken, free.get(eng_id, 0.0))
            if part > 0:
                free[eng_id] -= part
                taken += part
            if taken >= hours - 1e-9:
                break
        return taken


def _role_advice(name: str, key: str, need: float, team: str, people, pool: FreePool,
                 period: str) -> tuple[str, str]:
    """Нанять (нет в компании / есть, но заняты) или привлечь конкретных людей из пула."""
    staff = people.get(key, {})
    if not staff:
        return "hire", f"«{name}» — в компании нет ни одного"
    helpers, taken = [], 0.0
    for eng_id in sorted(staff, key=lambda e: -pool.available(e)):
        if staff[eng_id] == {team} or taken >= need - 1e-9:
            continue
        got = pool.take(eng_id, need - taken)
        if got > 0:
            taken += got
            sole = ", единственный носитель роли — риск Bus Factor" if len(staff) == 1 else ""
            helpers.append(f"{eng_id} из {', '.join(sorted(staff[eng_id] - {team}))} "
                           f"({_fmt(round(got, 1))} ч{sole})")
    who = "; ".join(helpers)
    if taken + 1e-6 < need:
        text = (f"«{name}» — в компании {len(staff)} чел., но {period} свободно только "
                f"{_fmt(round(taken, 1))} ч из нужных {_fmt(round(need, 1))} ч")
        return "hire", text + (f" (частично: {who})" if who else ", все заняты")
    return "borrow", f"«{name}» — {who}"


def _advice_text(items: list[tuple[str, str]]) -> str:
    hire = [t for kind, t in items if kind == "hire"]
    borrow = [t for kind, t in items if kind == "borrow"]
    parts = []
    if hire:
        parts.append("Нанять или обучить: " + "; ".join(hire) + ".")
    if borrow:
        parts.append("Привлечь из других команд: " + "; ".join(borrow) + ".")
    return " ".join(parts)


def blocked_by_postponed(plan: Any, dependencies: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Отложенная задача → цепочка до корневой отложенной блокирующей задачи."""
    current = {task_id(t): t for t in field(plan, "current", default=[]) or []}
    _, parents = dependency_graph(dependencies, set(current))

    def stuck(tid: str) -> bool:
        task = current[tid]
        return not is_done(task) and not is_included(task)

    chains: dict[str, list[str]] = {}
    for tid in current:
        if not stuck(tid):
            continue
        path, seen, node = [], {tid}, tid
        while True:
            nxt = next((p for p in parents.get(node, []) if stuck(p) and p not in seen), None)
            if nxt is None:
                break
            path.append(nxt)
            seen.add(nxt)
            node = nxt
        if path:
            chains[tid] = path
    return chains


def _team_sp(plan: Any) -> dict[str, float]:
    return (field(plan, "capacity", default={}) or {}).get("team_sp") or {}


def is_oversized(task: Any, team_sp: dict[str, float]) -> bool:
    """Остаток SP больше ёмкости команды за спринт — задача не может начаться."""
    team = team_id(task)
    return team in team_sp and _sp_left(task) > float(team_sp[team] or 0)


def _explainable_postponed(task: Any, blocked: dict[str, list[str]]) -> bool:
    """Отложена по ресурсам (а не из-за блокера, решения руководителя или отсутствия сметы)."""
    return (field(task, "decision") == "postponed" and task_id(task) not in blocked
            and not field(task, "deferred") and field(task, "reason_code") != "NOT_ESTIMATED")


def check_resource_deficit(
    plan: Any,
    engineers: list[dict[str, Any]],
    sprint: int | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> list[Alert]:
    """Оранжевые алерты по ресурсам.

    resource — как в условии: потребность роли на следующий спринт больше фонда
               часов всех сотрудников этой роли в компании. Потребность = часы
               роли в этом спринте по раскладке плана (allocation; если её нет —
               остаток / оставшиеся спринты задачи) + часы задач, отложенных из-за
               нехватки часов. Роли без единого человека — это hiring;
    hiring   — в компании нет ни одного инженера роли, и из-за этого задачи
               отложены или идут с частичной целью: один алерт на роль, со сроками;
    capacity — задача отложена, хотя все роли в компании есть: либо её SP
               больше ёмкости команды за спринт (совет — разбить), либо часы
               ролей заняты; тогда «привлечь» конкретных людей из общего пула
               свободных часов (один человек не отдаётся нескольким командам)
               или «нанять», если свободных часов до конца квартала не хватает.
    """
    detected = current_sprint(plan)
    target = sprint if sprint is not None else detected + 1
    have = company_hours(engineers)
    people = _people_by_role(engineers)
    blocked = blocked_by_postponed(plan, dependencies or [])
    team_sp = _team_sp(plan)
    role_name: dict[str, str] = {}
    prodf_of: dict[str, str] = {}
    alerts: list[Alert] = []

    demand: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    demand_team: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unmet: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    missing: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    partial: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    role_of = {str(e.get("engineer_id")): normalize_role(e.get("role", "")) for e in engineers}
    squeezed: list[Any] = []
    oversized: list[Any] = []
    busy_partial: list[tuple[Any, dict[str, tuple[str, float]]]] = []
    for task in sorted(_open_tasks(plan), key=lambda t: -float(field(t, "rung", default=0) or 0)):
        tid, team = task_id(task), team_id(task)
        prodf_of[tid] = prodf_id(task)
        left = remaining_hh(task)
        for key, (name, _) in left.items():
            role_name.setdefault(key, name)
        if is_included(task):
            unstaffed = {normalize_role(r) for r in field(task, "unstaffed_hh", default={}) or {}}
            busy = {}
            for key in left:
                if not people.get(key):
                    # Роль без людей: часы вынесены в дефицит, цель частичная.
                    partial[key][tid] += left[key][1]
                elif key in unstaffed:
                    busy[key] = left[key]
            if busy:
                busy_partial.append((task, busy))
            allocation = field(task, "allocation", default={}) or {}
            if allocation:
                for s_key, hours in allocation.items():
                    if int(s_key) != target or not isinstance(hours, dict):
                        continue
                    for eng_id, h in hours.items():
                        key = role_of.get(str(eng_id))
                        if key:
                            demand[key][tid] += float(h or 0)
                            demand_team[key][team] += float(h or 0)
                continue
            end = end_sprint(task)
            start = max(start_sprint(task) or target, target)
            if end is None or not start <= target <= end:
                continue
            for key, (_, hours) in left.items():
                demand[key][tid] += hours / (end - start + 1)
                demand_team[key][team] += hours / (end - start + 1)
        elif _explainable_postponed(task, blocked):
            absent = [k for k in left if not people.get(k)]
            for key in absent:
                missing[key][tid] += left[key][1]
            if is_oversized(task, team_sp):
                oversized.append(task)
            elif left and not absent:
                squeezed.append(task)
                # Отложена из-за часов — это тоже потребность бэклога на спринт.
                end = QUARTER_SPRINTS
                for key, (_, hours) in left.items():
                    share = hours / max(end - target + 1, 1)
                    unmet[key][tid] += share
                    demand_team[key][team] += share

    if target <= QUARTER_SPRINTS:
        for key in sorted(set(demand) | set(unmet)):
            fund = have.get(key, 0.0)
            if fund <= 0:
                continue          # роли нет в компании — это алерт hiring
            by_task = {**demand.get(key, {})}
            for tid, h in unmet.get(key, {}).items():
                by_task[tid] = by_task.get(tid, 0.0) + h
            need = sum(by_task.values())
            # Роль, у которой в спринте нет работы по плану, — не дефицит роли, а
            # отложенные задачи: их объясняет алерт capacity.
            if need <= fund + 0.05 or not demand.get(key):
                continue
            teams = demand_team[key]
            split = ", ".join(f"{t} {_fmt(round(h, 1))} ч" for t, h in sorted(teams.items(), key=lambda x: -x[1]))
            waiting = sorted(unmet.get(key, {}))
            wait_text = (f" Из них {_fmt(round(sum(unmet[key].values()), 1))} ч — задачи, "
                         f"отложенные из-за нехватки часов: {', '.join(waiting)}." if waiting else "")
            alerts.append(_orange(
                max(teams, key=teams.get), role_name[key], by_task, prodf_of, detected,
                deficit=need - fund,
                description=(
                    f"{sprint_label(target).capitalize()}: потребность роли «{role_name[key]}» — "
                    f"{_fmt(round(need, 1))} ч ({split}), а фонд всех сотрудников этой роли в "
                    f"компании — {_fmt(round(fund, 1))} ч (дефицит {_fmt(round(need - fund, 1))} ч)."
                    + wait_text
                ),
                recommendation=(
                    f"Сдвинуть менее приоритетные задачи роли «{role_name[key]}» на следующие "
                    f"спринты или нанять/обучить ещё одного специалиста."
                ),
            ))

    ready = ready_sprint(detected, HIRE_LEAD_SPRINTS)
    for key in sorted(set(missing) | set(partial)):
        postponed_tasks = missing.get(key, {})
        partial_tasks = partial.get(key, {})
        by_task = {**partial_tasks, **postponed_tasks}
        tasks = [t for t in _open_tasks(plan) if task_id(t) in by_task]
        teams: dict[str, float] = defaultdict(float)
        for task in tasks:
            teams[team_id(task)] += by_task[task_id(task)]
        total = sum(by_task.values())
        by_team = ", ".join(f"{t}: {_fmt(round(h, 1))} ч"
                            for t, h in sorted(teams.items(), key=lambda x: -x[1]))
        parts = []
        if partial_tasks:
            n = len(partial_tasks)
            parts.append(
                f"{n} {_plural(n, 'задача идёт', 'задачи идут', 'задач идут')} с частичной "
                f"целью — часы роли ({_fmt(round(sum(partial_tasks.values()), 1))} ч) вынесены "
                f"в дефицит: {', '.join(sorted(partial_tasks))}")
        if postponed_tasks:
            n = len(postponed_tasks)
            parts.append(
                f"{n} {_plural(n, 'задача отложена', 'задачи отложены', 'задач отложены')} "
                f"({_fmt(round(sum(postponed_tasks.values()), 1))} ч): "
                f"{', '.join(sorted(postponed_tasks))}")
        advice = [f"Нанять или обучить «{role_name[key]}»."]
        defer: tuple[str, ...] = ()
        if partial_tasks:
            if detected >= QUARTER_SPRINTS:
                advice.append("Квартал завершён: часы роли доделать в следующем квартале.")
            else:
                by_id = {task_id(t): t for t in tasks}
                upgrade = sorted(t for t in partial_tasks if (end_sprint(by_id[t]) or 0) >= ready)
                later = sorted(t for t in partial_tasks if t not in upgrade)
                if upgrade:
                    advice.append(f"С наймом (человек начнёт работать: {sprint_label(ready)}) до "
                                  f"полной цели успеют: {', '.join(upgrade)}.")
                if later:
                    advice.append(f"Закончатся раньше выхода нового сотрудника — доделать "
                                  f"часы роли в следующем квартале: {', '.join(later)}.")
        if postponed_tasks:
            timings = [task_timing(t, "hire", detected, is_oversized(t, team_sp))
                       for t in tasks if task_id(t) in postponed_tasks]
            advice.append(timing_text(timings, detected))
            defer = tuple(t["task_id"] for t in timings if not t["fits"])
        alerts.append(_orange(
            max(teams, key=teams.get), role_name[key], by_task, prodf_of, detected,
            category="hiring",
            deficit=total,
            description=(
                f"В компании нет ни одного инженера роли «{role_name[key]}» — нужно "
                f"{_fmt(round(total, 1))} ч ({by_team}). " + "; ".join(parts) + "."
            ),
            recommendation=" ".join(a for a in advice if a).strip(),
            defer=defer,
        ))

    for task in oversized:
        tid, team = task_id(task), team_id(task)
        cap = float(team_sp.get(team) or 0)
        sp_left = _sp_left(task)
        total = sum(h for _, h in remaining_hh(task).values())
        parts = max(2, int(-(-sp_left // cap))) if cap else 0
        alerts.append(_orange(
            team, None, {tid: total}, prodf_of, detected,
            category="capacity",
            deficit=total,
            description=(
                f"{tid} ({_fmt(round(sp_left, 1))} SP) больше ёмкости {team} за один спринт "
                f"({_fmt(round(cap, 1))} SP): планировщик не может её начать ни в одном спринте."
                if cap else
                f"{tid} не может быть запланирована: у {team} нет истории velocity, ёмкость 0 SP."
            ),
            recommendation=(
                f"Разбить {tid} на {parts} части по ≤{_fmt(round(cap, 1))} SP или согласовать "
                f"частичную цель (например, «2. Аналитика и архитектура завершены»)."
                if cap else
                f"Добавить историю спринтов {team} в Team_history или задать ёмкость вручную."
            ),
        ))

    borrow_from = ready_sprint(detected, BORROW_LEAD_SPRINTS)
    pool = FreePool(plan, engineers, borrow_from)
    period = f"до конца квартала (со {borrow_from}-го спринта)"
    for task in squeezed:
        tid, team = task_id(task), team_id(task)
        left = remaining_hh(task)
        advice, short = [], 0.0
        for key, (name, need) in sorted(left.items(), key=lambda kv: -kv[1][1]):
            staff = people.get(key, {})
            own = sum(pool.available(e) for e, teams in staff.items() if team in teams)
            if own + 1e-6 >= need:
                continue
            kind, text = _role_advice(name, key, need - own, team, people, pool, period)
            advice.append((kind, text))
            if kind == "hire":
                short += need - own
        reason = field(task, "reason_detail", default="") or "не хватило ёмкости"
        defer: tuple[str, ...] = ()
        if field(task, "reason_code") == "PLAN_EXCEEDED":
            # Не хватило SP команды, а не часов людей: привлекать людей бесполезно.
            total = sum(h for _, h in left.values())
            alerts.append(_orange(
                team, None, {tid: total}, prodf_of, detected,
                category="capacity",
                deficit=total,
                description=f"{tid} отложена: не хватило ёмкости команды {team} в SP ({reason})",
                recommendation=(
                    f"Освободить ёмкость {team}: сдвинуть менее приоритетную задачу команды "
                    f"или согласовать с заказчиком частичную цель из «Вариантов выбора цели»; "
                    f"иначе — перенос в следующий квартал."
                ),
            ))
            continue
        if advice:
            timing = task_timing(task, "hire" if any(k == "hire" for k, _ in advice) else "borrow",
                                 detected)
            text = f"{_advice_text(advice)} {timing_text([timing], detected)}".strip()
            defer = () if timing["fits"] else (tid,)
        else:
            text = ("По отдельности часов ролей хватает, но они свободны в разных спринтах: "
                    "сдвинуть менее приоритетную задачу, разбить эту на части или "
                    "согласовать частичную цель.")
        total = sum(h for _, h in left.values())
        alerts.append(_orange(
            team, next(iter(left.values()))[0], {tid: total}, prodf_of, detected,
            category="capacity",
            deficit=short or total,
            description=(
                f"{tid} отложена, хотя все нужные роли в компании есть: их часы заняты более "
                f"приоритетными задачами ({reason})."
            ),
            recommendation=text,
            defer=defer,
        ))

    # Задача идёт с частичной целью, потому что часы существующей роли заняты.
    for task, busy in busy_partial:
        tid, team = task_id(task), team_id(task)
        advice = [_role_advice(name, key, need, team, people, pool, period)
                  for key, (name, need) in sorted(busy.items(), key=lambda kv: -kv[1][1])]
        roles = ", ".join(f"«{name}» {_fmt(round(h, 1))} ч" for name, h in busy.values())
        target_label = field(task, "target_label", default="") or "частичная"
        total = sum(h for _, h in busy.values())
        alerts.append(_orange(
            team, next(iter(busy.values()))[0], {tid: total}, prodf_of, detected,
            category="capacity",
            deficit=total,
            description=(
                f"{tid} идёт с частичной целью «{target_label}»: свободных часов ролей "
                f"{roles} до конца квартала не хватает — они заняты задачами с более "
                f"высоким приоритетом."
            ),
            recommendation=(_advice_text(advice) + " Тогда задачу можно довести до полной цели.").strip(),
        ))
    return alerts


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def duration_sprints(task: Any, ramp: tuple[float, ...] = ()) -> int:
    """Сколько спринтов займёт задача, если по каждой роли работает один человек.

    ramp — продуктивность этого человека по спринтам с выхода (адаптация).
    """
    left = remaining_hh(task)
    if not left:
        return 1
    longest = 1
    for _, hours in left.values():
        done, sprints = 0.0, 0
        while done + 1e-9 < hours:
            done += SPRINT_HOURS * ramp_factor(sprints, ramp)
            sprints += 1
        longest = max(longest, sprints)
    return longest


def task_timing(task: Any, kind: str | None, now: int, oversized: bool = False) -> dict[str, Any]:
    """Успеет ли задача в квартал, если людей нужно нанять (kind="hire") или
    переключить из другой команды (kind="borrow"), а решение принято в спринте now.
    Учитывает срок выхода человека и его адаптацию. Задача больше ёмкости
    спринта не успевает без разбиения (split)."""
    lead = {"hire": HIRE_LEAD_SPRINTS, "borrow": BORROW_LEAD_SPRINTS}.get(kind or "", 0)
    ramp = {"hire": ONBOARDING_RAMP, "borrow": BORROW_RAMP}.get(kind or "", ())
    ready = ready_sprint(now, lead)
    duration = duration_sprints(task, ramp)
    latest = QUARTER_SPRINTS - duration + 1
    return {"task_id": task_id(task), "kind": kind, "ready": ready, "latest": latest,
            "duration": duration, "split": oversized, "fits": ready <= latest and not oversized}


def timing_text(timings: list[dict[str, Any]], now: int = 0) -> str:
    """«Успевают при найме: …; без разбиения не начнутся: …; не успеть в квартале: …»."""
    if not timings:
        return ""
    if now >= QUARTER_SPRINTS:
        return (f"Квартал завершён: подготовить к старту следующего ({next_quarter_start():%d.%m.%Y}): "
                + ", ".join(t["task_id"] for t in timings) + ".")
    parts = []
    for kind, verb in (("borrow", "при переключении людей"), ("hire", "при найме")):
        ok = [t for t in timings if t["fits"] and t["kind"] == kind]
        if ok:
            parts.append(f"успевают {verb} (человек начнёт работать: {sprint_label(ok[0]['ready'])}): "
                         + ", ".join(t["task_id"] for t in ok))
    split = [t for t in timings if t.get("split")]
    if split:
        parts.append("не начнутся без разбиения на части (больше ёмкости спринта): "
                     + ", ".join(t["task_id"] for t in split))
    late = [t for t in timings if not t["fits"] and not t.get("split")]
    if late:
        details = ", ".join(f"{t['task_id']} (начать не позже спринта {max(t['latest'], 0)})"
                            for t in late)
        parts.append(f"не успеть в квартале: {details} — перенести в следующий квартал "
                     f"({next_quarter_start():%d.%m.%Y}) и начать подготовку сейчас")
    ramp = "/".join(f"{r:.0%}" for r in ONBOARDING_RAMP)
    return (f"По срокам (найм ≈ {HIRE_LEAD_SPRINTS} спр. + адаптация {ramp} в первые спринты, "
            f"переключение ≈ {BORROW_LEAD_SPRINTS} спр.): " + "; ".join(parts) + ".")


def check_blocked(plan: Any, dependencies: list[dict[str, Any]]) -> list[Alert]:
    """Инфо: задача отложена, потому что не входит в квартал её блокирующая задача.

    Показывает цепочку до корневой причины — чтобы ни одна отложенная задача
    не осталась без объяснения. Цикл зависимостей называется циклом.
    """
    sprint = current_sprint(plan)
    current = {task_id(t): t for t in field(plan, "current", default=[]) or []}
    children, parents = dependency_graph(dependencies, set(current))
    chains = blocked_by_postponed(plan, dependencies)
    alerts: list[Alert] = []
    for tid, task in current.items():
        if is_done(task) or is_included(task):
            continue
        in_cycle = tid in downstream(children, tid) or tid in parents.get(tid, [])
        chain = chains.get(tid)
        if not chain and not in_cycle:
            continue
        if in_cycle:
            description = (f"{tid} не может войти в квартал: она в цикле зависимостей "
                           f"(задачи ждут друг друга). Данные Task_dependencies противоречивы.")
            recommendation = "Разорвать цикл зависимостей в Task_dependencies."
            affected = (tid, *(chain or ()))
        else:
            root = current[chain[-1]]
            why = field(root, "reason_detail", default="") or str(field(root, "decision", default=""))
            description = (f"{tid} не может войти в квартал: ждёт {' → '.join(chain)}. "
                           f"Корневая причина — {chain[-1]}: {why}")
            recommendation = f"Снять блокер у {chain[-1]} — это разблокирует и {tid}."
            affected = (tid, *chain)
        alerts.append(Alert(
            type="info",
            category="blocked",
            task_id=tid,
            prodf_id_at_risk=prodf_id(task),
            description=description,
            sprint_detected=sprint,
            recommendation=recommendation,
            team_id=team_id(task),
            affected_task_ids=affected,
            affected_prodf_ids=tuple(dict.fromkeys(prodf_id(current[t]) for t in affected)),
        ))
    return alerts


def _orange(team, role, by_task, prodf_of, detected, *, deficit, description, recommendation,
            category="resource", defer=()):
    ordered = sorted(by_task, key=lambda t: -by_task[t])
    prodfs = tuple(dict.fromkeys(prodf_of[t] for t in ordered))
    return Alert(
        type="orange",
        category=category,
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
        defer_task_ids=tuple(defer),
    )


# --- info: перерасход сметы ----------------------------------------------------


def check_overrun(plan: Any) -> list[Alert]:
    """Инфо: по роли потрачено больше сметы, или все часы израсходованы, а задача не закрыта."""
    sprint = current_sprint(plan)
    alerts: list[Alert] = []
    for task in _open_tasks(plan):
        if field(task, "decision") == "cancelled":
            continue
        estimated = field(task, "estimated_hh", default={}) or {}
        spent = field(task, "spent_hh", default={}) or {}
        if not isinstance(estimated, dict):
            continue
        planned: dict[str, list] = {}
        for role, hours in estimated.items():
            entry = planned.setdefault(normalize_role(role), [role, 0.0])
            entry[1] += float(hours or 0)
        used: dict[str, float] = defaultdict(float)
        for role, hours in dict(spent).items():
            used[normalize_role(role)] += float(hours or 0)
        over = []
        for key, hours in used.items():
            name, budget = planned.get(key, [key, 0.0])
            if hours > budget + 1e-9:
                over.append(f"«{name}»: {_fmt(round(hours, 1))} ч из {_fmt(round(budget, 1))} ч")
        exhausted = (not over and planned and not remaining_hh(task)
                     and sum(used.values()) > 0)
        if not over and not exhausted:
            continue
        tid, prodf, team = task_id(task), prodf_id(task), team_id(task)
        alerts.append(Alert(
            type="info",
            category="overrun",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=(f"Перерасход сметы по {tid}: {'; '.join(over)}." if over else
                         f"По {tid} израсходованы все часы сметы, но задача не закрыта."),
            sprint_detected=sprint,
            recommendation=(
                f"Переоценить остаток {tid} с командой {team}: если оценка занижена, "
                f"прогноз окончания и инициатива {prodf} под риском."
                if over else
                f"Закрыть {tid}, если работа сделана, или переоценить остаток — иначе задача "
                f"висит в плане без часов."
            ),
            team_id=team,
            affected_task_ids=(tid,),
            affected_prodf_ids=(prodf,),
        ))
    return alerts


# --- info: зависшие задачи -----------------------------------------------------


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        return None


def _spent_total(task: Any) -> float:
    return sum(float(h or 0) for h in dict(field(task, "spent_hh", default={}) or {}).values())


def check_stale(plan: Any, min_sprints: int = 2, stale_weeks: int = 8) -> list[Alert]:
    """Инфо: задача в работе без движения.

    Задача InProgress, начатая больше stale_weeks недель до старта квартала,
    зависшая, пока по ней нет прироста часов в квартале. Любая задача в работе —
    зависшая, если прироста часов нет min_sprints спринтов подряд.
    """
    sprint = current_sprint(plan)
    history = field(plan, "history", default=[]) or []
    lookback = min(min_sprints, sprint, len(history))
    before: dict[str, float] = {}
    if lookback:
        before = {task_id(t): _spent_total(t) for t in history[-lookback]["current"]}

    alerts: list[Alert] = []
    for task in _open_tasks(plan):
        # Отложенные задачи объясняются алертами про ресурсы/блокеры — не дублируем.
        if str(field(task, "status", default="")).casefold() != "inprogress" or not is_included(task):
            continue
        tid = task_id(task)
        no_progress = tid in before and _spent_total(task) <= before[tid]
        if sprint and not no_progress:
            continue
        started = _parse_date(field(task, "actual_start"))
        long_ago = started is not None and (QUARTER_START - started).days >= stale_weeks * 7
        if long_ago:
            weeks = (QUARTER_START - started).days // 7
            what = f"{tid} в работе с {started:%d.%m.%Y} — {weeks} {_plural(weeks, 'неделя', 'недели', 'недель')} к старту квартала"
            if sprint:
                what += f", и за последние {lookback} спр. прироста часов нет"
        elif lookback >= min_sprints:
            what = f"по {tid} нет прироста часов {min_sprints} спринта подряд"
        else:
            continue
        prodf, team = prodf_id(task), team_id(task)
        alerts.append(Alert(
            type="info",
            category="stale",
            task_id=tid,
            prodf_id_at_risk=prodf,
            description=f"Зависшая задача: {what}.",
            sprint_detected=sprint,
            recommendation=(
                f"Разобрать с командой {team}, что блокирует {tid}: закрыть, "
                f"переоценить остаток или снять с квартала."
            ),
            team_id=team,
            affected_task_ids=(tid,),
            affected_prodf_ids=(prodf,),
        ))
    return alerts


# --- info: KPI вне нормы -------------------------------------------------------


def check_kpi(plan: Any) -> list[Alert]:
    """Инфо: прогноз PI Predictability < 80% или Say/Do прошедшего спринта вне 90–105%."""
    from backend.kpi.metrics import PI_NORM, SAY_DO_NORM, kpi_breakdown

    sprint = current_sprint(plan)
    baseline = field(plan, "baseline", default=()) or ()
    current = field(plan, "current", default=[]) or []
    kpi = kpi_breakdown(baseline, current, current_sprint=sprint)
    alerts: list[Alert] = []

    pi = kpi["pi_predictability"]["forecast"]
    if pi["denominator"] and pi["value"] < PI_NORM[0]:
        failing = pi["not_completed_prodf"]
        first = next(iter(failing), "")
        alerts.append(Alert(
            type="info",
            category="kpi",
            task_id=next(iter(failing.get(first, [])), ""),
            prodf_id_at_risk=first,
            description=(
                f"Прогноз PI Predictability {pi['value']:.0f}% — ниже нормы {PI_NORM[0]:.0f}%: "
                f"из {pi['denominator']} обещанных инициатив в срок закроются {pi['numerator']}. "
                f"Не успевают: {', '.join(failing)}."
            ),
            sprint_detected=sprint,
            recommendation=(
                "Сфокусировать ресурсы на инициативах, которым не хватает одной-двух задач, "
                "или согласовать с заказчиками частичные цели."
            ),
            affected_task_ids=tuple(t for ids in failing.values() for t in ids),
            affected_prodf_ids=tuple(failing),
        ))

    if sprint:
        row = next((r for r in kpi["say_do"] if r["sprint"] == sprint), None)
        if row and row["planned_sp"] and not row["in_norm"]:
            low = row["value"] < SAY_DO_NORM[0]
            by_id = {task_id(t): t for t in current}
            manual = [t for t in row["planned_tasks"] if t not in row["done_tasks"] and t in by_id
                      and (field(by_id[t], "deferred") or field(by_id[t], "decision") == "cancelled")]
            early = [t for t in row["planned_tasks"] if t not in row["done_tasks"] and t in by_id
                     and is_done(by_id[t])
                     and (field(by_id[t], "actual_end_sprint") or sprint) < sprint]
            if low and manual:
                verdict = (f"ниже нормы: часть плана снята решением руководителя или заказчика "
                           f"({', '.join(manual)}).")
            elif low and early:
                verdict = (f"ниже нормы, но не из-за срыва: часть плана спринта закрыта раньше — "
                           + ", ".join(f"{t} в спринте {field(by_id[t], 'actual_end_sprint')}"
                                       for t in early) + ".")
            elif low:
                verdict = "ниже нормы: команды переобещали."
            else:
                verdict = "выше нормы: закрыто то, что планировали на другие спринты."
            missed = [t for t in row["planned_tasks"] if t not in row["done_tasks"]]
            focus = (missed or row["planned_tasks"] or row["done_tasks"] or [""])[0]
            alerts.append(Alert(
                type="info",
                category="kpi",
                task_id=focus,
                prodf_id_at_risk=prodf_id(by_id[focus]) if focus in by_id else "",
                description=(
                    f"Say/Do спринта {sprint}: {_pct(row['value'])}% (сделано {row['done_sp']:g} SP "
                    f"из запланированных {row['planned_sp']:g}) — {verdict}"
                ),
                sprint_detected=sprint,
                recommendation=(
                    "Проверить оценки и ёмкость команд перед следующим спринтом."
                    if low else "Пересобрать план: он не отражает реальный темп команд."
                ),
                affected_task_ids=tuple(dict.fromkeys(row["planned_tasks"] + row["done_tasks"])),
            ))
    return alerts


# --- info: единственный носитель роли -----------------------------------------


def _backup_text(role: str, engineers: list[dict[str, Any]]) -> str:
    """Кого обучить в дублёры (звёздная карта); пусто, если модуль недоступен."""
    try:
        from backend.starmap.recommendations import train_candidates

        best = train_candidates(role, engineers, limit=1)
    except Exception:
        return ""
    if not best:
        return ""
    c = best[0]
    return f" Кандидат в дублёры — {c['engineer_id']} ({c['role']}, {', '.join(c['teams'])}): {c['why']}."


def check_single_point(plan: Any, engineers: list[dict[str, Any]]) -> list[Alert]:
    """Инфо: задачи плана держатся на единственном в компании инженере роли.

    Один алерт на инженера: если он заболеет или уйдёт, все эти задачи встанут
    (последствия показывает сценарий engineer_leave).
    """
    sprint = current_sprint(plan)
    people: dict[str, set[str]] = defaultdict(set)
    role_of: dict[str, tuple[str, str]] = {}
    for eng in engineers:
        key = normalize_role(eng.get("role", ""))
        people[key].add(str(eng.get("engineer_id")))
        role_of.setdefault(str(eng.get("engineer_id")), (key, eng.get("role", "")))

    depends: dict[str, list[Any]] = defaultdict(list)
    display: dict[str, str] = {}
    for task in _open_tasks(plan):
        if not is_included(task):
            continue
        for key, (name, _) in remaining_hh(task).items():
            if len(people.get(key, ())) == 1:
                depends[next(iter(people[key]))].append(task)
                display.setdefault(key, name)

    alerts: list[Alert] = []
    for eng_id, tasks in depends.items():
        key = role_of[eng_id][0]
        role = display.get(key, role_of[eng_id][1])
        ids = tuple(dict.fromkeys(task_id(t) for t in tasks))
        prodfs = tuple(dict.fromkeys(prodf_id(t) for t in tasks))
        alerts.append(Alert(
            type="info",
            category="single_point",
            task_id=ids[0],
            prodf_id_at_risk=prodfs[0],
            description=(
                f"{eng_id} — единственный в компании «{role}». От него зависят задачи плана: "
                f"{', '.join(ids)} ({len(prodfs)} "
                f"{_plural(len(prodfs), 'инициатива', 'инициативы', 'инициатив')}). Bus Factor роли = 1."
            ),
            sprint_detected=sprint,
            recommendation=(
                f"Обучить второго специалиста роли «{role}» или нанять; зафиксировать знания "
                f"{eng_id}." + _backup_text(role, engineers)
                + " Последствия ухода — сценарий «Уход инженера»."
            ),
            role=role,
            affected_task_ids=ids,
            affected_prodf_ids=prodfs,
        ))
    return alerts


# --- сборка, жизненный цикл, сводка по инициативам ----------------------------


def collect_alerts(
    plan: Any,
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    reference: Iterable[Any] | None = None,
) -> list[Alert]:
    """Все алерты по плану: red → yellow → orange → info, затем по инициативе."""
    alerts = [
        *check_critical(plan, dependencies),
        *check_cascade(plan, dependencies, reference),
        *check_resource_deficit(plan, engineers, dependencies=dependencies),
        *check_blocked(plan, dependencies),
        *check_overrun(plan),
        *check_stale(plan),
        *check_kpi(plan),
        *check_single_point(plan, engineers),
    ]
    return sorted(alerts, key=lambda a: (_SEVERITY[a.type], a.prodf_id_at_risk, a.task_id))


def _attr(alert: Alert | dict[str, Any], name: str) -> Any:
    return alert.get(name) if isinstance(alert, dict) else getattr(alert, name)


def alert_key(alert: Alert | dict[str, Any]) -> tuple:
    """Идентичность алерта между спринтами (тексты и цифры могут меняться)."""
    category = _attr(alert, "category")
    if category in ("hiring", "resource", "single_point"):
        return category, normalize_role(_attr(alert, "role") or "")
    if category == "data_quality":
        return category, _attr(alert, "description")
    return category, _attr(alert, "task_id")


def annotate_lifecycle(
    alerts: list[Alert],
    previous: Iterable[Alert | dict[str, Any]] | None,
    sprint: int,
) -> tuple[list[Alert], list[dict[str, Any]]]:
    """Помечает алерты new/ongoing и возвращает решённые с прошлого спринта.

    Returns:
        (алерты со status и since_sprint, список решённых алертов как dict
         со status="resolved" и resolved_sprint).
    """
    before = {alert_key(a): a for a in previous or ()}
    marked = []
    for alert in alerts:
        old = before.get(alert_key(alert))
        if old is None:
            marked.append(replace(alert, status="new", since_sprint=alert.sprint_detected))
        else:
            since = _attr(old, "since_sprint")
            since = _attr(old, "sprint_detected") if since is None else since
            marked.append(replace(alert, status="ongoing", since_sprint=since))
    current = {alert_key(a) for a in alerts}
    resolved = []
    for key, old in before.items():
        if key not in current:
            data = old.to_dict() if isinstance(old, Alert) else dict(old)
            resolved.append({**data, "status": "resolved", "resolved_sprint": sprint})
    return marked, resolved


_VERDICT = {
    "red": "не будет выполнена в квартале без вмешательства",
    "yellow": "сроки сдвигаются по цепочке зависимостей",
    "orange": "не хватает людей нужных ролей",
    "info": "есть предупреждения",
}


def summarize_by_prodf(alerts: Iterable[Alert]) -> list[dict[str, Any]]:
    """Сводка для начальника: по каждой инициативе — худший статус и причины."""
    groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in alerts:
        for prodf in alert.affected_prodf_ids or (alert.prodf_id_at_risk,):
            if prodf:
                groups[prodf].append(alert)
    rows = []
    for prodf, items in groups.items():
        worst = min(items, key=lambda a: _SEVERITY[a.type]).type
        counts = Counter(a.type for a in items)
        rows.append({
            "prodf_id": prodf,
            "worst": worst,
            "counts": {t: counts.get(t, 0) for t in _SEVERITY},
            "headline": f"{prodf}: {_VERDICT[worst]}",
            "causes": sorted({a.task_id for a in items if a.type == worst}),
            "task_ids": sorted({t for a in items for t in a.affected_task_ids}),
            "recommendations": list(dict.fromkeys(
                a.recommendation for a in sorted(items, key=lambda a: _SEVERITY[a.type])
                if a.recommendation
            ))[:3],
        })
    return sorted(rows, key=lambda r: (_SEVERITY[r["worst"]], -sum(r["counts"].values()), r["prodf_id"]))
