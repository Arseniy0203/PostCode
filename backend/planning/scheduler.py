"""Планировщик квартала: раскладывает бэклог по 6 спринтам.

Жёсткие ограничения (условие задачи):
    * SP команды в спринте ≤ ёмкости (средний velocity × 0.8). SP задачи
      списываются по спринтам пропорционально выполненным в них часам — так
      задача крупнее спринтовой ёмкости растягивается, а не выпадает из плана;
    * часы инженера в спринте ≤ 80 × capacity_rate по каждой его команде
      (парттаймер 0.5 + 0.5 даёт по 40 ч каждой команде и не больше 80 ч всего);
    * зависимая задача начинается не раньше спринта, следующего за окончанием
      блокирующей;
    * стек: если технология из названия задачи есть у кого-то из инженеров
      роли, на эту роль берутся только её носители (пример PDF: Big Data + Python).

Порядок: топологический (блокирующие раньше зависимых), среди доступных —
сначала задачи в работе, затем по скорингу planning/scoring.py::score_task
(rung × бонус за блокируемые задачи).

Люди: сначала инженеры своей команды, затем — если свои заняты или их нет —
инженеры той же роли из других команд. Кого привлекли, пишется в причине.

Роли, которых нет ни у одного инженера компании (Руководитель проекта, 1С,
поддержка). Ответ экспертов: «нет ресурсов — задачу взять не можем + рекомендация,
в т. ч. частичная цель из „Вариантов выбора цели“». Поэтому по умолчанию
(missing_roles="partial") работа остальных ролей планируется, часы недостающих
выносятся в дефицит (unstaffed_hh), а цель понижается по справочнику
(explainability/reasons.py::partial_target) с рекомендацией «нанять». Если на
недостающие роли приходится больше MAX_MISSING_SHARE часов задачи, частичная
цель бессмысленна — задача переносится. missing_roles="postpone" — строгий
режим: любая недостающая роль переносит задачу (для сравнения в UI).

Причины решений — шаблоны explainability/reasons.py: код из справочника,
текст с цифрами, facts, цель и рекомендация.
"""

from __future__ import annotations

import copy
import heapq
import re
from typing import Any

from backend.explainability import reasons as R
from backend.planning.scoring import score_task

QUARTER_SPRINTS = 6
SPRINT_HOURS = 80.0
MISSING_ROLE_POLICY = "partial"   # "partial" | "postpone"
MAX_MISSING_SHARE = 0.5
_EPS = 1e-6
SP_TOLERANCE = 1e-6   # только погрешность float: ёмкость команды по SP — жёсткий лимит


def normalize_role(role: str) -> str:
    """Нормализует название роли: нижний регистр, без пробелов. Игнорирует 'Итого'."""
    if not role:
        return ""
    r = str(role).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if "итого" in r:
        return ""
    return r


def _num(value: Any) -> float:
    """Число из ячейки: '0,5' и '0.5' — одно и то же, мусор — 0."""
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return 0.0


def _fmt(value: float) -> str:
    return f"{round(value, 1):g}"


# --- Ресурсы ---------------------------------------------------------------------


