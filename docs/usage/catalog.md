# Чтение каталога

<p align="right"><a href="catalog.en.md">English</a> · <b>Русский</b></p>

Каждое чтение возвращает типизированный, frozen DTO — сырой wire-словарь никогда не
просачивается наружу из парсера. Деньги — `Decimal`, даты — с UTC-таймзоной, id — непрозрачные
(`ItemId`).

## Фильтрация: `LotFilter`

```python
from decimal import Decimal
from pylzt import LotFilter, Category, OrderBy


flt = LotFilter(
    category=Category.STEAM,  # сегмент пути, а не query-параметр
    pmin=Decimal("5"),  # минимальная цена
    pmax=Decimal("50"),  # максимальная цена
    title="cs2",  # заголовок содержит
    game=("730",),  # app id (кортеж → повторяющийся query-параметр)
    order_by=OrderBy.NEWEST,  # PRICE_ASC / PRICE_DESC / NEWEST / OLDEST
)
```

Каждое поле опционально; опускай то, по чему не фильтруешь. `category=None` листает по всем
категориям.

## Пагинация: `list_lots` → `Paginator[Lot]`

Пагинация — это async-итератор: ты никогда не трогаешь `page=` / `has_more` (Law 22).

```python
async with Client(tokens=["<token>"]) as client:
    # потоково, лениво, страница за страницей, все подходящие лоты
    async for lot in client.market.list_lots(flt):
        handle(lot)

    # или слить в список с ограничением
    first_20 = await client.market.list_lots(flt).collect(limit=20)

    # или только первая страница
    page = await client.market.list_lots(flt).first_page()
```

Ограничь общее число страниц через `max_pages`, чтобы обрезать широкий обход:

```python
client.market.list_lots(flt, max_pages=5)   # не более 5 запросов класса `search`
```

## Один лот: `get_lot`

```python
from pylzt import ItemId


lot = await client.market.get_lot(ItemId(123456))
print(lot.price, lot.currency, lot.item_state, lot.seller_id)
```

### Связанный `refresh()`

`Lot`, возвращённый клиентом, несёт в себе клиент, который его создал, поэтому может
перечитать себя заново (aiogram-style связанный метод):

```python
lot = await client.market.get_lot(ItemId(123456))
# …позже, когда состояние маркета могло измениться…
fresh = await lot.refresh()          # возвращает новый, связанный Lot
```

`Lot`, который ты собрал или распарсил сам, — **несвязанный**: вызов `refresh()` на нём
кидает `ModelNotBound` (падает громко, никогда не тихий no-op).

## Несколько сразу: `get_lots_batch`

N id разбиваются на `ceil(N / 10)` параллельных запросов `/batch` (сервер ограничивает один
запрос 10 задачами). Результаты приходят в порядке ввода; id, отсутствующие в ответе, тихо
пропускаются.

```python
lots = await client.market.get_lots_batch([ItemId(1), ItemId(2), ItemId(3)])
# len(lots) может быть < 3, если некоторых id больше не существует
```

Нужен `NotFound` для каждого отдельного элемента вместо тихого пропуска? Используй
request-coalescing примитив `BatchExecutor.submit(item_id)` (`pylzt.lib.batch`).

## Форма `Lot`

| Поле | Тип | Примечания |
|---|---|---|
| `item_id` | `ItemId` | непрозрачный int-id |
| `category` | `Category` | enum; неизвестные слаги → `OTHER` |
| `price` | `Decimal` | никогда не `float` |
| `currency` | `Currency` | всегда рядом с суммой |
| `title` | `str` | |
| `seller_id` | `SellerId` | |
| `published_at` | `datetime` | с UTC-таймзоной |
| `item_state` | `str` | сырое апстрим-состояние (словарь UNVERIFIED) |
| `item_origin` | `ItemOrigin` | как был получен аккаунт |
| `guarantee` | `str` | |
| `nsb` | `bool` | |
| `content_hash` | `str` | дайджест только по **ценообразующим** полям |
| `attributes` | `Mapping[str, str]` | описание / информация |

`content_hash` стабилен при чисто метаданном рефреше (счётчик просмотров, дата поднятия) —
dedup/diff-потребитель ключуется на нём, поэтому косметическое изменение не выглядит как
изменение лота.

## Категории

```python
cats = await client.market.list_categories()               # list[Category]
schema = await client.category_params(Category.STEAM)  # FilterSchema (кэшируется, TTL)
games = await client.category_games(Category.STEAM)    # list[CategoryGame]
```

`category_params` читается через кэш (по умолчанию in-memory, TTL —
`config.category_params_ttl`); подмени бэкенд через `Client(category_cache=...)` — см.
[Конфигурацию](configuration.md). `FilterSchema` / `CategoryGame` — именованные граничные
типы поверх пока ещё UNVERIFIED апстрим-формы.

## Кастомные эндпоинты

Две лазейки для эндпоинтов, которые SDK не оборачивает:

```python
# 1. Сырой запрос через тот же rate-limited канал (теряешь DTO, сохраняешь пул/ретраи):
resp = await client.request("GET", "/some/path", query={"x": 1})   # -> Response

# 2. Типизированный method-as-class (сохраняешь DTO). См. docs/extending.md.
result = await client.execute(MyMethod(...))
```
