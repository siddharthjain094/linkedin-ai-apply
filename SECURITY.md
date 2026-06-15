# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, or email the maintainers.

We'll acknowledge your report as soon as we can and keep you updated on a fix.

## Handling of secrets & personal data

This tool runs **entirely on your machine** and is designed to keep sensitive data local:

- **No passwords are stored.** LinkedIn auth lives in a persistent browser profile
  (`browser_profile/`), never in the repo.
- **Your LLM API key** lives in `.env` (git-ignored). Never commit it.
- **Personal data** (`profile/intake.yaml`, `profile/learned_answers.yaml`, your
  resume, the SQLite DB, and generated documents) is git-ignored by default.

Before sharing logs, screenshots, or a `data/jobs.xlsx` export, scrub anything that
identifies you or contains credentials.
