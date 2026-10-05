"""
Точка входа Streamlit-приложения сервиса PI-планирования.

Навигация между страницами через sidebar, поддержка режимов Baseline/Current
и безопасная загрузка данных с пересчетом факта.
"""

from __future__ import annotations

import os
import re
import sys
import copy
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st

from backend.starmap.bus_factor import starmap_summary
from backend.recalculation.recalc import apply_sprint_fact
from backend.kpi.metrics import kpi_breakdown
from backend.recalculation.pipeline import (
    dataset_from_parsed,
    fact_from_dataset_excel,
    week0_from_excel,
)

from frontend.theme import (PAGES, PAGE_LABELS, inject_custom_css, metric_card,
                   render_brand, render_header, render_welcome, render_footer, render_title, navigate)
from frontend.demo import DEMO_SCENARIOS, demo_snapshots
from views.kpi_view import render_kpi_view
from views.dashboard_view import render_dashboard_view
from views.plan_view import render_plan_view
from views.risk_view import render_risk_view
from views.star_map_view import render_star_map_view
from views.overload_view import render_overload_view


APP_TITLE = os.getenv("APP_TITLE", "ПочтаТех: PI-Планирование")


def section_label(text: str) -> None:
    """Надзаголовок раздела мелкими прописными — вместо эмодзи-иконок."""
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


@st.cache_data(show_spinner="Сборка Baseline и проверка рисков...")
def process_baseline(file_bytes: bytes) -> dict[str, Any]:
    """Парсит исходный датасет и строит Baseline тем же конвейером, что и пересчёт.

    Задачи в работе планируются на остаток (смета − Spent_time_roles), ёмкость
    команды — средний velocity × 0.8 (backend.recalculation.pipeline.week0_from_excel).
    """
    state = week0_from_excel(file_bytes)
    result = {key: state[key] for key in ("parsed", "clean_deps", "plan", "alerts", "starmap")}
    result["data_quality"] = list((state.get("report") or {}).get("data_quality") or [])
    result["overload"] = state.get("overload")
    return result


@st.cache_data(show_spinner="Применение факта и пересчет текущего плана...")
def process_fact(prev_plan: dict[str, Any], master_deps: list[dict[str, Any]], master_engineers: list[dict[str, Any]],
                 master_estimates: list[dict[str, Any]], master_history: list[dict[str, Any]],
                 fact_file_bytes: bytes) -> dict[str, Any]:
    """Обрабатывает факт, обновляет Plan.current на основе предыдущего плана и пересчитывает риски.

    Файл факта — датасет с НАКОПЛЕННЫМИ часами (как в demo_scenarios): уже учтённое
    вычитается, в пересчёт идут часы именно за этот спринт.
    """
    fact = fact_from_dataset_excel(fact_file_bytes, prev_plan)
    capacity = prev_plan.get("capacity") or dataset_from_parsed(
        {"team_history": master_history, "engineers_profiles": master_engineers})["capacity"]
    updated_plan = apply_sprint_fact(
        plan=copy.deepcopy(prev_plan),
        fact=fact,
        dependencies=master_deps,
        engineers=master_engineers,
        capacity=capacity,
    )
    return {
        "plan": updated_plan,
        "alerts": [a.to_dict() for a in updated_plan.get("alerts") or []],
        "starmap": starmap_summary(updated_plan, master_engineers, master_estimates),
    }


def init_session_state() -> None:
    """Инициализирует State Machine для 6 спринтов."""
    if "calculated_plans" not in st.session_state:
        st.session_state.calculated_plans = {}  # Ключи: "baseline", 1, 2, ..., 6
        st.session_state.master_dataset = None  # Сырые данные Недели 0
    st.session_state.setdefault("demo_mode", False)
    st.session_state.setdefault("fact_files", {})     # спринт → (имя файла, байты)
    st.session_state.setdefault("uploader_nonce", 0)  # новый ключ = пустой загрузчик


def _reset_uploaders() -> None:
    """Streamlit хранит файл в загрузчике между перезапусками: без смены ключа
    тот же файл обрабатывался бы снова и снова (бесконечный пересчёт)."""
    st.session_state.uploader_nonce = st.session_state.get("uploader_nonce", 0) + 1


