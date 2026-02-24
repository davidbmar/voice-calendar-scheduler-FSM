# Project Memory

## What is This?

Project Memory is a system for making every coding session, decision, and commit searchable and explainable with citations. No more "why did we do this?" or "what changed here?" Every vibe-coding session is traceable.

## Session ID Format

Every coding session gets a unique ID:

```
S-YYYY-MM-DD-HHMM-<slug>
```

**HHMM is UTC** — always use `date -u +%Y-%m-%d-%H%M` to generate the timestamp.

Example: `S-2026-02-22-1930-initial-scaffold`

## How It Links Together

```
Session → Commits → PRs → ADRs
```

1. Start a session, create a session doc with a Title
2. Make commits with human-readable subjects, `Session: S-...` in body
3. Create PR, reference Session ID
4. If you made a significant decision, write an ADR
5. Link them all together in the docs

## Directory Structure

- `sessions/` - Individual coding session logs
- `adr/` - Architecture Decision Records
- `backlog/` - Bug and feature backlog
- `runbooks/` - Operational procedures
- `architecture/` - System design docs and diagrams
