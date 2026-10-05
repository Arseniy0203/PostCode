"""Quarter overview laid out like the supplied Soft UI Dashboard."""

from collections import defaultdict
from html import escape
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from backend.recalculation.plan_fields import actual_end_sprint, end_sprint, is_done, planned_end_sprint
from frontend.theme import asset_uri, navigate
from .risk_view import CATEGORY_LABELS


def completion_series(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Cumulative commitments, forecast and observed completions; future fact is absent."""
    baseline = [t for t in plan.get("baseline", []) if t.get("decision") == "included"]
    committed = {t.get("task_id") for t in baseline}
    current = [t for t in plan.get("current", []) if t.get("task_id") in committed]
    sprint = int(plan.get("current_sprint") or 0)
    rows = []
    for number in range(1, 7):
        values = {
            "Исходный план": sum(1 for t in baseline if (planned_end_sprint(t) or 99) <= number),
            "Текущий прогноз": sum(1 for t in current if t.get("decision") == "included"
                                   and (end_sprint(t) or 99) <= number),
        }
        if number <= sprint:
            values["Факт"] = sum(1 for t in current if is_done(t)
                                and (actual_end_sprint(t) or 99) <= number)
        rows.extend({"Спринт": number, "Задачи": count, "Срез": name} for name, count in values.items())
    return rows


def _completion_chart(plan: dict[str, Any]) -> alt.Chart:
    return (alt.Chart(pd.DataFrame(completion_series(plan)))
            .mark_line(strokeWidth=3, point=alt.OverlayMarkDef(size=40, filled=True))
            .encode(
                x=alt.X("Спринт:Q", axis=alt.Axis(values=list(range(1, 7)), labelExpr="'С' + datum.value", grid=False), title=None),
                y=alt.Y("Задачи:Q", title=None, axis=alt.Axis(tickMinStep=1, gridDash=[3, 3])),
                color=alt.Color("Срез:N", scale=alt.Scale(
                    domain=["Исходный план", "Текущий прогноз", "Факт"],
                    range=["#a1a1aa", "#f97316", "#27272a"]),
                    legend=alt.Legend(orient="bottom", title=None)),
                strokeDash=alt.StrokeDash("Срез:N", scale=alt.Scale(
                    domain=["Исходный план", "Текущий прогноз", "Факт"], range=[[5, 4], [1, 0], [1, 0]]), legend=None),
                tooltip=["Спринт:Q", "Срез:N", "Задачи:Q"],
            ).properties(height=240).configure_view(stroke=None)
            .configure_axis(labelColor="#71717a", domain=False, ticks=False, labelPadding=10, gridColor="#e3e3e7")
            .configure_legend(labelColor="#71717a", labelFontSize=11))


def _start_chart(tasks: list[dict[str, Any]]) -> alt.Chart:
    rows = [{"Спринт": f"С{i}", "Задачи": sum(t.get("planned_start_sprint") == i for t in tasks)}
            for i in range(1, 7)]
    return (alt.Chart(pd.DataFrame(rows)).mark_bar(color="white", size=9, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(x=alt.X("Спринт:N", title=None, axis=alt.Axis(labelAngle=0, grid=False)),
                    y=alt.Y("Задачи:Q", title=None, axis=alt.Axis(tickMinStep=1, tickCount=4)),
                    tooltip=["Спринт:N", "Задачи:Q"])
            .properties(height=170, background="#27272a",
                        padding={"left": 18, "right": 18, "top": 18, "bottom": 18})
            .configure_view(stroke=None)
            .configure_axis(labelColor="#e3e3e7", domain=False, ticks=False, grid=False, labelPadding=10))


def render_dashboard_view(plan: dict[str, Any], alerts: list[dict[str, Any]], starmap: dict[str, Any]) -> None:
    tasks = [t for t in plan.get("current", []) if t.get("decision") == "included"]
    teams = sorted({t.get("team_id") for t in tasks if t.get("team_id")})
    done = sum(is_done(t) for t in tasks)
    sprint = int(plan.get("current_sprint") or 0)

    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.html('<div class="soft-hero soft-feature"><div class="soft-hero-copy">'
                '<p class="soft-eyebrow">ОТ СТРАТЕГИИ К РЕЗУЛЬТАТУ</p><h2>Ваш квартал под контролем</h2>'
                f'<p>{len(tasks)} задач в плане, {len(teams)} команд и 6 спринтов. '
                'Все зависимости, ресурсы и решения — в одном месте.</p>'
                f'<span class="soft-pill">{"Неделя 0 · исходный план" if not sprint else f"Учтён факт {sprint} из 6 спринтов"}</span></div>'
                f'<div class="soft-rocket"><img src="{asset_uri("rocket-white.png")}" alt="" /></div></div>')
        st.button("Открыть квартальный план →", key="overview_plan", on_click=navigate,
                  args=("План (PI-цикл)",), type="primary")
    with right:
        st.html(f'<div class="soft-photo" style="background-image: linear-gradient(310deg, #18181be8, #27272a66), url({asset_uri("ivancik.jpg")})">'
                '<div><h2>Сильные команды.<br>Уверенные решения.</h2>'
                '<p>Узнайте, на ком держатся ключевые компетенции и какие роли нужны команде для достижения целей.</p></div>'
                f'<small>{len(starmap.get("engineers", []))} инженеров · карта компетенций и рекомендации</small></div>')
        st.button("Посмотреть звёздную карту →", key="overview_starmap", on_click=navigate,
                  args=("Звёздная карта",))

    left, right = st.columns([.9, 1.35], gap="large")
    with left, st.container(border=True, key="soft-card-starts"):
        st.altair_chart(_start_chart(tasks), width="stretch", theme=None)
        st.subheader("Старт задач по спринтам")
        st.caption("Распределение включённых задач в выбранном срезе.")
        st.html('<div class="soft-mini-stats">'
                f'<div><small>Команды</small><strong>{len(teams)}</strong></div>'
                f'<div><small>В плане</small><strong>{len(tasks)}</strong></div>'
                f'<div><small>Выполнено</small><strong>{done}</strong></div>'
                f'<div><small>Спринты</small><strong>{sprint} / 6</strong></div></div>')
    with right, st.container(border=True, key="soft-card-completion"):
        st.subheader("Выполнение квартального плана")
        st.caption("Завершение задач нарастающим итогом · исходные обязательства")
        st.altair_chart(_completion_chart(plan), width="stretch", theme=None)
        st.caption("Факт появится после загрузки спринтов." if not sprint
                   else f"Факт учтён по спринт {sprint}. Будущие спринты показаны только в прогнозе.")

    left, right = st.columns([1.4, 1], gap="large")
    with left, st.container(border=True, key="soft-card-teams"):
        st.subheader("Команды и задачи")
        st.caption(f"{len(teams)} команд работают над планом квартала")
        grouped = defaultdict(list)
        for task in tasks:
            grouped[task.get("team_id") or "Без команды"].append(task)
        rows = []
        for i, (team, items) in enumerate(sorted(grouped.items()), 1):
            completed = sum(is_done(t) for t in items)
            pct = round(completed / len(items) * 100)
            sp = sum(float(t.get("estimation_sp") or 0) for t in items)
            rows.append(f'<tr><td><span class="soft-team">{i:02}</span><strong>{escape(str(team))}</strong></td>'
                        f'<td>{len(items)}</td><td>{sp:g} SP</td><td>{pct}%'
                        f'<div class="soft-progress"><span style="width:{pct}%"></span></div></td></tr>')
        st.html('<div class="soft-table-wrap"><table class="soft-table"><thead><tr>'
                '<th scope="col">Команда</th><th scope="col">Задачи</th><th scope="col">Объём</th><th scope="col">Выполнено</th>'
                '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
        if not rows:
            st.caption("В выбранном срезе нет включённых задач.")
        st.button("Сравнить загрузку команд →", key="overview_load", on_click=navigate,
                  args=("Всё сразу или по плану",))
    with right, st.container(border=True, key="soft-card-risks"):
        active = [a for a in alerts if isinstance(a, dict) and a.get("status") != "resolved"]
        important = sorted(active, key=lambda a: {"red": 0, "yellow": 1, "orange": 2}.get(a.get("type"), 3))
        st.subheader("Требуют внимания")
        st.caption(f"{len(active)} активных уведомлений в выбранном срезе")
        rows = []
        for alert in important[:4]:
            title = CATEGORY_LABELS.get(alert.get("category"), "Наблюдение")
            subject = alert.get("role") or alert.get("task_id") or alert.get("prodf_id_at_risk") or "Весь план"
            description = str(alert.get("description") or "")
            if len(description) > 145:
                description = description[:142].rsplit(" ", 1)[0] + "…"
            detected = alert.get("sprint_detected") or 0
            moment = f"Спринт {detected}" if detected else "Неделя 0"
            tone = alert.get("type") if alert.get("type") in {"red", "yellow", "orange"} else "info"
            rows.append(f'<li data-tone="{tone}"><strong>{escape(title)} · {escape(str(subject))}</strong>'
                        f'<p>{escape(description)}</p><small>{escape(moment)}</small></li>')
        st.html('<ul class="soft-timeline">' + ''.join(rows) + '</ul>')
        if not active:
            st.success("Активных рисков нет. План идёт по графику.")
        st.button("Все риски и рекомендации →", key="overview_risks", on_click=navigate,
                  args=("Риски и Алерты",))
