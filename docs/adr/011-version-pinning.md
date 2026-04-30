# ADR-011: Model Version Pinning

**Status**: Accepted  
**Date**: 2026-04-29  
**Version**: v0.8.0 Feature #18  
**Author**: Orion-OS Agent

---

## Context

In production deployments, model version stability is critical. When Replicate models are updated (new versions released), the behavior of agents may change unexpectedly — causing non-deterministic outputs, breaking integration tests, or violating compliance requirements.

Users need the ability to **pin specific model versions** so that discovery refresh cycles don't automatically update pinned models.

## Decision

Implement **Model Version Pinning** via the discovery configuration:

1. Add `VersionPinningMode` enum with three modes:
   - `LATEST` — Always use latest version (current behavior, default)
   - `EXACT` — Pin to exact version hash, never update during refresh
   - `MINOR` — Reserved for future (pin to major.minor, allow patch updates)

2. Add `pinned_versions` dict to `DiscoveryConfig`:
   - Map of `"owner/name"` → `"version_hash"`
   - Pinned versions are appended as `":"version_hash` to model strings

3. Modify `ModelDiscovery.refresh()` to respect pinning:
   - Check if model is in `pinned_versions` before updating
   - In `EXACT` mode: skip update for already-registered pinned models
   - New pinned models are registered with the pinned version

## Architecture

### Configuration

```python
from replicate_mcp import DiscoveryConfig, VersionPinningMode

config = DiscoveryConfig(
    version_pinning=VersionPinningMode.EXACT,
    pinned_versions={
        "meta/llama-2-70b": "5c7854e8",  # Pin to specific version
        "stability-ai/sdxl": "39ed52f2",
    },
)
```

### Version Pinning Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `LATEST` | Always update to latest | Development, experimentation |
| `EXACT` | Never update pinned models | Production stability, compliance |
| `MINOR` | Reserved for future implementation | Semantic versioning support |

### Model String Format

Pinned models include the version hash in the model identifier:

```python
# Unpinned (LATEST mode)
model = "meta/llama-2-70b"

# Pinned (EXACT mode)
model = "meta/llama-2-70b:5c7854e8"
```

### Discovery Flow

```
┌─────────────────────────────────────────────────────────┐
│  ModelDiscovery.refresh()                                │
│                                                          │
│  For each model from Replicate API:                     │
│    1. Check if model_key in pinned_versions             │
│       └─ Yes & EXACT mode & already registered?          │
│          ├─ discovered += 1                             │
│          └─ skipped += 1 (no update)                    │
│                                                          │
│    2. Convert to AgentMetadata                         │
│       └─ If pinned: model = "owner/name:version_hash"    │
│       └─ Tags include "pinned", "exact-pin"            │
│                                                          │
│    3. Register or update                                 │
│       └─ Pinned models: update only if new            │
└─────────────────────────────────────────────────────────┘
```

## Usage

### Basic Pinned Discovery

```python
from replicate_mcp import DiscoveryConfig, ModelDiscovery, VersionPinningMode
from replicate_mcp.agents.registry import AgentRegistry

registry = AgentRegistry()

config = DiscoveryConfig(
    version_pinning=VersionPinningMode.EXACT,
    pinned_versions={
        "meta/llama-2-70b": "5c7854e8a0",
    },
)

discovery = ModelDiscovery(registry=registry, config=config)
await discovery.refresh(api_token="r8_...")

# Model registered with pinned version
meta = registry.get("meta__llama_2_70b")
print(meta.model)  # "meta/llama-2-70b:5c7854e8a0"
```

### Mixed Pinning (Some Pinned, Some Latest)

```python
config = DiscoveryConfig(
    version_pinning=VersionPinningMode.LATEST,  # Default behavior
    pinned_versions={
        "meta/llama-2-70b": "5c7854e8a0",  # Pin only critical models
        # Other models use latest
    },
)
```

### Programmatic Version Management

