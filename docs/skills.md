# Agent Skills

This document covers the [Agent Skills](https://github.com/cockroachlabs/cockroachdb-skills)
(`SKILL.md` format) that ship in this repository's `skills/` directory, and how to add
Cockroach Labs' own operational skills alongside them.

## What ships in this repository

`skills/` contains two skills, each a `SKILL.md` file with YAML frontmatter (`name`,
`description`) followed by Markdown instructions — the same format used by
[`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills):

| Skill | Teaches | Read it |
|---|---|---|
| `roshambo-remember-and-recall` | Call `recall()` before starting unfamiliar work; write `remember()` trails for every outcome, failures included | [`skills/roshambo-remember-and-recall/SKILL.md`](../skills/roshambo-remember-and-recall/SKILL.md) |
| `roshambo-claim-work` | Call `claim()` before starting shareable work; treat a `ClaimDenied` as information, not an error; `heartbeat()` long work; `release()` promptly | [`skills/roshambo-claim-work/SKILL.md`](../skills/roshambo-claim-work/SKILL.md) |

Both skills describe *habits*, not API syntax — an agent that has only read
`roshambo-mcp`'s tool schemas knows the six verbs exist; these skills are what teaches
it to actually use `recall()` before acting and to treat a denied `claim()` as useful
information rather than something to retry in a loop. That distinction is why they
exist as skills and not just as tool docstrings (the docstrings in
`src/roshambo/mcp/server.py` carry the same guidance, more tersely, for clients that read
tool descriptions but do not separately load skills).

## Installing them

Copy or symlink `skills/roshambo-remember-and-recall` and `skills/roshambo-claim-work`
into whatever directory your agent tool loads Agent Skills from. For Claude Code, that
is typically a project's `.claude/skills/` or the user-level `~/.claude/skills/`
directory — see [Claude Code's Skills documentation](https://code.claude.com/docs/en/skills)
for the exact mechanism, which is not specific to Roshambo.

## Adding `cockroachlabs/cockroachdb-skills` alongside them

[`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills)
is a separate, much larger collection: general CockroachDB operational knowledge spanning
nine domains (onboarding and migrations, application development, performance and
scaling, operations and lifecycle, resilience and disaster recovery, observability and
diagnostics, security and governance, integrations and ecosystem, cost and usage
management), with dozens of individual skills across them. None of those nine domains is
about multi-agent coordination or negative memory — that repository teaches an agent how
to operate CockroachDB well; it has nothing to say about two agents claiming the same
resource or recalling a prior failure, which is the gap Roshambo's own two skills fill.

The two collections are complementary, not overlapping, and installing both is the
intended setup for an agent that both operates a CockroachDB cluster *and* participates
in a Roshambo-backed swarm. Clone or install
[`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills)
per its own instructions; this repository does not vendor a copy of it (see `NOTICE`).

## Related

- [`docs/mcp-managed.md`](mcp-managed.md) — the CockroachDB Managed MCP Server, the
  read-only inspection path these skills do not cover.
- Top-level [`README.md`](../README.md), section "Which CockroachDB tool, for what" —
  where Agent Skills fit among the four CockroachDB hackathon tools.