class _Pool:
    """Свободные часы по «слотам» (инженер, команда) и SP команд по спринтам.

    Все списания пишутся в журнал, чтобы откатить их, если задача не
    поместилась (иначе часы неудавшейся задачи терялись бы для остальных).
    """

    def __init__(self, capacity: dict[str, Any], engineers: list[dict[str, Any]], first: int):
        self.first = first
        eng_hh = capacity.get("engineer_hh", {}) or {}
        rows: dict[str, list[dict[str, Any]]] = {}
        for eng in engineers:
            if eng.get("engineer_id"):
                rows.setdefault(eng["engineer_id"], []).append(eng)

        self.slots: list[tuple[str, str, str]] = []           # (eng, team, role)
        self.skills: dict[str, set[str]] = {}
        per_slot: dict[tuple[str, str], float] = {}
        for eng_id, eng_rows in rows.items():
            rates = [_num(r.get("capacity_rate", 1.0)) for r in eng_rows]
            total_rate = sum(rates)
            total_hh = _num(eng_hh[eng_id]) if eng_id in eng_hh else SPRINT_HOURS * total_rate
            for row, rate in zip(eng_rows, rates):
                slot = (eng_id, str(row.get("team_id") or ""), normalize_role(row.get("role")))
                self.slots.append(slot)
                share = rate / total_rate if total_rate > 0 else 0.0
                per_slot[slot[:2]] = per_slot.get(slot[:2], 0.0) + total_hh * share
                skills = row.get("skills_declared") or []
                if isinstance(skills, str):
                    skills = [s for s in re.split(r",\s*(?![^()]*\))", skills) if s.strip()]
                self.skills.setdefault(eng_id, set()).update(s.strip() for s in skills)

        self.roles = {role for _, _, role in self.slots if role}
        self.hh = {s: {slot[:2]: per_slot.get(slot[:2], 0.0) for slot in self.slots}
                   for s in range(first, QUARTER_SPRINTS + 1)}
        self.team_sp = {s: {t: _num(v) for t, v in (capacity.get("team_sp") or {}).items()}
                        for s in range(first, QUARTER_SPRINTS + 1)}
        self.journal: list[tuple[str, int, Any, float]] = []

    # списания ---------------------------------------------------------------
    def take_hh(self, sprint: int, key: tuple[str, str], hours: float) -> None:
        self.hh[sprint][key] -= hours
        self.journal.append(("hh", sprint, key, hours))

    def take_sp(self, sprint: int, team: str, sp: float) -> None:
        if team in self.team_sp.get(sprint, {}):
            self.team_sp[sprint][team] -= sp
            self.journal.append(("sp", sprint, team, sp))

    def mark(self) -> int:
        return len(self.journal)

    def rollback(self, mark: int) -> None:
        while len(self.journal) > mark:
            kind, sprint, key, value = self.journal.pop()
            if kind == "hh":
                self.hh[sprint][key] += value
            else:
                self.team_sp[sprint][key] += value

    # кандидаты --------------------------------------------------------------
    def candidates(self, role: str, team: str, techs: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
        """Слоты роли: сначала своя команда, затем другие. С учётом стека."""
        slots = [(e, t) for e, t, r in self.slots if r == role]
        used: list[str] = []
        if techs:
            with_tech = [(e, t) for e, t in slots if self._has(e, techs)]
            if with_tech:
                used = sorted({tech for e, _ in with_tech for tech in techs if self._has(e, {tech})})
                slots = with_tech
        own = sorted(s for s in slots if s[1] == team)
        # Парттаймер уже отдаёт своей команде её долю (80 × rate) — вторую долю
        # он отдаёт другой своей команде, её нельзя «одолжить» (условие: по 40 ч).
        mine = {e for e, _ in own}
        other = sorted(s for s in slots if s[1] != team and s[0] not in mine)
        return own + other, used

    def _has(self, eng: str, techs: set[str]) -> bool:
        skills = {s.casefold() for s in self.skills.get(eng, set())}
        return any(any(t.casefold() in s for s in skills) for t in techs)

    def free(self, sprint: int, slots: list[tuple[str, str]]) -> float:
        return sum(max(self.hh[sprint].get(s, 0.0), 0.0) for s in slots)


def _task_techs(summary: str, pool: _Pool) -> set[str]:
    """Технологии, прямо названные в summary задачи (из заявленных навыков)."""
    found = set()
    text = str(summary or "")
    for skill in {s for skills in pool.skills.values() for s in skills}:
        name = re.sub(r"\s*\(.*?\)\s*", "", skill).strip()
        if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            found.add(name)
    return found


# --- Порядок ---------------------------------------------------------------------


def _in_progress(task: dict[str, Any]) -> bool:
    return (str(task.get("status", "")).strip().casefold() == "inprogress"
            or bool(task.get("spent_hh")))


def _order(tasks: list[dict[str, Any]], dependencies: list[dict[str, Any]]):
    """Топологический порядок с приоритетом.

    Сначала задачи с местом в прошлом плане (plan_rank — при пересчёте очередь
    обязательств не перетасовывается), затем задачи в работе, затем по
    скорингу, rung и порядку во входных данных.
    """
    ids = {t.get("task_id") for t in tasks}
    index = {t.get("task_id"): i for i, t in enumerate(tasks)}
    by_id = {t.get("task_id"): t for t in tasks}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for dep in dependencies:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        if a in ids and b in ids and a != b and b not in adj[a]:
            adj[a].append(b)
            indeg[b] += 1

    scores = {i: score_task({**by_id[i], "rung": _num(by_id[i].get("rung"))}, {}, dependencies)
              for i in ids}

    def key(tid: str):
        task = by_id[tid]
        rank = task.get("plan_rank")
        return (0 if rank else 1, _num(rank), -int(_in_progress(task)), -scores[tid],
                -_num(task.get("rung")), index[tid])

    heap = [(key(i), i) for i in ids if indeg[i] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, tid = heapq.heappop(heap)
        order.append(tid)
        for nxt in adj[tid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (key(nxt), nxt))
    leftovers = sorted((i for i in ids if i not in set(order)), key=lambda i: index[i])
    return order, leftovers, scores


def _cycle_of(tid: str, blocked_by: dict[str, list[str]], pool: set[str]) -> list[str]:
    """Путь по блокерам среди задач пула до первого повтора: A → B → A."""
    path, seen = [tid], {tid: 0}
    current = tid
    while True:
        nxt = next((b for b in blocked_by.get(current, []) if b in pool), None)
        if nxt is None:
            return []
        if nxt in seen:
            loop = path[seen[nxt]:] + [nxt]
            return loop
        seen[nxt] = len(path)
        path.append(nxt)
        current = nxt


# --- Основная функция -------------------------------------------------------------


def build_baseline_plan(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    estimates: list[dict[str, Any]] = None,
    start_sprint: int = 1,
    locked_tasks: list[str] = None,
    missing_roles: str | None = None,
) -> dict[str, Any]:
    """Строит план квартала со спринта start_sprint.

    Args:
        tasks: задачи; estimated_hh — {роль: часы} (остаток для задач в работе).
        capacity: {"team_sp": {команда: SP}, "engineer_hh": {инженер: часы}}.
        dependencies: [{"blocking_task_id", "blocked_task_id"}].
        engineers: строки Engineers_profiles (парттаймер — две строки).
        estimates: строки калькулятора (task_id, role, hours); если заданы —
            заменяют estimated_hh (калькулятор — целевая смета).
        start_sprint: первый спринт, куда можно ставить работу (при пересчёте — следующий).
        locked_tasks: зафиксированные задачи (Done): их часы/SP уже списаны.
        missing_roles: "partial" (по умолчанию) или "postpone" — см. модуль.
    """
    policy = missing_roles or MISSING_ROLE_POLICY
    locked_set = set(locked_tasks or [])
    pool = _Pool(capacity, engineers, start_sprint)

    blocked_by: dict[str, list[str]] = {}
    for dep in dependencies:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        if a and b and a not in blocked_by.get(b, []):
            blocked_by.setdefault(b, []).append(a)

    by_estimate: dict[str, dict[str, float]] = {}
    if estimates is not None:
        for row in estimates:
            hours = _num(row.get("hours"))
            if hours > 0 and normalize_role(row.get("role")):
                roles = by_estimate.setdefault(str(row.get("task_id")), {})
                roles[row["role"]] = roles.get(row["role"], 0.0) + hours

    end_of: dict[str, int] = {}
    results: dict[str, dict[str, Any]] = {}
    team_of = {t.get("task_id"): t.get("team_id") for t in tasks}

    # 1. Зафиксированные задачи: списываем их часы и SP в будущих спринтах.
    to_plan = []
    for task in tasks:
        tid = task.get("task_id", "")
        if tid not in locked_set:
            to_plan.append(task)
            continue
        item = copy.deepcopy(task)
        for s_key, hours in (item.get("allocation") or {}).items():
            s = int(s_key)
            if s in pool.hh:
                for eng_id, h in hours.items():
                    keys = sorted((k for k in pool.hh[s] if k[0] == eng_id),
                                  key=lambda k: k[1] != item.get("team_id"))
                    if keys:
                        pool.take_hh(s, keys[0], _num(h))
        for s_key, sp in (item.get("sp_by_sprint") or {}).items():
            if int(s_key) in pool.team_sp:
                pool.take_sp(int(s_key), item.get("team_id"), _num(sp))
        end_of[tid] = int(item.get("actual_end_sprint") or item.get("forecast_end_sprint")
                          or item.get("planned_end_sprint") or 0)
        results[tid] = item
    pool.journal.clear()

    order, leftovers, scores = _order(to_plan, dependencies)
    by_id = {t.get("task_id"): t for t in to_plan}
    ranks = {tid: n for n, tid in enumerate(order, 1)}

    def done_status(task: dict[str, Any]) -> bool:
        return str(task.get("status", "")).strip().casefold() == "done"

    def finish(task: dict[str, Any], reason: R.Reason, **fields: Any) -> None:
        item = copy.deepcopy(task)
        item.update({"allocation": {}, "assigned_engineers": [], "sp_by_sprint": {},
                     "planned_start_sprint": None, "planned_end_sprint": None,
                     "forecast_end_sprint": None, "unstaffed_hh": {},
                     "score": round(scores.get(task.get("task_id"), 0.0), 2)})
        item.update(reason.to_dict())
        item.update(fields)
        results[task.get("task_id")] = item

    # 2. Циклы: задачи из них не начать ни в каком порядке.
    left = set(leftovers)
    for tid in leftovers:
        cycle = _cycle_of(tid, blocked_by, left)
        if cycle and tid in cycle:
            finish(by_id[tid], R.dependency_cycle(tid, cycle))
        else:
            blocker = next((b for b in blocked_by.get(tid, []) if b in left), "")
            finish(by_id[tid], R.dependency_not_ready(tid, blocker, team_of.get(blocker)))

    # 3. Остальные — по порядку.
    for tid in order:
        task = by_id[tid]
        team = str(task.get("team_id") or "")

        if done_status(task):
            item = copy.deepcopy(task)
            item.setdefault("decision", "included")
            item.update(planned_start_sprint=0, planned_end_sprint=0, forecast_end_sprint=0)
            results[tid] = item
            end_of[tid] = 0
            continue

        raw = by_estimate.get(tid) if estimates is not None else task.get("estimated_hh")
        hours = {role: _num(h) for role, h in (raw or {}).items()
                 if normalize_role(role) and _num(h) > 0} if isinstance(raw, dict) else {}
        sp = _num(task.get("estimation_sp"))

        if not hours and not _in_progress(task):
            finish(task, R.not_estimated(tid))
            continue
        if team not in pool.team_sp.get(start_sprint, {}) and start_sprint <= QUARTER_SPRINTS:
            finish(task, R.unknown_team(tid, team))
            continue

        # Роли без людей в компании.
        missing = {role: h for role, h in hours.items() if normalize_role(role) not in pool.roles}
        # Доля недостающих ролей — от полной сметы задачи (у задачи в работе
        # estimated_hh — остаток, и почти сделанная задача не должна выпадать).
        total_hh = max(sum(hours.values()), _num(task.get("estimate_total_hh")))
        if missing:
            share = sum(missing.values()) / total_hh if total_hh else 1.0
            if policy == "postpone" or share > MAX_MISSING_SHARE:
                finish(task, R.role_absent(tid, missing, total_hh, strict=policy == "postpone"),
                       unstaffed_hh={r: round(h, 1) for r, h in missing.items()})
                continue
        work = {role: h for role, h in hours.items() if role not in missing}
        work_total = sum(work.values())
        # SP делим пропорционально часам, которые реально выполняются в квартале.
        sp_per_hour = sp / work_total if work_total > 0 else 0.0

        # Зависимости.
        earliest = start_sprint
        failed_blocker = None
        for blocker in blocked_by.get(tid, []):
            if blocker not in end_of:
                failed_blocker = (blocker, None)
                break
            earliest = max(earliest, end_of[blocker] + 1)
        if failed_blocker or earliest > QUARTER_SPRINTS:
            blocker, when = failed_blocker or (
                max(blocked_by.get(tid, []), key=lambda b: end_of.get(b, 0)), None)
            if not failed_blocker:
                when = end_of[blocker]
            finish(task, R.dependency_not_ready(tid, blocker, team_of.get(blocker), when),
                   unstaffed_hh={r: round(h, 1) for r, h in missing.items()})
            continue

        # Кандидаты по ролям (своя команда → другие, с учётом стека).
        techs = _task_techs(task.get("summary", ""), pool)
        cands, stack = {}, {}
        for role in work:
            cands[role], used = pool.candidates(normalize_role(role), team, techs)
            if used:
                stack[role] = used

        placed = _place(pool, team, work, sp, cands, earliest)
        busy: dict[str, float] = {}
        if placed["remaining"] > 0.05 and policy == "partial":
            # Роль есть, но её часы до конца квартала заняты более приоритетными
            # задачами (или новичок ещё не вышел): пробуем частичную цель без неё.
            sprints = range(earliest, QUARTER_SPRINTS + 1)
            busy = {role: h for role, h in work.items()
                    if h - sum(pool.free(s_, cands[role]) for s_ in sprints) > 0.05}
            rest = {role: h for role, h in work.items() if role not in busy}
            share = (sum(missing.values()) + sum(busy.values())) / total_hh if total_hh else 1.0
            if busy and (rest or _in_progress(task)) and share <= MAX_MISSING_SHARE:
                retry = _place(pool, team, rest, sp, cands, earliest)
                if retry["remaining"] <= 0.05:
                    placed, work, work_total = retry, rest, sum(rest.values())
                else:
                    busy = {}
            else:
                busy = {}

        if placed["remaining"] > 0.05:
            finish(task, _why_not(tid, team, sp, placed["left"], work, cands, pool, earliest,
                                  placed["sp_limited"]),
                   unstaffed_hh={r: round(h, 1) for r, h in missing.items()})
            continue

        allocation = placed["allocation"]
        by_role_sprint = placed["by_role_sprint"]
        sp_by_sprint = placed["sp_by_sprint"]
        borrowed = placed["borrowed"]
        start, last = placed["start"], placed["last"]
        unstaffed = {**missing, **busy}
        end_of[tid] = last
        facts_extra = {"score": round(scores[tid], 2), "rank": ranks[tid], "total": len(order)}
        extras = []
        if borrowed:
            facts_extra["borrowed"] = dict(sorted(borrowed.items()))
            extras.append("Привлечены из других команд: " + ", ".join(
                f"{e} ({t})" for e, t in sorted(borrowed.items())) + ".")
        if stack:
            facts_extra["stack"] = stack
            extras.append("Стек: " + "; ".join(
                f"роль «{r}» — только носители {', '.join(t)}" for r, t in stack.items()) + ".")
        blocks = sorted(b for b, blockers in blocked_by.items() if tid in blockers)

        if unstaffed:
            base = R.included_partial(tid, team, start, last, missing, work_total, busy=busy)
        elif last > start:
            role = max(by_role_sprint, key=lambda r: (max(by_role_sprint[r]), work[r]))
            per_sprint = max(by_role_sprint[role].values())
            base = R.included_stretched(tid, role, round(work[role], 1), round(per_sprint, 1),
                                        start, last)
        elif _in_progress(task):
            base = R.included_in_progress(tid)
        elif blocks:
            base = R.included_unblocks(tid, blocks)
        else:
            base = R.included_fits(tid, team, start, last, round(sp, 2),
                                   round(_num((capacity.get("team_sp") or {}).get(team)), 2))
        reason = R.make_reason(base.code, " ".join([base.detail, *extras]), target=base.target,
                               facts={**base.facts, **facts_extra},
                               recommendation=base.recommendation)
        item = copy.deepcopy(task)
        if estimates is not None and hours:
            # Калькулятор сметы — целевая смета (ответ экспертов): часы по ролям в задаче.
            item["estimated_hh"] = dict(hours)
        item.update(reason.to_dict())
        item.update({
            "planned_start_sprint": start,
            "planned_end_sprint": last,
            "forecast_end_sprint": last,
            "allocation": {s: {e: round(h, 2) for e, h in row.items()}
                           for s, row in sorted(allocation.items())},
            "assigned_engineers": sorted({e for row in allocation.values() for e in row}),
            "sp_by_sprint": {s: round(v, 2) for s, v in sorted(sp_by_sprint.items())},
            "unstaffed_hh": {r: round(h, 1) for r, h in unstaffed.items()},
            "score": round(scores[tid], 2),
        })
        results[tid] = item

    planned = [results[t.get("task_id")] for t in tasks if t.get("task_id") in results]
    return {"current_sprint": start_sprint, "baseline": planned, "current": planned}


def _place(pool: _Pool, team: str, work: dict[str, float], sp: float,
           cands: dict[str, list[tuple[str, str]]], earliest: int) -> dict[str, Any]:
    """Раскладывает часы ролей work по спринтам earliest…6.

    SP задачи списываются пропорционально выполненным часам и не превышают
    свободную ёмкость команды в спринте. Если задача не поместилась целиком,
    все списания откатываются (remaining > 0 в результате).
    """
    mark = pool.mark()
    work_total = sum(work.values())
    sp_per_hour = sp / work_total if work_total > 0 else 0.0
    remaining = dict(work)
    allocation: dict[int, dict[str, float]] = {}
    by_role_sprint: dict[str, dict[int, float]] = {}
    sp_by_sprint: dict[int, float] = {}
    borrowed: dict[str, str] = {}
    start, last, sprint, sp_limited = None, earliest, earliest, False

    if work_total <= _EPS:
        # Задача в работе с нулевым остатком по смете — готова к закрытию.
        start = last = earliest

    while work_total > _EPS and sprint <= QUARTER_SPRINTS and sum(remaining.values()) > _EPS:
        can = {role: min(need, pool.free(sprint, cands[role]))
               for role, need in remaining.items() if need > _EPS}
        achievable = sum(can.values())
        if achievable <= _EPS:
            sprint += 1
            continue
        factor = 1.0
        if sp_per_hour > 0:
            free_sp = max(pool.team_sp[sprint].get(team, 0.0) + SP_TOLERANCE, 0.0)
            cap_hours = free_sp / sp_per_hour
            if cap_hours <= _EPS:
                sp_limited = True
                sprint += 1
                continue
            if cap_hours < achievable:
                sp_limited = True
                factor = cap_hours / achievable
        done_hours = 0.0
        for role, amount in can.items():
            want = amount * factor
            for slot in cands[role]:
                if want <= _EPS:
                    break
                take = min(want, max(pool.hh[sprint].get(slot, 0.0), 0.0))
                if take <= _EPS:
                    continue
                pool.take_hh(sprint, slot, take)
                want -= take
                remaining[role] -= take
                done_hours += take
                eng_id, eng_team = slot
                row = allocation.setdefault(sprint, {})
                row[eng_id] = row.get(eng_id, 0.0) + take
                by_role_sprint.setdefault(role, {})
                by_role_sprint[role][sprint] = by_role_sprint[role].get(sprint, 0.0) + take
                if eng_team != team:
                    borrowed.setdefault(eng_id, eng_team)
        if done_hours > _EPS:
            used_sp = done_hours * sp_per_hour
            pool.take_sp(sprint, team, used_sp)
            sp_by_sprint[sprint] = sp_by_sprint.get(sprint, 0.0) + used_sp
            start = start if start is not None else sprint
            last = sprint
        if sum(remaining.values()) > _EPS:
            sprint += 1

    left = sum(remaining.values())
    if left > 0.05:
        pool.rollback(mark)
    return {"remaining": left, "left": remaining, "allocation": allocation,
            "by_role_sprint": by_role_sprint, "sp_by_sprint": sp_by_sprint,
            "borrowed": borrowed, "start": start, "last": last, "sp_limited": sp_limited}


def _why_not(tid, team, sp, remaining, work, cands, pool: _Pool, earliest,
             sp_limited) -> R.Reason:
    """Самое узкое место задачи, которая не поместилась до конца квартала."""
    sprints = range(earliest, QUARTER_SPRINTS + 1)
    worst, worst_gap = None, 0.0
    for role, need in work.items():
        free = sum(pool.free(s, cands[role]) for s in sprints)
        if need - free > worst_gap:
            worst, worst_gap = role, need - free
    if worst is not None:
        free = sum(pool.free(s, cands[worst]) for s in sprints)
        return R.no_resources(tid, worst, round(work[worst], 1), round(free, 1))
    if sp_limited and sp > 0:
        free_sp = sum(max(pool.team_sp[s].get(team, 0.0), 0.0) for s in sprints)
        return R.plan_exceeded(tid, team, round(sp, 2), round(free_sp, 2))
    role = max(remaining, key=remaining.get)
    free = sum(pool.free(s, cands[role]) for s in sprints)
    return R.no_resources(tid, role, round(work[role], 1), round(free, 1))
