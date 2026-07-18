# Деплой event-engine

<p align="right"><a href="deploy.en.md">English</a> · <b>Русский</b></p>

Пошаговый гайд по запуску демона `event_engine` на сервере, с опциональным
публичным доменом и автоматически продлеваемым TLS-сертификатом перед admin
API и inbound-вебхуком. Рассчитан на **полностью пустой** сервер — скрипты
сами ставят всё необходимое (Docker, `uv`, а при настройке домена — nginx и
certbot).

Есть два режима деплоя, выбери один:

- **Docker Compose** (рекомендуется) — `scripts/install.sh` управляет
  `deploy/docker-compose.yml`, который поднимает Postgres, Redis и engine в
  изолированных контейнерах.
- **Bare-metal / VM** — `deploy/lzt-core.service` запускает демон напрямую
  через `uv`, против Postgres/Redis, которые ты ставишь и обслуживаешь сам.

Оба режима используют один и тот же файл `.env`. TLS (ниже) — один и тот же
механизм для обоих: host-level nginx + certbot, настраивается через
`scripts/setup_tls.sh`.

## Быстрый старт (для ленивых — одна команда)

```bash
git clone https://github.com/open-lzt/lzt-eventus.git lzt-core && cd lzt-core && scripts/quickstart.sh
```

Интерактивно спрашивает токен lzt.market, опционально домен + контактный
email, сам генерирует `LZT_ADMIN_API_KEY`, дальше передаёт управление
`install.sh` — всё остальное скрипт делает сам. В конце — отчёт: ссылка на
health-check, admin API key (показывается один раз, та же конвенция, что и
для секретов вебхуков), ссылка на доки. При повторном запуске (когда `.env`
уже существует) сразу переходит к `install.sh` (правь `.env` руками для
изменений). Всё, что описано ниже в этом гайде — то, что `quickstart.sh`
делает за тебя автоматически; читай дальше, если нужен ручной/скриптуемый
путь или хочется понять, что означает каждый шаг.

## Предпосылки

- Сервер (VM или bare-metal) с публичным IP. `install.sh` рассчитан на
  Debian/Ubuntu (`apt-get`) и сам ставит Docker + `uv`, если их нет —
  предустанавливать ничего не нужно.
