"""Совместимость: KPI перенесены в backend/kpi/metrics.py."""

from backend.kpi.metrics import kpi_breakdown, pi_predictability, say_do_ratio

__all__ = ["kpi_breakdown", "pi_predictability", "say_do_ratio"]
