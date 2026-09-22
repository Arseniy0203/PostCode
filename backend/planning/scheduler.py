from __future__ import annotations
from typing import Any
import copy

QUARTER_SPRINTS = 6

REASON_MISSING_COMPETENCE = "MISSING_COMPETENCE"
REASON_DEPENDENCY_BLOCK = "DEPENDENCY_BLOCK"
REASON_CAPACITY_LIMIT = "CAPACITY_LIMIT"

try:
    from backend.explainability.reasons import (
        get_missing_competence_reason,
        get_dependency_block_reason,
        get_capacity_sp_reason,
        get_capacity_hh_reason
    )
except ImportError:
    def get_missing_competence_reason(roles: list[str]) -> dict[str, Any]:
        return {
            "reason_code": "MISSING_COMPETENCE", 
            "reason_detail": f"В компании нет необходимых ролей: {', '.join(roles)}",
            "reason_facts": {"missing_roles": roles}
        }
    def get_dependency_block_reason(blocker_id: str) -> dict[str, Any]:
        return {
            "reason_code": "DEPENDENCY_BLOCK", 
            "reason_detail": f"Блокирующая задача {blocker_id} не выполнена или выпадает за квартал",
            "reason_facts": {"blocker_task_id": blocker_id}
        }
    def get_capacity_sp_reason(req_sp: float, avail_sp: float) -> dict[str, Any]:
        return {
            "reason_code": "CAPACITY_LIMIT_SP", 
            "reason_detail": f"Не хватило SP команды. Требуется {req_sp}, макс. доступно {avail_sp}",
            "reason_facts": {"required_sp": req_sp, "max_available_sp": avail_sp}
        }
    def get_capacity_hh_reason(rem_hh: dict[str, float]) -> dict[str, Any]:
        return {
            "reason_code": "CAPACITY_LIMIT_HH", 
            "reason_detail": "Не хватило часов (ЧЧ) специалистов до конца квартала",
            "reason_facts": {"unallocated_hh": rem_hh}
        }


def normalize_role(role: str) -> str:
    if not role:
        return ""
    r = str(role).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if "итого" in r:
        return ""
    return r


def parse_float_safe(val: Any) -> float:
    """Безопасный парсинг чисел, даже если они пришли с запятой."""
    try:
        return float(str(val or 0).replace(',', '.'))
    except (ValueError, TypeError):
        return 0.0


