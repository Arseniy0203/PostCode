"""Read-only demo snapshots built from the dataset shipped with the repository."""

import copy

import streamlit as st

from backend.recalculation.pipeline import build_week0, load_dataset
from backend.recalculation.overload import overload_report
from backend.recalculation.scenarios import simulate
from backend.starmap.bus_factor import starmap_summary

DEMO_SCENARIOS = {
    "on_track": "Всё по плану",
    "key_task_delay": "Задержка ключевой задачи",
    "engineer_leave": "Уход инженера",
}


@st.cache_data(show_spinner="Подготовка демо: Неделя 0 и шесть спринтов…")
def demo_snapshots(scenario: str = "on_track") -> tuple[dict, dict]:
    if scenario not in DEMO_SCENARIOS:
        raise ValueError("Неизвестный демо-сценарий")
    data = load_dataset()
    plan = build_week0(data)
    master = {"overload": overload_report(data, plan),
              "data_quality": [a.to_dict() if hasattr(a, "to_dict") else a
                               for a in plan.get("data_quality", [])]}
    snapshots = {}
    for sprint in range(7):
        if sprint:
            simulate(plan, scenario, sprint, dependencies=data["dependencies"],
                     engineers=data["engineers"], capacity=data["capacity"])
        engineers = [e for e in data["engineers"]
                     if e["engineer_id"] not in (plan.get("engineers_unavailable") or [])]
        snapshots["baseline" if sprint == 0 else sprint] = {
            "plan": copy.deepcopy(plan),
            "alerts": [a.to_dict() if hasattr(a, "to_dict") else copy.deepcopy(a)
                       for a in plan.get("alerts", [])],
            "starmap": copy.deepcopy(starmap_summary(plan, engineers, data["estimates"])),
        }
        snapshots["baseline" if sprint == 0 else sprint]["plan"]["resolved_alerts"] = [
            a.to_dict() if hasattr(a, "to_dict") else copy.deepcopy(a)
            for a in plan.get("resolved_alerts", [])]
    return master, snapshots
