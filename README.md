<h1 align="center">lzt-eventus</h1>

<p align="right"><a href="README.en.md">English</a> · <b>Русский</b></p>

<p align="center">
  <strong>Self-hosted событийный слой поверх poll-only API lzt.market.</strong>
</p>

<p align="center">
  <a href="https://github.com/open-lzt/lzt-eventus/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"></a>
</p>

У lzt.market нет вебхуков. Есть только каталог, который можно опрашивать.

**Poll-only** — значит: хочешь знать, что появился лот, опрашивай выдачу сам, в цикле, и сам замечай, что изменилось.

Этот движок делает опрос один раз — за всех.

Он поллит каталог, сравнивает снимки, превращает разницу в **события** и складывает их в durable-лог на Postgres.

**Durable-лог** — append-only таблица: событие записано навсегда, у него есть сквозной номер `seq`, и его можно перечитать через месяц.

Дальше на этот лог подписывается кто угодно: твой же процесс, вебхук на другом хосте, SSE/WS-стрим, cron-поллер.

Ловушка, которую он закрывает: **подписчик не теряет события при рестарте**. У каждого свой курсор в логе — упал, поднялся, дочитал с того места, где остановился.

[Полная документация](docs/README.md) · [Быстрый старт](docs/usage/quickstart.md) · [Архитектура](docs/architecture.md) · [Расширение](docs/extending.md) · [Доки для AI-агентов](docs/for_ai/index.md) · [Правовая информация / ToS](docs/legal.md)

> **ToS.** Только чтение каталога и автоматизация аналитики. Никакого брутфорса, никакого обхода 2FA. См. [`docs/legal.md`](docs/legal.md).

---

## Соседние проекты

Движок не ходит в маркет сам — за него это делает SDK.

