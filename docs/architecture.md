# Архитектура

<p align="right"><a href="architecture.en.md">English</a> · <b>Русский</b></p>

Референс текущего состояния, привязанный к реальной раскладке пакетов (`src/lzt_eventus/`) —
а не исторический план. Для деталей по каждому пакету — у него свой `_MODULE.md`.

## Форма

```
pylzt (отдельный репо, git dep)         lzt-eventus (этот репо)                     downstream (отдельные репо)
┌──────────────────────────┐   poll   ┌──────────────────────────────────┐   push   ┌────────────────────┐
│ typed async SDK           │ ───────▶ │ sources/ → diff/ → events/       │ ───────▶ │ webhook receiver     │
│ token pool + rate limit   │          │ → log/ (durable) → bus/ (cursor) │          │ (любой язык)          │
│ client.market/.forum/...  │          │ → delivery/ (sinks) / web/ (API) │   pull   ├────────────────────┤
└──────────────────────────┘          └──────────────────────────────────┘ ───────▶ │ lzt-eventus-sdk     │
                                                                                       │ (Python SDK-клиент)  │
                                                                                       └────────────────────┘
```

## Путь данных (поллинг → durable-событие → доставка)

1. **`sources/`** — один поллер на область ответственности (`category`, `confirm`,
   `conversations`, `guarantee`, `notifications`, `payments`, `rating`). Каждый вызывает
   `Client.market.*`/`.forum.*` из `pylzt` и передаёт ответ дифферу или парсеру
   уведомления/платёжной операции.
2. **`diff/`** — `SnapshotDiffer` (чистый, без I/O) сравнивает текущий поллинг с durable
   бейзлайном и эмитит `NewLotAppeared` / `PriceDropped` / `LotUpdated`; `LotDisappeared`
   нужен счётчик пропусков + подтверждающий поллинг, поэтому это решение владеет источник,
   а не диффер.
3. **`events/`** — таксономия `DomainEvent`. `EventType` — полный каталог (один
   `StrEnum`); конкретные классы событий сгруппированы по семьям (`lot.py`, `payment.py`,
   `notification.py`, `message.py`, `reputation.py`, `account.py`, `marker.py`).
   Расширение без реестра для lot/lifecycle-событий (наследование + `EVENT_TYPE`) и
   через словарь для суб-событий уведомлений/платежей (`_CONTENT_TYPE_EVENTS` /
   `_BY_OPERATION_TYPE` в `events/notification.py` / `events/payment.py`) — новое
   суб-событие — это запись в словаре, никогда не ветка кода.
4. **`log/`** — `BaseEventLog` (Memory + Postgres), append-only, dedup через
   `UNIQUE(event_id)`, беспробельный закоммиченный `seq`.
5. **`bus/`** — `CatchUpBus`: один супервизируемый воркер на потребителя, тянет
   `log.read_after(cursor)`, воспроизводит в порядке seq. Последовательно *внутри*
   потребителя (инвариант порядка/курсора), параллельно *между* потребителями. Ядовитые
   события паркуются в DLQ (`bus/dlq.py`) после `max_handle_attempts`; курсор при этом
   всё равно продвигается.
6. **`consumers/`** — контракт плагина (`BaseConsumer` + `BaseSubscription`).
   `LoggingConsumer` — доказательство open-closed: реальный подписчик, ноль правок движка.
7. **`delivery/` + `web/`** — подписки превращаются в bus-потребителей с курсором.
   `Subscription` (delivery/subscription.py) несёт типизированный `scope`
   (`NoScope`/`CategoryScope`/`AccountScope` — что получает) и `ctx`
   (`WebhookCtx`/`WebSocketCtx`/`SseCtx`/`PollingCtx` — параметры доставки для каждого
   транспорта, например `PollingCtx.poll_delay_seconds`). Четыре транспорта: **webhook**
   (push, HMAC-подписанный, retry+DLQ через вынесенный `libs/webhook_engine`),
   **polling** (pull, `GET /events/pending` + явное подтверждение `read_events`, свой
   курсор на подписчика), **SSE**/**WebSocket** (pull-стрим). `web/` — management API на
   FastAPI (роуты → сервисы → репозитории → orm) — гейтится admin-ключом, по дизайну
   только POST/GET.

## Вспомогательные пакеты

- **`cursor/`** — `BaseCursorStore`: одна возобновляемая позиция на потребителя
  (`sink:<subscription_id>` для sink'ов доставки).
- **`dedup/`** — `BaseSeenCache`: предфильтр перед добавлением в durable-лог.
- **`baseline/`** — `BaseLastSeenStore`: durable-снимок, с которым сравнивает
  `SnapshotDiffer`.
- **`account/`** — сверка токен-аккаунтов для источников по аккаунту (сегодня —
  rating; payments/notifications/conversations/guarantee — как только будут подключены
  тем же способом).
- **`orm/`** — декларативные модели SQLAlchemy для каждого durable-хранилища; миграции в
  `alembic/versions/`.
- **`daemon/`** — advisory lease (единственный владелец) + подключение observability
  (`/healthz`/`/readyz`/`/metrics`).
- **`engine.py`** — `EventEngine`: собирает весь граф. `build()` (реальный демон
  Postgres/webhook) vs `build_memory()` (встроенный, без инфраструктуры — тесты или
  автономный скрипт, которому нужна доставка уровня движка без запуска демона).
  `drain_once()` — один цикл poll-всех-категорий + одна прокачка шины, используется в
  тестах и `--dry-run`.

## Межрепозиторная граница

Этот репозиторий владеет wire-контрактом (`web/schemas/dtos.py`,
`web/base/error_codes.py`). Репозитории-потребители зеркалят его и должны
поставляться вместе с любым изменением контракта (см. `AGENTS.md`):

- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — Python-клиент.
  Только httpx, без связи со стеком Postgres/FastAPI этого репо.

Однопроцессный скрипт, которому нужны живые события, но не durable/восстанавливаемая
после сбоя доставка *нескольким* потребителям, не нуждается ни в `delivery/`, ни в
`web/` — см. `examples/autobuy/_MODULE.md` для более лёгкого паттерна
(`EventEngine.build_memory()` + `BaseConsumer`, либо вообще обойти движок для
одноразового случая).

## Смотри также

`ROADMAP.md` — охват и non-goals (написан раньше нескольких уже выпущенных вех;
относись к маркерам волн там как к историческим, не к текущему статусу). Файлы
`_MODULE.md` по каждому пакету — детали control-flow, которые этот документ
намеренно опускает. `docs/extending.md` — карта швов для добавления нового типа
события / источника / бэкенда хранилища / транспорта.
