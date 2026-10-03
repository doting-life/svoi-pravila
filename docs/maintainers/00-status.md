# Статус проекта

Ведёт: Claude (CTO). Обновляется после каждого вердикта и каждого решения владельца.

## Текущее
- Дата: 2026-10-03 · Спринт 0 (Фундамент)
- Активная задача: **0005-b — честный бенчмарк, выбор модели, итерация промптов** · ветка `task/0005-llm-gigachat`
- Риск: качество/латентность GigaChat-2 на сценариях не проходят цели; выбор модели — по данным 0005-b; если ни одна модель не проходит — решение владельца (компромисс латентность/качество)
- Разработка и ручное тестирование — локально в Docker; VPS — после полной приёмки (ADR-0005)
- Основная ветка — `master` (решение владельца 03.10)
- Правило с 03.10: промпты и отчёты Cursor — только в чате, в репозитории не хранятся
- Следующая: 0006 — события использования и канал Telegram
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
| 0005-b | Trustworthy benchmark, model selection, prompt iteration | `task/0005-llm-gigachat` | выдан | — |

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
| D-10 | Репозиторий и CI (приватный GitHub) | открыто, до 04.10 |
| D-11 | Имена ботов, домен | открыто, до 07.10 |
