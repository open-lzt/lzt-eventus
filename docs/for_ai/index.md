# Доки для AI-агентов — карта модулей

<p align="right"><a href="index.en.md">English</a> · <b>Русский</b></p>

Сжатый набор указателей для агента, работающего в этом репозитории. Читай `_MODULE.md`
(написанное вручную намерение + подводные камни), затем `_MODULE_AUTO.md` (сгенерированный
список поверхности), и только потом исходники — именно в этом порядке. Полные нарративные
доки для людей лежат в [`../`](../README.md); эта страница существует, чтобы агенту не
пришлось реверс-инжинирить дерево с нуля.

## Точки входа верхнего уровня

- [`src/lzt_eventus/_MODULE.md`](../../src/lzt_eventus/_MODULE.md) — собственная карта движка:
  правила слоёв, почему некоторые потребители сгруппированы по фиче, а не по типу примитива,
  решение о симметрии Memory/Postgres.
- [`src/lzt_eventus/engine.py`](../../src/lzt_eventus/engine.py) — `EventEngine`, корень
  композиции. `build()` (демон Postgres/Redis) vs `build_memory()` (встраиваемый, без
  инфраструктуры, живой поллинг).
- [`src/lzt_eventus/devkit/_MODULE.md`](../../src/lzt_eventus/devkit/_MODULE.md) — `local_eventus()`,
  быстрый старт в один вызов, который поднимает реальный движок **и** его management API на
  эфемерном порту для скриптов/примеров/тестов. Сосед `build_memory()` по принципу
  progressive-disclosure.
- [`src/lzt_eventus/web/`](../../src/lzt_eventus/web/) — management API (FastAPI): роуты,
  DTO, репозитории подписок/токен-аккаунтов. **Wire-контракт заморожен** — смотри
  [`../../AGENTS.md`](../../AGENTS.md) перед изменением роутов/DTO/кодов ошибок/формата
  SSE-WS-webhook; изменения здесь должны сопровождаться соответствующим обновлением
  `lzt-eventus-sdk` в отдельном репозитории.
- [`src/lzt_eventus/delivery/subscription_scope.py`](../../src/lzt_eventus/delivery/subscription_scope.py) —
  типизированный фильтр подписки (`NoScope` / `CategoryScope` / `AccountScope`) и то, каким
  `EventType` каждый из них может соответствовать.

## Карты подмодулей (`_MODULE.md` там, где он есть)

`account/` · `baseline/` · `bus/` · `codecs/` · `consumers/` · `cursor/` · `daemon/` · `dedup/` ·
`delivery/` · `devkit/` · `diff/` · `events/` · `log/` · `orm/` · `sources/` · `transport.py` ·
`web/{base,middlewares,orm,repos,routes,schemas,services,shared}/` — у каждого есть (или
должен быть) соседний `_MODULE.md`; если он отсутствует или устарел — считай это багом и
заведи/подсвети его, а не реверс-инжинирь молча.

## Примеры

- [`examples/autobuy/_MODULE.md`](../../examples/autobuy/_MODULE.md) — потребитель на ~10 строк,
  построенный на `local_eventus` + `lzt-eventus-sdk`: подписка по фильтру категории, покупка
  на каждое совпадение, подсчёт покупок. Каноническая референс-реализация философии
  progressive-disclosure этого репозитория — «10 строк реальной логики, остальное —
  boilerplate».

## Скиллы (глубокие, заточенные под задачу гайды)

- [`.claude/skills/lzt-integration/SKILL.md`](../../.claude/skills/lzt-integration/SKILL.md) —
  **использование** библиотеки (чтение каталога, подписка in-process, приём вебхуков, поллинг
  `/events/pending` для pull-альтернативы).
- [`.claude/skills/lzt-extending/SKILL.md`](../../.claude/skills/lzt-extending/SKILL.md) —
  **расширение ядра** через subclass + inject (новый тип события, роут, источник,
  бэкенд хранилища/транспорта).

## Архитектура и охват

- [`../architecture.md`](../architecture.md) — текущая архитектура.
- [`../../ROADMAP.md`](../../ROADMAP.md) — охват и non-goals.
- [`../../AGENTS.md`](../../AGENTS.md) — правило синхронизации wire-контракта между
  репозиториями (читать перед изменением `web/`).
