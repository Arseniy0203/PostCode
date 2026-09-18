# Ответственный: Человек 1 (ingestion + planning)
"""Скоринг задач для приоритизации при дефиците ресурсов.

Итоговая формула скоринга должна быть задокументирована в README.md
(раздел "Допущения -> Правила приоритизации") при реализации.
"""

from __future__ import annotations

from typing import Any


def score_task(
    task: dict[str, Any],
    prodf_priority: dict[str, int],
    dependencies: list[dict[str, Any]],
) -> float:
    """Считает приоритетный балл задачи для планировщика.

    Заглушка. Ожидается, что итоговый скор комбинирует приоритет PRODF-цели,
    к которой относится задача, и структуру зависимостей (напр. задачи,
    блокирующие другие, получают более высокий приоритет).

    Args:
        task: задача (см. contracts/task_schema.md::Task).
        prodf_priority: словарь prodf_id -> числовой приоритет цели.
        dependencies: список зависимостей (Task_dependencies).

    Returns:
        Числовой балл приоритета задачи (чем выше — тем приоритетнее).
    """
    raise NotImplementedError
