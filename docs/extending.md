# Расширение pylzt

<p align="right"><a href="extending.en.md">English</a> · <b>Русский</b></p>

Всё изменяемое в SDK — это шов: наследуй базовый класс из **публичной поверхности**
(`pylzt.__all__`) и передай его — никогда не редактируй библиотеку напрямую. Это контракт,
на котором строится модуль-потребитель (например `autobuy`). Глубокий гайд для AI-агента —
скилл `lzt-extending`; эта страница — карта для человека.

## Карта швов

| Что варьируется… | Наследуй | Внедряй через | По умолчанию |
|---|---|---|---|
| API-операция | `BaseMethod[T]` | `client.execute(MyMethod(...))` | методы каталога |
| Апстрим-бэкенд | `BaseTransport` | `Client(transport=...)` | `HttpxSession` |
| Сквозная политика запросов | `BaseMiddleware` | `session.request_middlewares.register(...)` | `LoggingMiddleware` |
| «Это ответ с ошибкой?» | `LztError` (`__wire__ = True`) | наследование — саморегистрация | 9 встроенных wire-ошибок |
| Стратегия выбора токена | `BaseTokenSelector` | `RoundRobinTokenPool(tokens, selector=...)` | `RoundRobinSelector` |
| Весь пул токенов | `BaseTokenPool` | `Client(token_pool=...)` | `RoundRobinTokenPool` |
| Источник прокси | `BaseProxySource` | `Client(proxy_source=...)` | нет (`NullProxyPool`) |
| Политика ретраев | `BaseRetryPolicy` | `Client(retry=...)` | `ExponentialBackoff` |
| Бэкенд read-through кэша | `BaseCache[T]` | `Client(category_cache=...)` | `MemoryCache` |
| Приёмник метрик | `BaseMetrics` | `Client(metrics=...)` | `NullMetrics` |
| Источник времени (тесты) | `Clock` | `Client(clock=...)` | `RealClock` |
| DTO со связанными операциями | `BoundModel` | возвращается связанным из `execute` | `Lot` |

## Примеры

Кастомная операция (method-as-class — frozen dataclass, без pydantic):

```python
from dataclasses import dataclass
from pylzt import BaseMethod, HttpMethod, Client


@dataclass(frozen=True, slots=True)
class GetSellerRating(BaseMethod[float]):
    __http_method__ = HttpMethod.GET
    __url__ = "/seller/{seller_id}/rating"
    __path_fields__ = frozenset({"seller_id"})
    seller_id: int

    def parse_response(self, response):
        return float(response.body.get("rating", 0.0))


rating = await client.execute(GetSellerRating(seller_id=42))
```

Кастомная стратегия выбора токена (пул всё равно соблюдает бюджет частоты запросов):

```python
from pylzt import BaseTokenSelector, RoundRobinSelector  # наследуй любой из них
from pylzt.token_pool.round_robin import RoundRobinTokenPool


pool = RoundRobinTokenPool(tokens, selector=MyWeightedSelector())
client = Client(token_pool=pool)
```

Redis-бэкенд кэша, общий для нескольких процессов:

```python
from pylzt import BaseCache, Client


class RedisCache(BaseCache[dict]):
    async def get(self, key): ...

    async def set(self, key, value, *, ttl): ...


client = Client(tokens=[...], category_cache=RedisCache())
```

## Правила, которые должно соблюдать твоё расширение

Всё типизировано (`mypy --strict`); деньги — `Decimal`; даты — UTC; типизированный подкласс
ошибки от `LztError`, несущий **аргументы** (а не отформатированную строку); для нового ABC
хранилища поставляй дефолт `Memory*` + контрактный тест (`tests/pylzt/contracts/`), чтобы
твой бэкенд доказал, что ведёт себя идентично. Никогда не протаскивай сторонний тип
(`httpx.*`) через публичную сигнатуру.
