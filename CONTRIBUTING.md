# Contributing to Replicate MCP Agents

Thank you for your interest in contributing! This document covers the development workflow, coding standards, and release process.

## Development Setup

```bash
git clone https://github.com/pt-act/replicate-mcp-agents.git
cd replicate-mcp-agents
poetry install --with dev,docs
poetry run pre-commit install
```

## Branch Strategy

- **`main`** — stable, release-ready code
- **`feature/<name>`** — feature branches (branch from `main`)
- **`fix/<name>`** — bug fix branches

All changes go through pull requests. Direct pushes to `main` are prohibited.

## Sprint Cadence

| Event | Frequency | Duration | Purpose |
|-------|-----------|----------|---------|
| Sprint Planning | Bi-weekly (Monday) | 2 hours | Scope sprint, assign stories |
| Daily Standup | Daily | 15 min | Blockers, progress |
| Sprint Demo | Bi-weekly (Friday) | 30 min | Demo working software |
| Sprint Retro | Bi-weekly (Friday) | 30 min | Process improvements |

**Sprint duration:** 2 weeks  
**Release cadence:** End of each sprint (feature-complete sprints only)

## Coding Standards

### Python
- **Style:** Ruff (lint + format), configured in `pyproject.toml`
- **Types:** mypy with `disallow_untyped_defs = true`
- **Imports:** `from __future__ import annotations` in every module
- **Exports:** Every module defines `__all__`

### Testing
- **Framework:** pytest + pytest-asyncio
- **Coverage target:** ≥80% (CI gate), ≥95% for utils/mcp modules
- **Run tests:** `poetry run pytest`
- **Coverage report:** `poetry run pytest --cov-report=html`

### Security
- **No `eval()` or `exec()`** — use the `TransformRegistry` for dynamic behaviour
- **No string-encoded lambdas** in YAML or config files
- **Never log API tokens** — use the `REPLICATE_API_TOKEN` env var

## Pull Request Process

1. Create a feature/fix branch from `main`
2. Write code + tests (tests required for all new functionality)
3. Run `poetry run pytest` — all tests must pass
4. Run `poetry run ruff check . && poetry run ruff format --check .`
5. Open PR with description, link to issue/story, and test evidence
6. At least 1 approval required before merge
7. Squash-merge to `main`

## Architecture Decision Records (ADRs)

Significant design decisions are documented in `docs/adr/`. When making a decision that:
- Changes a dependency
- Introduces a new architectural pattern
- Removes or replaces a component

Create a new ADR following the template in `docs/adr/001-dependency-cleanup.md`.

## Release Process

1. Update version in `src/replicate_mcp/__init__.py` and `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create a git tag: `git tag v0.x.0`
4. Push tag: `git push origin v0.x.0`
5. CI automatically builds and publishes to PyPI