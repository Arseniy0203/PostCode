"""Resource comparison with labelled cards and reversible chart/table views."""

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from .components import chart_table, detail_grid

COLOR_OVER = "#C54A51"
COLOR_PLAN = "#294DA6"
COLOR_RULE = "#64748B"


def _tasks_word(n: int) -> str:
    return "задача" if n % 10 == 1 and n % 100 != 11 else (
        "задачи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "задач")


def _polish(chart):
    return (chart.configure_view(stroke=None)
            .configure_axis(labelFontSize=13, titleFontSize=12, labelColor="#5B6B84",
                            titleColor="#5B6B84", labelPadding=10, titlePadding=14,
                            domain=False, ticks=False, gridColor="#E8EEF8", gridDash=[3, 4])
            .configure_legend(labelFontSize=12, labelColor="#5B6B84", padding=14)
            .properties(padding={"left": 8, "right": 45, "top": 10, "bottom": 8}))


def _team_chart(teams: list[dict[str, Any]]):
    rows = [{"Команда": t["team_id"], "Вариант": name, "Загрузка, %": t[field] or 0}
            for t in teams for name, field in [("Все задачи сразу", "all_at_once_pct"),
                                               ("По плану", "plan_peak_pct")]]
    data = pd.DataFrame(rows)
    order = [t["team_id"] for t in sorted(teams, key=lambda t: -(t["all_at_once_pct"] or 0))]
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=5, size=14).encode(
        y=alt.Y("Команда:N", title=None, sort=order, axis=alt.Axis(labelLimit=180, grid=False)),
        yOffset=alt.YOffset("Вариант:N", sort=["Все задачи сразу", "По плану"]),
        x=alt.X("Загрузка, %:Q", title="Загрузка команды, %", axis=alt.Axis(tickCount=7)),
        color=alt.Color("Вариант:N", scale=alt.Scale(domain=["Все задачи сразу", "По плану"],
                                                    range=[COLOR_OVER, COLOR_PLAN]),
                        legend=alt.Legend(orient="top", title=None, symbolType="circle")),
        tooltip=["Команда:N", "Вариант:N", alt.Tooltip("Загрузка, %:Q", format=".1f")],
    )
    labels = bars.mark_text(align="left", dx=8, fontSize=12).encode(
        text=alt.Text("Загрузка, %:Q", format=".0f"), color=alt.value(COLOR_RULE))
    rule = alt.Chart(pd.DataFrame({"limit": [100]})).mark_rule(
        strokeDash=[5, 4], color="#92A7CB").encode(x="limit:Q")
    return _polish((bars + labels + rule).properties(height=max(220, len(teams) * 58)))


def _role_chart(roles: list[dict[str, Any]]):
    data = pd.DataFrame([{"Роль": r["role"], "Загрузка, %": r["all_at_once_pct"] or 0,
                          "Нужно, ч": r["need_hh"], "Фонд, ч": r["fund_sprint_hh"],
                          "Состояние": "Выше ёмкости" if (r["all_at_once_pct"] or 0) > 100 else "В пределах ёмкости"}
                         for r in roles])
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=5, size=16).encode(
        y=alt.Y("Роль:N", title=None, sort=data.sort_values("Загрузка, %", ascending=False)["Роль"].tolist(),
                axis=alt.Axis(labelLimit=250, grid=False)),
        x=alt.X("Загрузка, %:Q", title="Потребность от фонда роли за спринт, %", axis=alt.Axis(tickCount=6)),
        color=alt.Color("Состояние:N", scale=alt.Scale(domain=["Выше ёмкости", "В пределах ёмкости"],
                                                      range=[COLOR_OVER, COLOR_PLAN]),
                        legend=alt.Legend(orient="top", title=None, symbolType="circle")),
        tooltip=["Роль:N", "Нужно, ч:Q", "Фонд, ч:Q", alt.Tooltip("Загрузка, %:Q", format=".1f")],
    )
    labels = bars.mark_text(align="left", dx=8, fontSize=12).encode(
        text=alt.Text("Загрузка, %:Q", format=".0f"), color=alt.value(COLOR_RULE))
    rule = alt.Chart(pd.DataFrame({"limit": [100]})).mark_rule(
        strokeDash=[5, 4], color="#92A7CB").encode(x="limit:Q")
    return _polish((bars + labels + rule).properties(height=max(220, len(roles) * 34)))


