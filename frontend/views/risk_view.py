# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница списка алертов (рисков)."""

from __future__ import annotations

from typing import Any
import streamlit as st


def render_risk_view(alerts: list[Any]) -> None:
    """Отрисовывает список алертов с привязкой к PRODF-цели.

    Args:
        alerts: список Alert (см. contracts/alert_schema.md), каждый содержит
            prodf_id_at_risk для привязки к PRODF-цели.
    """
    st.header("Дашборд рисков и алертов")

    if not alerts:
        st.success("✅ Рисков не обнаружено! Все задачи идут по графику.")
        return

    red_alerts = [a for a in alerts if a.get("type") == "red"]
    yellow_alerts = [a for a in alerts if a.get("type") == "yellow"]
    orange_alerts = [a for a in alerts if a.get("type") == "orange"]

    affected_prodfs = len(set(a.get("prodf_id_at_risk") for a in red_alerts if a.get("prodf_id_at_risk")))

    col1, col2 = st.columns(2)
    col1.metric("Критичные риски (Red)", len(red_alerts))
    col2.metric("Затронутые PRODF-цели", affected_prodfs)

    st.subheader("Детализация алертов")
    tabs = st.tabs(["🔴 Критические (Red)", "🟡 Каскадные (Yellow)", "🟠 Ресурсные (Orange)"])

    def _render_alert_card(alert: dict[str, Any], render_func: Any) -> None:
        prodf = alert.get("prodf_id_at_risk", "Неизвестно")
        sprint = alert.get("sprint_detected", "?")
        task_id = alert.get("task_id", "Неизвестно")
        desc = alert.get("description", "Описание отсутствует")

        render_func(f"### 🎯 PRODF: {prodf}\n"
                    f"**Спринт обнаружения:** {sprint} | **Источник:** {task_id}\n\n"
                    f"{desc}")

    with tabs[0]:
        for a in red_alerts:
            _render_alert_card(a, st.error)

    with tabs[1]:
        for a in yellow_alerts:
            _render_alert_card(a, st.warning)

    with tabs[2]:
        for a in orange_alerts:
            _render_alert_card(a, st.info)
