# Схема звёздной карты (EngineerCompetency)

Реализация: `backend/starmap/bus_factor.py::starmap_summary`

## EngineerCompetency

| Поле             | Тип       | Описание                                                        |
|------------------|-----------|-----------------------------------------------------------------|
| competency       | str       | Роль («Разработчик .net») или технология («C#»)                 |
| engineers_count  | int       | Число инженеров-носителей (уникальных, а не строк таблицы)      |
| bus_factor       | int       | То же число; 1 — единственный носитель, 0 — носителей нет       |
| is_risk          | bool      | `bus_factor <= 1`                                                |
| linked_task_ids  | list[str] | Задачи текущего плана, зависящие от этой компетенции            |
| engineer_ids     | list[str] | Носители компетенции                                             |

## Что возвращает `starmap_summary(plan, engineers, estimates)`

| Ключ                      | Тип                          | Для чего                                    |
|---------------------------|------------------------------|---------------------------------------------|
| competencies              | list[EngineerCompetency]     | Таблица карты                               |
| engineers                 | list[профиль]                | Профили инженеров (фронт ищет тут носителей)|
| bus_factors               | dict[str, int]               | Компетенция -> Bus Factor                   |
| linked_tasks              | dict[str, list[задача]]      | Компетенция-одиночка -> задачи под риском   |
| single_points_of_failure  | list[str]                    | Компетенции с Bus Factor = 1                |
| missing_competencies      | list[str]                    | Bus Factor = 0: план требует, носителей нет |
| absence_impact            | dict[engineer_id, влияние]   | Что встанет без конкретного инженера        |
| engineer_profiles         | dict[engineer_id, профиль]   | Карточка инженера                           |
| team_profiles             | dict[team_id, профиль]       | Состав команды, роли, ёмкость в часах       |
| method                    | str                          | Описание способа расчёта (требование ТЗ)    |

Ключи `engineers`, `bus_factors` и `linked_tasks` совпадают с тем, что ожидает
`frontend/views/star_map_view.py`.

## Профиль инженера

`engineer_id`, `role`, `grade`, `teams` {команда: ставка}, `capacity_rate`
(сумма ставок), `hours_per_sprint`, `hours_by_team`, `is_shared`,
`skills_declared` (канонические названия технологий).

Инженер с частичной ставкой записан в датасете несколькими строками
(ENG-405, ENG-406, ENG-419, ENG-426). В профиле он один: Bus Factor считает
людей, а не строки.

## Задача под риском (элемент linked_tasks)

`task_id`, `prodf_id`, `team_id`, `role`, `hours`, `planned_start_sprint`,
`planned_end_sprint`, `owner_engineer_ids`.

## Способ расчёта Bus Factor

Компетенции берутся на двух уровнях:

- **роль** — из сметы задач (Estimates). Только через роль задача связывается
  с людьми: технологий в задачах нет;
- **технология** — из `skills_declared` профиля инженера.

Названия приводятся к единому виду: «Девопс» = «ДевОпс», «Разработчик IOS» =
«Разработчик iOS», «Kafka» = «Apache Kafka». Строка `CI/CD (GitLab, Jenkins)`
разбирается как одна технология, а не как две.

В карту попадают только компетенции, нужные незавершённым задачам плана:
роли из сметы и их ключевой стек (`ROLE_CORE_TECH` в `competencies.py`).
Иначе карта превращается в 117 технологий, из которых 87 имеют единственного
носителя, включая Jira и TOGAF.

## Пример вызова

```python
from backend.starmap.bus_factor import starmap_summary

data = starmap_summary(plan, engineers_profiles, estimates)
render_star_map_view(data)
```

## Дополнительные ключи `starmap_summary`

| Ключ                   | Для чего                                                               |
|------------------------|------------------------------------------------------------------------|
| team_bus_factor        | `{команда: {rows, risks, headline}}` — Bus Factor внутри команды (эксперты п. 9). Строка: `competency`, `level` (role/tech), `hours`, `tasks`, `in_team`, `elsewhere`, `bus_factor`, `status` (`missing` / `borrowed` / `single` / `ok`), `status_label` |
| recommendations.hire   | Роли без носителей: `role`, `hours`, `tasks`, `teams`, `train_instead`, `headline` |
| recommendations.train  | Кого обучить для ролей с Bus Factor = 1: `role`, `owner_id`, `candidate` (`engineer_id`, `why`, `overlap`), `alternatives`, `headline` |
| single_points_grouped  | Одиночки, сгруппированные по носителю: «Разработчик .net (ENG-420)» → [C#, .NET Core] |

`absence_impact[engineer]` дополнительно содержит `hours_at_risk`, `postponed_tasks` и
`backup_candidates`; `sole_competencies` — только компетенции, нужные плану.
Функции — `backend/starmap/recommendations.py`.
