from __future__ import annotations

from typing import Any

FOCUS_FACTOR = 0.8
SPRINT_HOURS = 80.0

def team_capacity_sp(team_id: str, team_history: list[dict[str, Any]]) -> float:
    team_records = [
        float(record.get("velocity_sp", record.get("velocity_achieved", 0)))
        for record in team_history
        if record.get("team_id") == team_id
    ]

    if not team_records:
        return 0.0

    avg_velocity = sum(team_records) / len(team_records)
    return float(avg_velocity * FOCUS_FACTOR)

def engineer_capacity_hh(
    engineer_id: str,
    sprint: int,
    engineers: list[dict[str, Any]],
) -> float:
    total_rate = sum(
        float(eng.get("capacity_rate", 0.0))
        for eng in engineers
        if eng.get("engineer_id") == engineer_id
    )
    
    return float(SPRINT_HOURS * total_rate)