def _recalculate_from(sprint: int, master: dict[str, Any]) -> tuple[int, str | None]:
    """Пересчитывает спринты начиная с sprint по сохранённым файлам факта.

    Returns:
        (последний успешно пересчитанный спринт, текст ошибки или None).
    """
    plans = st.session_state.calculated_plans
    files = st.session_state.fact_files
    for j in range(sprint, 7):
        plans.pop(j, None)
    last = sprint - 1
    for j in range(sprint, 7):
        if j not in files:
            break
        prev_plan = plans["baseline"]["plan"] if j == 1 else plans[j - 1]["plan"]
        try:
            plans[j] = process_fact(prev_plan, master["clean_deps"],
                                    master["parsed"]["engineers_profiles"], master["parsed"]["estimates"],
                                    master["parsed"]["team_history"], files[j][1])
        except Exception as exc:  # noqa: BLE001 — показываем пользователю, не роняем страницу
            name = files[j][0]
            for k in range(j, 7):
                files.pop(k, None)
            return last, f"Спринт {j} ({name}): {exc}"
        last = j
    return last, None


def _file_sprint_mismatch(name: str, sprint: int) -> str | None:
    """Предупреждение, если в имени файла явно другой номер спринта (Sprint_3 в слот 2)."""
    match = re.search(r"(?:sprint|спринт)[ _-]*(\d+)", name, flags=re.IGNORECASE)
    if match and int(match.group(1)) != sprint:
        return (f"Похоже, это файл спринта {match.group(1)}, а загружается спринт {sprint}. "
                "Проверьте файл.")
    return None


def _accept_fact(sprint: int, uploaded: Any, master: dict[str, Any], confirmed: bool = False) -> None:
    """Сохраняет файл спринта и пересчитывает его и все следующие загруженные спринты.

    Файл с другим номером спринта в имени не применяется без подтверждения.
    """
    name, data = (uploaded if isinstance(uploaded, tuple) else (uploaded.name, uploaded.getvalue()))
    if not confirmed and _file_sprint_mismatch(name, sprint):
        st.session_state.pending_fact = (sprint, name, data)
        _reset_uploaders()
        st.rerun()
    st.session_state.pop("pending_fact", None)
    st.session_state.fact_files[sprint] = (name, data)
    warning = None
    later = [j for j in st.session_state.fact_files if j > sprint]
    last, error = _recalculate_from(sprint, master)
    if error:
        message = ("error", f"Не удалось пересчитать план. {error}")
    elif later and last > sprint:
        message = ("success", f"Спринт {sprint} заменён; спринты {sprint + 1}–{last} "
                              "пересчитаны заново по уже загруженным файлам.")
    else:
        message = ("success", f"Факт спринта {sprint} учтён, план пересчитан.")
    st.session_state["snapshot"] = last if last > 0 else "baseline"
    st.session_state.fact_message = message
    st.session_state.pop(f"replace_{sprint}", None)
    _reset_uploaders()
    st.rerun()


def render_fact_uploads(master: dict[str, Any]) -> None:
    """Сайдбар: загруженные спринты с кнопками «Заменить» / «Удалить» и загрузчик следующего."""
    plans = st.session_state.calculated_plans
    files = st.session_state.fact_files
    nonce = st.session_state.uploader_nonce

    message = st.session_state.pop("fact_message", None)
    if message:
        {"error": st.sidebar.error, "warning": st.sidebar.warning}.get(
            message[0], st.sidebar.success)(message[1])

    pending = st.session_state.get("pending_fact")
    if pending:
        sprint, name, data = pending
        st.sidebar.warning(f"{_file_sprint_mismatch(name, sprint)} Файл «{name}» пока не применён.")
        col_yes, col_no = st.sidebar.columns(2)
        if col_yes.button("Всё равно загрузить", key="pending_yes", width="stretch"):
            _accept_fact(sprint, (name, data), master, confirmed=True)
        if col_no.button("Отмена", key="pending_no", width="stretch"):
            st.session_state.pop("pending_fact", None)
            st.rerun()

    for i in range(1, 7):
        if i in plans:
            name = files.get(i, ("файл загружен",))[0]
            with st.sidebar.expander(f"Спринт {i} · факт загружен", expanded=False):
                st.caption(name)
                col_replace, col_delete = st.columns(2)
                if col_replace.button("Заменить", key=f"replace_btn_{i}", width="stretch",
                                      help="Загрузить другой файл факта за этот спринт"):
                    st.session_state[f"replace_{i}"] = not st.session_state.get(f"replace_{i}", False)
                    st.rerun()
                if col_delete.button("Удалить", key=f"delete_btn_{i}", width="stretch",
                                     help="Убрать факт этого спринта и всех следующих"):
                    for j in range(i, 7):
                        plans.pop(j, None)
                        files.pop(j, None)
                    last_text = f"спринта {i}" if i == 6 else f"спринтов {i}–6"
                    st.session_state.fact_message = (
                        "success", f"Факт {last_text} удалён. Загрузите спринт {i} заново.")
                    _reset_uploaders()
                    st.rerun()
                if st.session_state.get(f"replace_{i}"):
                    later = [j for j in plans if isinstance(j, int) and j > i]
                    if later:
                        st.caption(f"Спринты {i + 1}–{max(later)} пересчитаются заново "
                                   "по уже загруженным файлам.")
                    new_fact = st.file_uploader(f"Новый файл факта спринта {i}", type=["xlsx"],
                                                key=f"reupload_fact_{i}_{nonce}")
                    if new_fact:
                        _accept_fact(i, new_fact, master)
        else:
            # Разблокируем только следующий по порядку спринт
            fact_file = st.sidebar.file_uploader(
                f"Загрузить факт Спринта {i}",
                type=["xlsx"],
                key=f"upload_fact_{i}_{nonce}",
                help="Файл в формате датасета с накопленными часами — как в demo_scenarios/.",
            )
            if fact_file:
                _accept_fact(i, fact_file, master)
            break