def build_baseline_plan(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
    estimates: list[dict[str, Any]] = None,
    start_sprint: int = 1,
    locked_tasks: list[str] = None,
) -> dict[str, Any]:
    locked_set = set(locked_tasks or [])
    
    team_sp_limits = capacity.get("team_sp", {})
    eng_hh_limits = capacity.get("engineer_hh", {})

    rem_team_sp = {s: copy.deepcopy(team_sp_limits) for s in range(1, QUARTER_SPRINTS + 1)}
    rem_eng_hh = {s: copy.deepcopy(eng_hh_limits) for s in range(1, QUARTER_SPRINTS + 1)}

    task_end_sprint: dict[str, int] = {}
    planned_tasks: list[dict[str, Any]] = []
    unlocked_tasks: list[dict[str, Any]] = []

    blocked_by: dict[str, list[str]] = {}
    for dep in dependencies:
        blocked = dep.get("blocked_task_id", "")
        blocking = dep.get("blocking_task_id", "")
        if blocked and blocking:
            blocked_by.setdefault(blocked, []).append(blocking)

    team_role_engs: dict[str, dict[str, list[str]]] = {}
    global_role_engs: dict[str, list[str]] = {}
    
    for eng in engineers:
        t_id = eng.get("team_id")
        role = normalize_role(eng.get("role"))
        e_id = eng.get("engineer_id")
        if role and e_id:
            global_role_engs.setdefault(role, []).append(e_id)
            if t_id:
                team_role_engs.setdefault(t_id, {}).setdefault(role, []).append(e_id)

    # 1. ПРЕДОБРАБОТКА ЗАБЛОКИРОВАННЫХ ЗАДАЧ (ФАКТ)
    for task in tasks:
        t_id = task.get("task_id", "")
        if t_id in locked_set:
            planned_task = copy.deepcopy(task)
            start_s = planned_task.get("planned_start_sprint")
            team = planned_task.get("team_id")
            
            # Принудительно проставляем решение для прошлых/зафиксированных задач
            if not planned_task.get("decision"):
                planned_task["decision"] = "included"
            if not planned_task.get("target_option"):
                planned_task["target_option"] = "Реализовано"
            
            if start_s and team and start_s >= start_sprint:
                sp = parse_float_safe(planned_task.get("estimation_sp"))
                if start_s in rem_team_sp and team in rem_team_sp[start_s]:
                    rem_team_sp[start_s][team] -= sp
            
            allocation = planned_task.get("allocation", {})
            for s_str, eng_hours in allocation.items():
                s = int(s_str)
                if s >= start_sprint and s in rem_eng_hh:
                    for e_id, hours in eng_hours.items():
                        if e_id in rem_eng_hh[s]:
                            rem_eng_hh[s][e_id] -= parse_float_safe(hours)
                            
            end_s = planned_task.get("forecast_end_sprint") or planned_task.get("planned_end_sprint") or 0
            task_end_sprint[t_id] = end_s
            planned_tasks.append(planned_task)
        else:
            unlocked_tasks.append(task)

    # 2. ТОПОЛОГИЧЕСКАЯ СОРТИРОВКА + ЖАДНОСТЬ ПО RUNG
    unlocked_ids = {t.get("task_id") for t in unlocked_tasks}
    in_degree = {t_id: 0 for t_id in unlocked_ids}
    adj_list = {t_id: [] for t_id in unlocked_ids}
    
    for dep in dependencies:
        blocking = dep.get("blocking_task_id", "")
        blocked = dep.get("blocked_task_id", "")
        if blocking in unlocked_ids and blocked in unlocked_ids:
            adj_list[blocking].append(blocked)
            in_degree[blocked] += 1

    available_queue = [t for t in unlocked_tasks if in_degree[t.get("task_id")] == 0]
    sorted_tasks = []

    while available_queue:
        available_queue.sort(
            key=lambda x: parse_float_safe(x.get("rung")), 
            reverse=True
        )
        current = available_queue.pop(0)
        sorted_tasks.append(current)
        
        c_id = current.get("task_id")
        for neighbor in adj_list.get(c_id, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                neighbor_task = next((t for t in unlocked_tasks if t.get("task_id") == neighbor), None)
                if neighbor_task:
                    available_queue.append(neighbor_task)

    processed_ids = {t.get("task_id") for t in sorted_tasks}
    leftovers = [t for t in unlocked_tasks if t.get("task_id") not in processed_ids]
    leftovers.sort(key=lambda x: parse_float_safe(x.get("rung")), reverse=True)
    sorted_tasks.extend(leftovers)

    # 3. ПЛАНИРОВАНИЕ ОСТАВШЕГОСЯ ХВОСТА
    for task in sorted_tasks:
        t_id = task.get("task_id", "")
        team = task.get("team_id", "")
        status = str(task.get("status", "")).strip().lower()

        planned_task = copy.deepcopy(task)
        planned_task["allocation"] = {}
        planned_task["assigned_engineers"] = []

        if status == "done":
            planned_task.update({
                "decision": "included",
                "planned_start_sprint": 0,
                "planned_end_sprint": 0,
                "forecast_end_sprint": 0,
                "target_option": "Реализовано"
            })
            task_end_sprint[t_id] = 0
            planned_tasks.append(planned_task)
            continue

        sp = parse_float_safe(task.get("estimation_sp"))
        
        est_hh: dict[str, float] = {}
        if estimates is not None:
            task_estimates = [e for e in estimates if e.get("task_id") == t_id]
            for e in task_estimates:
                r = normalize_role(e.get("role"))
                h = parse_float_safe(e.get("hours"))
                if r and h > 0:
                    est_hh[r] = est_hh.get(r, 0) + h
            
            calc_total = sum(est_hh.values())
            raw_hh = task.get("estimated_hh", {})
            task_total = sum(parse_float_safe(v) for v in raw_hh.values()) if isinstance(raw_hh, dict) else 0.0
            
            if calc_total != task_total:
                print(f"ВНИМАНИЕ: Расхождение часов в задаче {t_id}. В estimated_hh: {task_total} ч, в Калькуляторе: {calc_total} ч")
        else:
            raw_hh = task.get("estimated_hh", {})
            est_hh = {normalize_role(k): parse_float_safe(v) for k, v in raw_hh.items()}

        missing_roles = []
        for role in est_hh.keys():
            if role not in team_role_engs.get(team, {}) and role not in global_role_engs:
                missing_roles.append(role)
                
        if missing_roles:
            planned_task.update({
                "decision": "postponed",
                "target_option": "Перенос"
            })
            planned_task.update(get_missing_competence_reason(missing_roles))
            planned_tasks.append(planned_task)
            continue

        blockers = blocked_by.get(t_id, [])
        earliest_start = start_sprint
        dependency_failed = False
        failed_blocker_id = ""

        for b_id in blockers:
            if b_id not in task_end_sprint:
                dependency_failed = True
                failed_blocker_id = b_id
                break
            earliest_start = max(earliest_start, task_end_sprint[b_id] + 1)

        if dependency_failed or earliest_start > QUARTER_SPRINTS:
            planned_task.update({
                "decision": "postponed",
                "target_option": "Перенос"
            })
            planned_task.update(get_dependency_block_reason(failed_blocker_id))
            planned_tasks.append(planned_task)
            continue

        current_sprint = earliest_start
        task_started = False
        start_s = None
        remaining_hh = {role: float(hours) for role, hours in est_hh.items()}
        
        while current_sprint <= QUARTER_SPRINTS and sum(remaining_hh.values()) > 0:
            
            if not task_started:
                avail_sp = rem_team_sp[current_sprint].get(team, 0)
                if avail_sp < sp:
                    current_sprint += 1
                    continue
                rem_team_sp[current_sprint][team] = avail_sp - sp
                start_s = current_sprint
                task_started = True

            sprint_allocation = {}
            for role, req_hours in list(remaining_hh.items()):
                if req_hours <= 0:
                    continue
                
                avail_engineers = team_role_engs.get(team, {}).get(role, [])
                if not avail_engineers:
                    avail_engineers = global_role_engs.get(role, [])
                    
                for eng_id in avail_engineers:
                    e_avail_hh = rem_eng_hh[current_sprint].get(eng_id, 0)
                    if e_avail_hh > 0:
                        take_hh = min(req_hours, e_avail_hh)
                        rem_eng_hh[current_sprint][eng_id] -= take_hh
                        req_hours -= take_hh
                        
                        sprint_allocation[eng_id] = sprint_allocation.get(eng_id, 0) + take_hh
                        if eng_id not in planned_task["assigned_engineers"]:
                            planned_task["assigned_engineers"].append(eng_id)
                            
                        if req_hours <= 0:
                            break
                            
                remaining_hh[role] = req_hours

            if sprint_allocation:
                planned_task["allocation"][current_sprint] = sprint_allocation

            if sum(remaining_hh.values()) > 0:
                current_sprint += 1

        if sum(remaining_hh.values()) == 0:
            planned_task.update({
                "decision": "included",
                "planned_start_sprint": start_s,
                "planned_end_sprint": current_sprint,
                "forecast_end_sprint": current_sprint,
                "target_option": "Реализовано"
            })
            task_end_sprint[t_id] = current_sprint
        else:
            planned_task.update({
                "decision": "postponed",
                "target_option": "Перенос",
                "allocation": {},
                "assigned_engineers": []
            })
            
            if not task_started:
                max_sp_available = max([rem_team_sp[s].get(team, 0) for s in range(start_sprint, QUARTER_SPRINTS + 1)] + [0])
                planned_task.update(get_capacity_sp_reason(sp, max_sp_available))
            else:
                rem_team_sp[start_s][team] += sp
                planned_task.update(get_capacity_hh_reason(remaining_hh))
            
        planned_tasks.append(planned_task)

    return {
        "current_sprint": start_sprint,
        "baseline": planned_tasks,
        "current": planned_tasks
    }