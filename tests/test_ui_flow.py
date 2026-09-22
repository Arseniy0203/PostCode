"""Сквозной сценарий интерфейса через streamlit AppTest: загрузка мастер-датасета,
фактов спринтов, замена и удаление спринта, все страницы и карточки задач.

Ловит то, что не видно по бэкенд-тестам: бесконечный пересчёт при повторной
загрузке файла, пропажу последующих спринтов, падения страниц.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("streamlit_agraph")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "data" / "raw" / "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx"
SCENARIO = ROOT / "demo_scenarios" / "Scenario_3_Canceled_Task"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload(at: AppTest, label_part: str, path: Path) -> None:
    uploader = next(u for u in at.file_uploader if label_part in u.label)
    uploader.set_value((path.name, path.read_bytes(), XLSX))
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _sprints(at: AppTest) -> list[int]:
    return sorted(k for k in at.session_state["calculated_plans"] if isinstance(k, int))


def _click(at: AppTest, key: str) -> None:
    next(b for b in at.button if b.key == key).click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.skipif(not MASTER.exists() or not SCENARIO.exists(), reason="нет данных")
def test_upload_replace_delete_and_all_pages() -> None:
    at = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=300)
    at.run()
    _upload(at, "Master", MASTER)

    for sprint in (1, 2):
        _upload(at, f"Спринта {sprint}", SCENARIO / f"Sprint_{sprint}_Fact.xlsx")
        at.run()  # тот же файл в загрузчике не обрабатывается повторно
        assert _sprints(at) == list(range(1, sprint + 1))

    # Замена спринта 1 пересчитывает спринт 2 по сохранённому файлу, а не стирает его
    _click(at, "replace_btn_1")
    _upload(at, "Новый файл факта спринта 1", SCENARIO / "Sprint_1_Fact.xlsx")
    assert _sprints(at) == [1, 2]
    assert "пересчитаны заново" in " ".join(s.value for s in at.sidebar.success)

    # Файл другого спринта не применяется без подтверждения
    _click(at, "replace_btn_2")
    _upload(at, "Новый файл факта спринта 2", SCENARIO / "Sprint_3_Fact.xlsx")
    assert at.session_state["pending_fact"][0] == 2
    _click(at, "pending_no")
    assert "pending_fact" not in at.session_state and _sprints(at) == [1, 2]

    # Удаление спринта 2 возвращает загрузчик спринта 2
    _click(at, "delete_btn_2")
    assert _sprints(at) == [1]
    assert any("Спринта 2" in u.label for u in at.file_uploader)

    for page in ("План (PI-цикл)", "Всё сразу или по плану", "Риски и Алерты", "Звёздная карта"):
        at.sidebar.selectbox[0].set_value(page)
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])

    at.sidebar.selectbox[0].set_value("План (PI-цикл)")
    at.run()
    # Карточка раскрывается прямо в сетке спринтов: у каждой задачи свой блок,
    # искать её в отдельном списке не нужно.
    labels = [e.label for e in at.expander]
    for task_id in ("SRV-4043", "AI-303", "SRV-4042", "ONK-2475"):
        assert any(label.startswith(task_id) for label in labels), (task_id, labels[:5])
    text = " ".join(str(m.value) for m in at.markdown)
    assert "POSTPONED" not in text and "INCLUDED" not in text
