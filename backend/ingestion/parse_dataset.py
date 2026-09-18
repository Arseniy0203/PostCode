# Ответственный: Человек 1 (ingestion + planning)
"""Парсинг единого Excel-датасета в набор доменных сущностей.

Исходный файл (см. data/raw/) содержит несколько листов, включая лист
"роль x задача", который необходимо транспонировать в плоский вид
задача-роль-часы.
"""

from __future__ import annotations

from typing import Any


def parse_excel(filepath: str) -> dict[str, Any]:
    """Парсит Excel-датасет в словарь из 6 сущностей.

    Ожидаемый формат возвращаемого словаря:
        {
            "tasks": list[dict],
                # Tasks — по одной записи на задачу, поля соответствуют
                # contracts/task_schema.md::Task (без estimated_hh —
                # оно добавляется отдельно из "estimates").
            "estimates": list[dict],
                # Estimates — результат транспонирования листа "роль x задача":
                # [{"task_id": ..., "role": ..., "hours": ...}, ...]
            "spent_time_roles": list[dict],
                # Spent_time_roles — факт затраченного времени по ролям:
                # [{"task_id": ..., "role": ..., "sprint_number": ..., "hours": ...}, ...]
            "task_dependencies": list[dict],
                # Task_dependencies — рёбра графа зависимостей:
                # [{"task_id": ..., "depends_on_task_id": ...}, ...]
            "engineers_profiles": list[dict],
                # Engineers_profiles — по одной записи на инженера:
                # [{"engineer_id": ..., "team_id": ..., "capacity_rate": ...,
                #   "skills_declared": [...]}, ...]
            "team_history": list[dict],
                # Team_history — исторические срезы по командам:
                # [{"team_id": ..., "sprint_number": ..., "velocity_sp": ...}, ...]
        }

    Args:
        filepath: путь к исходному Excel-файлу.

    Returns:
        Словарь с 6 ключами, описанными выше.
    """
    raise NotImplementedError


def filter_orphan_dependencies(
    deps: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Исключает из Task_dependencies записи, ссылающиеся на несуществующие задачи.

    "Осиротевшей" считается запись, у которой task_id или depends_on_task_id
    отсутствует среди task_id в tasks.

    Args:
        deps: список зависимостей (см. Task_dependencies в parse_excel).
        tasks: список задач (см. Tasks в parse_excel).

    Returns:
        Кортеж (clean_deps, excluded_log), где:
            clean_deps — зависимости, у которых обе стороны существуют в tasks;
            excluded_log — список исключённых записей с причиной исключения,
                напр. [{"dependency": {...}, "reason": "task_id not found"}, ...].
    """
    raise NotImplementedError
