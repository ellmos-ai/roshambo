# Plan pointer

The authoritative build plan for this repository lives outside the repository, in the
maintainer's planning workspace. It is deliberately not committed: it contains competition
scheduling, decision records and working notes that are not part of the product.

What lives where:

| Artefact | Location | Committed? |
|---|---|---|
| Build plan, architecture rationale, phase acceptance criteria | maintainer's planning workspace, `MANIFEST.md` | no |
| Interface contract between parallel build lanes | `CONTRACT.md` (repo root) | yes |
| Evidence of executed runs | `docs/EVIDENCE-*.md` | yes |
| Cross-lane requests | `docs/HANDOFF.md` | yes |
| Public documentation | `README.md`, `README_de.md`, `docs/` | yes |

If you are contributing and need the reasoning behind a design decision, `CONTRACT.md` and the
architecture section of `README.md` carry everything that matters for the code. The planning
document adds no technical information beyond those two.
