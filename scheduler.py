from __future__ import annotations
from typing import Any
import copy

QUARTER_SPRINTS = 6

def normalize_role(role: str) -> str:
    """Нормализует название роли для избежания багов с регистрами и пробелами."""
    return str(role).strip().lower() if role else ""


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
    for eng in engineers:
        t_id = eng.get("team_id")
        role = normalize_role(eng.get("role"))
        e_id = eng.get("engineer_id")
        if t_id and role and e_id:
            team_role_engs.setdefault(t_id, {}).setdefault(role, []).append(e_id)

    # 1. ПРЕДОБРАБОТКА ЗАБЛОКИРОВАННЫХ ЗАДАЧ (ФАКТ ИЛИ ПРОШЛОЕ)
    for task in tasks:
        t_id = task.get("task_id", "")
        if t_id in locked_set:
            planned_task = copy.deepcopy(task)
            
            # Списываем SP, если задача началась в текущем окне планирования или позже
            start_s = planned_task.get("planned_start_sprint")
            team = planned_task.get("team_id")
            if start_s and team and start_s >= start_sprint:
                sp = float(planned_task.get("estimation_sp", 0) or 0)
                if start_s in rem_team_sp and team in rem_team_sp[start_s]:
                    rem_team_sp[start_s][team] -= sp
            
            # Списываем ЧЧ конкретных инженеров из аллокации
            allocation = planned_task.get("allocation", {})
            for s_str, eng_hours in allocation.items():
                s = int(s_str)
                if s >= start_sprint and s in rem_eng_hh:
                    for e_id, hours in eng_hours.items():
                        if e_id in rem_eng_hh[s]:
                            rem_eng_hh[s][e_id] -= float(hours)
                            
            end_s = planned_task.get("forecast_end_sprint") or planned_task.get("planned_end_sprint") or 0
            task_end_sprint[t_id] = end_s
            planned_tasks.append(planned_task)
        else:
            unlocked_tasks.append(task)

    # Сортировка оставшихся задач
    sorted_tasks = sorted(
        unlocked_tasks, 
        key=lambda x: float(x.get("rung", 0) if x.get("rung") is not None else 0), 
        reverse=True
    )

    # 2. ПЛАНИРОВАНИЕ ОСТАВШЕГОСЯ ХВОСТА
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
                "forecast_end_sprint": 0
            })
            task_end_sprint[t_id] = 0
            planned_tasks.append(planned_task)
            continue

        sp = float(task.get("estimation_sp", 0) or 0)
        
        est_hh: dict[str, float] = {}
        if estimates is not None:
            task_estimates = [e for e in estimates if e.get("task_id") == t_id]
            for e in task_estimates:
                r = normalize_role(e.get("role"))
                h = float(e.get("hours", 0) or 0)
                if h > 0:
                    est_hh[r] = est_hh.get(r, 0) + h
            
            calc_total = sum(est_hh.values())
            task_total = float(task.get("spent_time", 0) or 0)
            if calc_total != task_total:
                print(f"ВНИМАНИЕ: Расхождение часов в задаче {t_id}. В Tasks: {task_total} ч, в Калькуляторе: {calc_total} ч")
        else:
            raw_hh = task.get("estimated_hh", {})
            est_hh = {normalize_role(k): float(v) for k, v in raw_hh.items()}

        missing_roles = [role for role in est_hh.keys() if role not in team_role_engs.get(team, {})]
        if missing_roles:
            planned_task.update({
                "decision": "postponed",
                "reason_code": "MISSING_COMPETENCE",
                "reason_detail": f"В команде {team} нет необходимых ролей: {', '.join(missing_roles)}"
            })
            planned_tasks.append(planned_task)
            continue

        blockers = blocked_by.get(t_id, [])
        earliest_start = start_sprint # Начинаем планировать не раньше стартового спринта
        dependency_failed = False

        for b_id in blockers:
            if b_id not in task_end_sprint:
                dependency_failed = True
                break
            earliest_start = max(earliest_start, task_end_sprint[b_id] + 1)

        if dependency_failed or earliest_start > QUARTER_SPRINTS:
            planned_task.update({
                "decision": "postponed",
                "reason_code": "DEPENDENCY_BLOCK",
                "reason_detail": "Блокирующая задача не выполнена или выпадает за квартал"
            })
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
                
                avail_engineers = team_role_engs[team][role]
                for eng_id in avail_engineers:
                    e_avail_hh = rem_eng_hh[current_sprint].get(eng_id, 0)
                    if e_avail_hh > 0:
                        take_hh = min(req_hours, e_avail_hh)
                        rem_eng_hh[current_sprint][eng_id] -= take_hh
                        req_hours -= take_hh
                        
                        # Записываем часы на конкретного инженера!
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
                "forecast_end_sprint": current_sprint
            })
            task_end_sprint[t_id] = current_sprint
        else:
            if task_started:
                rem_team_sp[start_s][team] += sp
                
            planned_task.update({
                "decision": "postponed",
                "reason_code": "CAPACITY_LIMIT",
                "reason_detail": "Не хватило часов (ЧЧ) до конца квартала",
                "allocation": {},
                "assigned_engineers": []
            })
            
        planned_tasks.append(planned_task)

    return {
        "current_sprint": start_sprint,
        "baseline": planned_tasks,
        "current": planned_tasks
    }