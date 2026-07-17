# ADR-013: Transitive Dependency Security Posture

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** Engineering team
**Supersedes:** The informal "Security: force patched versions" block previously in `[tool.poetry.group.dev.dependencies]`

## Context

The `pip-audit` Security CI job failed after `pip-audit`'s advisory feed surfaced 22 known vulnerabilities across 6 packages reachable through the dependency closure:

| Package | Reaches us via | Code imports it directly? |
|---|---|---|
| `click` | core direct dep | yes |
| `starlette` | `mcp` → `sse-starlette` → `starlette` | **yes** (`worker_server.py`) |
| `python-multipart` | `mcp` | no |
| `pyjwt` | `mcp` (extras=`crypto`) | no |
| `cryptography` | `pyjwt[crypto]` | no |
| `idna` | `httpx` / `anyio` | no |

Two things were wrong with the pre-existing setup, one mechanical and one conceptual:

1. **Mechanical.** The "force patched versions" block in `[tool.poetry.group.dev.dependencies]` had **stale floors** (`starlette>=0.49.1` permitted the now-vulnerable 1.0.0; `python-multipart>=0.0.26` *is* the vulnerable version) and was **missing** four newly-vulnerable transitives (`cryptography`, `idna`, `pyjwt`, and arguably `pygments`/`python-dotenv`).

2. **Conceptual.** That block only influences **lock resolution** — i.e. it bound `poetry.lock`, CI, and contributors. It did **not** ship in the published wheel's `Requires-Dist`. A plain `pip install replicate-mcp-agents` consumer resolved against upstream's loose ranges (e.g. `mcp` declares `starlette>=0.27`) and could land on the vulnerable versions. The project's posture was therefore "clean repo, uncontrolled wheel" without that being stated anywhere.

### The design tension

There are two ways to *guarantee* every install is patched, and both require **declaring transitive deps as core deps** in `[project.dependencies]` so they ship in the wheel's `Requires-Dist`:

- Move all floors into `[project.dependencies]`.

We rejected this on glass-box / honesty grounds (§00 of AGENTS.md). Four of the six packages (`cryptography`, `pyjwt`, `idna`, `python-multipart`) are genuine transitives — our code never imports them. Advertising them as direct deps would misrepresent the dependency graph: a reader of `Requires-Dist` would infer we depend on them, when in fact we depend on `mcp` / `httpx` / `anyio` and *they* pull these in. Honesty about the graph outweighs paternalistic enforcement of a closure we don't own.

The counter-position — "enforce nothing for consumers, document it" — was also considered and rejected: it abandons security-conscious users who would happily opt into enforcement, and it hides the truth about what consumers actually get.

## Decision

We adopt an **honesty-first, opt-in-enforcement, detect-and-disclose** posture with four parts.

### 1. Direct deps are declared honestly; `starlette` is corrected

`starlette` is promoted to the `http` extra (it is genuinely imported in `worker_server.py` — the prior reliance on `mcp → sse-starlette → starlette` was a latent bug, not a clean graph). If `mcp` ever drops `sse-starlette`, the HTTP transport no longer silently breaks.

The four genuine transitives (`cryptography`, `pyjwt`, `idna`, `python-multipart`) are **not** added to `[project.dependencies]`. They are not our direct deps.

### 2. Opt-in enforcement via a `secure` extra

A new `[secure]` extra declares floors for the transitive packages:

```toml
secure = [
    "python-multipart>=0.0.31",   # via mcp: PYSEC-2026-3036/3037/3039/3040
    "pyjwt>=2.13.0",              # via mcp[crypto]: PYSEC-2026-175/176/177/178/179
    "cryptography>=48.0.1",       # via pyjwt[crypto]: GHSA-537c-gmf6-5ccf
    "idna>=3.15",                 # via httpx/anyio: PYSEC-2026-215
    "pygments>=2.20.0",           # via rich/pytest: CVE-2026-4539
    "python-dotenv>=1.2.2",       # via mcp: CVE-2026-28684
]
```

Security-conscious consumers opt in with `pip install "replicate-mcp-agents[secure]"`. The extra is honestly named and honestly scoped: it does not claim to be a core dep, it claims to be optional hardening. User agency is preserved — consumers who manage their own constraints are not over-constrained.