def render_kpi_widgets(baseline_tasks: list[dict[str, Any]], current_tasks: list[dict[str, Any]],
                       current_sprint: int = 0) -> None:
    """Отрисовывает виджеты KPI в шапке приложения.

    current_sprint — последний спринт с загруженным фактом (0 — Неделя 0): Say/Do
    показывается за него, PI до конца квартала — прогнозом по текущему плану.
    """
    if not baseline_tasks:
        return
    current_sprint = int(current_sprint or 0)
    metrics = kpi_breakdown(baseline_tasks, current_tasks, current_sprint=current_sprint)
    quarter_over = current_sprint >= 6
    pi = metrics["pi_predictability"]["fact" if quarter_over else "forecast"]
    pi_value = pi["value"]
    pi_norm = metrics["norms"]["pi_predictability"]
    latest = next((row for row in metrics["say_do"] if row["sprint"] == current_sprint), None)
    included = [t for t in current_tasks if t.get("decision") == "included"]
    teams = {t.get("team_id") for t in included if t.get("team_id")}
    done = sum(t.get("status") == "Done" for t in included)
    cols = st.columns(4)
    with cols[0]:
        metric_card("Задачи в квартале", str(len(included)), f"{done} выполнено из {len(included)}", "plan", page="План (PI-цикл)")
    with cols[1]:
        metric_card("PI Predictability" + ("" if quarter_over else " · прогноз"),
                    "—" if pi.get("status") == "no_data" else f"{pi_value:.1f}%",
                    f"Норма {pi_norm[0]:g}–{pi_norm[1]:g}%", "chart",
                    "success" if pi_norm[0] <= pi_value <= pi_norm[1] else "warning",
                    page="Показатели", section="PI Predictability")
    with cols[2]:
        has_fact = latest is not None and latest.get("status") not in ("future", "no_data")
        metric_card("Say/Do Ratio", f"{latest['value']:.1f}%" if has_fact else "—",
                    f"Спринт {current_sprint} · норма 90–105%" if has_fact else "Ожидаем факт спринта", "check",
                    ("success" if latest["status"] == "ok" else "warning") if has_fact else "muted",
                    page="Показатели", section="Say/Do")
    with cols[3]:
        metric_card("Команды в плане", str(len(teams)), f"Учтено {current_sprint} из 6 спринтов", "people", page="Всё сразу или по плану")


def reset_workspace() -> None:
    """Drop session-only data and widget choices before rendering a fresh title page."""
    for key in list(st.session_state):
        del st.session_state[key]
    init_session_state()
    st.session_state["next_page"] = "Титульная"


def start_demo(scenario: str = "on_track") -> None:
    master, snapshots = demo_snapshots(scenario)
    st.session_state.update(master_dataset=master, calculated_plans=snapshots,
                            demo_mode=True, demo_loaded_scenario=scenario, fact_files={},
                            master_name="Демонстрационный датасет", snapshot="baseline",
                            next_page="Обзор")


