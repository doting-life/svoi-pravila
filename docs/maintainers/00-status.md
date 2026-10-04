# Статус проекта

Ведёт: Claude (CTO). Обновляется после каждого вердикта и каждого решения владельца.

## Текущее
- Дата: 2026-10-03 · Спринт 0 (Фундамент)
- Активная задача: **0006.2 — «Расшифровать» в личке, события использования** · ветка `task/0006-2-dm-decode` · бюджет GigaChat ≤ 15 000 токенов; старт после мержа 0006.1 владельцем (CI полностью зелёный)
- 0005 влит в `master` (`4818b1b`); репозиторий `doting-life/svoi-pravila` (GitHub, приватный) создан, CI работает: на ветке 0006.1 зелёные `backend`, `secrets`, `stack-smoke`, красный `image`
- Тестовый бот создан, токен в локальном `.env` (владелец, 04.10)
- Модели (ADR-0006 ред. 1.2): «Смягчить», «Помоги сказать» — GigaChat-3-Lightning; «Расшифровать» — GigaChat-2-Pro
- Правило с 04.10 (владелец): ключ разработки жёстко ограничен по токенам; живые вызовы — только с бюджетом в промпте (`60-live-provider-budget`)
- Риск: лимиты GigaChat (429) на личном ключе мешают полным прогонам — владелец проверяет остаток и лимиты в кабинете
- Разработка и ручное тестирование — локально в Docker; VPS — после полной приёмки (ADR-0005)
- Основная ветка — `master` (решение владельца 03.10)
- Правило с 03.10: промпты и отчёты Cursor — только в чате, в репозитории не хранятся
- Следующая: 0006.3 — `/revoke`, `/delete`, `/export`
- Допущение до решения владельца: D-8 (18+) реализуется как рекомендовано

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
| 0006.2 | DM decode with streaming, usage events, scenario limit | `task/0006-2-dm-decode` | BLOCKED 04.10 (корректно: 0006.1 не влит в `master`) | ждёт мержа |
| 0006.1-b | Image gate: Debian security updates, base digest pin, Dependabot | `task/0006-telegram-channel` | проверен 04.10 | **ACCEPT** (Trivy: 21 CVE → 0) |

## Решения
| № | Решение | Статус |
|---|---|---|
| D-1 | Только Telegram: бот + мини-приложение | принято 03.10 (ADR-0003) |
| D-2 | LLM: GigaChat; доступ к API создан | принято 03.10 |
| D-3 | Хостинг: OVH VPS, выход после локальной приёмки в Docker | принято 03.10 (ADR-0003, ADR-0005) |
| D-4 | Юридическое оформление — вне зоны проекта | принято 03.10 (ADR-0003) |
| D-5 | «via @bot» + «Копировать» | открыто, до 08.10 |
| D-6 | Правила: предлагаются, подтверждаются | открыто, до 20.10 |
| D-7 | Выход из общего свода | открыто, до 27.10 |
| D-8 | Возраст 18+ | открыто, до 08.10 |
| D-9 | «Обращение» = завершённый сценарий | решение CTO 03.10 |
| D-10 | Репозиторий и CI (приватный GitHub) | принято 04.10: `doting-life/svoi-pravila` |
| D-11 | Имена ботов, домен | открыто, до 07.10 |