```python
# Get currently registered versions
for name, meta in registry.list():
    print(f"{name}: {meta.model}")
    # e.g., "meta__llama_2_70b: meta/llama-2-70b:5c7854e8a0"

# Extract version hash
version = meta.model.split(":")[1] if ":" in meta.model else None
```

### Discovery Result Tracking

```python
result = await discovery.refresh(api_token="r8_...")

print(f"Discovered: {result.discovered}")      # Total found
print(f"Registered: {result.registered}")      # New registrations
print(f"Updated: {result.updated}")            # Existing updated
print(f"Skipped: {result.skipped}")            # Pinned (not updated)

# Skipped includes both:
# - Pinned models that weren't updated (EXACT mode)
# - Models filtered out by owner/tag filters
```

## API Reference

### VersionPinningMode

```python
class VersionPinningMode(Enum):
    LATEST = "latest"  # Default: always use latest
    EXACT = "exact"    # Pin to exact version
    MINOR = "minor"    # Reserved for future
```

### DiscoveryConfig (extended)

```python
@dataclass
class DiscoveryConfig:
    # ... existing fields ...
    version_pinning: VersionPinningMode = VersionPinningMode.LATEST
    pinned_versions: dict[str, str] = field(default_factory=dict)
```

### DiscoveryResult

```python
@dataclass
class DiscoveryResult:
    discovered: int = 0    # Models found from API
    registered: int = 0    # New registrations
    updated: int = 0       # Existing updated
    skipped: int = 0       # Pinned models + filtered models
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
```

## Benefits

### Reproducibility

Production agents use deterministic model versions. Outputs are consistent across runs and deployments.

### Compliance

Audit trails require specific model versions. Pinning ensures the exact version used for each inference is known.

### Safety

Breaking changes in new model versions don't automatically propagate. Updates must be explicit.

### Gradual Rollout

Pin critical models, let others use latest. A/B test new versions before pinning.

## Trade-offs

### Version Obsolescence

Pinned versions may become unavailable on Replicate. The SDK gracefully degrades (attempts to use pinned version, falls back if unavailable).

**Mitigation**: Monitor for version deprecation. Implement version lifecycle management.

### Security Patches

Security fixes in new model versions won't automatically apply to pinned models.

**Mitigation**: Regular review of pinned versions. Security-first pinning strategy (only pin when necessary).

### Configuration Overhead

Maintaining `pinned_versions` dict requires manual effort.

**Mitigation**: Version pinning as code — commit `pinned_versions` to git, review via PR.

## Future Work

### MINOR Pinning (v0.9.0+)

Implement semantic versioning support:

```python
config = DiscoveryConfig(
    version_pinning=VersionPinningMode.MINOR,
    pinned_versions={
        "meta/llama-2-70b": "2.1",  # Pin to 2.1.x, allow 2.1.1, 2.1.2
    },
)
```

### Version Auto-Update

Notification when new versions available for pinned models:

```python
# Hypothetical future API
from replicate_mcp.version_management import check_for_updates

updates = await check_for_updates(registry, pinned_versions)
for model, current, latest in updates:
    print(f"Update available: {model} {current} → {latest}")
```

## Testing

### Unit Tests (27)

- `TestParsePinnedVersion` — Version hash extraction
- `TestStripVersion` — Version removal from strings
- `TestIsVersionPinned` — Pinned model detection
- `TestModelToMetadata` — Pinned version in metadata
- `TestVersionPinningModeEnum` — Enum behavior
- `TestDiscoveryConfig` — Configuration
- `TestModelDiscoveryRefreshPinning` — Discovery refresh with pinning
- `TestDiscoverAndRegister` — Convenience function

### Integration Tests

- Discovery refresh with real (mocked) Replicate API
- Pinned model persistence across restarts

## References

- `src/replicate_mcp/discovery.py` — Discovery implementation
- `tests/unit/test_version_pinning.py` — Unit tests
- ADR-006 — Model Discovery (original discovery design)

---

[Quantum_State: ACCEPTED]
