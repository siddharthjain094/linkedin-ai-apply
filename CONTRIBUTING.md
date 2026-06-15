# Contributing

Thanks for your interest in improving **linkedin-ai-apply**! Contributions of all
kinds are welcome — bug reports, docs, selectors that survive LinkedIn's next DOM
change, and new features.

## Development setup

```bash
git clone <your-fork-url>
cd linkedin-ai-apply
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ui,pdf]"
playwright install chromium
```

## Before you open a PR

Run the same checks CI runs:

```bash
ruff check agent tests      # lint
pytest -q                   # tests
```

- Keep the line length at 100 (configured in `pyproject.toml`).
- Add or update tests for any behavior change. The suite is fast and runs without a
  browser or live network — keep it that way (mock the browser/LLM boundaries).
- Prefer small, focused PRs with a clear description of the *why*.

## Project layout

See the **Project layout** section in [`README.md`](README.md). In short:

- `agent/pipeline/` — the phases (`discover`, `match`, `generate`, `apply`).
- `agent/browser/` — Playwright session + page interactions (the fragile part).
- `agent/llm/` — pluggable LLM provider (litellm) + prompts.
- `agent/web/` — the optional local FastAPI UI.

## Reporting bugs / requesting features

Open an issue using the templates. For browser/selector breakage, please include the
job URL pattern and a screenshot if possible (LinkedIn changes its DOM often).

## A note on scope & ethics

This project automates actions against LinkedIn, which violates their User Agreement.
Please keep contributions oriented toward responsible, conservative use (rate limits,
review modes, transparency). PRs that exist purely to evade detection or scale abuse
will be declined.

## License

By contributing, you agree that your contributions will be licensed under the
project's **AGPL-3.0-or-later** license.
