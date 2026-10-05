"""Shared readable information blocks and reversible chart/table views."""

from html import escape
import re

import pandas as pd
import streamlit as st


def detail_grid(items, *, tone="neutral", compact=False):
    """Keep complete values, escaped as text, in small labelled panels."""
    tone = tone if tone in {"neutral", "success", "warning", "danger"} else "neutral"
    cells = []
    for title, value in items:
        if value is None or value == "":
            continue
        paragraphs = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z])", str(value))
        body = "".join(f"<p>{escape(part)}</p>" for part in paragraphs)
        cells.append(f'<section class="detail-cell"><span>{escape(str(title))}</span>{body}</section>')
    if cells:
        st.html(f'<div class="detail-grid detail-{tone}{" detail-compact" if compact else ""}">'
                + "".join(cells) + "</div>")


def styled_table(data, *, key=None, column_config=None):
    frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    st.dataframe(frame, hide_index=True, width="stretch", row_height=42,
                 column_config=column_config, key=key)


def chart_table(chart, data, *, key, column_config=None):
    """The switch remains outside the visualization, including its native data view."""
    with st.container(key=f"chart-panel-{key}"):
        mode = st.radio("Представление", ["График", "Таблица"], horizontal=True,
                        key=f"chart-mode-{key}", label_visibility="collapsed")
        if mode == "Таблица":
            styled_table(data, key=f"table-{key}", column_config=column_config)
        else:
            st.altair_chart(chart, width="stretch", theme=None, key=f"chart-{key}")
