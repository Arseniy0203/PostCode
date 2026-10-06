"""Resource comparison with labelled cards and reversible chart/table views."""

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

COLOR_OVER = "#B3261E"   # приглушённый красный — как у критических рисков
COLOR_PLAN = "#294DA6"   # синий из логотипа
COLOR_RULE = "#71717a"
COLOR_INK = "#27272a"
COLOR_GRID = "#e3e3e7"
COLOR_SURFACE = "#ffffff"
BAR_RADIUS = 7           # скруглённый конец столбца — как у карточек интерфейса
LABEL_FONT = 11


def _style(chart: alt.Chart) -> alt.Chart:
    """Общий вид графиков: без рамки, тонкая сетка, подписи цветом текста."""
    return (chart
            .configure_view(stroke=None, fill=COLOR_SURFACE)
            .configure_axis(domain=False, ticks=False, grid=False, labelColor=COLOR_INK,
                            titleColor=COLOR_RULE, labelFontSize=11, titleFontSize=11,
                            titlePadding=12, labelPadding=6)
            .configure_axisX(grid=True, gridColor=COLOR_GRID, gridDash=[2, 3])
            .configure_legend(labelColor=COLOR_INK, labelFontSize=11, symbolType="circle",
                              symbolSize=90)
            .configure_text(font="sans-serif"))


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
    order = ["Всё сразу в спринте 1", "План: самый загруженный спринт"]
    bars = alt.Chart(data).mark_bar(
        cornerRadiusEnd=BAR_RADIUS, height=16, stroke=COLOR_SURFACE, strokeWidth=2,
    ).encode(
        y=alt.Y("Команда:N", title=None, sort="-x", axis=alt.Axis(labelLimit=200)),
        yOffset=alt.YOffset("Вариант:N", sort=order),
        x=alt.X("Загрузка, %:Q", title="Загрузка ёмкости команды по SP, %",
                axis=alt.Axis(format="d", values=[0, 100, 200, 400, 600])),
        color=alt.Color("Вариант:N", sort=order,
                        scale=alt.Scale(domain=order, range=[COLOR_OVER, COLOR_PLAN]),
                        legend=alt.Legend(orient="top", title=None, labelLimit=0, offset=4)),
        tooltip=["Команда", "Вариант", "Загрузка, %", "SP"],
    )
    # Значение у конца столбца: контраст оранжевого к белому ниже 3:1,
    # поэтому величина читается цифрой, а не только длиной
    labels = bars.mark_text(align="left", dx=6, fontSize=LABEL_FONT, color=COLOR_INK).encode(
        text=alt.Text("Загрузка, %:Q", format=".0f"), color=alt.value(COLOR_INK))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(
        strokeDash=[4, 4], color=COLOR_RULE, strokeWidth=1).encode(x="x:Q")
    # Подпись к пунктиру не ставим: она попадает в середину графика. Что такое
    # 100%, сказано в тексте над графиком.
    return _style((bars + labels + rule).properties(height=alt.Step(44)))


def _role_chart(roles: list[dict[str, Any]]) -> alt.Chart:
    data = pd.DataFrame([{
        "Роль": r["role"],
        "Нужно от фонда спринта, %": r["all_at_once_pct"],
        "Детали": f"{r['need_hh']} ч бэклога · фонд {r['fund_sprint_hh']} ч/спринт · "
                  f"{r['people']} чел.",
        "Перегрузка": (r["all_at_once_pct"] or 0) > 100,
    } for r in roles])
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=BAR_RADIUS, height=16).encode(
        y=alt.Y("Роль:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
        x=alt.X("Нужно от фонда спринта, %:Q", title="Часы бэклога от фонда роли за спринт, %",
                axis=alt.Axis(format="d")),
        color=alt.condition(alt.datum["Перегрузка"], alt.value(COLOR_OVER), alt.value(COLOR_PLAN)),
        tooltip=["Роль", "Нужно от фонда спринта, %", "Детали"],
    )
    labels = bars.mark_text(align="left", dx=6, fontSize=LABEL_FONT, color=COLOR_INK).encode(
        text=alt.Text("Нужно от фонда спринта, %:Q", format=".0f"), color=alt.value(COLOR_INK))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(
        strokeDash=[4, 4], color=COLOR_RULE, strokeWidth=1).encode(x="x:Q")
    return _style((bars + labels + rule).properties(height=alt.Step(26)))


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
        if not load.empty:
            load["Спринт"] = "Спринт " + load["sprint"].astype(str)
            heat = alt.Chart(load).mark_rect(
                cornerRadius=6, stroke=COLOR_SURFACE, strokeWidth=3,
            ).encode(
                x=alt.X("Спринт:N", title=None, sort=[f"Спринт {i}" for i in range(1, 7)],
                        axis=alt.Axis(labelAngle=0, orient="top")),
                y=alt.Y("team_id:N", title=None, axis=alt.Axis(labelLimit=200)),
                # Длина шкалы по умолчанию (200px) выше самой карты — подпись «0» обрезалась
                color=alt.Color("pct:Q", title="Загрузка, %",
                                scale=alt.Scale(domain=[0, 100], range=["#EEF2FB", COLOR_PLAN]),
                                legend=alt.Legend(values=[0, 50, 100], gradientLength=120)),
                tooltip=[alt.Tooltip("team_id:N", title="Команда"), "Спринт",
                         alt.Tooltip("sp:Q", title="SP"), alt.Tooltip("pct:Q", title="Загрузка, %")],
            )
            text = heat.mark_text(fontSize=LABEL_FONT).encode(
                text=alt.Text("pct:Q", format=".0f"),
                color=alt.condition(alt.datum.pct > 60, alt.value("#FFFFFF"), alt.value("#1E293B")))
            st.altair_chart((heat + text).properties(height=alt.Step(30)), width="stretch")
            st.caption("Процент ёмкости команды по SP в каждом спринте плана Недели 0.")
            idle = sorted(load.loc[(load["sprint"] == load["sprint"].max()) & (load["pct"] == 0),
                                   "team_id"].unique())
            if idle:
                st.caption(
                    f"Пустой последний спринт у {', '.join(idle)} — не простой, а резерв: вся "
                    "работа, которую могут сделать имеющиеся люди, уже разложена в спринтах "
                    "раньше. Остаток — часы ролей, которых нет в компании (руководитель проекта, "
                    "поддержка, 1С): их не закрыть, пока роль не наняли. Свободная ёмкость — "
                    "запас на срывы сроков, новые задачи от заказчика или работу нанятого "
                    "специалиста.")
