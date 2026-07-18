# Конфигурация и dependency injection

<p align="right"><a href="configuration.en.md">English</a> · <b>Русский</b></p>

Всё подключаемое — это аргумент конструктора с рабочим значением по умолчанию: ничего не
захардкожено, глобалов нет. Передавай только то, что хочешь изменить.

## Конструктор `Client`

```python
Client(
    tokens=None,            # Sequence[str | Token] — сырые токены, оборачиваются в пул
    *,
    transport=None,         # BaseTransport   — по умолчанию: HttpxSession(config.base_url)
    token_pool=None,        # BaseTokenPool   — по умолчанию: RoundRobinTokenPool(tokens)
    proxy_source=None,      # BaseProxySource — по умолчанию: нет (NullProxyPool)
    retry=None,             # BaseRetryPolicy — по умолчанию: ExponentialBackoff
    metrics=None,           # BaseMetrics     — по умолчанию: NullMetrics
    clock=None,             # Clock           — по умолчанию: RealClock
    category_cache=None,    # BaseCache       — по умолчанию: MemoryCache
    config=None,            # ClientConfig    — по умолчанию: ClientConfig()
)
```

Передавай либо `tokens=` (пул строится за тебя), **либо** уже полностью собранный
`token_pool=`.

## `ClientConfig`

Единый frozen, типизированный конфиг-объект (Law 20) — никакого kwargs-супа:

```python
from pylzt import ClientConfig, Client


config = ClientConfig(
    base_url="https://prod-api.lzt.market",
    general_per_min=20,  # бюджет класса `general` на токен
    search_per_min=10,  # бюджет класса `search` на токен (страницы листинга)
    request_timeout=30.0,
    per_page=50,
    batch_size=50,  # лимит задач в /batch
    batch_linger=0.05,  # окно коалесцирования батча (секунды)
    category_params_ttl=3600.,  # TTL кэша category_params
)
client = Client(tokens=["<token>"], config=config)
```

## Ограничение частоты запросов (почему это автоматически)

Каждый запрос декларирует `RateClass` (`GENERAL` или `SEARCH`); пул токенов блокируется, пока
у токена не появится бюджет в этом классе, поэтому **тебе никогда не нужно спать или считать
запросы самому**. Бюджеты — **на токен**: добавляй больше токенов, чтобы линейно
масштабировать пропускную способность. Страницы листинга — класса `search`; отдельные лоты,
батчи и категории — класса `general`.

## Замена бэкендов

### Кэш (общий для нескольких процессов)

```python
from pylzt import BaseCache, Client


class RedisCache(BaseCache[dict]):
    async def get(self, key): ...

    async def set(self, key, value, *, ttl): ...


client = Client(tokens=["<token>"], category_cache=RedisCache())
```

### Прокси

Поставляй exit-IP, реализовав `BaseProxySource`; пул привязывает **липкий** прокси на токен
и передаёт результаты success/ban обратно в health каждого прокси.

```python
client = Client(tokens=[...], proxy_source=MyProxySource())
```

### Политика ретраев

```python
from pylzt import BaseRetryPolicy, ExponentialBackoff


class NoRetry(BaseRetryPolicy):
    def next_delay(self, attempt: int, exc) -> float | None:
        return None  # сразу сдаться


client = Client(tokens=[...], retry=NoRetry())  # по умолчанию: ExponentialBackoff
```

`ExponentialBackoff` уважает `Retry-After` при `429`, добавляет джиттер и считает
`AuthFailed / Forbidden / NotFound / BadRequest` терминальными (без ретрая).

### Метрики и наблюдаемость

Клиент эмитит счётчики через внедрённый `BaseMetrics`; по умолчанию — no-op. Подключай
Prometheus / OTel без того, чтобы SDK импортировал хоть один из них:

```python
from pylzt import BaseMetrics, Client


class PromMetrics(BaseMetrics):
    def incr(self, name, value=1, **labels): ...

    def gauge(self, name, value, **labels): ...

    def observe(self, name, value, **labels): ...


client = Client(tokens=[...], metrics=PromMetrics())
```

### Стратегия выбора токена

Дефолтный пул честно ротирует по всему флоту. Перестрой приоритеты (взвешенно, с учётом
здоровья, LRU), внедрив `BaseTokenSelector` — пул всё равно продолжает соблюдать бюджет
каждого токена, селектор решает только **порядок**, в котором токены пробуются:

```python
from pylzt import BaseTokenSelector, RoundRobinSelector
from pylzt.token_pool.round_robin import RoundRobinTokenPool


pool = RoundRobinTokenPool(tokens, selector=MyWeightedSelector())
client = Client(token_pool=pool)
```

### Транспорт + middleware

`HttpxSession` — единственный транспорт SDK: `BaseTransport` на httpx с цепочкой request
middleware. Сужение ответа (статус/тело → типизированный `LztError`) — это
саморегистрирующийся реестр `LztError.check()`, а не опция транспорта — расширяй его,
наследуя `LztError` (подробности в [Обработке ошибок](errors.md) и
[Расширении](../extending.md)):

```python
from pylzt import HttpxSession, Client


session = HttpxSession(base_url="https://prod-api.lzt.market")
session.request_middlewares.register(MyLoggingMiddleware())
client = Client(tokens=[...], transport=session)
```

## Тестирование с фейковыми часами

`clock=FakeClock()` делает логику rate-limit и cache-TTL детерминированной в тестах —
двигай время вручную вместо реального ожидания:

```python
from pylzt import Client, FakeClock


clock = FakeClock()
client = Client(tokens=[...], clock=clock)
clock.advance(60.0)  # минута пополнения бюджета без реального ожидания
```
