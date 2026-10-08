# Руководство по эксплуатации «Своих правил»

Документ для владельца сервиса: как развернуть, обновить, откатить и восстановить прод на одном VPS. Все команды выполняются на сервере от пользователя `deploy`, если не сказано иное. Сам код и образы собирает CI; сервер ничего не собирает.

## 1. Как устроен прод

Один VPS с Ubuntu 24.04 и Docker Compose. Файл стека — `deploy/compose.prod.yaml` (проект `svoi-pravila`).

| Сервис | Назначение | Доступ |
|---|---|---|
| `miniapp` (Caddy) | HTTPS на `SP_DOMAIN`, статика мини-приложения, прокси `/api/*` и вебхука `/tg/<секрет>` | порты 80 и 443 хоста (внутри 8080 и 8443) |
| `api` | бот и API мини-приложения | только сеть `edge` и внутренние сети |
| `migrate` | одноразовый запуск Alembic и создание роли Grafana | нет |
| `postgres`, `valkey` | данные и кэш | только внутренняя сеть `data`, наружу не публикуются |
| `prometheus` | сбор метрик `api:9100` | внутренние сети |
| `grafana` | дашборды | только `127.0.0.1:3000` (SSH-туннель) |
| `backup` | зашифрованный бэкап PostgreSQL (профиль `backup`) | запускается по таймеру и релизом |

Образы `api`, `miniapp`, `grafana`, `backup` публикуются в `ghcr.io/<владелец>/svoi-pravila-<имя>:<git-sha>` автоматически после каждого слияния в `master`, когда зелёны все проверки. На сервер они попадают по digest: `release.sh` пишет их в `/srv/svoi-pravila/releases/<sha>.env`.

Каталоги на сервере:

- `/srv/svoi-pravila/repo` — клон репозитория (compose-файл, Prometheus, дашборды Grafana, скрипты). Релиз переключает его на нужный коммит.
- `/srv/svoi-pravila/releases/<sha>.env` — закреплённые digest образов релиза.
- `/srv/svoi-pravila/state/` — `current`, `previous` (sha) и ссылка `current.env`.
- `/srv/svoi-pravila/backups/` — локальные зашифрованные бэкапы (`daily/`, `monthly/`, `pre-release/`, режим 700).
- `/etc/svoi-pravila/env` — все настройки и секреты (режим 600).
- `/etc/svoi-pravila/ghcr-token` — токен `read:packages` для скачивания образов (режим 600).
- `/etc/svoi-pravila/backup-remote/` — каталог только для опционального `rclone.conf` (режим 700; контейнер backup монтирует его read-only, без остальных секретов).

Важно знать:

- Переписка пользователей, черновики и ответы LLM нигде не сохраняются. В бэкапах только структурированные данные; правила и метки зашифрованы ключом `SP_DATA_KEK` ещё внутри приложения.
- Valkey не бэкапится: там только короткоживущие ключи (лимиты, дедупликация, зашифрованные одноразовые результаты).
- Docker сам публикует порты в обход ufw. Поэтому в compose наружу опубликованы только 80 и 443; Grafana привязана к `127.0.0.1`. Не добавляйте `ports:` к другим сервисам.
- Подсеть `edge` зафиксирована как `172.30.250.0/24`, адрес Caddy — `172.30.250.10`: только ему API доверяет заголовки `X-Forwarded-*` (`SP_FORWARDED_ALLOW_IPS`). Если эта подсеть занята на хосте, поменяйте её в `compose.prod.yaml` в двух местах.

## 2. Что подготовить заранее

