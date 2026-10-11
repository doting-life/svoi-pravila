# miniapp/AGENTS.md — mini-app rules

Adds to the root `AGENTS.md`. Applies to everything under `miniapp/`.

## Stack

- Vite + React + TypeScript with `strict: true` and `noUncheckedIndexedAccess: true`. Node and pnpm versions are pinned in `package.json` (`engines`, `packageManager`); commit `pnpm-lock.yaml`.
- Lint and format: ESLint + Prettier (configuration in this folder). Tests: Vitest + Testing Library. Gate: `pnpm check` (format, lint, storage ban, typecheck, tests with coverage, build, audit).

## Rules

- The API client and types are generated from the backend OpenAPI schema (`src/api/openapi.json` → `schema.d.ts`, `openapi-fetch`). Never hand-write API types or duplicate backend contracts.
- Server state through the existing hooks (`src/hooks`); no new state or data library without a task. No global mutable singletons.
- Telegram only through the adapter `src/telegram/webapp.ts` over the official `telegram-web-app.js`. `initData` is sent to the backend unmodified and never trusted client-side. Launch kinds are explicit (ADR-0013, amendment 1).
- Never store personal data, rule text or contact labels in `localStorage`, `sessionStorage`, `DeviceStorage` or cookies (enforced by `lint:storage-ban`). UI preferences only.
- No third-party analytics, fonts, CDNs or trackers. Assets are bundled; the Content-Security-Policy stays strict.
- All user-facing strings go through the localization catalog (`src/localization`); no hard-coded limits — limits come from the server.
- Accessibility: semantic elements, labelled controls, respects Telegram theme parameters.
- Tests: behaviour from the user's point of view; every launch kind and error state of a screen you touch is covered; coverage thresholds in `vitest.config.ts` are never lowered.
