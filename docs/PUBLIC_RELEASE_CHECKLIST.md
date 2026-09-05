# Public Preview release checklist

This checklist records the GitHub and organizational steps that remain manual.
Complete it while the repository is still internal unless a step explicitly
follows the visibility change.

## Approval and ownership

- [ ] MotherDuck Legal/OSS approves publishing the code and reachable Git
      history under Apache-2.0, the Google-derived SQL attribution in `NOTICE`,
      and the project name/branding.
- [ ] Customer Engineering accepts ownership of releases, support escalation,
      privacy-sensitive changes, and dependency alerts.
- [ ] Give `@motherduckdb/customer-eng` write access to the repository so the
      entries in `.github/CODEOWNERS` take effect.

## Merge and release

- [ ] Merge the Public Preview readiness changes while the repository is still
      internal.
- [ ] Confirm the `ci` workflow passes on `main`, including Python 3.10, 3.12,
      and 3.14 tests and the package build.
- [ ] Create the protected `v0.1.2` tag from the reviewed `main` commit.
- [ ] Confirm the release workflow publishes the wheel, source archive, and
      `SHA256SUMS`; verify the checksums before changing visibility.
- [ ] Confirm the README's pinned `v0.1.2` installation command works in a clean
      environment.

## GitHub governance

- [ ] Set the default `GITHUB_TOKEN` permission to read-only and disable the
      option that lets workflows create or approve pull requests.
- [ ] Require actions to be pinned to full commit SHAs.
- [ ] Restrict allowed actions to the GitHub-owned actions and the reviewed
      `astral-sh/setup-uv` and `softprops/action-gh-release` actions used here.
- [x] Protect `main`: ruleset `protect-main` requires a pull request with one
      approval, Code Owner review, and conversation resolution, dismisses stale
      reviews, and blocks force pushes and deletion. Admins may bypass only via
      a pull request.
- [ ] Add the `ci` jobs as required status checks on `protect-main` once the
      `ci` workflow is on `main` (requiring them earlier would block the PR
      that introduces them).
- [x] Protect `v*` tags: ruleset `protect-release-tags` allows only repository
      admins to create, update, or delete them. Add the Customer Engineering
      release group as a bypass actor if tags should not require an admin.
- [ ] Enable immutable GitHub releases if available for the organization.
- [ ] Leave PyPI publishing gated off (`PUBLISH_TO_PYPI` unset) until a
      MotherDuck-owned PyPI identity exists with trusted publishing configured
      for this repo, workflow `release.yml`, environment `pypi`; then create the
      `pypi` GitHub environment with required reviewers before setting the
      variable to `true`.
- [ ] Confirm only the intended teams and administrators have write, maintain,
      or release authority.

## Visibility and security

- [ ] Set an accurate repository description, homepage, and topics.
- [ ] Change visibility from internal to public.
- [ ] Immediately re-check branch and tag rules after the visibility change;
      GitHub may disable some push rulesets during an internal-to-public change.
- [ ] Enable and verify secret scanning, push protection, Dependabot alerts and
      security updates, and CodeQL default setup.
- [ ] Review any alerts before sharing the repository with prospects.
- [ ] Verify the code, branches, tags, releases, pull requests, Actions history,
      and logs as an unauthenticated visitor.

## GTM handoff

- [ ] Give GTM the Public Preview qualification, support, and escalation path.
- [ ] Require users to read `docs/DATA_HANDLING.md` before sharing a handoff.
- [ ] Ensure GTM describes the output as factual assessment evidence, not a
      compatibility guarantee or automated migration plan.
