"""Explain the same KPI values used in the dashboard cards."""

import pandas as pd
import streamlit as st

from backend.kpi.metrics import kpi_breakdown
from .components import detail_grid, styled_table


def render_kpi_view(plan):
    st.header("Показатели квартала")
    metrics = kpi_breakdown(plan.get("baseline", []), plan.get("current", []),
                            current_sprint=plan.get("current_sprint", 0))
    section = st.radio("Показатель", ["PI Predictability", "Say/Do"],
                       key="kpi_section", horizontal=True)
    with st.container(border=True, key="soft-card-kpi-details"):
        if section == "PI Predictability":
            mode = "fact" if plan.get("current_sprint", 0) >= 6 else "forecast"
            pi = metrics["pi_predictability"][mode]
            st.subheader("PI Predictability · " + ("факт" if mode == "fact" else "прогноз"))
            detail_grid([
                ("Завершённые инициативы" if mode == "fact" else "Завершатся по прогнозу", pi["numerator"]),
                ("Обязательства Недели 0", pi["denominator"]),
                ("Как считается", "Завершённые инициативы / инициативы исходного плана × 100%."),
                ("Норма", "80–100%. До конца квартала на карточке показан прогноз."),
            ])
            completed = set(pi["completed_prodf"])
            rows = [{"Инициатива": name, "Задач в обязательстве": len(tasks),
                     "Результат": ("Выполнена" if mode == "fact" else "Укладывается в квартал")
                     if name in completed else "Под риском / не завершена",
                     "Незавершённые задачи": ", ".join(pi["not_completed_prodf"].get(name, [])) or "—"}
                    for name, tasks in pi["baseline_scope"].items()]
            styled_table(rows, key="pi-details-table")
        else:
            st.subheader("Say/Do · выполнение спринтов")
            detail_grid([("Как считается", "Выполненные Story Points / запланированные на спринт Story Points × 100%."),
                         ("Норма", "90–105%. Будущие спринты и спринты без плана не считаются нулевым результатом.")])
            rows = [{"Спринт": r["sprint"], "План, SP": r["planned_sp"],
                     "Факт, SP": r["done_sp"] if r["status"] not in ("future", "no_data") else None,
                     "Say/Do, %": r["value"] if r["status"] not in ("future", "no_data") else None,
                     "Состояние": r["status_label"]} for r in metrics["say_do"]]
            styled_table(pd.DataFrame(rows), key="say-do-table", column_config={
                "Say/Do, %": st.column_config.NumberColumn(format="%.1f%%"),
                "План, SP": st.column_config.NumberColumn(format="%.1f"),
                "Факт, SP": st.column_config.NumberColumn(format="%.1f"),
            })
