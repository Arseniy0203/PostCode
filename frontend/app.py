# Ответственный: Человек 4 (frontend + tests + docs)
"""Точка входа Streamlit-приложения.

Навигация между 4 страницами через sidebar:
    1. План (frontend/views/plan_view.py::render_plan_view)
    2. Риски (frontend/views/risk_view.py::render_risk_view)
    3. Звёздная карта (frontend/views/star_map_view.py::render_star_map_view)
    4. (зарезервировано под доп. страницу, напр. загрузку датасета)
"""

from __future__ import annotations


def main() -> None:
    """Инициализирует Streamlit-страницу и sidebar-навигацию между views."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
