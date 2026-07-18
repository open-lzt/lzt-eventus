# Event engine

<p align="right"><a href="event-engine.en.md">English</a> · <b>Русский</b></p>

Опциональный пакет `event_engine` превращает каталог в **durable, воспроизводимый поток
событий**: он поллит каждую категорию, диффит снимки, пишет факты в append-only лог и
рассылает их подписчикам по курсору для каждого потребителя (поллинг → диффинг → лог →
catch-up шина). Подписчик, зарегистрировавшийся поздно, воспроизводит весь лог с seq 0 без
пропусков; обработчик, который постоянно падает, паркуется в dead-letter очереди, вместо
того чтобы блокировать поток.

Установи runtime-экстра для durable-бэкенда (Postgres/Redis):

```bash
pip install "lzt-eventus[engine] @ git+https://github.com/open-lzt/lzt-eventus.git"
```

## События

| Событие | Поля | `EventType` |
|---|---|---|
| `NewLotAppeared` | `lot` | `NEW_LOT` |
| `PriceDropped` | `old_price`, `new_price`, `lot` | `PRICE_DROPPED` |
| `LotUpdated` | `lot`, `changed` | `LOT_UPDATED` |
| `LotDisappeared` | `reason`, `confidence` | `LOT_DISAPPEARED` |
| `SnapshotInitialized` | `category`, `lot_count` | `SNAPSHOT_INITIALIZED` |

На холодном старте ты получаешь **одно** `SnapshotInitialized` на категорию (никогда не
шквал событий по каждому лоту); дальше инкрементальные диффы эмитят остальное.
`LotDisappeared` несёт `Confidence` (`NORMAL` / `LOW`), чтобы можно было отличить
подтверждённую продажу от догадки.

## Подписка: `BaseModule`

Подписчик декларирует, какие типы событий ему нужны, и обрабатывает их:

```python
from lzt_eventus.plugins.module import BaseModule, BaseSubscription
from lzt_eventus.events.base import DomainEvent, EventType


class DealWatcher(BaseModule):
    name = "deals"  # уникально — ключ курсора этого потребителя

    def __init__(self) -> None:
        self.subscriptions = [
            BaseSubscription(event_types=frozenset({EventType.PRICE_DROPPED}))
        ]

    async def handle(self, event: DomainEvent) -> None:
        # диспатч последовательный и упорядочен по seq в рамках модуля; локальное состояние безопасно
        print("price dropped:", event)
```

`BaseSubscription.filters` дополнительно сужает по payload (`all(payload[k] == v)`) —
каждое событие `NewLotAppeared`/`PriceDropped`/`LotUpdated`/`LotDisappeared`/
`SnapshotInitialized` несёт `category` в payload, поэтому один подписчик может следить за
одной категорией, пока `config.categories` поллит сразу несколько:

```python
BaseSubscription(
    event_types=frozenset({EventType.PRICE_DROPPED}),
    filters={"category": "steam"},
)
```

## Подписка: декоратор `EventRouter`

Для множества обработчиков на множество типов событий роутер читается естественнее — тот же
контракт, регистрируется так же:

```python
from lzt_eventus.plugins.router import EventRouter
from lzt_eventus.events.base import EventType


router = EventRouter(name="deals")


@router.on(EventType.PRICE_DROPPED)
async def on_drop(event) -> None:
    ...


@router.on(EventType.NEW_LOT, EventType.LOT_DISAPPEARED)
async def on_churn(event) -> None:
    ...
```

## Сборка и запуск

`build_memory` собирает in-process пайплайн (отлично для демо, теста или однонодового
прогона); `build` собирает durable-хранилища Postgres/Redis для настоящего демона.

```python
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from pylzt import Client, Category


config = EngineConfig(tokens=["<token>"], categories=[Category.STEAM])
client = Client(tokens=config.tokens)

engine = EventEngine.build_memory(config, client=client, modules=[DealWatcher()])
await engine.run()  # берёт лизинг, супервизирует поллеры + шину, дренирует при остановке
```

`engine.run()` блокирует, супервизируя флот поллеров и диспатч-шину под одной task-группой.
Останавливай его аккуратно откуда угодно через `engine.request_stop()` (или Ctrl-C).

Для одного детерминированного тика (тесты / `--dry-run`) используй `drain_once()` — один
поллинг всех категорий плюс одна прокачка шины:

```python
await engine.drain_once()
```

## Конфигурация

`EngineConfig` загружается из окружения (префикс `LZT_*`, поддерживается `.env`), поэтому
ни один секрет не живёт в коде:

```bash
LZT_TOKENS=["tok1","tok2"]
LZT_DATABASE_URL=postgresql://lzt:lzt@localhost:5432/lzt_core
LZT_CATEGORIES=["steam","discord"]
LZT_DEFAULT_CADENCE=30
LZT_BUS_MAX_CONCURRENT_MODULES=8      # bulkhead: макс. модулей, диспатчащих одновременно
```

```python
config = EngineConfig()               # читает окружение
```

Ключевые параметры: `categories`, `default_cadence` / `min_cadence` / `max_cadence`
(адаптивный интервал поллинга), `disappear_polls` (сколько подтверждающих проходов до того,
как исчезновение объявлено), `max_handle_attempts` (сколько ретраев до dead-letter),
`bus_max_concurrent_modules` (bulkhead для межмодульного диспатча).

## Изменения в рантайме (без рестарта)

Источники и подписчики можно добавлять/убирать, пока движок работает:

```python
engine.add_module(DealWatcher())      # подхватится на следующей прокачке шины
engine.remove_module("deals")         # курсор остаётся закоммиченным → безопасно возобновить при повторном добавлении
engine.add_poller(my_poller)          # супервизор сразу запускает его задачу
engine.remove_poller("seller-rating") # аккуратно останавливает эту задачу
```

Инфраструктурные синглтоны (`client`, хранилища, config, lease, clock) задаются только на
этапе конструирования — горячая замена log/cursor посреди прокачки застранит курсоры. Чтобы
изменить их — останови и пересобери.

## Что дальше

Добавление нового типа события, нового источника событий (поллера), HTTP-роута, бэкенда
хранилища, транспорта вебхука или in-process подписчика — всё через наследование, никогда
через редактирование движка — описано в скилле **`lzt-extending`** и в
[Точках расширения](../extending.md).
