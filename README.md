# ПочтаТех — сервис квартального PI-планирования и звёздной карты компетенций

> Черновик. Заполнять по ходу разработки (не в конце).

## Проблема

<!-- TODO: 2-3 абзаца о проблеме квартального планирования PRODF, дефиците ресурсов
     и отсутствии видимости по компетенциям (Bus Factor). -->

## Архитектура

<!-- TODO: диаграмма/описание слоёв: ingestion -> planning -> recalculation/risk/starmap
     -> frontend (Streamlit). Указать, используется ли backend/api (FastAPI) или
     Streamlit ходит в backend напрямую как в библиотеку. -->

## Как запустить

<!-- TODO:
     pip install -r requirements.txt
     streamlit run frontend/app.py
     (опционально) uvicorn backend.api.server:app --reload
-->

## Допущения

### Преобразование данных

<!-- TODO: как транспонируется лист "роль x задача" в задача-роль-часы,
     как обрабатываются пропуски, как фильтруются "осиротевшие" зависимости. -->

### Правила приоритизации

<!-- TODO: итоговая формула score_task, как учитывается приоритет PRODF. -->

### Связь SP ↔ ЧЧ

<!-- TODO: как Story Points соотносятся с человеко-часами по ролям,
     как считается Focus Factor и лимиты по ролям/инженерам. -->

## Команда

<!-- TODO:
     Человек 1 — ingestion + planning
     Человек 2 — recalculation + risk
     Человек 3 — starmap + explainability
     Человек 4 — frontend + tests + docs
-->
