# Подключение нового участника

Владелец документа: Claude (CTO). Исполнители этот документ не редактируют.
Редакция: 1.0 · 2026-10-11 (ADR-0014)

Репозиторий публичный: в Issue, PR, коммитах и файлах — никаких имён, логинов, e-mail, локальных путей, доменов, секретов и пользовательских данных.

## 1. Доступы (делает владелец)
- Приглашение в организацию на GitHub с нужной ролью; для владельцев — участие в команде `builders`.
- Сервер прода и ключи прода новым участникам не выдаются.

## 2. Инструменты (делает участник)
- Docker Desktop, включён автозапуск при входе в систему (Settings → General).
- `uv` версии из `[tool.uv] required-version` в `backend/pyproject.toml`.
- Node версии из `miniapp/.nvmrc`; pnpm версии из `packageManager` в `miniapp/package.json` (например, `corepack enable` и `corepack prepare pnpm@<версия> --activate`).
- `git`, `make`, `curl`.
- Редактор с ИИ-агентом: Cursor или VS Code с GitHub Copilot (режим агента). Оба читают `AGENTS.md` сами; в VS Code должна быть включена поддержка файлов `AGENTS.md` в настройках Copilot Chat.

## 3. Рабочая копия
```
git clone https://github.com/doting-life/svoi-pravila.git
cd svoi-pravila
cp .env.example .env
make dev-env        # заполняет ключ шифрования разработки
make install        # зависимости и хуки git
make toolchain-check
```
Свои значения в `.env`:
- токен **своего** тестового бота (создаётся у @BotFather; не бот прода);
- **свой** ключ GigaChat разработки с дневным лимитом.

Файл `.env` никогда не коммитится, не пересылается и не публикуется. Проверка: `make infra-up` и `make quick` проходят.

## 4. Первая задача
CTO создаёт Issue с меткой `task` и `executor:<инструмент>`. Порядок работы — `docs/maintainers/04-workflow.md`, правила — `AGENTS.md`. Агенту достаточно сказать: «Implement GitHub issue #N following AGENTS.md» и дать текст Issue.

## 5. Связь с CTO
Вопросы и предложения — через GitHub: комментарий с меткой `needs:cto` или Issue «Proposal». Отдельных чатов с CTO у участников нет.