- Токен(ы) API lzt.market — https://lzt.market/account/api
- **Опционально, для публичного домена**: домен/поддомен с A- (и/или AAAA-)
  записью, указывающей на IP сервера, и доступные снаружи порты TCP 80 + 443
  (HTTP-01 challenge Let's Encrypt требует порт 80). Без домена всё
  остальное всё равно работает — движок остаётся доступен только по
  loopback, через SSH-туннель или приватную сеть вместо HTTPS.

Каждый порт, который публикует стек (`LZT_HEALTH_PORT`, `LZT_POSTGRES_PORT`,
`LZT_REDIS_PORT`), по умолчанию — конкретное нестандартное пятизначное число
(27543 / 27542 / 27541 — см. `.env.example`), а не привычные 9189/5432/6379.
На общем сервере, где уже крутятся другие проекты, шанс, что там уже занят
именно такой «странный» номер, гораздо ниже, чем что кто-то уже держит
дефолтный порт. Меняй значения в `.env` только если реально столкнулся с
коллизией (`scripts/install.sh` проверяет это и громко предупреждает, но
никогда не перебинживает молча).

```bash
git clone https://github.com/open-lzt/lzt-eventus.git lzt-core
cd lzt-core
cp .env.example .env
```

Отредактируй `.env`:

```ini
LZT_TOKENS=["your-real-token"]
LZT_ADMIN_API_KEY=<вставь вывод: openssl rand -hex 32>
LZT_CATEGORIES=["steam"]                    # JSON-массив категорий для опроса

# Только если есть домен (см. «Домен + автоматический TLS» ниже):
LZT_DOMAIN=events.example.com
LZT_ACME_EMAIL=you@example.com
```

## Вариант A — Docker Compose (`scripts/install.sh`)

```bash
scripts/install.sh
```

Одна идемпотентная команда: ставит Docker + `uv`, если их нет, создаёт
`.env` из `.env.example`, если его ещё нет, поднимает Postgres + Redis,
прогоняет Alembic-миграции, собирает и запускает движок, гейтит его по
health-чеку, и — если в `.env` задан `LZT_DOMAIN` — запускает
`scripts/setup_tls.sh`, чтобы выдать доверенный сертификат. Безопасно
перезапускать в любой момент (`--no-start` только провижинит хранилища и
мигрирует, без запуска движка).

Проверка:

```bash
curl -s http://127.0.0.1:27543/healthz
scripts/status.sh
```

Порт движка публикуется только на `127.0.0.1` — без TLS сам по себе он не
предназначен для прямого доступа из интернета. Задай `LZT_DOMAIN` (ниже),
чтобы сделать его доступным по HTTPS, либо оставь loopback-only и заходи
через SSH-туннель / приватную сеть.

## Вариант B — Bare-metal / VM (systemd)

Нужен `uv` и Postgres 16 + Redis 7, которые ты запускаешь сам (должны
соответствовать `LZT_DATABASE_URL` / `LZT_REDIS_URL` в `.env`).

```bash
sudo mkdir -p /opt/lzt-core
sudo cp -r . /opt/lzt-core          # или клонируй сразу в /opt/lzt-core
sudo cp .env /opt/lzt-core/.env
sudo useradd --system --home /opt/lzt-core lzt || true
sudo chown -R lzt:lzt /opt/lzt-core

sudo cp deploy/lzt-core.service /etc/systemd/system/lzt-core.service
sudo systemctl daemon-reload
sudo systemctl enable --now lzt-core
```

`ExecStartPre` при каждом старте прогоняет `uv sync`, так что после
`git pull` достаточно `systemctl restart lzt-core`, чтобы подхватить новые
зависимости. Проверка:

```bash
systemctl status lzt-core
curl -s http://127.0.0.1:27543/healthz
```

### Авто-обновление (оба режима)

`deploy/autoupdate.yml` + `scripts/autoupdate.py` опрашивают git-реф, и при
новом коммите: pull → `uv sync` → `alembic upgrade` → гейт `pytest -m e2e` →
рестарт → health-check → автоматический откат, если `/healthz` не
восстановился. **Выключено по умолчанию** (`enabled: false`) — это
осознанно, не включай, если не хочешь неконтролируемых раскаток. Включить на
bare-metal:

```bash
sudo cp deploy/lzt-core-autoupdate.service deploy/lzt-core-autoupdate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lzt-core-autoupdate.timer
```

Сначала выстави `enabled: true` в `deploy/autoupdate.yml` — иначе таймер
ничего не делает. Выключи обратно (`false`), когда закончишь тестировать:
поставляемый дефолт (и любой свежий клон) держит это выключенным.

## Домен + автоматический TLS

Движок закрывается **host-level nginx + certbot**
(`scripts/setup_tls.sh`), не отдельным контейнером — это осознанное решение:
если на сервере уже заняты порты 80/443 другими сайтами (общий VPS,
существующий reverse-proxy), туда просто добавляется **ещё один vhost**, а
не второй процесс, который спорит за те же порты. На реально пустом сервере
скрипт сам ставит nginx и certbot. Скрипт один и тот же в обоих случаях.

Предпосылки: `LZT_DOMAIN` резолвится на этот сервер
(`dig +short $LZT_DOMAIN @1.1.1.1` совпадает с IP сервера — проверяй с
самого сервера, не с ноутбука) и `LZT_ACME_EMAIL` задан в `.env`.

```bash
scripts/setup_tls.sh
```

Что делает: ставит `nginx`/`certbot`/`python3-certbot-nginx`, если их нет,
пишет `/etc/nginx/sites-available/$LZT_DOMAIN.conf` с проксированием на
`127.0.0.1:$LZT_HEALTH_PORT` (по умолчанию 27543), симлинкует в
`sites-enabled`, перезагружает nginx, затем запускает nginx-плагин certbot,
чтобы выпустить сертификат и переписать vhost под HTTPS + редирект. Уже
вызывается автоматически из `scripts/install.sh`, если задан `LZT_DOMAIN` —
запускай отдельно только чтобы добавить/продлить TLS постфактум (например,
задал `LZT_DOMAIN` уже после установки) или переприменить vhost, если
правил его руками.

Проверка:

```bash
curl -s https://$LZT_DOMAIN/healthz
```

### Заметки

- Без `LZT_DOMAIN` движок остаётся loopback-only — `setup_tls.sh` ничего не
  делает (ни при вызове из `install.sh`, ни отдельно).
- **certbot сам следит за продлением** — пакет Debian/Ubuntu ставит
  собственный `certbot.timer`/cron-запись при установке; ничего
  дополнительно настраивать не нужно.
- **Общий (multi-tenant) сервер:** `setup_tls.sh` пишет только
  `sites-available/$LZT_DOMAIN.conf` и его симлинк — никогда не трогает
  другие vhost'ы, а certbot запрашивает сертификат только для
  `$LZT_DOMAIN`. Проверено — безопасно рядом с другими сайтами на nginx на
  том же хосте.
- Рейт-лимиты: Let's Encrypt ограничивает число запросов сертификата на
  домен в неделю. Не гоняй `scripts/setup_tls.sh` в цикле, пока отлаживаешь
  DNS — сначала почини DNS (`dig`), потом выпускай сертификат один раз.
- Admin API (`LZT_ADMIN_API_KEY`) и inbound-вебхук
  (`LZT_LOLZ_WEBHOOK_SECRET`) всё равно гейтятся собственной авторизацией за
  nginx — TLS шифрует транспорт, но не заменяет эти проверки.

## Файрвол

Только если сам управляешь файрволом (на свежем сервере обычно ничего не
активно) — **сначала проверь `ufw status`**; не включай его вслепую на
сервере, где другие сервисы уже полагаются на существующие правила:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

**Не** открывай порт 27543 наружу — это открытый HTTP; доступ только через
nginx (443) или `127.0.0.1`.

## Диагностика проблем

| Симптом | Вероятная причина |
|---|---|
| `certbot --nginx` падает с `Timeout during connect` | `LZT_DOMAIN` ещё не резолвится на IP этого сервера — проверь `dig +short $LZT_DOMAIN @1.1.1.1` с самого сервера, подожди распространения DNS. |
| `too many certificates already issued` | Рейт-лимит Let's Encrypt — перестань пытаться, дождись окна (точная длительность в их доках). |
| `/healthz` не отвечает после `scripts/install.sh` | Сам движок нездоров, дело не в TLS — сначала смотри `scripts/logs.sh`. |
| Auto-update откатывается на каждом прогоне | Упал `e2e_gate: true` — свежепритянутый коммит не проходит `pytest -m e2e`; почини тест/регрессию перед следующей раскаткой. |
| `docker compose` ошибается на относительных путях | Compose ≥ 5 резолвит `context:`/`env_file:`/пути volume относительно `--project-directory`, а не относительно compose-файла — `compose()` в `scripts/_lib.sh` уже передаёт абсолютный путь `-f`, поэтому project directory резолвится верно; не вызывай `docker compose` напрямую с относительным `-f` из другого `cwd`. |
| `pydantic_settings.exceptions.SettingsError` при разборе `categories` | `LZT_CATEGORIES` должен быть JSON-массивом (`["steam"]`), как и `LZT_TOKENS` — парсера строки через запятую нет, что бы ни говорили старые доки. |
| `ModuleNotFoundError: No module named 'webhook_engine'` внутри контейнера | Исправлено в текущем `deploy/Dockerfile` этого репо (там есть `COPY libs ./libs` и установка с `--no-editable`) — если форкнул/вендорнул Dockerfile, убедись, что оба есть: editable-хук hatchling эмитит `.pth` только для `src/`, молча теряя воркспейс-пакет `libs/webhook_engine`, хотя список `packages` в `pyproject.toml` для wheel его называет. |
| `ModuleNotFoundError: No module named 'psycopg2'` при старте демона (миграции при этом прошли нормально) | «Голый» DSN `postgresql://` заставляет SQLAlchemy по умолчанию выбрать синхронный драйвер psycopg2; `alembic/env.py` уже защитно переписывает его в `postgresql+asyncpg://`, `event_engine.orm.base.build_async_sessionmaker` теперь делает то же самое — исправлено в текущем `src/event_engine/orm/base.py` этого репо. |
| Alembic коннектится не в ту базу (`localhost:5432` вместо compose-сети) | Фоллбэк вида `${LZT_DATABASE_URL:-postgres:5432-дефолт}` в блоке `environment:` `docker-compose.yml` небезопасен — любой скрипт, уже экспортировавший bare-metal DSN из `.env` в свой шелл (`load_env` в `_lib.sh`), заставляет унаследованное из шелла значение выигрывать у дефолта из compose-файла, когда `docker compose` его резолвит. В текущем compose-файле DSN на compose-сеть захардкожен безусловно именно поэтому — не возвращай туда фоллбэк через `:-`. |
| `compose run`/`compose up` молча использует устаревший образ после изменения исходников | Ни та, ни другая команда сама не пересобирает уже собранный образ. `install.sh`/`update.sh` теперь явно гоняют `compose build engine` перед миграцией/рестартом — не переходи сразу к `compose run`/`up`, минуя сборку. |
| Громкое предупреждение о занятом порте при `install.sh` на повторном запуске **своего же** уже поднятого стека | Ожидаемо и безобидно — `port_in_use` не может отличить «твой же контейнер уже слушает с прошлого запуска» от настоящей коллизии с чужим сервисом. Разбираться стоит, только если предупреждение вылезло на действительно первом запуске. |

## Смотри также

- [Гайд по event engine](usage/event-engine.md) — подписки, модули, конфиг-параметры.
- [Конфигурация](usage/configuration.md) — справочник `EngineConfig` / `ClientConfig`.
