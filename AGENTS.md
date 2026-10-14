# AGENTS.md — rules for every executor

This repository is "Svoi Pravila" — an AI helper for difficult conversations (Telegram inline bot + mini-app) that remembers the agreements between two people.

These rules bind every executor — Cursor, GitHub Copilot or any other coding agent — and the person driving it. Area rules add to them; read the one for the area you touch before editing:
- `backend/**` → `backend/AGENTS.md`
- `miniapp/**` → `miniapp/AGENTS.md`

## Roles
- **CTO (Claude):** designs, writes task issues, verifies results by reading the source and running the gates, reviews pull requests.
- **Owners:** decide, approve, merge and release. Only the owner who operates production touches the server.
- **Executor:** implements exactly one task issue at a time. No server access; never deploys; never requests or handles production secrets.

## Task flow
1. **Task.** A GitHub issue titled `NNNN — <title>` with label `task`. It is the plan: start implementing, do not write a separate plan. Read the issue and only the document sections it cites.
2. **Branch.** `task/NNNN-slug` from up-to-date `master`. Conventional Commits.
3. **Checks.** While working run only `make quick`. The pre-push hook runs `make ci` once on the pushed commit (see Checks).
4. **Pull request.** Title `NNNN — <title>`, body = the report (template below; the PR template provides it), `Closes #<issue>`. Do not wait for GitHub CI.
5. **Review.** The CTO answers in the PR: `ACCEPT`, or `CHANGES REQUIRED` with a follow-up prompt `NNNN-a`. Fix in the same branch and PR; post the new report as a PR comment.
6. **Merge.** An owner squash-merges after ACCEPT and green CI. Executors never merge and never push to `master`.
7. **Questions.** Ask in the issue or PR and add label `needs:cto`. If the question blocks you, stop there (see Stop conditions).

## Public repository — confidentiality
The repository is public. Never put into code, commits, branch names, issues, PRs or documents: names of people, account logins, e-mail addresses, local file paths, company names, production domains or IP addresses, secrets or tokens, real user data, business materials. Refer to roles only (owner, executor, CTO).

## Scope discipline
- Implement exactly what the task specifies. Nothing more, nothing less.
- Do not refactor, rename, reformat or "improve" code outside the task. A defect outside scope — including a new scanner finding in an image you did not change — goes to the report under "Open questions".
- Do not add, upgrade or remove dependencies unless the task allows it. When allowed: latest stable release, verified on the official registry at execution time, exact version in the lockfile and in the report.
- Never modify CTO-owned paths — the list in `.github/cto-owned-paths` (the list itself included).

## Golden rule — no workarounds
Forbidden in any form:
- `TODO`, `FIXME`, `XXX`, placeholder implementations, `pass`/`...` bodies standing in for logic, `NotImplementedError` outside abstract protocols.
- Commented-out code, dead code, unused imports/functions/files/dependencies.
- Suppressions: `# type: ignore`, `# noqa`, `# pragma: no cover`, `@ts-ignore`, `@ts-expect-error`, `eslint-disable`, lowering lint/type/coverage thresholds, skipping or xfail-ing tests — unless the task explicitly permits a specific one with a reason.
- Disabling TLS verification or signature checks, catching broad exceptions to hide errors, retry loops masking bugs, sleeps to "fix" races.
- Hard-coded secrets, tokens, environment URLs or user data.
When the task supersedes old code, delete it and its tests completely and clean up its dependencies.

## Stop conditions — report BLOCKED instead of improvising
Stop, post the report with `Status: BLOCKED` in the PR (or the issue, if there is no PR yet) and add label `needs:cto` when:
- the task is ambiguous or contradicts the architecture documents or these rules;
- a clean implementation needs a decision outside the task (new dependency, schema change, API contract change, security trade-off);
- a library does not support what the task requires;
- a gate cannot be made green without a suppression or without work outside the task's scope.
- the requirements produce two controls on one screen with the same result, or the implementation would differ from the task's "User-visible behaviour" table.
Ask one concrete question per blocker and describe the options you see.

## Honesty
Never claim a gate passed without running it. Never fabricate versions, test results or behaviour. If something was not verified, say so.

## Git
- Never discard, stash, reset, clean or overwrite changes you did not make. If the working tree contains changes outside your task, stop and report BLOCKED.
- Never modify, move, copy over, regenerate or delete the repository `.env` (it holds credentials that cannot be restored). Fresh-environment checks use a separate env file outside the repository.
- Never force-push, never push to `master`, never delete remote branches, never merge.
- One push per review round. Never push to "see what CI says" and never push fix-up commits to turn CI green.

## Checks — local first, once
- CI is a mirror, never a discovery tool. `make ci` runs locally what the GitHub workflow runs, with the same pinned toolchain, hermetically (own env file, own compose project, removed afterwards; clean worktree; the exact pushed commit).
- Prerequisites: Docker running; tool versions as pinned in the repository (`make toolchain-check`).
- While working run only `make quick`. Do not run `make ci` by hand; the pre-push hook runs it once, limited to the jobs the change can affect. Put its one-screen summary in the report.
- After the push do not watch, poll or re-run GitHub CI. A red GitHub job after a green local `make ci` is a parity defect, handled as a separate task.
- Read CI output in its quiet form (stage summary plus the tail of a failing stage). Open `.ci-logs/` only when the tail does not explain the failure.

## Not part of a task unless it asks
Screenshots; coverage tables; file-by-file change maps; copies of command output beyond the summaries below; updates to CTO documents; extra documentation.

## Privacy and security — always
Reference: `docs/maintainers/03-privacy-and-security.md`, `docs/maintainers/adr/ADR-0002-*`.
1. Conversation texts (drafts, incoming messages, LLM prompts and outputs) are never persisted: not in the database, not in Valkey (except the encrypted, single-use, short-TTL tokens defined by the architecture), not in logs, metrics, traces, exceptions or fixtures taken from real users.
2. Third-party metadata (forward origin, sender names, usernames, ids) is dropped at the channel adapter boundary before any use case is called.
3. Special-category data (rule text, contact labels) is encrypted through the `Cipher` port before persistence. Never add a plaintext column for it.
4. Analytics events carry the HMAC pseudonym of the user, never the raw Telegram id, never text.
5. No external SaaS for logs, errors, analytics or LLM inference beyond the approved providers. No new outbound network destination unless the task names it.
6. TLS verification is never disabled. Secrets come only from settings; `.env.example` holds names and safe descriptions only.
7. Every user- and partner-supplied text is untrusted: bounded at the edges, passed to the LLM only as a delimited data block, LLM output validated against its schema.
8. Live LLM provider calls only with a "Live budget" section in the task (see `backend/AGENTS.md`).

## Report — the pull request description
Keep it short; the CTO verifies the code itself.

```
## Report NNNN — <title>
Closes #<issue>
Status: DONE | BLOCKED
Branch: task/NNNN-slug · Commits: <short hashes>
Done: <one line per task section>
Deviations: <none | each with justification>
Behaviour: <each row of the task's "User-visible behaviour" table: done | differs (why) — or n/a>
Dependencies: <package — exact version — reason | none>
Data classes: <new persisted fields with C0/C1/C2 | none>
make ci (hook, <sha>): <one-line summary>
Live budget: <tokens spent | 0>
Open questions: <none | questions>
```
