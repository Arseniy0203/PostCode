# Схема Plan

Ответственный: Человек 1 (planning) / Человек 2 (recalculation)

## Plan

| Поле      | Тип                    | Описание                                                                 |
|-----------|--------------------------|---------------------------------------------------------------------|
| baseline  | list[PlannedTask]        | Неизменяемый снимок плана, зафиксированный на Неделе 0                |
| current   | list[PlannedTask]        | Обновляемый список PlannedTask, актуальный план на текущий момент     |

## Правило неизменности baseline

**`baseline` не изменяется при пересчёте плана.** Он формируется один раз функцией
`backend/recalculation/baseline.py::save_baseline` на Неделе 0 и в дальнейшем доступен
только на чтение.

Все KPI (`pi_predictability`, `say_do_ratio`, см. `backend/recalculation/kpi.py`)
считаются как сравнение `current` (или факта) против `baseline`, а не против
предыдущей версии `current`.

`current` обновляется функцией `backend/recalculation/recalc.py::apply_sprint_fact`
на каждом спринте, затрагивая только незавершённый хвост плана (Todo/InProgress),
не трогая задачи в статусе Done и не трогая `baseline`.
