# Статус проекта

Ведёт: Claude (CTO). Обновляется после каждого вердикта и каждого решения владельца.

## Текущее
- Дата: 2026-10-04 · Спринт 0 (Фундамент)
- Активная задача: **0015.2-a — права пользователя после отзыва согласий** (экспорт и удаление доступны, тексты раскрытия) · ветка `task/0015-2-miniapp-privacy` · GigaChat 0 токенов
- В `master`: 0015.1 (`a60f168`, squash #16)
- В `master`: 0014 (`e8f6539`, squash #15)
- В `master`: 0013 (`e274171`, squash #14)
- В `master`: 0011.2 (`2be0872`, squash #13)
- Решение по 0012: «Расшифровать» в группах не делаем в MVP — ADR-0007
- В `master`: 0011.1 (`66bf03e`, squash #12)
- Расход GigaChat в 0011.2: 6 313 из 8 000 (3 221 — прогон без нужных метрик, нарушение правила 60; 3 092 — итоговый смоук)
- В `master`: 0010 (`2180caa`, squash #11)
- В `master`: 0009.2 (`16037df`, squash #10)
- В `master`: 0009.1 (`18ba3f1`, squash #9)
- Расход GigaChat на eval 0008-b…d: ≈ 68 000 токенов (30 618 + 26 965 + 8 585 + разминки)
- Eval v1 (04.10, 30 618 токенов, 51/60 случаев): кризис 100 %, ложных кризисов 0 %, утечек 0; **отказ от манипуляций 0 %** — «Помоги сказать» помогает формулировать манипуляции; Lightning при отказе добавляет варианты → сейчас ошибка вместо отказа
- Инцидент 04.10: `.env` владельца перезаписан сгенерированным (пусты ключ GigaChat и токен бота, новые KEK и pepper) — вероятная причина: процедура «свежего окружения» с `rm .env` из промптов CTO. Правило: исполнитель не трогает `.env`; свежее окружение — отдельный файл через `ENV_FILE`
- Живая проверка «Расшифровать» (≤ 3 разбора, ≤ 15 000 токенов) — владелец, на `master` после мержа 0006.2
- В `master`: 0005 (`4818b1b`), 0006.1 (`b031a29`, CI полностью зелёный, включая `image`). Репозиторий `doting-life/svoi-pravila`
- В `master` также 0006.2 (`c07bc0a`). Dependabot удалён (решение владельца 04.10); Cursor мержит и пушит по поручению владельца после ACCEPT
- Тестовый бот создан, токен в локальном `.env` (владелец, 04.10)
- Модели (ADR-0006 ред. 1.2): «Смягчить», «Помоги сказать» — GigaChat-3-Lightning; «Расшифровать» — GigaChat-2-Pro
- Правило с 04.10 (владелец): ключ разработки жёстко ограничен по токенам; живые вызовы — только с бюджетом в промпте (`60-live-provider-budget`)
- Риск: лимиты GigaChat (429) на личном ключе мешают полным прогонам — владелец проверяет остаток и лимиты в кабинете
- Разработка и ручное тестирование — локально в Docker; VPS — после полной приёмки (ADR-0005)
- Основная ветка — `master` (решение владельца 03.10)
- Правило с 03.10: промпты и отчёты Cursor — только в чате, в репозитории не хранятся
- Следующая: 0015.3 — «Расшифровать» длинных текстов в мини-приложении (живой бюджет)
- Допущения до решения владельца: D-8 (18+) и D-7 (выход из свода: каждый сохраняет сформулированные им правила, общий свод удаляется) реализуются как рекомендовано
- Допущение CTO: удаление аккаунта удаляет и записи согласий; срок хранения доказательств согласия — настройка выпускающей стороны (пакет передачи, 0024)
- Ресурсы помощи (0008) — черновой список, проверен 04.10 по открытым источникам; перед релизом выпускающая сторона перепроверяет (данные, не код)

## Журнал задач
| № | Задача | Ветка | Статус | Вердикт |
|---|---|---|---|---|
| 0001 | Repository bootstrap | `task/0001-repo-bootstrap` | проверен 03.10 | CHANGES REQUIRED (10 пунктов) |
| 0001-a | Bootstrap review fixes | `task/0001-repo-bootstrap` | проверен 03.10 | CHANGES REQUIRED (4 пункта) |
| 0001-b | Exception-message redaction, test determinism | `task/0001-repo-bootstrap` | проверен 03.10 | продуктовый код принят; тестовая инфраструктура → 0001-c |
| 0001-c | Test logging isolation, strict typing of tests | `task/0001-repo-bootstrap` | проверен 03.10 | **ACCEPT** (0001 целиком) |
| 0002 | Domain core: model, ports, management use cases | `task/0002-domain-core` | проверен 03.10 | CHANGES REQUIRED (10 пунктов, 1 критичный по приватности) |
| 0002-a | Domain core review fixes | `task/0002-domain-core` | проверен 03.10 | **ACCEPT** (0002 целиком) |
| 0003 | Persistence, field encryption, system adapters | `task/0003-persistence` | проверен 03.10 | CHANGES REQUIRED (8 пунктов; CI упал бы на чистой БД) |
| 0003-a | Persistence review fixes | `task/0003-persistence` | проверен 03.10 | CHANGES REQUIRED (`make typecheck` красный) |
| 0003-b | Typed contract fixture | `task/0003-persistence` | проверен 03.10 | **ACCEPT** (0003 целиком) |
| 0004 | Local full stack in Docker | `task/0004-local-stack` | проверен 03.10 | CHANGES REQUIRED (повторный `make check` красный, флаки-тесты) |
| 0004-a | Local stack review fixes | `task/0004-local-stack` | проверен 03.10 | **ACCEPT** (0004 целиком) |
| 0005 | Generation port, GigaChat adapter, prompt registry, benchmark | `task/0005-llm-gigachat` | проверен 03.10 | BLOCKED (нет ключей) + CHANGES REQUIRED (9 пунктов) |
| 0005-a | GigaChat adapter fixes, live benchmark, HTTP-level tests | `task/0005-llm-gigachat` | проверен 03.10 | CHANGES REQUIRED (адаптер принят; бенчмарк недостоверен: широкий `except`, TTFC; нераскрытые omit/подавления) |
| 0005-b | Trustworthy benchmark, model selection, prompt iteration | `task/0005-llm-gigachat` | проверен 03.10 | BLOCKED (decode) + CHANGES REQUIRED: протокол decode_stream не допускает повтора; неразрешённые per-file ignores (с 0005); 4xx → `server` |
| 0005-c | Two-phase decode, suppression cleanup, final benchmark | `task/0005-llm-gigachat` | проверен 03.10 | BLOCKED (429) + CHANGES REQUIRED: `except Exception`-диспетчеры, недостижимый код, `Any`/`cast`, токены повторов теряются, ветки фазы A не покрыты |
| 0005-d | Error-path cleanup, token accounting, final benchmark | `task/0005-llm-gigachat` | проверен 04.10 | код принят; BLOCKED ложный — ошибка метрики «первая попытка» для двухфазного вызова; ~310 тыс. токенов на прогоны |
| 0005-e | Token budget guard, metric fixes, decode surface removal, model defaults | `task/0005-llm-gigachat` | проверен 04.10 | CHANGES REQUIRED (мелкие): нет `max_tokens` в запросах, необязательный учёт в ошибках, HTTP-детали в слое application, регресс покрытия 100 % → мёртвые ветки; 0 токенов |
| 0005-f | Output caps, strict accounting, dead-branch cleanup | `task/0005-llm-gigachat` | проверен 04.10 | адаптер принят; CHANGES REQUIRED в бенчмарке: перехват 429/записи через подмену приватного `_transport` не работает при HTTP-прокси (тест красный у CTO), `out_writer` 93 % |
| 0005-g | Bench HTTP observation via public httpx hooks | `task/0005-llm-gigachat` | проверен 04.10 | **ACCEPT** (0005 целиком) |
| 0006.1 | Telegram channel foundation, onboarding | `task/0006-telegram-channel` | проверен 04.10 | CHANGES REQUIRED: гейты красные без ручных переопределений окружения (миграции и тесты требуют токен бота); нераскрытые подавления; согласия отображаются с сырой Markdown-разметкой и не называют GigaChat |
| 0006.1-a | Telegram channel review fixes | `task/0006-telegram-channel` | проверен 04.10 | **ACCEPT** (0006.1 целиком); ручная проверка в Telegram — за владельцем |
| 0006.2 | DM decode with streaming, usage events, scenario limit | `task/0006-2-dm-decode` | проверен 04.10 | CHANGES REQUIRED: `except Exception` в application, дублирование логики правил, «unknown» версия промпта в событиях, событие пишется до показа результата |
| 0006.2-a | DM decode review fixes, Dependabot policy | `task/0006-2-dm-decode` | проверен 04.10 | **ACCEPT** (0006.2 целиком); лишний перехват `asyncpg.PostgresError` + mypy-override — в 0006.3, часть 0 |
| 0006.3 | User rights: /revoke, /delete (crypto-shredding, D-7), /export | `task/0006-3-user-rights` | проверен 04.10 | CHANGES REQUIRED: перенесённым правилам задним числом ставится дата вступления = дата предложения (ложная цитата «вы договорились…») |
| 0006.3-a | Rehomed rule effective dates | `task/0006-3-user-rights` | проверен 04.10 | **ACCEPT** (0006.3 и 0006 целиком) |
| 0007 | Inline soften/help-say, debounce, prepared results, chosen_inline_result | `task/0007-inline` | проверен 04.10 | CHANGES REQUIRED: не протестированы отбрасывание устаревших результатов и отказ prepared-токена; обход линтера `"p" + "_"`; тестовый метод в боевом коде; параллельные генерации одного пользователя |
| 0007-a | Inline review fixes | `task/0007-inline` | проверен 04.10 | CHANGES REQUIRED: ожидающий запрос падает вместе с упавшей генерацией (`await flying`); ошибки Telegram при ответе (истёкший запрос) и сбои фоновых задач не обрабатываются и не логируются |
| 0007-b | Inline background task failures | `task/0007-inline` | проверен 04.10 | **ACCEPT** (0007 целиком) |
| 0008 | Safety layer (crisis screen, resources, prompt-leak guard) + eval v1 | `task/0008-safety` | проверен 04.10 | BLOCKED корректно (оценка худшего случая 271 тыс. > 40 тыс.; правило 60 уточнено) + CHANGES REQUIRED: фильтр ловит гиперболу «убью тебя» — ядро сценария «Смягчить» (ошибка спецификации CTO); бенчмарк/eval требуют токен бота |
| 0008-a | Crisis screen precision, tool settings role, live eval | `task/0008-safety` | проверен 04.10 | код принят; BLOCKED: пустой `SP_GIGACHAT_CREDENTIALS` в `.env` → все вызовы `auth`, 0 токенов; найдено: пустые учётные данные проходят валидацию, нет остановки на `auth` |
| 0008-b | Empty-credential validation, auth fail-fast, ENV_FILE, live eval | `task/0008-safety` | проверен 04.10 | **ACCEPT** (0008 целиком); eval: 30 618 токенов, INCOMPLETE по потолку; цели по манипуляциям не достигнуты → 0008-c |
| 0008-c | Manipulation refusal: verdict precedence, prompts v2, eval fixes | `task/0008-c-manipulation` | проверен 04.10 | **ACCEPT**: отказ от манипуляций 100 %, ложных отказов 4,2 %, валидность 100 %, утечек 0; 26 965 токенов. Остаток: ложный «кризис» 6,2 % (help_say: «насилие» в правиле кризиса ловит гиперболу) |
| 0008-d | help_say v4: crisis rule precision | `task/0008-d-crisis-precision` | проверен 04.10 | **ACCEPT**: help_say ложных кризисов 0 на ordinary/mild, отказ от манипуляций 100 %, валидность 100 %; 8 585 токенов. `heated-12` («убью, если ты уйдёшь») → crisis — по решению CTO это принуждающая угроза, разметка набора ошибочна; гиперболы 19–20 не измерены (потолок) |
| 0009.1 | Contacts via bot, dialog state; eval metric fixes | `task/0009-1-contacts` | проверен 04.10 | **ACCEPT**; ветки ошибок в `handlers/contacts.py` (93 %) — в 0009.2, часть 0 |
| 0009.2 | Rules via bot, applied-rule citation | `task/0009-2-rules` | проверен 04.10 | **CHANGES** → 0009.2-a: кнопки «Архивировать» неотличимы, список > 4096 символов падает, диалог застревает после лимита/недоступного контакта (то же в `/contacts`), устаревшая кнопка архива → общая ошибка, приватные импорты между обработчиками |
| 0009.2-a | /rules fixes, shared handler helpers | `task/0009-2-rules` | проверен 04.10 | **ACCEPT** (730 тестов, 99,25 %) |
| 0010 | Inline optimization: result reuse, normalization, answer latency | `task/0010-inline-opt` | проверен 04.10 | **CHANGES** → 0010-a: истёкшие результаты (C2) остаются в памяти без трафика, telegram id удалённых пользователей хранятся вечно (`_forget_gen`), `ApplicationError.reuse` и клоны ошибок, недопустимое состояние резолюции, широкий `except ApplicationError` в обработчике |
| 0010-a | Reuse retention, typed failures | `task/0010-inline-opt` | проверен 04.10 | **CHANGES** → 0010-b: строгий срок и ограниченное состояние — приняты; осталась дыра в типах (`ReuseFailed.error: ApplicationError` + проверка `TypeError` и тест на невозможный случай), недостижимая ветка без цикла событий, лишние блокировка и задача при истечении |
| 0010-b | Produce errors as values, synchronous expiry | `task/0010-inline-opt` | проверен 04.10 | **ACCEPT** (770 тестов, 99,28 %); дублирующий кортеж типов ошибок в адаптере — в 0011.1, часть 0 |
| 0011.1 | Rule suggestions: tone signal | `task/0011-1-tone-suggestions` | проверен 05.10 | **CHANGES** → 0011.1-a: при ожидающем тон-кандидате и смене доминирующей жёсткости уникальный индекс откатывает транзакцию — сигнал замерзает навсегда; приложение глушит `OSError`/`RuntimeError`/`TimeoutError`; потерянное обновление `tone_signals`; новые репозитории без интеграционных тестов (`repositories.py` 91 %) |
| 0011.1-a | Tone signal fixes, repository integration tests | `task/0011-1-tone-suggestions` | проверен 05.10 | **ACCEPT** (820 тестов, 99,31 %); геттер с записью `tone_signals.get(for_update=True)` — переименовать, в 0011.2 часть 0 |
| 0011.2 | Suggest rule from decode (LLM, one-time token) | `task/0011-2-suggest-from-decode` | проверен 05.10 | **BLOCKED** → 0011.2-a: живой прогон (3 221 токен) без метрик категории/`none`/цитирования; в промпте нет определений категорий; ошибочная метка sr-4; результат `suggest_rule` допускает невозможные состояния; квота тратится без вызова модели |
| 0011.2-a | suggest_rule prompt, metrics, typed result; one live re-run | `task/0011-2-suggest-from-decode` | проверен 05.10 | **ACCEPT** (852 теста, 98,67 %; смоук: категория 7/7, `none` 2/2, дубль не измерен; критерий совпадений снят — ADR-0006 ред. 1.3) |
| 0012 | R&D spike: decode in groups | — (отчёт в чате) | проверен 05.10 | **ACCEPT**; рекомендация «Guest + ephemeral» отклонена: вызов `@bot` виден всем, ephemeral только в группах → ADR-0007 «не делаем в MVP»; ветку спайка удалить |
| 0013 | Mini-app bootstrap (Vite/React/TS, OpenAPI client, CSP, gates) | `task/0013-miniapp-bootstrap` | проверен 05.10 | **CHANGES** → 0013-a: Caddy 2.10.2 вместо актуального 2.11.4, уязвимости stdlib скрыты флагом `--ignore-unfixed`; клиент API допускает чужой `baseUrl`; образ 136 МБ без объяснения |
| 0013-a | Mini-app bootstrap fixes | `task/0013-miniapp-bootstrap` | проверен 05.10 | **CHANGES** → 0013-b: клиент и размер образа (136 → 75 МБ) приняты; официальный Caddy 2.11.4 несёт 17 HIGH с доступными исправлениями — CI красный → ADR-0008 |
| 0013-b | Caddy built from source on current Go | `task/0013-miniapp-bootstrap` | проверен 05.10 | **ACCEPT** (Dockerfile по ADR-0008, distroless, 75 МБ; Trivy 0 HIGH и CI #14 — по отчёту и PR, у CTO нет доступа к GitHub); исполнитель запушил `master` с документами CTO без поручения — отмечено; Caddyfile и `/srv` доступны на запись процессу — в 0014, часть 0 |
| 0014 | Mini-app API: initData auth, contacts, rules, suggestions | `task/0014-miniapp-api` | проверен 05.10 | **CHANGES** → 0014-a: `GET …/rules` отдаёт архивные и отклонённые правила и для действующего правила с правкой — текст правки вместо действующего; лимит 16 КиБ проверяется после чтения и разбора тела; зависимости через `app.state` с защитными ветками; повтор обработки ошибок в каждом маршруте; логи строками вместо структурных полей |
| 0014-a | Mini-app API fixes | `task/0014-miniapp-api` | проверен 05.10 | **CHANGES** → 0014-b: проекция правил, ошибки, логи приняты; вместо `app.state` — модульный глобальный слот `_auth_slot`; после превышения лимита тело дочитывается до конца |
| 0014-b | Auth binding without globals; body limit without draining | `task/0014-miniapp-api` | проверен 05.10 | **ACCEPT** (900 тестов, 98,77 %; `api/` 100 %) |
| 0015.1 | Mini-app UI: contacts, rules, suggestions | `task/0015-1-miniapp-ui` | проверен 05.10 | **ACCEPT** (бэкенд 908, мини-приложение 49 тестов); в 0015.2 часть 0: заголовок карточки контакта — «Контакт» вместо имени, кнопка «Отправить» → «Сохранить правило», Playwright 1.56.1 → актуальная |
| 0015.2 | Mini-app privacy: export to bot chat, revoke, delete | `task/0015-2-miniapp-privacy` | проверен 05.10 | **CHANGES** → 0015.2-a: после отзыва согласий экспорт в мини-приложении закрыт (в боте `/export` работает), а экран-заглушка не даёт ни выгрузить, ни удалить; текст экспорта не упоминает предложения правил и сигналы тона; в тексте отзыва лишнее «Подтвердите отзыв.» |
| 0015.2-a | Rights after consent revocation; disclosure copy | `task/0015-2-miniapp-privacy` | выдан | — |
| 0006.1-b | Image gate: Debian security updates, base digest pin, Dependabot | `task/0006-telegram-channel` | проверен 04.10 | **ACCEPT** (Trivy: 21 CVE → 0) |

## Решения
| № | Решение | Статус |
|---|---|---|
| D-1 | Только Telegram: бот + мини-приложение | принято 03.10 (ADR-0003) |
| D-2 | LLM: GigaChat; доступ к API создан | принято 03.10 |
| D-3 | Хостинг: OVH VPS, выход после локальной приёмки в Docker | принято 03.10 (ADR-0003, ADR-0005) |
| D-4 | Юридическое оформление — вне зоны проекта | принято 03.10 (ADR-0003) |
| D-5 | «via @bot» + «Копировать» | открыто, до 08.10 |
| D-6 | Правила: предлагаются, подтверждаются; сигналы v1 — тон в inline + кнопка «Сделать правилом» | **принято владельцем 04.10** |
| D-7 | Выход из общего свода | открыто, до 27.10 |
| D-8 | Возраст 18+ | открыто, до 08.10 |
| D-9 | «Обращение» = завершённый сценарий | решение CTO 03.10 |
| D-10 | Репозиторий и CI (приватный GitHub) | принято 04.10: `doting-life/svoi-pravila` |
| D-11 | Имена ботов, домен | открыто, до 07.10 |
