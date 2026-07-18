# Документация lzt-eventus

<p align="right"><a href="README.en.md">English</a> · <b>Русский</b></p>

Типизированный async SDK с пулом токенов над каталожным API lzt.market, плюс опциональный
durable **event engine** (поллинг → диффинг → воспроизводимый лог → catch-up шина).

README — входная дверь; это — сам дом. Всё, что импортирует потребитель, есть в
`pylzt.__all__` — стабильной публичной поверхности.

## Использование

- [Быстрый старт](usage/quickstart.md) — установка, создание `Client`, первое чтение.
- [Чтение каталога](usage/catalog.md) — лоты, фильтры, пагинация, батчи, связанный `refresh()`.
- [Конфигурация и dependency injection](usage/configuration.md) — `ClientConfig`, замена транспорта / кэша / прокси / ретраев / метрик / выбора токена.
- [Обработка ошибок](usage/errors.md) — типизированное дерево ошибок, ретраи, реестр check().
- [Event engine](usage/event-engine.md) — подписка на события каталога и запуск демона.

## Деплой

- [Гайд по деплою](deploy.md) — Docker Compose vs bare-metal, автообновление, домен + автоматический TLS.
- [Deploy guide](deploy.en.md) — то же самое на английском.

## Расширение

- [Точки расширения](extending.md) — карта швов: наследуй базовый класс, внедри его, никогда не форкай.
- Скилл `lzt-extending` — глубокий гайд для AI-агента по тем же швам.

## Установка

```bash
pip install "git+https://github.com/open-lzt/pylzt.git"                            # только SDK pylzt
pip install "lzt-eventus[engine] @ git+https://github.com/open-lzt/lzt-eventus.git"  # + durable-хранилища и рантайм демона (postgres/redis/fastapi)
```

`import pylzt` не выполняет **никакого I/O** — `httpx` импортируется лениво, только когда
`Client`/`HttpxSession` реально отправляет запрос.
