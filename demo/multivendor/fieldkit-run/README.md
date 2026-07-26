# roshambo-fieldkit — what the three vendors actually built

This directory is a **record, not a library.** It is kept exactly as the agents wrote it,
which is why it is excluded from this project's linting and formatting
(`pyproject.toml`, `tool.ruff.extend-exclude`): reformatting it would falsify the record.
Nothing here is imported by Roshambo.

Twelve small independent helper functions, one per task, written during the multi-vendor
field run of 2026-07-27 by three coding agents from three different vendors, coordinating
through nothing but a CockroachDB cluster. Each agent was a fresh operating-system
process with a fresh session; they shared no memory, no message bus and no file handle.

**Measured, on the finished result: 44 tests, 44 passed.** Run it with `pytest` from
this directory.

## Who built what

Attribution is taken from the granted `claim` rows in `audit_log`, not from the agents'
own reports.

| Task | Module | Won by | Vendor |
|---|---|---|---|
| 01 | `human_duration` | `claude-code-3` | Anthropic |
| 02 | `parse_resource` | `codex-1` | OpenAI |
| 03 | `backoff_delays` | `codex-3` | OpenAI |
| 04 | `truncate_middle` | `agy-2` | Google |
| 05 | `is_expired` | `agy-3` | Google |
| 06 | `slugify` | `agy-1` | Google |
| 07 | `chunk` | `claude-code-1` | Anthropic |
| 08 | `percent` | `claude-code-1` | Anthropic |
| 09 | `ordinal` | `agy-2` | Google |
| 10 | `merge_intents` | `claude-code-3`, then `agy-1` | Anthropic, then Google |
| 11 | `clamp` | `codex-3` | OpenAI |
| 12 | `format_table` | `agy-3` | Google |

Five tasks to Google, four to Anthropic, three to OpenAI. Nobody assigned that split;
it is what the lease handed out.

## The one duplicate, and why it is left in

Task 10 was granted twice: to `claude-code-3` at 23:07:52, and to `agy-1` at 23:11:29
after the first holder had finished and released. `INDEX.md` therefore carries the task
10 line twice — and that duplicate is left in place because it is the visible
consequence of a real limitation:

**A lease says nobody is working on this. It does not say the work still needs doing.**

Both checks are necessary. The agent protocol asks for both (claim *and* check whether
the module file already exists), but the lease alone cannot enforce the second, and here
the second was skipped. This was first seen in the pilot run and is recorded in
`../PROTOCOL.md` and `../../../docs/EVIDENCE-multivendor.md` rather than quietly cleaned
up.

While `claude-code-3` held that lease it refused four other agents — `claude-code-2`,
`codex-3`, `codex-1` and `codex-2` — each told who was working and on what. Three of
those four refusals crossed a vendor boundary.

## Also here

- `INDEX.md` — the shared registry the agents took turns writing to, claimed as
  `fieldkit:index` before each append.
- `run.json` — every invocation with its start and end time, so the overlap between
  processes can be checked independently.
- `evidence.json` — the full output of `../collect_evidence.py`, produced by applying
  the rules in `../PROTOCOL.md` to the `audit_log`.

`INDEX.md` holds ten lines covering nine distinct tasks, because task 10 is registered
twice. Tasks **02, 03 and 11** were never registered at all — all three OpenAI's, and the
reason is in the run logs: those agents spent their remaining turn trying to run `pytest`,
which their sandbox could not reach, and stopped before the registration step. The modules
and their tests exist and pass; only the registry line is missing.