1. VPS с Ubuntu 24.04, IPv4-адрес, SSH-доступ от root (только для первого запуска).
2. Доменное имя и DNS-запись A (и AAAA, если есть IPv6) на адрес VPS. Без неё Caddy не получит сертификат Let's Encrypt.
3. Бот в [@BotFather](https://t.me/BotFather): токен, Main Mini App с адресом `https://<домен>`, inline-режим, `/setinlinefeedback` Enabled (100%), запрет добавления в группы (см. раздел «Telegram setup» в `README.md`).
4. Ключ GigaChat и решение по `SP_LLM_DAILY_TOKEN_BUDGET`.
5. Пара ключей age для шифрования бэкапов, создаётся на вашем компьютере, не на сервере:

   ```bash
   age-keygen -o svoi-pravila-backup.key
   ```

   В `SP_BACKUP_AGE_RECIPIENT` кладётся только публичная строка `age1...`. Секретный файл сохраните в менеджере паролей и в офлайн-копии. Без него бэкапы нельзя расшифровать. Локальные бэкапы пишутся на диск VPS; Object Storage не обязателен (внешняя копия — по желанию, раздел «Внешняя копия»).
6. SSH-ключ для пользователя `deploy` (публичная часть пойдёт в `bootstrap.sh`).
7. Токен GitHub с правом `read:packages` (для `ghcr-token`), если пакеты приватные. Логин — в `SP_GHCR_USER`.
8. GitHub Actions: публикация образов использует встроенный `GITHUB_TOKEN`; в настройках репозитория (Settings, Actions, General, Workflow permissions) токену должна быть разрешена запись пакетов. Первый раз после публикации проверьте видимость пакетов в профиле владельца.

## 3. Первичная подготовка сервера

Скрипт `deploy/server/bootstrap.sh` идемпотентен: его можно запускать повторно. Скопируйте его на сервер и запустите от root:

```bash
sudo SP_DEPLOY_SSH_PUBKEY='ssh-ed25519 AAAA... вы@ноутбук' \
     SP_REPO_SSH_URL='git@github.com:<владелец>/<репозиторий>.git' \
     bash bootstrap.sh
```

Скрипт:

- создаёт пользователя `deploy` с фиксированным UID/GID `1500:1500` и вашим SSH-ключом, добавляет его в группу `sudo` (если пользователь уже есть с другим UID — останавливается);
- если у `deploy` ещё нет пароля, запрашивает его интерактивно (пароль только для `sudo`; SSH остаётся по ключу). Без TTY скрипт останавливается до hardening SSH;
- закрывает SSH: вход только по ключу, только `deploy`, без root — только после ключа и пароля sudo;
- включает ufw: открыты 22, 80, 443;
- включает автоматические обновления безопасности (перезагрузка в 04:30 UTC при необходимости; контейнеры поднимаются сами);
- ставит Docker из официального apt-репозитория (отпечаток ключа проверяется);
- ограничивает размер журналов Docker и journald, ставит часовой пояс UTC;
- создаёт `/srv/svoi-pravila`, `/srv/svoi-pravila/backups` (режим 700, владелец `deploy` / 1500:1500), `/etc/svoi-pravila` и `/etc/svoi-pravila/backup-remote` (режим 700);
- генерирует read-only deploy-ключ для чтения репозитория. Если репозиторий ещё недоступен, скрипт печатает публичный ключ и завершается с кодом 3. Добавьте ключ в GitHub (Settings, Deploy keys, без права записи) и запустите скрипт ещё раз;
- копирует `deploy/env.prod.example` в `/etc/svoi-pravila/env` (если файла нет) и создаёт пустой `/etc/svoi-pravila/ghcr-token`;
- ставит и включает `svoi-pravila-backup.timer` (каждый день 03:30 UTC; до первого успешного релиза unit пропускается через `ExecCondition`).

Не закрывайте сессию root, пока не проверите из второго окна: `ssh deploy@<сервер>` и `sudo true`.

### Проверка доступности GigaChat с сервера

После bootstrap и до первого релиза убедитесь, что с VPS открыт путь к API GigaChat (токены не тратятся: ожидаются отказы авторизации, не сетевой обрыв). Команды от `deploy`:

```bash
CACERT=/srv/svoi-pravila/repo/backend/certs/russian_trusted_root_ca.pem

curl -sS -o /dev/null -w '%{http_code}\n' --cacert "$CACERT" \
  -X POST 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Accept: application/json' \
  -d 'scope=GIGACHAT_API_PERS'

curl -sS -o /dev/null -w '%{http_code}\n' --cacert "$CACERT" \
  'https://gigachat.devices.sberbank.ru/api/v1/models'
```

Ожидайте код `400` или `401`. Если в ответе `000` (нет соединения) — остановитесь и разберитесь с сетью/DNS/TLS до релиза.

## 4. Секреты и настройки

Откройте файл редактором без записи секретов в историю shell:

```bash
nano /etc/svoi-pravila/env
```

Замените все значения `CHANGE_ME`. У каждой переменной в файле есть комментарий и команда генерации. Правила:

- без кавычек и без символа `$` в значениях;
- права файла 600, владелец `deploy`;
- `SP_DATA_KEK` и `SP_PSEUDONYM_PEPPER` генерируются один раз (`openssl rand -base64 32`). Потеря `SP_DATA_KEK` означает безвозвратную потерю всех правил и меток. Сразу сохраните копию в менеджере паролей, отдельно от бэкапов базы. Менять ключ можно только по процедуре ротации (раздел 9);
- пароли и секреты вебхука: `openssl rand -hex 24`;
- токен для ghcr (не светить в истории shell):

  ```bash
  read -rs GHCR_TOKEN
  printf '%s' "$GHCR_TOKEN" > /etc/svoi-pravila/ghcr-token
  unset GHCR_TOKEN
  chmod 600 /etc/svoi-pravila/ghcr-token
  ```

`release.sh` перед каждым релизом сверяет файл с `deploy/env.prod.example`: любая переменная из примера должна быть заполнена. Настройки, которые задаёт compose (`SP_DATABASE_URL`, `SP_VALKEY_URL`, режим вебхука, `SP_MINIAPP_URL` и другие), в файл не добавляйте.

## 5. Релиз

### Обычный релиз

1. Слейте задачу в `master`. CI прогонит все проверки и опубликует образы с тегом коммита (задача `publish`). В логе задачи напечатаны digest образов.
2. Возьмите полный sha коммита (40 символов) из GitHub.
3. На сервере:

   ```bash
   /srv/svoi-pravila/repo/deploy/release.sh <sha>
   ```

Скрипт по шагам:

1. переключает клон репозитория на этот коммит (он должен быть в `origin/master`);
2. проверяет окружение: Docker, права файла `env`, заполненность переменных, свободное место, токен ghcr;
3. входит в ghcr.io, скачивает четыре образа по тегу и записывает digest в `releases/<sha>.env`;
4. запоминает текущий релиз как `previous`;
5. поднимает `postgres` и `valkey`, делает бэкап `--pre-release`;
6. выполняет миграции;
7. запускает стек и ждёт здоровья всех сервисов;
8. проверяет готовность API, регистрацию вебхука в Telegram (`getWebhookInfo`) и HTTPS: статус 200, заголовки CSP и HSTS (`max-age=31536000; includeSubDomains`).

Повторный запуск того же sha безопасен (используются уже записанные digest). Нужен только один релиз за раз; не запускайте два `release.sh` одновременно.

### Первый релиз

Порядок тот же. Дополнительно: перед ним DNS должен указывать на сервер, а в Main Mini App в BotFather указан `https://<SP_DOMAIN>`. Выпуск сертификата занимает до минуты; скрипт ждёт до 4 минут. После успеха напишите боту и откройте мини-приложение из меню.

Если проверка не прошла, скрипт печатает последние строки логов и команду отката. Первичные причины: нет DNS (сертификат не выдан), неверный токен бота (вебхук не зарегистрирован), порты 80/443 заняты.

## 6. Откат

```bash
/srv/svoi-pravila/repo/deploy/rollback.sh
```

Скрипт берёт `state/previous` и запускает `release.sh --rollback <sha>`: так же переключает клон на тот коммит, делает бэкап и поднимает старые образы, но не запускает миграции. После отката `current` и `previous` меняются местами: повторный `rollback.sh` вернёт вас к версии, с которой начали.

Откат кода не откатывает схему базы. Поэтому правило для всех изменений схемы: **миграции всегда совместимы вперёд и назад на один релиз** (expand/contract):

- сначала добавляем (новые таблицы, колонки как `NULL` или со значением по умолчанию), код при этом работает и со старой, и с новой схемой;
- удаляем и переименовываем только в одном из следующих релизов, когда ни один выпущенный код этого уже не читает;
- нельзя в одном релизе добавлять обязательное поле без значения по умолчанию, удалять колонку, менять тип или смысл данных;
- миграция, которую нельзя сделать совместимой, требует отдельного решения владельца и окна обслуживания, а не обычного релиза.

Если откат нужен после миграции, которая нарушила это правило, восстановите базу из бэкапа `pre-release/`, сделанного перед этим релизом (раздел 7), и только потом откатывайте код.

## 7. Резервные копии

- Таймер `svoi-pravila-backup.timer` запускает бэкап каждый день в 03:30 UTC (с догоном, если сервер был выключен). До первого успешного релиза unit спокойно пропускается (`ExecCondition` на `/srv/svoi-pravila/state/current.env`). Проверка: `systemctl list-timers svoi-pravila-backup.timer`, журнал: `journalctl -u svoi-pravila-backup.service -n 50`.
- Формат: `pg_dump -Fc`, поток шифруется `age` публичным ключом и атомарно пишется в `/srv/svoi-pravila/backups/<dir>/` (внутри контейнера `/backups`). Открытый дамп на диск не пишется. Права файлов 600, владелец `deploy`.
- Хранение локально: `daily/` — 30 последних, `monthly/` — 12 (первая копия каждого месяца), `pre-release/` — 10 копий перед релизами. Имя: `svoi-pravila-<UTC время>[-<sha>].dump.age`.
- Ручной запуск: `docker compose --project-directory /srv/svoi-pravila/repo/deploy -f /srv/svoi-pravila/repo/deploy/compose.prod.yaml --env-file /etc/svoi-pravila/env --env-file /srv/svoi-pravila/state/current.env run --rm -T backup`.
- Раз в месяц скопируйте месячную копию на ноутбук:

  ```bash
  scp deploy@<host>:/srv/svoi-pravila/backups/monthly/<файл> ~/Backups/svoi-pravila/
  ```

### Внешняя копия (по желанию)

Если нужен второй экземпляр вне VPS, настройте rclone на своём компьютере (remote с именем `sp`), скопируйте конфиг на сервер:

```bash
scp rclone.conf deploy@<host>:/tmp/rclone.conf
ssh deploy@<host> 'install -m 600 -o deploy -g deploy /tmp/rclone.conf /etc/svoi-pravila/backup-remote/rclone.conf && rm /tmp/rclone.conf'
```

Примеры фрагментов (создайте через `rclone config` на Mac, затем перенесите файл):

Яндекс.Диск:

```ini
[sp]
type = yandex
token = {"access_token":"...","token_type":"bearer","expiry":"..."}
```

Google Drive:

```ini
[sp]
type = drive
scope = drive.file
token = {"access_token":"...","token_type":"bearer","refresh_token":"...","expiry":"..."}
```

Префикс пути задаётся `SP_BACKUP_REMOTE_PATH` (по умолчанию `svoi-pravila`). Без файла `/etc/svoi-pravila/backup-remote/rclone.conf` бэкап пишет только локально и пишет предупреждение `offsite not configured (local copy only)`. Контейнер backup работает от UID 1500 без root и без доступа к `/etc/svoi-pravila/env`.

### Проверка восстановления (раз в месяц)

Копия, которую не пробовали восстановить, бэкапом не считается. Скрипт `restore-verify.sh` по умолчанию читает локальный `/backups`; с флагом `--remote` — с remote `sp`. Расшифровывает последнюю копию, разворачивает во временную базу, проверяет `alembic_version` и наличие таблиц, удаляет временную базу. Боевая база не затрагивается. Секретный ключ age временно кладётся на сервер и удаляется сразу после проверки:

```bash
install -m 600 /dev/null /tmp/backup.key   # затем вставьте содержимое файла svoi-pravila-backup.key
docker compose --project-directory /srv/svoi-pravila/repo/deploy \
  -f /srv/svoi-pravila/repo/deploy/compose.prod.yaml \
  --env-file /etc/svoi-pravila/env --env-file /srv/svoi-pravila/state/current.env \
  run --rm -T --no-deps -v /tmp/backup.key:/run/age.key:ro \
  -e SP_BACKUP_AGE_IDENTITY_FILE=/run/age.key --entrypoint restore-verify.sh backup
shred -u /tmp/backup.key
```

Параметры скрипта: `--remote`, `--dir daily|monthly|pre-release` и имя объекта вторым аргументом.

### Восстановление боевой базы

1. Остановите приложение: `compose stop miniapp api`.
2. Убедитесь, что в `/etc/svoi-pravila/env` тот же `SP_DATA_KEK`, что был при создании копии. С другим ключом данные C2 не расшифровать.
3. Запустите `compose up -d --wait postgres valkey` и `compose run --rm -T migrate` (создаёт роли и схему).
4. Загрузите копию поверх: выполните внутри контейнера backup (ключ age смонтируйте, как выше)

   ```bash
   age --decrypt --identity /run/age.key </backups/daily/<объект> \
     | pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE"
   ```

   через `compose run --rm -T --no-deps -v /tmp/backup.key:/run/age.key:ro --entrypoint bash backup -c '<команда>'`. Для внешней копии: `rclone cat sp:${SP_BACKUP_REMOTE_PATH}/daily/<объект> | age ...`.
5. Снова `compose run --rm -T migrate`, затем `compose up -d --wait`.
6. Проверьте, что `current.env` указывает на нужный релиз, и запустите `release.sh <sha>`, чтобы выполнить все проверки.

Здесь и ниже `compose` — сокращение для `docker compose --project-directory /srv/svoi-pravila/repo/deploy -f /srv/svoi-pravila/repo/deploy/compose.prod.yaml --env-file /etc/svoi-pravila/env --env-file /srv/svoi-pravila/state/current.env`.

### Потеря сервера целиком

Новый VPS, `bootstrap.sh`, тот же `/etc/svoi-pravila/env` (включая `SP_DATA_KEK` из вашей копии), обновить DNS, `release.sh <sha>` последнего релиза и восстановление из последней копии (локальной с ноутбука, с внешней копии или с уцелевшего диска) по шагам выше.

## 8. Наблюдение и диагностика

- Статус: `compose ps`. Логи: `compose logs --tail 100 api` (также `miniapp`, `migrate`). Логи не содержат текстов переписки; ротация: 10 МБ × 5 файлов на контейнер.
- Grafana доступна только с сервера. С вашего компьютера: `ssh -L 3000:127.0.0.1:3000 deploy@<сервер>`, затем `http://127.0.0.1:3000`, пользователь `admin`, пароль `SP_GRAFANA_ADMIN_PASSWORD`. Дашборды: «Эксплуатация» (метрики, расход токенов) и продуктовая аналитика.
- Бюджет LLM: при приближении к `SP_LLM_DAILY_TOKEN_BUDGET` бот перестаёт генерировать; смотрите дашборд «Эксплуатация».
- Вебхук: `getWebhookInfo` показывает ожидающие обновления и последнюю ошибку (токен бота и путь вебхука не выводите в логи и чаты). `release.sh` печатает число ожидающих обновлений и текст последней ошибки.
- Сертификат: Caddy продлевает его сам (данные в томе `caddy_data`). Если домен перестал указывать на сервер, обновление остановится: проверьте DNS и порты 80 и 443.
- Диск: `docker system df`, `df -h /var/lib/docker`. Старые образы после успешных релизов: `docker image prune -af --filter "until=720h"` (образы двух последних релизов нужны для отката, не удаляйте их раньше).

## 9. Обслуживание, ротация и инциденты

| Ситуация | Действия |
|---|---|
| Сменить токен бота | В BotFather: Revoke current token; обновить `SP_TELEGRAM_BOT_TOKEN`; `compose up -d api` (вебхук перерегистрируется) |
| Сменить секрет вебхука | Новые `SP_TELEGRAM_WEBHOOK_PATH_SECRET` и `SP_TELEGRAM_WEBHOOK_SECRET_TOKEN`; `compose up -d api miniapp` (Caddy читает путь из окружения) |
| Сменить ключ GigaChat | Обновить `SP_GIGACHAT_CREDENTIALS`; `compose up -d api` |
| Сменить пароли PostgreSQL или Valkey | Для Valkey достаточно новых `VALKEY_PASSWORD`, затем `compose up -d valkey api migrate`. Для PostgreSQL сначала `ALTER ROLE` внутри базы, потом новый `POSTGRES_PASSWORD` и перезапуск `api`, `migrate`, `grafana` |
| Сменить ключ бэкапов (age) | Создать новую пару, обновить `SP_BACKUP_AGE_RECIPIENT`; старые копии расшифровываются старым секретным ключом, поэтому храните его, пока живёт последняя такая копия |
| Ротация `SP_DATA_KEK` | Требует отдельной задачи разработки (перешифрование данных под новым `SP_DATA_KEK_ID`). Самостоятельно ключ не меняйте |
| Подозрение на утечку `SP_DATA_KEK` или базы | Остановить `api` и `miniapp`, сохранить бэкап, сообщить разработчику и действовать по плану реагирования на инциденты из документации по безопасности |
| Сервер перегружен или `api` перезапускается | `compose logs --tail 200 api`, `docker stats`; лимиты памяти заданы в `compose.prod.yaml` (api 512 МБ, postgres 768 МБ) |
| Релиз сломан | `rollback.sh` (раздел 6) |
| Обновления ОС | Ставятся автоматически; раз в месяц проверьте `uptime` и `needrestart`/`/var/run/reboot-required` |

После любой смены значения в `/etc/svoi-pravila/env` перезапускайте затронутые сервисы командой `compose up -d <сервис>`: контейнеры читают файл только при создании. Для смены образов используйте только `release.sh`.
