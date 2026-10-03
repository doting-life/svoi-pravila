# AGENTS.md — Executor entry point

This repository is "Svoi Pravila" — an AI helper for difficult conversations (Telegram inline bot + mini-app) that remembers the agreements between two people.

You are the **executor** (Cursor). You implement exactly one task prompt at a time.

## Read before any work
1. `.cursor/rules/` — binding engineering rules (always apply).
2. The task prompt the owner pasted into the chat. Prompts are never stored in the repository.
3. Architecture reference (read-only for you): `docs/maintainers/02-architecture.md`, `docs/maintainers/03-privacy-and-security.md`, `docs/maintainers/adr/`.

## Ownership
- CTO-owned, **never modify**: `AGENTS.md`, `.cursor/**`, `docs/maintainers/**`.
- Your report is your final chat reply, using the template in `.cursor/rules/00-executor-contract.mdc`. Never commit prompts or reports to the repository.

## Golden rule
No temporary patches, workarounds, stubs, TODOs or dead code. If the prompt cannot be fulfilled cleanly, stop and report `Status: BLOCKED` with a concrete question.
