# Contributing

Thank you for your interest in `md-migration-assessment`. The project is
maintained by MotherDuck and is in Public Preview, so its interfaces, output
schemas, and roadmap are still moving. External contributions are welcome,
and whether a change is accepted is at the discretion of the MotherDuck
maintainers.

## Before you start

- **Open an issue first** for anything beyond a small fix: a new extractor, a
  new source adapter, a change to the output schema or the handoff policy.
  That lets us say early whether the change fits the roadmap, before you spend
  time on it.
- Small, self-contained fixes (typos, a broken probe, a missing column
  classification) can go straight to a pull request.
- Bugs and setup problems can also go to **support@motherduck.com**; see
  [SUPPORT.md](SUPPORT.md). Security issues must follow
  [SECURITY.md](SECURITY.md), never a public issue.

## What we look for

- **Privacy first.** Every raw column an extractor lands must be classified in
  the manifest's `sensitive_fields`, and the handoff column policy must keep
  source bodies and query text out. A change that widens what is collected
  needs a clear reason and a test.
- **Facts, not judgments.** The report layer records what was observed and
  how well it was observed. Compatibility ratings and migration-effort
  estimates are out of scope for this repository.
- **Coverage honesty.** Missing evidence is recorded as unavailable or
  unknown, never as zero. Changes to the runner or report must keep the
  status contract in `meta.extract_runs` intact.
- **Tests.** `uv run --extra dev pytest` must pass. New adapters should mirror
  `tests/test_adapter_seam.py`; Snowflake changes go under `tests/snowflake/`.

## Process

1. Fork the repository and branch from `main`.
2. Keep pull requests focused. Explain what changed and why, and note any
   change to the raw or meta schema version.
3. A MotherDuck maintainer reviews every pull request. We may ask for changes,
   accept the change, or decline it if it does not fit the project's direction.
   Declining is not a judgment on the quality of the work.

By contributing you agree that your contribution is licensed under the
project's [Apache-2.0 license](LICENSE).