def render_overload_view(overload: dict[str, Any] | None) -> None:
    st.header("Загрузка команд и ролей")
    if not overload:
        st.info("Загрузите мастер-датасет заново, чтобы построить сравнение.")
        return
    summary = overload["summary"]
    worst = summary.get("max_team") or {}
    col1, col2, col3 = st.columns(3)
    col1.metric("Если запустить всё сразу", f"до {worst.get('all_at_once_pct', 0)}%",
                delta=worst.get("team_id", ""), delta_color="off")
    col2.metric("План: пик загрузки", f"{summary.get('plan_peak_pct', 0)}%",
                delta="лимит ёмкости — 100%", delta_color="off")
    col3.metric("Задач в квартале", f"{summary.get('plan_tasks', 0)} из {summary.get('open_tasks', 0)}",
                delta=(f"При строгом отборе: {summary['strict_tasks']}" if summary.get("strict_tasks") is not None else None),
                delta_color="off")

    with st.container(border=True, key="soft-card-team-load"):
        st.subheader("Команды · Story Points")
        detail_grid([("Все задачи сразу", f"{summary.get('teams_overloaded', 0)} команд превысят ёмкость при одновременном старте."),
                     ("По плану", "Работа распределена между спринтами с учётом зависимостей. Пунктир на графике — лимит 100%.")])
        teams = overload["teams"]
        if teams:
            table = pd.DataFrame([{"Команда": t["team_id"], "Бэклог, SP": t["backlog_sp"],
                                   "Ёмкость спринта, SP": t["capacity_sp"],
                                   "Все сразу, %": t["all_at_once_pct"], "По плану, %": t["plan_peak_pct"]} for t in teams])
            chart_table(_team_chart(teams), table, key="team-load", column_config={
                "Все сразу, %": st.column_config.ProgressColumn(min_value=0, max_value=max(100, float(table["Все сразу, %"].max())), format="%.0f%%"),
                "По плану, %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
            })
        else:
            st.caption("Нет данных о загрузке команд.")

    missing = [r for r in overload["roles"] if not r["fund_sprint_hh"]]
    staffed = [r for r in overload["roles"] if r["fund_sprint_hh"]]
    with st.container(border=True, key="soft-card-role-load"):
        st.subheader("Роли · рабочие часы")
        if missing:
            with st.expander(f"Нет специалистов · {len(missing)} ролей", expanded=True):
                detail_grid([(r["role"], f"{r['need_hh']:g} ч · {r['tasks']} {_tasks_word(r['tasks'])}")
                             for r in missing], tone="warning", compact=True)
                st.caption("Эти часы требуют найма или обучения. Остальная работа остаётся в плане с частичной целью.")
        if staffed:
            over_quarter = summary.get("roles_over_quarter") or []
            detail_grid([("За один спринт", "Красные полосы показывают потребность выше фонда роли."),
                         ("За квартал", "Фонда достаточно. Работу нужно выполнять по очереди." if not over_quarter
                          else "Фонда не хватает: " + ", ".join(over_quarter))])
            table = pd.DataFrame([{"Роль": r["role"], "Нужно, ч": r["need_hh"], "Фонд за спринт, ч": r["fund_sprint_hh"],
                                   "Специалистов": r["people"], "Загрузка, %": r["all_at_once_pct"]} for r in staffed])
            chart_table(_role_chart(staffed), table, key="role-load", column_config={
                "Загрузка, %": st.column_config.ProgressColumn(min_value=0, max_value=max(100, float(table["Загрузка, %"].max())), format="%.0f%%")})

    with st.container(border=True, key="soft-card-sprint-load"):
        st.subheader("Распределение нагрузки по спринтам")
        load = pd.DataFrame(overload.get("plan_load") or [])
        if load.empty:
            st.caption("Нет данных о нагрузке по спринтам.")
            return
        load["Спринт"] = "Спринт " + load["sprint"].astype(str)
        heat = alt.Chart(load).mark_rect(cornerRadius=5, stroke="white", strokeWidth=4).encode(
            x=alt.X("Спринт:N", title=None, sort=[f"Спринт {i}" for i in range(1, 7)],
                    axis=alt.Axis(labelAngle=0, orient="top", grid=False)),
            y=alt.Y("team_id:N", title=None, axis=alt.Axis(labelLimit=170, grid=False)),
            color=alt.Color("pct:Q", title="Загрузка, %", scale=alt.Scale(domain=[0, 100], range=["#EEF2FB", COLOR_PLAN]),
                            legend=alt.Legend(values=[0, 50, 100], gradientLength=120)),
            tooltip=[alt.Tooltip("team_id:N", title="Команда"), "Спринт:N",
                     alt.Tooltip("sp:Q", title="SP", format=".1f"), alt.Tooltip("pct:Q", title="Загрузка, %", format=".1f")])
        labels = heat.mark_text(fontSize=13, fontWeight=600).encode(
            text=alt.Text("pct:Q", format=".0f"),
            color=alt.condition(alt.datum.pct > 60, alt.value("white"), alt.value("#294DA6")))
        table = load.pivot(index="team_id", columns="Спринт", values="pct").reset_index().rename(columns={"team_id": "Команда"})
        chart_table(_polish((heat + labels).properties(height=alt.Step(46))), table, key="sprint-load",
                    column_config={name: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%")
                                   for name in table.columns if name != "Команда"})
        detail_grid([("Как читать", "Число в ячейке — процент ёмкости команды по Story Points. Более насыщенный синий означает большую загрузку."),
                     ("Свободная ёмкость", "Пустые ячейки — резерв на изменения. Работа отсутствующих ролей не занимает ёмкость команды, пока не найден специалист.")])
