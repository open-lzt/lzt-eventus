# Быстрый старт

<p align="right"><a href="quickstart.en.md">English</a> · <b>Русский</b></p>

## Установка

```bash
pip install pylzt
```

Сам по себе `import pylzt` не открывает ни одного сокета — `httpx` (единственный
транспорт SDK, `HttpxSession`) импортируется лениво, только когда `Client` реально
отправляет запрос.

## Создание клиента

Клиент — это async context manager: всегда используй его именно так, чтобы соединения и
пул токенов освобождались при выходе (Law 24).

```python
import asyncio
from pylzt import Client, LotFilter, Category


async def main() -> None:
    async with Client(tokens=["<your-lzt-market-token>"]) as client:
        # 20 самых свежих лотов Steam
        lots = await client.market.list_lots(
            LotFilter(category=Category.STEAM)
        ).collect(limit=20)
        for lot in lots:
            print(lot.item_id, lot.price, lot.currency, lot.title)


asyncio.run(main())
```

`tokens=[...]` принимает сырые строки токенов (внутри оборачиваются в `Token`) — передай
больше одного, и пул будет ротировать между ними, учитывая бюджет частоты запросов каждого
токена отдельно.

## Добавление request middleware

`Client(tokens=[...])` сам строит `HttpxSession` под капотом; передай собственный экземпляр,
чтобы зарегистрировать middleware (логирование, трейсинг) в цепочке запроса (см.
[Конфигурацию](configuration.md)):

```python
from pylzt import Client, HttpxSession


session = HttpxSession(base_url="https://prod-api.lzt.market")
session.request_middlewares.register(MyLoggingMiddleware())
async with Client(tokens=["<token>"], transport=session) as client:
    lot = await client.market.get_lot(ItemId(123456))
```

## Что предоставляет клиент

| Вызов | Возвращает | Примечания |
|---|---|---|
| `list_lots(filter, *, max_pages=None)` | `Paginator[Lot]` | `async for` или `.collect(limit=)` |
| `get_lot(item_id)` | `Lot` | связанный — `await lot.refresh()` перечитывает заново |
| `get_lots_batch(item_ids)` | `list[Lot]` | запросы `/batch`, разбитые на чанки по лимиту сервера в 10 задач, в порядке ввода |
| `list_categories()` | `list[Category]` | активные категории маркета |
| `category_params(category)` | `FilterSchema` | схема фильтра, кэшируется (TTL) |
| `category_games(category)` | `list[CategoryGame]` | игры в категории |
| `execute(method)` | `T` | выполнить кастомный `BaseMethod[T]` |
| `request(method, path, ...)` | `Response` | лазейка для незавёрнутых эндпоинтов |

Это **read-only**-поверхность — здесь нет методов покупки/публикации (это живёт в
модуле-потребителе, построенном поверх этих швов). Далее: [Чтение каталога](catalog.md).