def main() -> None:
    """Shared navigation for title, demo and uploaded datasets."""
    st.set_page_config(page_title=APP_TITLE, page_icon="◈", layout="wide")
    inject_custom_css()
    init_session_state()
    ready = "baseline" in st.session_state.calculated_plans
    if "next_page" in st.session_state:
        st.session_state["navigation"] = st.session_state.pop("next_page")
    if not ready:
        st.session_state["navigation"] = "Титульная"

    with st.sidebar:
        render_brand()
        section_label("Рабочее пространство")
        with st.container(key="sidebar-nav"):
            page = st.radio("Раздел", options=PAGES, format_func=PAGE_LABELS.get,
                            key="navigation", label_visibility="collapsed", disabled=not ready,
                            width="stretch")
        if not ready:
            st.caption("Разделы откроются после загрузки датасета или запуска демо.")

    if not ready:
        render_header("Титульная", "Начало работы")
        uploaded_file = render_welcome()
        if st.button("Открыть пример на демо-данных", key="load_demo", type="primary"):
            try:
                start_demo()
                st.rerun()
            except Exception as exc:
                st.error(f"Не удалось подготовить демо: {exc}")
        if uploaded_file:
            try:
                base_state = process_baseline(uploaded_file.getvalue())
                st.session_state.update(
                    master_dataset=base_state, master_name=uploaded_file.name,
                    fact_files={}, demo_mode=False, snapshot="baseline", next_page="Обзор",
                    calculated_plans={"baseline": {key: base_state[key] for key in ("plan", "alerts", "starmap")}},
                )
                st.rerun()
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) and str(exc) else "файл не похож на датасет в формате организаторов."
                st.error(f"Не удалось прочитать мастер-датасет: {reason}")
                with st.expander("Технические детали"):
                    st.code(f"{type(exc).__name__}: {exc}")
        render_footer()
        return

    demo = st.session_state.demo_mode
    with st.sidebar:
        section_label("Демо-режим" if demo else "Данные квартала")
    if demo:
        scenario = st.sidebar.selectbox("Демо-сценарий", list(DEMO_SCENARIOS),
                                        format_func=DEMO_SCENARIOS.get, key="demo_scenario")
        if scenario != st.session_state.demo_loaded_scenario:
            try:
                master, snapshots = demo_snapshots(scenario)
                st.session_state.update(master_dataset=master, calculated_plans=snapshots,
                                        demo_loaded_scenario=scenario)
            except Exception as exc:
                st.error(f"Не удалось загрузить сценарий: {exc}")
                return
        st.sidebar.caption("Неделя 0 и все 6 спринтов готовы. Выберите срез ниже. Данные демонстрационные.")
        st.sidebar.button("Выйти из демо", key="exit_demo", on_click=reset_workspace, width="stretch")
    else:
        with st.sidebar.expander("Мастер-датасет · Неделя 0", expanded=False):
            st.caption(st.session_state.get("master_name") or "Датасет загружен.")
            st.button("Сбросить весь план и начать заново", key="reset_plan",
                      on_click=reset_workspace, width="stretch")
        render_fact_uploads(st.session_state.master_dataset)

    with st.sidebar:
        section_label("Срез данных")
    options = {"baseline": "Снимок (Baseline Неделя 0)"}
    options.update({i: f"После Спринта {i}" for i in range(1, 7) if i in st.session_state.calculated_plans})
    if st.session_state.get("snapshot") not in options:
        st.session_state["snapshot"] = list(options)[-1]
    selected_key = st.sidebar.radio("Текущий срез данных", list(options),
                                    format_func=options.get, key="snapshot")
    selected_mode_label = options[selected_key]
    active_state = st.session_state.calculated_plans[selected_key]
    plan_dict, alerts, starmap = (active_state[k] for k in ("plan", "alerts", "starmap"))
    render_header(page, ("Демо · " if demo else "") + selected_mode_label)
    if page == "Титульная":
        render_title(ready=True, demo=demo)
        render_footer()
        return
    if page != "Обзор":
        st.button("← Вернуться к обзору", key="back_to_overview", on_click=navigate, args=("Обзор",))
    render_kpi_widgets(plan_dict.get("baseline", []), plan_dict.get("current", []),
                       plan_dict.get("current_sprint", 0))
    if page == "Обзор":
        render_dashboard_view(plan_dict, alerts, starmap)
    elif page == "Показатели":
        render_kpi_view(plan_dict)
    elif page == "План (PI-цикл)":
        render_plan_view(plan_dict, selected_mode_label, alerts, starmap)
    elif page == "Всё сразу или по плану":
        render_overload_view((st.session_state.master_dataset or {}).get("overload"))
    elif page == "Риски и Алерты":
        render_risk_view(alerts, plan_dict.get("resolved_alerts") or [],
                         (st.session_state.master_dataset or {}).get("data_quality") or [])
    elif page == "Звёздная карта":
        render_star_map_view(starmap)
    render_footer()


if __name__ == "__main__":
    main()
