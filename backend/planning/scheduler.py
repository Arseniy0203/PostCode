from __future__ import annotations
from typing import Any
import copy

QUARTER_SPRINTS = 6

def build_baseline_plan(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
) -> Any:
    # Инициализация лимитов из предрассчитанного capacity
    # Ожидаем структуру: {"team_sp": {team_id: float}, "engineer_hh": {eng_id: float}}
    team_sp_limits = capacity.get("team_sp", {})
    eng_hh_limits = capacity.get("engineer_hh", {})

    # Трекеры доступных ресурсов по спринтам (от 1 до 6)
    rem_team_sp = {s: copy.deepcopy(team_sp_limits) for s in range(1, QUARTER_SPRINTS + 1)}
    rem_eng_hh = {s: copy.deepcopy(eng_hh_limits) for s in range(1, QUARTER_SPRINTS + 1)}

    # Трекеры завершения задач для связей
    task_end_sprint: dict[str, int] = {}
    planned_tasks: list[dict[str, Any]] = []

    # Карта зависимостей: blocked_task_id -> [blocking_task_id_1, ...]
    blocked_by: dict[str, list[str]] = {}
    for dep in dependencies:
        blocked = dep.get("blocked_task_id", "")
        blocking = dep.get("blocking_task_id", "")
        if blocked and blocking:
            blocked_by.setdefault(blocked, []).append(blocking)

    # Карта инженеров: team_id -> role -> [engineer_id_1, ...]
    team_role_engs: dict[str, dict[str, list[str]]] = {}
    for eng in engineers:
        t_id = eng.get("team_id")
        role = eng.get("role")
        e_id = eng.get("engineer_id")
        if t_id and role and e_id:
            team_role_engs.setdefault(t_id, {}).setdefault(role, []).append(e_id)

    # Сортировка задач по Rung (жадный алгоритм - сначала самые ценные)
    sorted_tasks = sorted(
        tasks, 
        key=lambda x: float(x.get("rung", 0) if x.get("rung") is not None else 0), 
        reverse=True
    )

    for task in sorted_tasks:
        t_id = task.get("task_id", "")
        team = task.get("team_id", "")
        sp = float(task.get("estimation_sp", 0))
        est_hh: dict[str, float] = task.get("estimated_hh", {})

        planned_task = copy.deepcopy(task)
        planned_task["allocation"] = {}
        planned_task["assigned_engineers"] = []

        # 1. Проверка зависимостей
        blockers = blocked_by.get(t_id, [])
        earliest_start = 1
        dependency_failed = False

        for b_id in blockers:
            if b_id not in task_end_sprint:
                # Блокирующая задача не поместилась в план (отменена/отложена)
                dependency_failed = True
                break
            # Зазор минимум 1 спринт
            earliest_start = max(earliest_start, task_end_sprint[b_id] + 1)

        if dependency_failed or earliest_start > QUARTER_SPRINTS:
            planned_task.update({
                "decision": "postponed",
                "reason_code": "DEPENDENCY_BLOCK",
                "reason_detail": "Блокирующая задача не выполнена или выпадает за квартал"
            })
            planned_tasks.append(planned_task)
            continue

        # 2. Поиск слота и сплитование
        current_sprint = earliest_start
        task_started = False
        start_sprint = None
        
        # Копируем часы, чтобы вычитать их по мере планирования
        remaining_hh = {role: float(hours) for role, hours in est_hh.items()}
        
        # Проверка наличия компетенций в команде ДО старта
        missing_competence = False
        for role in remaining_hh.keys():
            if role not in team_role_engs.get(team, {}):
                missing_competence = True
                break
                
        if missing_competence:
            planned_task.update({
                "decision": "cancelled",
                "reason_code": "MISSING_COMPETENCE",
                "reason_detail": f"В команде {team} нет необходимых ролей"
            })
            planned_tasks.append(planned_task)
            continue

        # Пытаемся разложить задачу по спринтам
        while current_sprint <= QUARTER_SPRINTS and sum(remaining_hh.values()) > 0:
            
            # Проверка SP (списываем один раз на старте задачи)
            if not task_started:
                avail_sp = rem_team_sp[current_sprint].get(team, 0)
                if avail_sp < sp:
                    current_sprint += 1
                    continue
                rem_team_sp[current_sprint][team] = avail_sp - sp
                start_sprint = current_sprint
                task_started = True

            # Списывание ЧЧ (сплитование)
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
                        
                        sprint_allocation[role] = sprint_allocation.get(role, 0) + take_hh
                        if eng_id not in planned_task["assigned_engineers"]:
                            planned_task["assigned_engineers"].append(eng_id)
                            
                        if req_hours <= 0:
                            break
                            
                remaining_hh[role] = req_hours

            if sprint_allocation:
                planned_task["allocation"][current_sprint] = sprint_allocation

            # Переходим к следующему спринту, если остались несписанные часы
            if sum(remaining_hh.values()) > 0:
                current_sprint += 1

        # 3. Фиксация результата
        if sum(remaining_hh.values()) == 0:
            planned_task.update({
                "decision": "included",
                "planned_start_sprint": start_sprint,
                "planned_end_sprint": current_sprint,
                "forecast_end_sprint": current_sprint
            })
            task_end_sprint[t_id] = current_sprint
        else:
            # Откат SP, если задача начала планироваться, но не влезла до конца квартала
            if task_started:
                rem_team_sp[start_sprint][team] += sp
                
            planned_task.update({
                "decision": "postponed",
                "reason_code": "CAPACITY_LIMIT",
                "reason_detail": "Не хватило часов (ЧЧ) до конца квартала",
                "allocation": {},
                "assigned_engineers": []
            })

        planned_tasks.append(planned_task)

    # Имитация возврата объекта Plan (в виде словаря)
    return {
        "current_sprint": 0,
        "baseline": planned_tasks,
        "current": planned_tasks
    }