# Ответственный: Человек 2 (recalculation + risk)
"""Обнаружение рисков по текущему плану и построение алертов.

Три типа из условия (contracts/alert_schema.md):
    red    — прогноз окончания задачи за пределами 6-го спринта (12 недель);
    yellow — задача задерживается и сдвигает цепочку зависимых задач;
    orange — потребность роли на следующий спринт больше фонда часов команды.
    orange/hiring — в команде нет ни одного инженера нужной роли (нанять/обучить);
    orange/capacity — роли есть, но их часы заняты более приоритетными задачами.
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

import math
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
    HIRE_LEAD_SPRINTS,
    QUARTER_START,
    next_quarter_start,
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
        chain = [c for c in downstream(children, tid)
                 if not is_done(current[c]) and is_included(current[c])]
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


def role_supply(engineers: Iterable[dict[str, Any]]) -> dict[tuple[str, str], float]:
    # Строка профиля = доля инженера в конкретной команде (парттайм 0.5 → 40 ч).
    supply: dict[tuple[str, str], float] = defaultdict(float)
    for eng in engineers:
        key = (str(eng.get("team_id")), normalize_role(eng.get("role", "")))
        supply[key] += SPRINT_HOURS * float(eng.get("capacity_rate") or 0)
    return supply


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


def _borrowable(people, free_by_sprint, team, role_key) -> tuple[float, list[tuple[str, str, float]]]:
    """Сколько часов роли свободно у инженеров других команд за указанные спринты."""
    result = []
    for eng_id, teams in people.get(role_key, {}).items():
        if teams == {team}:
            continue
        hours = sum(free.get(eng_id, 0.0) for free in free_by_sprint.values())
        if hours > 0:
            result.append((eng_id, ", ".join(sorted(teams - {team})), hours))
    result.sort(key=lambda r: -r[2])
    return sum(r[2] for r in result), result


def _staffing_advice(name, need, people_count, available, helpers, period) -> tuple[str, str]:
    """Классифицирует роль: нанять (нет в компании / есть, но заняты) или привлечь."""
    who = "; ".join(f"{e} из {src} (свободно {_fmt(round(h, 1))} ч)" for e, src, h in helpers[:2])
    if people_count == 0:
        return "hire", f"«{name}» — в компании нет ни одного"
    if available + 1e-6 < need:
        text = (f"«{name}» — в компании {people_count} чел., но {period} свободно только "
                f"{_fmt(round(available, 1))} ч из нужных {_fmt(round(need, 1))} ч")
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


def check_resource_deficit(
    plan: Any,
    engineers: list[dict[str, Any]],
    sprint: int | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> list[Alert]:
    """Оранжевые алерты по ресурсам.

    resource — потребность роли на следующий спринт больше фонда часов команды
               (потребность = остаток часов / оставшиеся спринты задачи);
    hiring   — задачи отложены, потому что в команде нет ни одного инженера роли;
    capacity — задачи отложены, хотя все роли в команде есть: часы съели более
               приоритетные задачи.
    «Нанять» рекомендуется, если роли нет в компании ИЛИ свободных часов этой
    роли у других команд до конца квартала меньше, чем нужно; иначе —
    «привлечь» с конкретными инженерами и их свободными часами.
    """
    detected = current_sprint(plan)
    target = sprint if sprint is not None else detected + 1
    supply = role_supply(engineers)
    people = _people_by_role(engineers)
    rest = {s: free_hours(plan, engineers, s) for s in range(max(target, 1), QUARTER_SPRINTS + 1)}
    period = f"до конца квартала (с {sprint_label(max(target, 1))})"
    blocked = blocked_by_postponed(plan, dependencies or [])
    team_sp = (field(plan, "capacity", default={}) or {}).get("team_sp") or {}
    by_id = {task_id(t): t for t in _open_tasks(plan)}

    demand: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    missing: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    squeezed: list[Any] = []
    oversized: list[tuple[Any, float, float]] = []
    role_name: dict[str, str] = {}
    prodf_of: dict[str, str] = {}

    for task in _open_tasks(plan):
        tid, team = task_id(task), team_id(task)
        prodf_of[tid] = prodf_id(task)
        left = remaining_hh(task)
        for key, (name, _) in left.items():
            role_name.setdefault(key, name)
        if is_included(task):
            end = end_sprint(task)
            start = max(start_sprint(task) or target, target)
            if end is None or not start <= target <= end:
                continue
            for key, (_, hours) in left.items():
                demand[(team, key)][tid] += hours / (end - start + 1)
        elif (field(task, "decision") == "postponed" and tid not in blocked
              and not field(task, "deferred") and field(task, "reason_code") != "NOT_ESTIMATED"):
            absent = [k for k in left if supply.get((team, k), 0) == 0]
            for key in absent:
                missing[(team, key)][tid] += left[key][1]
            sp_left = _sp_left(task)
            if team in team_sp and sp_left > float(team_sp[team] or 0):
                oversized.append((task, sp_left, float(team_sp[team] or 0)))
            elif left and not absent:
                squeezed.append(task)

    alerts: list[Alert] = []
    if target <= QUARTER_SPRINTS:
        now = {target: rest.get(target, {})}
        for (team, key), by_task in demand.items():
            need, have = sum(by_task.values()), supply.get((team, key), 0.0)
            if need <= have:
                continue
            available, helpers = _borrowable(people, now, team, key)
            _, advice = _staffing_advice(role_name[key], need - have, len(people.get(key, {})),
                                         available, helpers, f"в {sprint_label(target)}")
            alerts.append(_orange(
                team, role_name[key], by_task, prodf_of, detected,
                deficit=need - have,
                description=(
                    f"{sprint_label(target).capitalize()}: роли «{role_name[key]}» в {team} нужно "
                    f"{_fmt(round(need, 1))} ч, доступно {_fmt(have)} ч "
                    f"(дефицит {_fmt(round(need - have, 1))} ч)."
                ),
                recommendation=(
                    f"Сдвинуть менее приоритетные задачи роли на следующие спринты. {advice}."
                ),
            ))

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
        advice = []
        for k in ordered:
            available, helpers = _borrowable(people, rest, team, k)
            advice.append(_staffing_advice(role_name[k], hours[k], len(people.get(k, {})),
                                           available, helpers, period))
        top = next((k for k, (kind, _) in zip(ordered, advice) if kind == "hire"), ordered[0])
        listing = ", ".join(f"«{role_name[k]}» ({_fmt(round(hours[k], 1))} ч)" for k in ordered)
        role_kind = {k: kind for k, (kind, _) in zip(ordered, advice)}
        timings = [
            task_timing(by_id[tid], "hire" if any(role_kind[k] == "hire" for k in roles
                                                   if tid in roles[k]) else "borrow", detected)
            for tid in sorted(tasks_hh)
        ]
        alerts.append(_orange(
            team, role_name[top], tasks_hh, prodf_of, detected,
            category="hiring",
            deficit=sum(hours.values()),
            description=(
                f"В {team} нет ни одного инженера ролей: {listing}. Из-за этого перенесены "
                f"задачи: {', '.join(sorted(tasks_hh))}."
            ),
            recommendation=_advice_text(advice) + " " + timing_text(timings),
            defer=tuple(t["task_id"] for t in timings if not t["fits"]),
        ))

    for task, sp_left, cap in oversized:
        tid, team = task_id(task), team_id(task)
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
                f"Разбить {tid} на {parts} части по ≤{_fmt(round(cap, 1))} SP или "
                f"согласовать частичную цель (например, «2. Аналитика и архитектура завершены»)."
                if cap else
                f"Добавить историю спринтов {team} в Team_history или задать ёмкость вручную."
            ),
        ))

    for task in squeezed:
        tid, team = task_id(task), team_id(task)
        left = remaining_hh(task)
        advice, short = [], 0.0
        for key, (name, need) in sorted(left.items(), key=lambda kv: -kv[1][1]):
            staff = people.get(key, {})
            available = sum(sum(f.get(e, 0.0) for f in rest.values()) for e in staff)
            if available + 1e-6 < need:
                _, helpers = _borrowable(people, rest, team, key)
                advice.append(_staffing_advice(name, need, len(staff), available, helpers, period))
                short += need - available
        reason = field(task, "reason_detail", default="не хватило ёмкости")
        defer: tuple[str, ...] = ()
        if advice:
            timing = task_timing(task, "hire" if any(k == "hire" for k, _ in advice) else "borrow",
                                 detected)
            text = _advice_text(advice) + " " + timing_text([timing])
            defer = () if timing["fits"] else (tid,)
        else:
            text = ("По отдельности часов ролей хватает, но они свободны в разных спринтах или "
                    "не хватило SP команды: разбить задачу на части, сдвинуть менее приоритетную "
                    "или согласовать частичную цель.")
        total = sum(h for _, h in left.values())
        alerts.append(_orange(
            team, next(iter(left.values()))[0], {tid: total}, prodf_of, detected,
            category="capacity",
            deficit=short or total,
            description=(
                f"{tid} отложена, хотя все нужные роли в {team} есть: часы заняты более "
                f"приоритетными задачами ({reason})."
            ),
            recommendation=text,
            defer=defer,
        ))
    return alerts


def duration_sprints(task: Any) -> int:
    """Сколько спринтов займёт задача, если по каждой роли работает один человек."""
    left = remaining_hh(task)
    if not left:
        return 1
    return max(1, max(math.ceil(hours / SPRINT_HOURS) for _, hours in left.values()))


def task_timing(task: Any, kind: str | None, now: int) -> dict[str, Any]:
    """Успеет ли задача в квартал, если людей нужно нанять (kind="hire") или
    переключить из другой команды (kind="borrow"), а решение принято в спринте now."""
    lead = {"hire": HIRE_LEAD_SPRINTS, "borrow": BORROW_LEAD_SPRINTS}.get(kind or "", 0)
    ready = ready_sprint(now, lead)
    latest = QUARTER_SPRINTS - duration_sprints(task) + 1
    return {"task_id": task_id(task), "kind": kind, "ready": ready, "latest": latest,
            "fits": ready <= latest}


def timing_text(timings: list[dict[str, Any]]) -> str:
    """«Успевают при найме: …; не успеть в квартале: … — перенести в следующий квартал»."""
    if not timings:
        return ""
    parts = []
    for kind, verb in (("borrow", "при переключении людей"), ("hire", "при найме")):
        ok = [t for t in timings if t["fits"] and t["kind"] == kind]
        if ok:
            parts.append(f"успевают {verb} (человек начнёт работать: {sprint_label(ok[0]['ready'])}): "
                         + ", ".join(t["task_id"] for t in ok))
    late = [t for t in timings if not t["fits"]]
    if late:
        details = ", ".join(f"{t['task_id']} (начать не позже спринта {max(t['latest'], 0)})"
                            for t in late)
        parts.append(f"не успеть в квартале: {details} — перенести в следующий квартал "
                     f"({next_quarter_start():%d.%m.%Y}) и начать подготовку сейчас")
    return (f"По срокам (найм ≈ {HIRE_LEAD_SPRINTS} спр., переключение ≈ {BORROW_LEAD_SPRINTS} спр.): "
            + "; ".join(parts) + ".")


def check_blocked(plan: Any, dependencies: list[dict[str, Any]]) -> list[Alert]:
    """Инфо: задача отложена, потому что не входит в квартал её блокирующая задача.

    Показывает цепочку до корневой причины — чтобы ни одна отложенная задача
    не осталась без объяснения.
    """
    sprint = current_sprint(plan)
    current = {task_id(t): t for t in field(plan, "current", default=[]) or []}
    alerts: list[Alert] = []
    for tid, chain in blocked_by_postponed(plan, dependencies).items():
        task, root = current[tid], current[chain[-1]]
        why = field(root, "reason_detail", default="") or str(field(root, "decision", default=""))
        prodfs = tuple(dict.fromkeys(prodf_id(current[t]) for t in (tid, *chain)))
        alerts.append(Alert(
            type="info",
            category="blocked",
            task_id=tid,
            prodf_id_at_risk=prodf_id(task),
            description=(
                f"{tid} не может войти в квартал: ждёт {' → '.join(chain)}. "
                f"Корневая причина — {chain[-1]}: {why}"
            ),
            sprint_detected=sprint,
            recommendation=f"Снять блокер у {chain[-1]} — это разблокирует и {tid}.",
            team_id=team_id(task),
            affected_task_ids=(tid, *chain),
            affected_prodf_ids=prodfs,
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
        if str(field(task, "status", default="")).casefold() != "inprogress":
            continue
        tid = task_id(task)
        no_progress = tid in before and _spent_total(task) <= before[tid]
        if sprint and not no_progress:
            continue
        started = _parse_date(field(task, "actual_start"))
        long_ago = started is not None and (QUARTER_START - started).days >= stale_weeks * 7
        if long_ago:
            weeks = (QUARTER_START - started).days // 7
            what = f"{tid} в работе с {started:%d.%m.%Y} — {weeks} недель к старту квартала"
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
    kpi = kpi_breakdown(baseline, current)
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
            alerts.append(Alert(
                type="info",
                category="kpi",
                task_id=(row["planned_tasks"] or row["done_tasks"] or [""])[0],
                prodf_id_at_risk="",
                description=(
                    f"Say/Do спринта {sprint}: {row['value']:.0f}% (сделано {row['done_sp']:g} SP "
                    f"из запланированных {row['planned_sp']:g}) — "
                    + ("ниже нормы: команды переобещали." if low
                       else "выше нормы: закрыто то, что планировали на другие спринты.")
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
    for task in _open_tasks(plan):
        if not is_included(task):
            continue
        for key in remaining_hh(task):
            if len(people.get(key, ())) == 1:
                depends[next(iter(people[key]))].append(task)

    alerts: list[Alert] = []
    for eng_id, tasks in depends.items():
        role = role_of[eng_id][1]
        ids = tuple(dict.fromkeys(task_id(t) for t in tasks))
        prodfs = tuple(dict.fromkeys(prodf_id(t) for t in tasks))
        alerts.append(Alert(
            type="info",
            category="single_point",
            task_id=ids[0],
            prodf_id_at_risk=prodfs[0],
            description=(
                f"{eng_id} — единственный в компании «{role}». От него зависят задачи плана: "
                f"{', '.join(ids)} ({len(prodfs)} инициатив(ы)). Bus Factor роли = 1."
            ),
            sprint_detected=sprint,
            recommendation=(
                f"Обучить второго специалиста роли «{role}» или нанять; зафиксировать знания "
                f"{eng_id}. Последствия ухода — сценарий «Уход инженера»."
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
        *check_critical(plan),
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
    if category == "hiring":
        return category, _attr(alert, "team_id")
    if category == "resource":
        return category, _attr(alert, "team_id"), _attr(alert, "role")
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