**Side effect (intentional, load-bearing):** Poetry resolves *all* extras at lock time. Because `secure` and `http` exist and declare floors, `poetry.lock` resolves to patched versions even when those extras aren't installed. This means the existing lockfile-based `pip-audit` CI job stays green without a separate dev-group block. The extras are dual-purpose: opt-in consumer enforcement *and* the mechanism that keeps our own lock clean. This replaces the previous dev-group "force patched versions" block, which is removed.

### 3. Consumer-side detection via a non-blocking `wheel-audit` CI job

A new `wheel-audit` job in `.github/workflows/security.yml` is the glass-box counterpart to the lockfile-based `pip-audit` job:

- **Lockfile `pip-audit` (blocking):** guarantees our declared dev/CI environment is clean. We control the lock; a failure is our problem.
- **`wheel-audit` (non-blocking):** builds the published wheel, installs it into a clean consumer venv (resolving from PyPI the way `pip install replicate-mcp-agents` would, **without** the lockfile), and runs `pip-audit` against that closure. `continue-on-error: true`.

The `wheel-audit` job is **non-blocking by design**. We do not enforce transitive security for consumers — those packages are not our direct deps. The job *surfaces the truth* about what consumers get rather than guaranteeing it. A finding is a decision point (add to `secure`, wait for upstream, or document), not a release blocker. It is also the early-warning system: when upstream `mcp` releases drag in the *next* vulnerable transitive that our `secure` floors didn't anticipate, we learn it here.

### 4. Disclosure of the posture

This ADR is the canonical disclosure. The posture in one sentence: **we pin and audit our direct deps; transitive security is upstream's responsibility, and we offer `[secure]` for opt-in enforcement plus a non-blocking wheel-audit for visibility.**

## Consequences

### Positive

- **Honest dependency graph.** The published wheel's `Requires-Dist` lists only what we import. No false advertising.
- **Every install path is covered, honestly:**
  - Lockfile / CI / contributors: patched (via the extras' lock-resolution side effect).
  - `pip install replicate-mcp-agents[secure]`: patched (opt-in enforcement).
  - Plain `pip install replicate-mcp-agents`: *not* guaranteed — and we say so.
- **Latent bug fixed.** `starlette` is now a declared dependency of the `http` feature, not a free ride on a transitive.
- **Early warning for upstream regressions.** `wheel-audit` catches the next vulnerable transitive before a user files an issue.
- **User agency preserved.** Consumers who manage their own constraints are not over-constrained.

### Negative

- A plain `pip install replicate-mcp-agents` *can* resolve to vulnerable `cryptography` / `pyjwt` / `idna` / `python-multipart` if upstream ranges permit it at install time. This is the explicit, disclosed trade-off of the honesty-first stance. We mitigate (not eliminate) it via `[secure]` and `wheel-audit`.
- Two CI jobs to maintain instead of one. `wheel-audit` is non-blocking, so drift is possible if it's ignored; team discipline required to review its output.
- The `secure` extra's floors can go stale if a *new* CVE affects a version above the current floor. Mitigation: the lockfile `pip-audit` job catches this for our env; `wheel-audit` catches it for consumers.

### Neutral

- When upstream `mcp` raises its own floors (e.g. `pyjwt>=2.13.0`, `python-multipart>=0.0.31`, `starlette>=1.3.1`), the `secure` extra's corresponding entries become redundant and should be removed. This is tracked as a follow-up — see "Follow-ups" below. The destination is a clean `secure` extra that shrinks over time as upstream catches up, eventually disappearing.

## Alternatives considered

- **Move all floors to `[project.dependencies]`.** Rejected: misrepresents transitives as direct deps, violates glass-box/honesty values. Would also slightly worsen install resolution flexibility for consumers.
- **Keep the dev-group block, accept the consumer gap silently.** Rejected: hides the truth about what consumers get, contradicts the project's security-positioned posture (audit evidence bundle, dedicated security workflow).
- **Block `wheel-audit` findings.** Rejected: would force us to either over-declare transitives as core deps (back to alternative 1) or block releases on packages we don't own. Non-blocking detection is the honest middle.

## Follow-ups

- **Upstream `mcp` floors.** File / track an item: when `mcp` publishes a release raising its own floors for `pyjwt`, `python-multipart`, `starlette`, etc., bump our `mcp` floor and remove the now-redundant entries from the `secure` extra. The `secure` extra should shrink over time, not grow.
- **Review `wheel-audit` output on schedule.** The nightly cron already runs the Security workflow; treat any `wheel-audit` finding as a triage item in the next planning cycle.
- **README mention.** Add a one-line pointer to `[secure]` in the README's installation section so consumers discover the opt-in without reading this ADR.
