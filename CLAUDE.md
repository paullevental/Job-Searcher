# CLAUDE.md

Project context for agents working in this repo.

**Rules live in [`AGENTS.md`](AGENTS.md), not here.** Hard rules, schema, service
contract, milestones and acceptance checks are all there, and it wins on any
conflict.

Precedence: Paul's global `~/.claude/CLAUDE.md` > `AGENTS.md` > `docs/OFFERRADAR.md`
> this file. One `main` branch only — Paul's instruction on 2026-11-16 supersedes
AGENTS.md §10's branch-per-task line.

---

## Comments

Paul's instruction, 2026-11-18. Applies to every file and language.

- Comment only what the code cannot say: a non-obvious constraint, a unit or
  encoding, a deliberate omission, a line that looks removable but isn't.
- Idiomatic config gets none. `pluginManagement { repositories { ... } }` is not
  complex and needs no explanation.
- When something genuinely is complex, point form, one line per fact.
- Decision history, measurements and rejected alternatives do not belong in
  code. They go in an ADR or a commit message.
- Never restate the next line, and never explain why a block is *absent*.

Tightened by Paul, 2026-11-22. This overrides anything above it.

- **Do not comment unless it is necessary.** Most code gets nothing.
- When necessary: one `#` line directly above the function, class or constant.
- **No docstrings.** Not on modules, classes or functions.
- Bullets only when one line genuinely cannot carry it.
- Applies to every file and language, existing code included.

---

## Response style

Paul's instruction, 2026-11-22. **Be extremely concise. Sacrifice grammar for
the sake of being concise.**

- Every response, every prompt, no exceptions. Plans, explanations, reviews,
  reports — all of it, not just code-adjacent replies.
- Not a suggestion. Mandatory.

---

## Milestone retrospectives

What was built, what broke, and why — moved to `docs/milestones/` on
2026-11-22 to keep this file small. Decision history belongs there, not here,
per the Comments rule above.

| Milestone | Goal | File |
|---|---|---|
| M0 | Stack boots with one command, CI gates every merge | `docs/milestones/M0.md` |
| M1 | §7 schema as versioned migrations | `docs/milestones/M1.md` |
| M2 | Scout collects postings from four sources | `docs/milestones/M2.md` |
| M3 | Dedup collapses duplicates, dead postings retire | `docs/milestones/M3.md` |

Current acceptance-check state, build-environment facts and defect counts live
in those files, not here — read the one for the milestone in question.
