# Обработка ошибок

<p align="right"><a href="errors.en.md">English</a> · <b>Русский</b></p>

Каждый апстрим-сигнал сужается ровно до одного типизированного подкласса `LztError`,
несущего **структурированные аргументы** (никогда не отформатированную строку), поэтому ты
ветвишься по типу, а не по коду статуса или сообщению. Один маппинг владеет всем
апстрим-словарём; ничто downstream не инспектирует сырой статус.

## Дерево

```
LztError                       code: ErrorCode          возникает при
├─ RateLimited(retry_after)    RATE_LIMITED             429 — пул уважает Retry-After
├─ RetryableUpstream(hint)     RETRY_REQUEST             временный сбой апстрима, ретрай
├─ CaptchaRequired()           CAPTCHA                   капча-гейт
├─ ProxyChallenge(kind)        STEAM_CAPTCHA             exit IP получил челлендж → ротация прокси
├─ AuthFailed(token_id)        UNAUTHORIZED              401 — токен помещён в карантин
├─ Forbidden(scope)            FORBIDDEN                 403
├─ NotFound(item_id)           NOT_FOUND                 404
├─ BadRequest(field)           BAD_REQUEST               4xx клиентская ошибка
├─ TransportError(status)      UPSTREAM_ERROR            5xx
├─ DependencyMissing(extra)    DEP_MISSING               опциональный бэкенд не установлен
├─ MethodDeclarationError(...) METHOD_DECLARATION        некорректно объявленный BaseMethod (на импорте)
└─ ModelNotBound(model)        MODEL_NOT_BOUND           связанная операция на несвязанном DTO
```

Ветвись по типу и читай аргументы:

```python
from pylzt import NotFound, RateLimited, AuthFailed, LztError, ItemId


try:
    lot = await client.market.get_lot(ItemId(123))
except NotFound as e:
    print("исчез:", e.item_id)
except RateLimited as e:
    print("притормози, retry after", e.retry_after)
except AuthFailed as e:
    print("мёртвый токен:", e.token_id)
except LztError as e:
    print("другой апстрим-сигнал:", e.code)  # enum ErrorCode, стабильная классификация
```

Ловишь `LztError` как catch-all; ловишь подкласс для конкретного случая. `e.code` — это
`ErrorCode` (`StrEnum`) — используй его как измерение для логов/метрик.

## Ретраи автоматические

`Client` прогоняет каждый запрос через свой `BaseRetryPolicy` (по умолчанию
`ExponentialBackoff`) **до того**, как ошибка дойдёт до тебя — ты видишь исключение только
после того, как ретраи исчерпаны или ошибка терминальна.

- **Ретраятся** с backoff + джиттером: `RateLimited` (уважает `Retry-After`),
  `TransportError` (5xx), `RetryableUpstream`, `ProxyChallenge` (после ротации прокси).
- **Терминальные** — никогда не ретраятся: `AuthFailed`, `Forbidden`, `NotFound`,
  `BadRequest`.

Меняй политику для конкретного клиента (см. [Конфигурацию](configuration.md)):

```python
from pylzt import BaseRetryPolicy, Client


class FixedTwice(BaseRetryPolicy):
    def next_delay(self, attempt: int, exc) -> float | None:
        return 1.0 if attempt < 2 else None


client = Client(tokens=[...], retry=FixedTwice())
```

## Сужение ответа — реестр `check()` (что считается ошибкой)

«Является ли этот ответ ошибкой?» решает `LztError.match(status, headers, body)`: каждый
wire-facing подкласс `LztError` саморегистрируется (через `__init_subclass__`, включаясь
флагом `__wire__ = True`) и владеет classmethod'ом `check()`; `match()` обходит реестр в
порядке `__priority__` (сначала меньшие) и возвращает первое совпадение либо `None` при
успехе. `HttpxSession` сам вызывает `match()` — ничего дополнительно подключать не нужно.

| статус | ошибка |
|---|---|
| `< 400` (если не совпал маркер в теле, см. ниже) | *(ок)* |
| `429` | `RateLimited` (читает `Retry-After`) |
| `401` | `AuthFailed` |
| `403` | `Forbidden` |
| `404` | `NotFound` |
| `>= 500` | `TransportError` |
| прочие `4xx` | `BadRequest` |

Некоторые API возвращают `200 OK` с ошибкой в теле — `RetryableUpstream`, `ProxyChallenge`
и `CaptchaRequired` все проверяют текст ошибки в теле независимо от статуса и выполняются
раньше проверок по бакетам статусов (меньший `__priority__`).

Расширяй реестр, наследуя `LztError` — подкласс регистрируется сам в момент объявления, без
подключения проводов, без цепочек:

```python
from collections.abc import Mapping
from typing import Any

from pylzt import LztError, ErrorCode


class PurchaseRejected(LztError):
    __wire__ = True
    __priority__ = 15  # раньше встроенных бакетов статусов, после RateLimited

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(ErrorCode.BAD_REQUEST)

    @classmethod
    def check(cls, status: int, headers: Mapping[str, str], body: Mapping[str, Any]) -> "LztError | None":
        if body.get("status") == "error":
            return cls(body.get("reason", ""))
        return None
```

Меньший `__priority__` выполняется раньше; выбирай число относительно встроенной таблицы
выше (`RateLimited=10` … `TransportError=90`) в зависимости от специфичности твоей проверки.

## Ошибки опциональных зависимостей

`HttpxSession` импортирует `httpx` лениво; если он вдруг отсутствует в окружении,
конструирование транспорта кидает `DependencyMissing("httpx")` — типизированную,
actionable-ошибку, а не голый `ImportError` на импорте. `httpx` — базовая зависимость
`pylzt`, так что это срабатывает только при сломанной установке.

## User-facing поверхность

Аргументы `LztError` предназначены для **тебя** (интегратора). Если показываешь сбои
конечным пользователям — маппи на своей границе в дружелюбное сообщение + стабильный код
(`e.code`) и держи структурированные детали на стороне сервера — не протекай `token_id` /
инфраструктурные детали наружу.
