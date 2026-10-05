"""Защита от неверных файлов и свёртка длинных перечислений в текстах."""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "frontend"))

from views.text import collapse_task_lists  # noqa: E402

APP = str(ROOT / "frontend" / "app.py")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MASTER = ROOT / "data/raw/Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx"
TEMPLATE = ROOT / "data/template/Шаблон_датасета.xlsx"
FACT_1 = ROOT / "demo_scenarios/Scenario_1_Ideal/Sprint_1_Fact.xlsx"


def test_collapse_keeps_short_lists():
    text = "Задачи: SRV-4011, SRV-4012."
    short, hidden = collapse_task_lists(text)
    assert short == text and hidden == []


def test_collapse_shortens_long_lists():
    tasks = [f"SRV-40{i:02d}" for i in range(10)]
    short, hidden = collapse_task_lists(f"В дефицит: {', '.join(tasks)}.")
    assert "и ещё 7" in short
    assert hidden == tasks
    assert len(short) < 60


@pytest.fixture(scope="module")
def loaded_app():
    app = AppTest.from_file(APP, default_timeout=120).run()
    app.file_uploader[0].set_value((MASTER.name, MASTER.read_bytes(), XLSX))
    app.run()
    return app


def test_master_dataset_loads(loaded_app):
    assert not loaded_app.exception
    assert "baseline" in loaded_app.session_state["calculated_plans"]


def test_master_file_in_sprint_slot_is_not_applied(loaded_app):
    """Мастер-датасет в слоте спринта — частая ошибка: форматы одинаковые."""
    app = loaded_app
    uploader = next(u for u in app.sidebar.file_uploader if "Спринта 1" in u.label)
    uploader.set_value((MASTER.name, MASTER.read_bytes(), XLSX))
    app.run()
    assert app.session_state.get("pending_fact"), "файл применился без подтверждения"
    assert 1 not in app.session_state["calculated_plans"]
    warning = " ".join(w.value for w in app.sidebar.warning)
    assert "мастер-датасет" in warning.lower()


def test_empty_template_gives_readable_error():
    app = AppTest.from_file(APP, default_timeout=120).run()
    app.file_uploader[0].set_value((TEMPLATE.name, TEMPLATE.read_bytes(), XLSX))
    app.run()
    assert not app.exception
    error = " ".join(e.value for e in app.error)
    assert "шаблон" in error.lower()


def test_fact_file_applies(loaded_app):
    app = loaded_app
    if "pending_fact" in app.session_state:
        del app.session_state["pending_fact"]
    uploader = next(u for u in app.sidebar.file_uploader if "Спринта 1" in u.label)
    uploader.set_value((FACT_1.name, FACT_1.read_bytes(), XLSX))
    app.run()
    assert not app.exception
    assert 1 in app.session_state["calculated_plans"]