- **[`pylzt`](https://github.com/open-lzt/pylzt)** — типизированный async-SDK над API маркета. Пул токенов + рейт-лимитер на каждый токен, поэтому поллинг всего каталога не ловит `429`. Отдельный репозиторий, зависимость этого.
- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — клиент к management API *этого* движка: подписки, поллинг, верификация вебхуков. Только httpx, без Postgres и FastAPI. Ставь его, если пишешь приёмник событий, а не сам движок.

---

## Каталог событий

В `EventType` **42** имени. **31** движок эмитит сегодня; остальные 11 — зарезервированные имена без реализации, они перечислены в конце.

Подписка фильтруется по этим строкам: `event_types=[EventType.NEW_LOT, ...]`.

### Каталог и лоты

Источник — дифф снимков выдачи.

| Событие | Когда |
|---|---|
| `new_lot` | В выдаче появился лот, которого не было в прошлом снимке |
| `price_dropped` | Цена лота стала ниже; в событии и старая, и новая |
| `lot_updated` | Изменилось что-то, кроме цены |
| `lot_disappeared` | Лот пропал из выдачи — продан или снят; догадка в поле `reason` |
| `snapshot_initialized` | Маркер холодного старта: одно событие вместо потока `new_lot` на первом же поллинге |

### Лот в сделке

Источник — уведомления маркета, а не дифф.

| Событие | Когда |
|---|---|
| `lot_reserved` | Покупатель поставил лот в холд |
| `purchase_confirmed` | Продавец подтвердил покупку. Это **не** `item_sold` — то про деньги, это про сделку |

### Деньги

Операции по балансу аккаунта.

| Событие | Когда |
|---|---|
| `income_received` | Приход на баланс |
| `expense_recorded` | Расход с баланса |
| `balance_refilled` | Пополнение баланса |
| `balance_withdrawn` | Вывод с баланса |
| `item_purchased` | Куплен товар |
| `item_sold` | Продан товар — денежная сторона сделки |
| `money_transferred` | Перевод отправлен |
| `money_received` | Перевод получен |
| `internal_purchase` | Внутренняя покупка на площадке |
| `hold_claimed` | Забраны средства из холда |
| `auto_payment_triggered` | Сработал автоплатёж |
| `balance_exchanged` | Обмен валюты баланса |

### Аккаунт и гарантия

| Событие | Когда |
|---|---|
| `guarantee_expiring` | Гарантия на купленный аккаунт скоро истекает |
| `account_invalid` | Аккаунт перестал быть валидным |
| `dispute_opened` | Открыт спор |
| `claim_filed` | Подана жалоба |

### Диалоги, репутация, уведомления

| Событие | Когда |
|---|---|
| `new_conversation` | Начат новый диалог |
| `new_message` | Новое сообщение в диалоге |
| `rating_changed` | Изменился рейтинг |
| `market_notification_received` | Уведомление маркета |
| `forum_notification_received` | Уведомление форума |

### Инвойсы

Единственная группа, которая приходит **не** поллингом, а входящим вебхуком на `POST /inbound` с проверкой HMAC.

| Событие | Когда |
|---|---|
| `invoice_created` | Инвойс создан |
| `invoice_paid` | Инвойс оплачен |
| `invoice_expired` | Инвойс протух |

> **Не подтверждено на реальном вебхуке.** Формат тела и схема подписи реализованы защитно, по предположению. Сверь с настоящим захваченным вебхуком, прежде чем полагаться на это в проде — см. [`web/routes/inbound.py`](src/lzt_eventus/web/routes/inbound.py).

### Зарезервированные имена

Есть в `EventType`, но пока не эмитятся: `payout_requested`, `transfer_held`, `transfer_cancelled`, `reserve_expired`, `purchase_cancelled`, `deal_detected`, `price_vs_ai_changed`, `inventory_revalued`, `discount_requested`, `discount_approved`, `discount_declined`.

Подписаться на них можно — приходить пока нечему.

Добавить своё событие: [`docs/extending.md`](docs/extending.md). Кодек безреестровый, хватает подкласса `DomainEvent` и члена в `EventType`.

**Запомнить:** `event_id` детерминированный — uuid5 от `(aggregate_id, event_type, content_hash, poll_epoch)`. Один и тот же логический факт всегда даёт один и тот же id, поэтому повторный поллинг после падения не задваивает событие, а спотыкается об UNIQUE в логе.

---

## Быстрый старт

Движок — долгоживущий демон на **Postgres 16 + Redis 7**, оба поднимаются на том же хосте через Docker Compose.

Нужен токен lzt.market: https://lzt.market/account/api

**В одну команду, с вопросами:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core && cd lzt-core && scripts/quickstart.sh
```

Спросит токен, опционально домен и email для TLS, сгенерирует admin-ключ, поставит всё и дождётся `/healthz`. В конце напечатает ключ и ссылки.

**То же самое, но скриптуемо:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core
cd lzt-core

scripts/install.sh          # создаст .env из .env.example и остановится

# впиши в .env:
#   LZT_TOKENS=["<токен>"]                    # JSON-массив
#   LZT_ADMIN_API_KEY=<openssl rand -hex 32>  # ключ management API

scripts/install.sh          # повторно — идемпотентно
```

Оба пути поднимают стек из [`deploy/docker-compose.yml`](deploy/docker-compose.yml).

**Без Docker:**

```bash
uv sync --extra engine
uv run python -m lzt_eventus run     # --dry-run: поллит и диффит, но не пишет
```

**Bare-metal под systemd:** [`deploy/lzt-core.service`](deploy/lzt-core.service). Ожидает, что Postgres и Redis уже на хосте.

**Домен и TLS:** задай `LZT_DOMAIN` + `LZT_ACME_EMAIL` в `.env` и перезапусти `install.sh` — поднимет nginx + certbot и выпустит Let's Encrypt. Добавляет один vhost, чужие сайты на хосте не трогает. Гайд: [`docs/deploy.md`](docs/deploy.md).

**Авто-обновление:** выключено. Включается в [`deploy/autoupdate.yml`](deploy/autoupdate.yml) — поллит git-реф, раскатывает с health-gate и автооткатом.

---

## Как подписаться

Четыре транспорта, одна и та же семантика: свой курсор, catch-up после простоя, ретраи, DLQ.

**DLQ** — dead-letter queue: событие, которое не удалось доставить за `LZT_MAX_HANDLE_ATTEMPTS` попыток, откладывается сюда, а не теряется и не блокирует очередь.

### 1. In-process — движок внутри твоего приложения

```python
import asyncio
from decimal import Decimal

from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.module import BaseModule, BaseSubscription


class DealWatcher(BaseModule):
    name = "deal-watcher"  # ← это и есть его курсор

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
    engine, _ = EventEngine.build(EngineConfig(), modules=[DealWatcher()])
    await engine.run()


asyncio.run(main())
```

`name` — это курсорная идентичность. Поменяешь строку — модуль начнёт читать лог заново.

Тот же модуль, но декораторами вместо подкласса — `EventRouter("price-bot")` с `@router.on(EventType.NEW_LOT)`. Один роутер = один курсор.

Модули и поллеры можно добавлять и убирать на живом `run()`: `engine.add_module(...)`, `engine.remove_poller(...)`. Курсор удалённого модуля остаётся закоммиченным, вернуть его позже безопасно.

Для тестов — без Postgres:

```python
engine = EventEngine.build_memory(EngineConfig(), client=client, modules=[DealWatcher()])
await engine.drain_once()   # один поллинг + одна прокачка, детерминированно
```

### 2. Webhook — приёмник на другом хосте, любой язык

```python
from lzt_eventus_sdk import CategoryScope, EventType, ManagementClient, MarketCategory, SubscriptionTransport

async with ManagementClient("http://<host>:27543", api_key=LZT_ADMIN_API_KEY) as mgmt:
    sub = await mgmt.create_subscription(
        transport=SubscriptionTransport.WEBHOOK,
        endpoint="https://you.example/hook",
        event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
        scope=CategoryScope(category=MarketCategory.STEAM),
    )
    print(sub.secret)  # секрет подписи — отдаётся ОДИН раз, сохрани сейчас
```

`scope` сужает то, что получит **этот** подписчик. Что движок поллит вообще — решает `LZT_CATEGORIES`, и этот пайплайн общий для всех. Скоуп, который никогда не совпадёт с `event_types` (например категория на `rating_changed`), отклоняется при создании.

На своей стороне проверь подпись:

```python
from fastapi import FastAPI, Request, Response
from lzt_eventus.delivery.signing import verify_webhook

app = FastAPI()


@app.post("/hook")
async def hook(request: Request) -> Response:
    body = await request.body()  # СЫРЫЕ байты, до парсинга
    if not verify_webhook(secret=SECRET, body=body, presented=request.headers.get("X-LZT-Signature")):
        return Response(status_code=401)
    # доставка at-least-once → дедуплицируй по Idempotency-Key, потом обрабатывай
    return Response(status_code=200)  # 2xx подтверждает; не-2xx → ретрай → DLQ
```

### 3. SSE / WebSocket — стрим

`SubscriptionTransport.SSE` или `.WEBSOCKET` при создании подписки. Остальное — как у вебхука.

### 4. Polling — pull вместо push

Когда наружу торчать нечем: за файрволом, из cron.

```python
sub = await mgmt.create_subscription(
    transport=SubscriptionTransport.POLLING,
    endpoint="my-cron-poller",
    event_types=[EventType.NEW_LOT],
)
# sub.secret и sub.stream_token оба None — поллинг уже закрыт admin-ключом,
# push-credential минтить не нужно

batch = await mgmt.poll_pending(sub.subscription_id, limit=100)
for event in batch.items:
    print(event.seq, event.event_type, event.data)

await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
```

Ловушка: по умолчанию (`read_all=False`) `poll_pending` курсор **не** двигает. Тот же батч вернётся при ретрае — это нарочно, чтобы можно было посмотреть на события до коммита.

Подтверждать — либо инлайн (`read_all=True` коммитит ровно просканированное), либо явно через `confirm_read` по границе `seq`, если обработалась только часть. `confirm_read` идемпотентен: повтор со старым `seq` — no-op.

Ошибки management API — всегда типизированный конверт `{"error": "<code>", "detail": {...}, "request_id": "..."}`, никогда голый `HTTPException`:

| Код | Статус | Когда |
|---|---|---|
| `unknown_event_type` | 400 | Фильтра нет в каталоге `EventType` |
| `invalid_limit` | 400 | `limit` не положительное целое |
| `limit_too_large` | 400 | `limit` больше `LZT_MAX_QUERY_LIMIT` (по умолчанию 500) |
| `not_a_polling_subscription` | 400 | Подписка есть, но зарегистрирована с push-транспортом |
| `subscription_not_found` | 404 | Подписки нет |

Границу `limit` держит `LimitValidationMiddleware` — читает `?limit=` из query до входа в роут, поэтому любой нынешний и будущий эндпоинт получает одинаковый потолок бесплатно.

### Devkit — движок и его API одним `async with`

Для скриптов и экспериментов: живой поллящий движок + management API на эфемерном порту, без Postgres и Redis.

```python
from lzt_eventus.devkit import local_eventus

async with local_eventus(tokens=["<token>"]) as server:
    async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
        ...
```

Полный потребитель на ~10 строк поверх него — [`examples/autobuy`](examples/autobuy).

---

## Management API

HTTP API под admin-ключом (`LZT_ADMIN_API_KEY`): подписки, курсоры, инспекция DLQ, `/events/pending` и `/events/read_events` для pull-поллинга.

**Только POST и GET** — никаких PUT/PATCH/DELETE, это проверяет CI.

Справочник движок хостит сам, внешний доксайт не нужен:

- `http://<host>:27543/scalar` — [Scalar](https://github.com/scalar/scalar), каждый роут и DTO можно потыкать
- `http://<host>:27543/docs` — Swagger UI

Оба гасятся через `LZT_WEB_DOCS_ENABLED=false`.

Правило синхронизации wire-контракта с [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk) — в [`AGENTS.md`](AGENTS.md).

---

## Конфигурация

Всё читает `EngineConfig` с префиксом `LZT_`. Аннотированный полный список — в [`.env.example`](.env.example).

Обязательные помечены `*`, у остального рабочий дефолт.

| Переменная | По умолчанию | Значение |
|---|---|---|
| `LZT_TOKENS` `*` | `[]` | Токен(ы) lzt.market, JSON-массив |
| `LZT_ADMIN_API_KEY` `*` | — | Ключ management API |
| `LZT_DATABASE_URL` | `postgresql://…` | DSN Postgres |
| `LZT_REDIS_URL` | `redis://localhost:6379/0` | URL Redis |
| `LZT_CATEGORIES` | `["steam"]` | Какие категории поллить |
| `LZT_MIN/MAX/DEFAULT_CADENCE` | `6` / `120` / `30` | Границы частоты поллинга, секунды |
| `LZT_PER_PAGE` | `50` | Размер страницы каталога |
| `LZT_DISAPPEAR_POLLS` | `3` | Сколько поллингов лот должен отсутствовать, чтобы считаться пропавшим |
| `LZT_CONFIRM_BUDGET_FRACTION` / `_BATCH_SIZE` | `0.25` / `50` | Бюджет и батч подтверждений |
| `LZT_SEEN_TTL_SECONDS` | `86400` | Окно дедупа увиденных лотов |
| `LZT_BATCH_SIZE` / `LZT_BATCH_LINGER` | `50` / `0.05` | Батчинг приёма |
| `LZT_MAX_HANDLE_ATTEMPTS` | `5` | Попыток доставки до DLQ |
| `LZT_RETENTION_MONTHS` | `3` | Retention event-лога |
| `LZT_MAX_SINK_LAG` | `100000` | Отставание потребителя до аларма |
| `LZT_WARN_WINDOW_HOURS` | `24` | Окно предупреждения аналитики |
| `LZT_DEAL_THRESHOLD` | `0.85` | `price < ai_price * threshold` |
| `LZT_HEALTH_HOST` / `_PORT` | `0.0.0.0` / `27543` | HTTP-сервер (`/healthz`, `/metrics`) |
| `LZT_POSTGRES_PORT` / `LZT_REDIS_PORT` | `27542` / `27541` | Хостовые порты compose, на loopback |
| `LZT_ADVISORY_LOCK_KEY` / `LZT_RUN_ID` | `1819571811` / `engine` | Выборы единственного писателя, id прогона |
| `LZT_MAX_QUERY_LIMIT` | `500` | Потолок `?limit=` на любом эндпоинте |
| `LZT_WEB_DOCS_ENABLED` | `true` | Отдавать `/docs` и `/scalar` |

Порт нестандартный намеренно — см. [гайд по деплою](docs/deploy.md).

---

## Скрипты

Все под [`scripts/`](scripts/): `set -euo pipefail`, `--help`, идемпотентны.

| Скрипт | Назначение |
|---|---|
| `quickstart.sh` | Интерактивный бутстрап: вопросы → `.env` → `install.sh` → отчёт |
| `install.sh` | Чистый хост → работающий демон, в один проход |
| `setup_tls.sh` | nginx + certbot для `LZT_DOMAIN`, вызывается из `install.sh` |
| `update.sh` | Rolling-обновление с health-gate и автооткатом |
| `rollback.sh` | Откат последнего обновления: код + один шаг миграции + рестарт |
| `migrate.sh` | `alembic upgrade head` |
| `seed.sh` | Загрузить записанные страницы каталога офлайн, для dev/CI |
| `replay.sh` | `--consumer X --from-seq N` — отмотать курсор для бэкфилла |
| `redrive.sh` | `--consumer X` — переинжектить события из DLQ после фикса |
| `prune.sh` | Retention: удалить строки лога ниже watermark потребителя |
| `backup.sh` / `restore.sh` | pg_dump / pg_restore event-лога |
| `status.sh` / `logs.sh` / `stop.sh` / `restart.sh` | Жизненный цикл и наблюдаемость |
| `autoupdate.py` | Rolling авто-апдейтер, конфигурируемый |
| `health.py` | Отдельный probe `/healthz` + `/readyz`, им гейтится обновление |

Снести всё: `docker compose -f deploy/docker-compose.yml down -v`. Без `-v` данные Postgres и Redis переживут переустановку.

---

## Для AI-агентов

Два скилла Claude Code в [`.claude/skills/`](.claude/skills/), чтобы агент не реверсил проект:

- [`lzt-integration`](.claude/skills/lzt-integration/SKILL.md) — использование: чтение каталога, подписка in-process, приём вебхуков, поллинг.
- [`lzt-extending`](.claude/skills/lzt-extending/SKILL.md) — расширение ядра наследованием и внедрением: свой тип события, роут, источник, бэкенд хранилища или транспорта, без правки исходников либы.

---

## Контрибьютинг

`main` защищена: PR обязан пройти CI ([ruff, ruff format, `mypy --strict`, `pytest --cov-fail-under=80`, gitleaks, pip-audit](.github/workflows/ci.yml)) и ревью CODEOWNERS.

Конвенции и локальный CI-порог — [`CONTRIBUTING.md`](CONTRIBUTING.md). Охват и non-goals — [`ROADMAP.md`](ROADMAP.md). Баги и запросы фич — [issues](https://github.com/open-lzt/lzt-eventus/issues/new/choose).

<a href="https://github.com/zlexdev"><img src="https://github.com/zlexdev.png" width="48" height="48" style="border-radius:50%" alt="zlexdev"></a>

## Лицензия

[MIT](LICENSE). Перед использованием прочитай [дисклеймер legal / ToS](docs/legal.md) — только чтение каталога и автоматизация аналитики; за соблюдение условий lzt.market отвечаешь ты.
