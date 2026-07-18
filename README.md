<h1 align="center">lzt-eventus</h1>

<p align="right"><a href="README.en.md">English</a> · <b>Русский</b></p>

<p align="center">
  <strong>Self-hosted событийный слой поверх poll-only API lzt.market — поллинг, диффинг, персистентность, воспроизведение.</strong>
</p>

<p align="center">
  <a href="https://github.com/open-lzt/lzt-eventus/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"></a>
</p>

**lzt-eventus** — self-hosted event engine, который превращает poll-only каталожный API
[lzt.market](https://lzt.market) в доменные события с durable, воспроизводимым логом —
чтобы любое число подписчиков могло реагировать (in-process, webhook, SSE/WS или pull-poll)
без необходимости самостоятельно поллить маркетплейс или терять события при рестарте.

[Полная документация](docs/README.md) · [Доки для AI-агентов](docs/for_ai/index.md) ·
[Быстрый старт](docs/usage/quickstart.md) · [Архитектура](docs/architecture.md) ·
[Расширение](docs/extending.md) · [Правовая информация / ToS](docs/legal.md)

Построен на отдельном SDK [`pylzt`](https://github.com/open-lzt/pylzt):

- **[`pylzt`](https://github.com/open-lzt/pylzt)** — типизированный async SDK, чьи чтения
  каталога проходят через центральный пул токенов + rate limiter на токен, так что флот
  листает весь каталог, ни разу не словив `429`. Живёт в своём собственном репозитории; этот
  движок от него зависит.
- **`lzt_eventus`** — поллеры, которые диффят снимки каталога в доменные события,
  сохраняют их в durable append-only лог на Postgres, и catch-up шина, которая позволяет
  любому плагину подписаться и возобновиться со своего курсора (без потерь, воспроизводимо).
- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — Python-клиент для
  *потребления* management API этого движка (подписки, поллинг, верификация вебхуков).
  Только httpx, без связи со стеком Postgres/FastAPI этого репо — ставь его отдельно, если
  пишешь webhook receiver или поллер, а не весь движок.

> **Правовая информация / ToS.** Только чтение каталога + автоматизация аналитики. Никакого
> брутфорса, никакого обхода 2FA. См. [`docs/legal.md`](docs/legal.md).

## Быстрый старт — от клона до работающего демона

Движок — это долгоживущий демон на **self-hosted Postgres 16 + Redis 7**
(оба запускаются на одном хосте через Docker Compose). Один скрипт всё разворачивает.

**Для ленивых — одна интерактивная команда:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core && cd lzt-core && scripts/quickstart.sh
```

Спрашивает токен lzt.market, опционально домен + контактный email (для автоматического
TLS), сам генерирует admin key, затем прогоняет полную установку — зависимости, Postgres +
Redis, миграции, демон, health gate, TLS, если настроен. В конце — отчёт: ссылка на
health-check, admin API key, ссылка на доки.

**Ручной / скриптуемый путь, тот же результат:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core
cd lzt-core

# 1. Получи API-токен lzt.market: https://lzt.market/account/api
# 2. Бутстрап (проверка зависимостей → .env → Postgres+Redis → миграция → запуск демона):
scripts/install.sh

# 3. install.sh создаёт .env из .env.example при первом запуске. Отредактируй его:
#      LZT_TOKENS=["<твой-токен>"]                 # JSON-массив, через запятую для нескольких
#      LZT_ADMIN_API_KEY=<openssl rand -hex 32>    # admin-ключ management API
#    затем перезапусти (идемпотентно):
scripts/install.sh
```

Любой из путей поднимает полный стек, описанный в
[`deploy/docker-compose.yml`](deploy/docker-compose.yml) (postgres + redis +
образ движка, собранный из [`deploy/Dockerfile`](deploy/Dockerfile)) и
гейтит демон по `/healthz`.

Управление работающим стеком:

```bash
scripts/status.sh          # здоровье движка + Postgres + Redis, развёрнутая ревизия
scripts/logs.sh --follow   # стримить логи демона
scripts/update.sh          # rolling-обновление: pull → sync → migrate → restart (health-gated, автооткат)
scripts/stop.sh            # аккуратный SIGTERM
scripts/restart.sh         # stop → start с health gate
```

### Режимы запуска

| Режим | Как | Примечания |
|---|---|---|
| **Docker Compose** (по умолчанию) | `scripts/install.sh` | Postgres + Redis + движок все в compose; хранилища персистятся в именованные volume. |
| **systemd** (bare-metal) | [`deploy/lzt-core.service`](deploy/lzt-core.service) | `ExecStart=uv run python -m lzt_eventus run`, `EnvironmentFile=.env`, аккуратный SIGTERM, `Restart=on-failure`. Ожидает, что Postgres + Redis уже на хосте. |

### Авто-обновление (опционально)

Config-driven rolling авто-апдейтер поллит git-реф и раскатывает обновления с health gate +
автооткатом. Выключен по умолчанию — включается через
[`deploy/autoupdate.yml`](deploy/autoupdate.yml) (`enabled: true`):

```bash
uv run python scripts/autoupdate.py --daemon   # in-process цикл поллинга
# или альтернатива через systemd timer:
#   deploy/lzt-core-autoupdate.service + .timer  (по умолчанию: каждые 5 минут)
```

### Домен + автоматический TLS (опционально)

Задай `LZT_DOMAIN` + `LZT_ACME_EMAIL` в `.env` и перезапусти `scripts/install.sh` —
это закроет движок host-level nginx + certbot (`scripts/setup_tls.sh`) и выпустит
настоящий сертификат Let's Encrypt. Безопасно на общем сервере, где уже крутятся другие
сайты (добавляет один vhost, не трогает остальные). Полный гайд:
**[docs/deploy.md](docs/deploy.md)** (на русском) /
**[docs/deploy.en.md](docs/deploy.en.md)** (на английском).

### Запуск без Docker

```bash
uv sync --extra engine
uv run python -m lzt_eventus run            # или --dry-run для поллинга+диффинга без записи
```

## Управление работающим деплоем

- **Мониторинг** — `scripts/status.sh` (здоровье движка + Postgres + Redis, развёрнутая
  ревизия), `scripts/logs.sh --follow`, либо напрямую `/healthz` / `/readyz` / `/metrics`.
- **Обновление** — `scripts/update.sh` (pull → sync → migrate → restart, health-gated,
  автооткат при сбое).
- **Удаление** — `docker compose -f deploy/docker-compose.yml down -v` (добавь `-v`, чтобы
  также снести volume Postgres/Redis; опусти, чтобы сохранить данные для будущей
  переустановки).
- **Управление** — [management API](#management-api-волна-4) (`/subscriptions`,
  `/events/pending`, инспекция DLQ) плюс `/scalar` для интерактивного справочника; полный
  список скриптов ниже.

## Использование

Это self-hosted система, а не библиотека — примеры ниже это точки интеграции с
**работающим** движком: встрой его в свой процесс, принимай его вебхуки или поллить его
management API. Весь I/O асинхронный. Более развёрнутые прохождения — в двух встроенных
скиллах (`.claude/skills/lzt-integration`, `.claude/skills/lzt-extending`).

`lzt_eventus` поллит каталог через [`pylzt`](https://github.com/open-lzt/pylzt) —
типизированный async SDK является отдельной зависимостью со своим README; сырые чтения
каталога (`client.market.get_lot`, `list_lots`, пагинация, DI, обработка ошибок)
задокументированы там, не дублируются здесь. Что относится к *этому* README — это то, что
даёт движок поверх него: доменные события, durable лог и доставка нескольким подписчикам.

### Движок — подписка на события in-process (плагин `BaseModule`)

Встрой движок в своё приложение и реагируй на доменные события. Шина — это **catch-up**
диспетчер: у каждого модуля стабильное `name` (его ключ курсора), и он возобновляется точно
с того места, где остановился после рестарта.

```python
import asyncio
from decimal import Decimal

from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.module import BaseModule, BaseSubscription


class DealWatcher(BaseModule):
    name = "deal-watcher"  # ← его курсорная идентичность

    def __init__(self) -> None:
        self.subscriptions = [
            BaseSubscription(
                event_types=frozenset({EventType.PRICE_DROPPED}),
                event_cls=PriceDropped,  # сужает тип в handle()
            )
        ]

    async def handle(self, event: DomainEvent) -> None:
        assert isinstance(event, PriceDropped)
        if event.new_price < event.old_price * Decimal("0.85"):
            print("deal:", event.lot.item_id, event.new_price)


async def main() -> None:
    config = EngineConfig()  # читает окружение LZT_*
    engine, _sessionmaker = EventEngine.build(config, modules=[DealWatcher()])  # на Postgres
    await engine.run()  # супервизирует поллеры + шину до остановки


asyncio.run(main())
```

Для тестов / embed без Postgres используй in-memory хранилища (передай свой `Client`):

```python
engine = EventEngine.build_memory(EngineConfig(), client=Client(tokens=["<token>"]),
                                                                    modules=[DealWatcher()])
await engine.drain_once()        # один poll + одна прокачка (детерминированно, отлично для тестов)
```

### Движок — регистрация обработчиков декораторами (`EventRouter`)

Предпочитаешь декораторы вместо подкласса `BaseModule`? `EventRouter` *и есть* модуль (один
курсор), чьи обработчики привязаны через `@router.on(...)`. Регистрируется как любой другой
модуль:

```python
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.router import EventRouter


router = EventRouter("price-bot")  # name = его курсорная идентичность


@router.on(EventType.NEW_LOT)
async def on_new_lot(event: DomainEvent) -> None:
    ...


@router.on(EventType.PRICE_DROPPED, event_cls=PriceDropped)
async def on_drop(event: DomainEvent) -> None:
    assert isinstance(event, PriceDropped)  # сужен через event_cls
    print(event.old_price, "→", event.new_price)


engine.add_module(router)  # или EventEngine.build(config, modules=[router])
```

Один обработчик может покрывать несколько типов —
`@router.on(EventType.NEW_LOT, EventType.LOT_UPDATED)`.

### Движок — добавление/удаление источников и подписчиков в рантайме

Источники (поллеры) и подписчики (модули) можно менять, пока `run()` живёт — без рестарта:

```python
runner = asyncio.create_task(engine.run())

engine.add_module(DealWatcher())            # подхватится на следующей прокачке шины
engine.remove_module("deal-watcher")        # курсор остаётся закоммиченным → безопасно добавить обратно позже

engine.add_poller(my_source)                # супервизор сразу запускает его задачу
engine.remove_poller("my-source")           # его задача аккуратно останавливается
print(engine.poller_names, engine.module_names)
```

### Движок — кастомный источник событий (`BasePoller`)

```python
from lzt_eventus.poller.base import BasePoller


class HeartbeatPoller(BasePoller):
    name = "heartbeat"

    def __init__(self, log, bus) -> None:
        super().__init__(min_cadence=5, max_cadence=60, cadence=10)
        self._log, self._bus = log, bus

    async def poll_once(self) -> int:
        events = await self._scan()  # собери + верни свои экземпляры DomainEvent
        for e in events:
            await self._log.append(e)
        if events:
            self._bus.notify()
        return len(events)


# внедрение на этапе сборки …
engine = EventEngine.build_memory(
    EngineConfig(), client=client, modules=[DealWatcher()],
    extra_pollers=[HeartbeatPoller(log=..., bus=...)]
)
# … или горячее добавление позже: engine.add_poller(HeartbeatPoller(...))
```

### Движок — приём событий через подписанный webhook (любой язык)

Используй Python-клиент [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk),
чтобы говорить с management API — это httpx-only аналог этого репо, без притянутого
Postgres/FastAPI (`pip install lzt-eventus-sdk`). Каждый пример ниже использует его; сырой
`curl` работает идентично против тех же роутов для потребителей не на Python.

Зарегистрируй подписку на webhook, затем проверь HMAC-подпись на своём receiver'е. Доставки
получают catch-up + retry + DLQ так же, как in-process модули.

```python
from lzt_eventus_sdk import (
        CategoryScope,
        EventType,
        ManagementClient,
        MarketCategory,
        SubscriptionTransport,
)

async with ManagementClient("http://<engine-host>:27543", api_key=LZT_ADMIN_API_KEY) as mgmt:
        sub = await mgmt.create_subscription(
                transport=SubscriptionTransport.WEBHOOK,
                endpoint="https://you.example/hook",
                event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
                # LZT_CATEGORIES контролирует, какие категории поллит *движок* вообще — каждый
                # подписчик разделяет этот пайплайн. `scope` сужает то, что получает *этот*
                # подписчик среди них; отклоняется при создании, если никогда не сможет
                # совпасть с `event_types` (например category scope на `rating_changed`) —
                # см. SubscriptionScopeMismatch.
                scope=CategoryScope(category=MarketCategory.STEAM),
        )
        print(sub.secret)  # секрет подписи для этой подписки — одноразовый, сохрани сейчас
```

```python
from fastapi import FastAPI, Request, Response
from lzt_eventus.delivery.signing import verify_webhook


app = FastAPI()
SECRET = "<секрет, возвращённый выше>"


@app.post("/hook")
async def hook(request: Request) -> Response:
    body = await request.body()  # СЫРЫЕ байты, до парсинга
    if not verify_webhook(secret=SECRET, body=body, presented=request.headers.get("X-LZT-Signature")):
        return Response(status_code=401)
    # де-дупликация по Idempotency-Key (доставка at-least-once), затем обработка…
    return Response(status_code=200)  # 2xx подтверждает; не-2xx ретраится → DLQ
```

### Движок — поллинг pending-событий вместо подписки (без webhook/стрима)

Если предпочитаешь pull, а не push (нет публичного эндпоинта для экспозиции, проще запускать
за файрволом/cron), зарегистрируй подписку `SubscriptionTransport.POLLING` вместо
`WEBHOOK`/`SSE`/`WEBSOCKET`. Каждая polling-подписка отслеживает **свой** курсор —
независимые поллеры никогда не гонятся друг с другом, и можно фильтровать по `event_type` в
каждом запросе.

```python
sub = await mgmt.create_subscription(
        transport=SubscriptionTransport.POLLING,
        endpoint="my-cron-poller",
        event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
)
# sub.secret / sub.stream_token оба None — polling только pull и уже гейтится
# admin-ключом, никакого push-credential минтить не нужно.
```

`poll_pending` возвращает события после закоммиченного курсора подписки. По умолчанию
(`read_all=False`) курсор **не** продвигается — тот же батч воспроизводится при ретрае, так
что можно инспектировать его перед коммитом:

```python
batch = await mgmt.poll_pending(sub.subscription_id, event_type=[EventType.NEW_LOT], limit=100)
for event in batch.items:
        print(event.seq, event.event_type, event.data)
# batch.next_seq / batch.last_read_seq / batch.drained — см. PendingBatch в lzt-eventus-sdk
```

Подтверди то, что реально обработал, либо инлайн (`read_all=True` на `poll_pending`
коммитит ровно просканированный батч), либо явно относительно границы `seq` — например,
если из батча успешно обработалась только часть элементов:

```python
last_seq = await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
# идемпотентно — повторная отправка более старого/равного seq — это no-op
```

Каждая ошибка management/polling — это типизированный конверт
`{"error": "<code>", "detail": {...}, "request_id": "..."}` (никогда голый `HTTPException`).
Коды, относящиеся к поллингу:

| Код | Статус | Когда |
|---|---|---|
| `unknown_event_type` | 400 | Фильтр `event_type` (или `event_types` при создании) не входит в каталог `EventType`. |
| `invalid_limit` | 400 | `limit` не положительное целое число. |
| `limit_too_large` | 400 | `limit` превышает `LZT_MAX_QUERY_LIMIT` (по умолчанию 500). |
| `not_a_polling_subscription` | 400 | `subscription_id` существует, но был зарегистрирован с push-транспортом. |
| `subscription_not_found` | 404 | `subscription_id` не существует. |

`invalid_limit`/`limit_too_large` обеспечиваются `LimitValidationMiddleware`
([`web/middlewares/limits.py`](src/lzt_eventus/web/middlewares/limits.py)) — он читает
`?limit=` прямо из query-строки **до** выполнения любого роута, поэтому каждый текущий и
будущий `limit`-принимающий эндпоинт получает одинаковую границу и одинаковую форму ошибки
бесплатно.

### Движок — локальный devkit одним вызовом (скрипты, примеры, быстрые эксперименты)

`local_eventus` — быстрый старт progressive-disclosure для веб/подписочной стороны: один
`async with` даёт реальный, живо-поллящий движок **и** его management API на эфемерном
порту — без Postgres/Redis, без ручного подключения `EngineHandle`. Всё, что он подключает
(`client`, `config`, `consumers`, `extra_sources`, dedup, хранилища), — тот же
переопределяемый шов, что уже открывает `build_memory` — это просто поставляет рабочие
дефолты для остального. См. [`examples/autobuy`](examples/autobuy) для полного потребителя
на ~10 строк, построенного на нём.

```python
from lzt_eventus.devkit import local_eventus
from pylzt.types import Category
from lzt_eventus_sdk import CategoryScope, EventType, ManagementClient, SubscriptionTransport


async with local_eventus(tokens=["<token>"]) as server:
    async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
        sub = await mgmt.create_subscription(
            transport=SubscriptionTransport.POLLING, endpoint="quickstart",
            event_types=[EventType.NEW_LOT], scope=CategoryScope(category=Category.TELEGRAM),
        )
        batch = await mgmt.poll_pending(sub.subscription_id, limit=100)
        for event in batch.items:
            print(event.data["lot"]["item_id"], event.data["lot"]["price"])
```

### Движок — замена бэкенда хранилища (наследование + внедрение)

Каждое хранилище — ABC с дефолтом `Memory*` и реализацией `Postgres*` — наследуй для нового
бэкенда и передай пакет `Stores` прямо в конструктор:

```python
from lzt_eventus.engine import EventEngine, Stores
from lzt_eventus.cursor.memory import MemoryCursorStore
from lzt_eventus.bus.dlq import MemoryDeadLetterStore
from lzt_eventus.baseline.store import MemoryLastSeenStore


last_seen = MemoryLastSeenStore()
stores = Stores(
    log=MyRedisEventLog(...), last_seen=last_seen,
    cursor=MemoryCursorStore(), dlq=MemoryDeadLetterStore()
)
engine = EventEngine(client=client, stores=stores, config=EngineConfig(), modules=[DealWatcher()])
```

## Операционные скрипты

Все под [`scripts/`](scripts/) — `set -euo pipefail`, цветной вывод, `--help`, идемпотентны.

| Скрипт | Назначение |
|---|---|
| `quickstart.sh` | Интерактивный бутстрап в одну команду: prompt → `.env` → `install.sh` → отчёт. |
| `install.sh` | Бутстрап в один проход: чистый хост → работающий демон. |
| `setup_tls.sh` | Host nginx + certbot vhost/сертификат для `LZT_DOMAIN` (вызывается из `install.sh`). |
| `update.sh` | Rolling-обновление с health gate + автооткатом. |
| `rollback.sh` | Откат последнего обновления (код + один шаг миграции + рестарт). |
| `migrate.sh` | `alembic upgrade head` (идемпотентно). |
| `seed.sh` | Загрузить записанные страницы каталога офлайн (`--file`) для dev/CI. |
| `replay.sh` | `--consumer X --from-seq N` — отмотать курсор для бэкфилла. |
| `redrive.sh` | `--consumer X` — переинжектить dead-lettered события после фикса. |
| `prune.sh` | Retention: удалить строки event-лога ниже watermark потребителя. |
| `backup.sh` / `restore.sh` | pg_dump / pg_restore event-лога (обратимо). |
| `stop.sh` / `restart.sh` / `status.sh` / `logs.sh` | Жизненный цикл + наблюдаемость. |
| `autoupdate.py` | Config-driven rolling авто-апдейтер (типизирован, покрыт юнит-тестами). |
| `health.py` | Отдельный probe `/healthz` + `/readyz` (используется гейтом обновления). |

## Конфигурация

Каждую переменную читает `EngineConfig` с префиксом `LZT_`
([`src/lzt_eventus/config.py`](src/lzt_eventus/config.py)). Полный аннотированный список —
в [`.env.example`](.env.example) — скопируй его в `.env`.

Обязательные переменные помечены `*`; у всего остального есть рабочий дефолт.

| Переменная | По умолчанию | Значение |
|---|---|---|
| `LZT_TOKENS` `*` | `[]` | Токен(ы) lzt.market, JSON-массив. [Получить](https://lzt.market/account/api). |
| `LZT_ADMIN_API_KEY` `*` | — | Ключ management API. `openssl rand -hex 32`. |
| `LZT_DATABASE_URL` | `postgresql://…` | DSN Postgres. |
| `LZT_REDIS_URL` | `redis://localhost:6379/0` | URL Redis. |
| `LZT_CATEGORIES` | `["steam"]` | Категории для поллинга, JSON-массив. |
| `LZT_MIN/MAX/DEFAULT_CADENCE` | `6` / `120` / `30` | Границы частоты поллинга, секунды. |
| `LZT_PER_PAGE` | `50` | Размер страницы каталога. |
| `LZT_DISAPPEAR_POLLS` | `3` | Пропущенных поллингов до статуса «продан». |
| `LZT_CONFIRM_BUDGET_FRACTION` / `_BATCH_SIZE` | `0.25` / `50` | Бюджет частоты подтверждения + размер батча. |
| `LZT_SEEN_TTL_SECONDS` | `86400` | Окно dedup для увиденных лотов. |
| `LZT_BATCH_SIZE` / `LZT_BATCH_LINGER` | `50` / `0.05` | Батчинг приёма. |
| `LZT_MAX_HANDLE_ATTEMPTS` | `5` | Доставок до DLQ. |
| `LZT_RETENTION_MONTHS` | `3` | Retention event-лога. |
| `LZT_MAX_SINK_LAG` | `100000` | Максимальное отставание потребителя до алармa. |
| `LZT_WARN_WINDOW_HOURS` | `24` | Окно предупреждения аналитики. |
| `LZT_DEAL_THRESHOLD` | `0.85` | `price < ai_price * threshold`. |
| `LZT_HEALTH_HOST` / `_PORT` | `0.0.0.0` / `27543` | HTTP-сервер (`/healthz`, `/metrics`). |
| `LZT_POSTGRES_PORT` / `LZT_REDIS_PORT` | `27542` / `27541` | Хостовые порты compose (loopback). |
| `LZT_ADVISORY_LOCK_KEY` / `LZT_RUN_ID` | `1819571811` / `engine` | Выборы единственного писателя + id прогона. |
| `LZT_MAX_QUERY_LIMIT` | `500` | Максимальный `?limit=` на любом эндпоинте. |
| `LZT_WEB_DOCS_ENABLED` | `true` | Отдавать `/docs` + `/scalar`. |

Нестандартный health-порт намеренно — см. [гайд по деплою](docs/deploy.md).

## Management API (волна 4)

HTTP API, защищённый admin-ключом, предоставляет управление подписками
(регистрация/список потребителей, инспекция курсоров и DLQ) плюс `/events/pending` +
`/events/read_events` для pull-based поллинга (см. [выше](#движок--поллинг-pending-событий-вместо-подписки-без-webhookстрима)).
Аутентификация через `LZT_ADMIN_API_KEY`, заданный в `.env`. **По дизайну API только
POST/GET** — никаких PUT/PATCH/DELETE (CI это обеспечивает над `src/lzt_eventus/web`). См.
[`ROADMAP.md`](ROADMAP.md).

См. [`AGENTS.md`](AGENTS.md) для правила синхронизации wire-контракта, применимого к любому
потребителю этого API в отдельном репозитории (например
[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)).

**Доки.** Движок хостит собственный интерактивный справочник — никакого внешнего
доксайта, никакого аккаунта на scalar.com:

- `http://<engine-host>:27543/scalar` — справочник [Scalar](https://github.com/scalar/scalar)
  (читает `/openapi.json`; каждый роут, DTO и код ошибки выше — просматриваемо/тестируемо).
- `http://<engine-host>:27543/docs` — Swagger UI (встроенная альтернатива FastAPI).

Оба гейтятся `LZT_WEB_DOCS_ENABLED` (по умолчанию `true`) — выставь `false`, чтобы не
отдавать ни один из них на продакшен-деплое, где не хочешь выставлять doc UI наружу.

## Защита ветки

`main` защищена: каждый PR должен пройти CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml) — ruff + ruff format +
`mypy --strict` + `pytest --cov-fail-under=80` + gitleaks + pip-audit) и получить ревью
CODEOWNERS перед мержем. Настрой в **Settings → Branches → Branch protection rules**:
требовать status checks, требовать ревью Code Owner, никаких прямых пушей в `main`.

## Для AI-агентов, строящих поверх этого репо

Два скилла Claude Code лежат в [`.claude/skills/`](.claude/skills/), чтобы агент мог
освоить поверхность проекта без реверс-инжиниринга:

- [`lzt-integration`](.claude/skills/lzt-integration/SKILL.md) — **использование**
  библиотеки: чтение каталога через `Client`, подписка in-process через плагин
  `BaseModule`, приём подписанных вебхуков, либо поллинг `/events/pending` как
  pull-based альтернатива.
- [`lzt-extending`](.claude/skills/lzt-extending/SKILL.md) — **расширение ядра** через
  наследование + внедрение (новый тип события, роут, источник, бэкенд хранилища/транспорта)
  без правки исходников библиотеки.

## Статус и контрибьютинг

См. [`docs/architecture.md`](docs/architecture.md) для текущей архитектуры и
[`ROADMAP.md`](ROADMAP.md) для охвата и non-goals. Настройка контрибьютинга, локальный
CI floor и конвенции — в [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Сообщество

См. [CONTRIBUTING.md](CONTRIBUTING.md) для гайдлайнов и того, как отправлять PR. Используй
[issues](https://github.com/open-lzt/lzt-eventus/issues/new/choose) для багов и запросов
фич.

<a href="https://github.com/zlexdev"><img src="https://github.com/zlexdev.png" width="48" height="48" style="border-radius:50%" alt="zlexdev"></a>

## Лицензия и правовая информация

[MIT](LICENSE). Прочитай [дисклеймер legal / ToS](docs/legal.md) перед использованием —
только чтение каталога + автоматизация аналитики; ты отвечаешь за соблюдение условий
использования lzt.market.
