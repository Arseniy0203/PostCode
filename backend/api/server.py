# Ответственный: Человек 4 (frontend + tests + docs)
"""Тонкий API-слой (FastAPI) — на случай, если фронт будет ходить за данными
отдельно от Streamlit, а не использовать backend как библиотеку напрямую.

Заглушка. Роуты ниже не реализованы.
"""

from __future__ import annotations

# from fastapi import FastAPI
#
# app = FastAPI()


def get_plan() -> None:
    """GET /plan — возвращает текущий Plan (baseline + current)."""
    raise NotImplementedError


def get_alerts() -> None:
    """GET /alerts — возвращает список текущих Alert."""
    raise NotImplementedError


def get_starmap() -> None:
    """GET /starmap — возвращает профили команд/инженеров и Bus Factor."""
    raise NotImplementedError
