# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Review-before-apply flow**: generate tailored documents for all matched jobs,
  review/edit the editable `.docx`, approve, then submit only approved jobs
  (`generate`, `resumes`, `approve`, `profile`, `apply --only-approved`).
- **Local web UI** (`linkedin-apply ui`): filterable job grid, bulk approve/reject,
  one-click fetch/generate/apply, a **Stop** button, and a **Recent runs** panel.
- **Robustness**: mid-run logout / auth-wall detection, a single-instance run-lock,
  cooperative cancellation (Stop), a loud LLM-failure abort, and persisted run
  history (`RunLog`, surfaced via `/api/runs`).
- Full **AGPL-3.0** `LICENSE`, contributor docs, security policy, and CI.

### Changed
- Document generation is decoupled from applying; edits made during review are what
  get submitted (PDF is rendered from the edited docx at submit time).
- More resilient LinkedIn description fetching (expands the "…more" button) and
  apply-type detection.

### Fixed
- Robust LLM score parsing (handles `"85%"` and other junk).
- Form answers that can't be confidently mapped now go to `human_review` instead of
  silently selecting the wrong option.
- `.env.example` is no longer accidentally git-ignored.

## [0.1.0]
- Initial agentic discover → score → generate → apply pipeline with a pluggable LLM,
  spreadsheet export, and human-review resolution.
