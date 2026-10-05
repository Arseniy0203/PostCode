"""Страница «Всё сразу или по плану»: перегрузка из подсказки организаторов и то,
как планировщик её разложил."""

from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

COLOR_OVER = "#B3261E"   # приглушённый красный — как у критических рисков
COLOR_PLAN = "#294DA6"   # синий из логотипа
COLOR_RULE = "#71717a"


def _tasks_word(n: int) -> str:
    return "задача" if n % 10 == 1 and n % 100 != 11 else (
        "задачи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "задач")


def _team_chart(teams: list[dict[str, Any]]) -> alt.Chart:
    rows = []
    for t in teams:
        rows.append({"Команда": t["team_id"], "Вариант": "Всё сразу в спринте 1",
                     "Загрузка, %": t["all_at_once_pct"] or 0,
                     "SP": f"{t['backlog_sp']:g} SP при ёмкости {t['capacity_sp']:g}"})
        rows.append({"Команда": t["team_id"], "Вариант": "План: самый загруженный спринт",
                     "Загрузка, %": t["plan_peak_pct"] or 0,
                     "SP": f"ёмкость {t['capacity_sp']:g} SP за спринт"})
    data = pd.DataFrame(rows)
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y("Команда:N", title=None, sort="-x", axis=alt.Axis(labelLimit=200)),
        yOffset=alt.YOffset("Вариант:N", sort=["Всё сразу в спринте 1"]),
        x=alt.X("Загрузка, %:Q", title="Загрузка ёмкости команды по SP, %"),
        color=alt.Color("Вариант:N", sort=["Всё сразу в спринте 1"],
                        scale=alt.Scale(range=[COLOR_OVER, COLOR_PLAN]),
                        legend=alt.Legend(orient="top", title=None, labelLimit=0)),
        tooltip=["Команда", "Вариант", "Загрузка, %", "SP"],
    )
    labels = bars.mark_text(align="left", dx=4, fontSize=11, color=COLOR_RULE).encode(
        text=alt.Text("Загрузка, %:Q", format=".0f"), color=alt.value(COLOR_RULE))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(
        strokeDash=[4, 3], color=COLOR_RULE).encode(x="x:Q")
    return (bars + labels + rule).properties(height=alt.Step(14))


def _role_chart(roles: list[dict[str, Any]]) -> alt.Chart:
    data = pd.DataFrame([{
        "Роль": r["role"],
        "Нужно от фонда спринта, %": r["all_at_once_pct"],
        "Детали": f"{r['need_hh']} ч бэклога · фонд {r['fund_sprint_hh']} ч/спринт · "
                  f"{r['people']} чел.",
        "Перегрузка": (r["all_at_once_pct"] or 0) > 100,
    } for r in roles])
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y("Роль:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
        x=alt.X("Нужно от фонда спринта, %:Q", title="Часы бэклога от фонда роли за спринт, %"),
        color=alt.condition(alt.datum["Перегрузка"], alt.value(COLOR_OVER), alt.value(COLOR_PLAN)),
        tooltip=["Роль", "Нужно от фонда спринта, %", "Детали"],
    )
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(
        strokeDash=[4, 3], color=COLOR_RULE).encode(x="x:Q")
    return (bars + rule).properties(height=alt.Step(20))


def render_overload_view(overload: dict[str, Any] | None) -> None:
    """Отрисовывает сравнение «всё сразу» с планом Недели 0.

    Args:
        overload: результат backend.recalculation.overload.overload_report.
    """
    st.header("Всё сразу или по плану")
    if not overload:
        st.info("Загрузите мастер-датасет заново, чтобы построить сравнение.")
        return
    summary = overload["summary"]
    worst = summary.get("max_team") or {}

    st.caption("Подсказка организаторов: датасет намеренно перегружен — если запустить все "
               "задачи одновременно, команды перегрузятся и по Story Points, и по часам. "
               "Ниже — насколько, и как план Недели 0 разложил ту же работу.")

    col1, col2, col3 = st.columns(3)
    col1.metric("Если запустить всё сразу", f"до {worst.get('all_at_once_pct', 0)}%",
                delta=f"{worst.get('team_id', '')}: {worst.get('backlog_sp', 0):g} SP "
                      f"при ёмкости {worst.get('capacity_sp', 0):g}",
                delta_color="inverse")
    col2.metric("План: пик загрузки спринта", f"{summary.get('plan_peak_pct', 0)}%",
                delta="ни одна команда не перегружена", delta_color="normal")
    strict = summary.get("strict_tasks")
    col3.metric("Задач в квартале по плану",
                f"{summary.get('plan_tasks', 0)} из {summary.get('open_tasks', 0)}",
                delta=(f"«нет роли — не берём»: {strict}" if strict is not None else None),
                delta_color="off",
                help="Строгое правило: задачу, для которой в компании нет хотя бы одной роли, "
                     "не берём в квартал вовсе. План вместо этого берёт работу доступных ролей "
                     "и понижает цель по справочнику «Варианты выбора цели».")

    st.subheader("Команды: Story Points")
    st.caption(f"Если стартовать все {summary.get('open_tasks', 0)} задач в спринте 1, "
               f"перегружены все {summary.get('teams_overloaded', 0)} команд. План растягивает "
               "крупные задачи по спринтам пропорционально часам и ставит их в очередь по "
               "скорингу и зависимостям — загрузка каждого спринта не выше ёмкости "
               "(пунктир — 100%).")
    st.altair_chart(_team_chart(overload["teams"]), width="stretch")

    st.subheader("Роли: часы")
    missing = [r for r in overload["roles"] if not r["fund_sprint_hh"]]
    staffed = [r for r in overload["roles"] if r["fund_sprint_hh"]]
    if missing:
        st.markdown("**Нет ни одного человека:** " + ", ".join(
            f"{r['role']} — {r['need_hh']} ч ({r['tasks']} {_tasks_word(r['tasks'])})"
            for r in missing))
        st.caption("Сколько ни раскладывай — эти часы в квартале не закрыть. План берёт работу "
                   "остальных ролей, а цель задачи понижает по справочнику «Варианты выбора "
                   "цели»; «нанять» и «обучить» — на звёздной карте.")
    if staffed:
        over_quarter = summary.get("roles_over_quarter") or []
        st.caption("Роли с людьми: при одновременном старте красные не справляются за спринт. "
                   + ("За квартал фонда хватает всем — поэтому работа идёт очередью, а не разом."
                      if not over_quarter else
                      "Даже за квартал не хватает фонда: " + ", ".join(over_quarter) + "."))
        st.altair_chart(_role_chart(staffed), width="stretch")

    with st.expander("Как план разложил нагрузку по спринтам"):
        load = pd.DataFrame(overload.get("plan_load") or [])
        if not load.empty:
            load["Спринт"] = "Спринт " + load["sprint"].astype(str)
            heat = alt.Chart(load).mark_rect().encode(
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
            text = heat.mark_text(fontSize=11).encode(
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
