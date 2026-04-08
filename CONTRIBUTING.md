# Contributing

## Ground Rules

- Open an issue before large changes or strategy rewrites.
- Keep pull requests focused on one behavior change at a time.
- Do not commit secrets, `.env`, runtime state, or logs.
- Default to paper-trading-safe behavior unless the change explicitly targets live execution.

## Local Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run a basic smoke check before opening a PR:

```bash
python -m compileall .
```

If your change affects detection or accounting, include a short note about:

- what behavior changed
- how you verified it
- whether it changes paper/live risk

## Pull Request Notes

- Link the related issue when there is one.
- Mention config changes explicitly.
- Include log snippets or screenshots only when they clarify behavior.
