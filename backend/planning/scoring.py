from __future__ import annotations
from typing import Any

def score_task(
    task: dict[str, Any],
    prodf_priority: dict[str, int],
    dependencies: list[dict[str, Any]],
) -> float:
    try:
        base_score = float(task.get("rung", 0))
    except (ValueError, TypeError):
        base_score = 0.0

    prodf_id = task.get("prodf_id", "")
    initiative_boost = prodf_priority.get(prodf_id, 0)

    task_id = task.get("task_id", "")
    blocking_count = sum(
        1 for dep in dependencies 
        if dep.get("blocking_task_id") == task_id
    )
    
    dependency_multiplier = 1.0 + (blocking_count * 0.5)

    final_score = (base_score + initiative_boost) * dependency_multiplier
    return float(final_score)