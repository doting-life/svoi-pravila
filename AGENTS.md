# AGENTS.md — Executor entry point

This repository is "Svoi Pravila" — an AI helper for difficult conversations (Telegram inline bot + mini-app) that remembers the agreements between two people.

You are the **executor** (Cursor). You implement exactly one task prompt at a time.

## Read before any work
1. `.cursor/rules/` — binding engineering rules (always apply).
2. The task prompt you were given: `docs/prompts/NNNN-*.md`.
3. Architecture reference (read-only for you): `docs/maintainers/02-architecture.md`, `docs/maintainers/03-privacy-and-security.md`, `docs/maintainers/adr/`.

## Ownership
- CTO-owned, **never modify**: `AGENTS.md`, `.cursor/**`, `docs/maintainers/**`, `docs/prompts/**`.
- You write your report to `docs/reports/NNNN-*.md` (the only place in `docs/` you may write).

## Golden rule
No temporary patches, workarounds, stubs, TODOs or dead code. If the prompt cannot be fulfilled cleanly, stop and report `Status: BLOCKED` with a concrete question.
