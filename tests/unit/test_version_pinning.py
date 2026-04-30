"""Unit tests for Model Version Pinning (Feature #18).

Version pinning prevents models from being updated during discovery refresh,
enabling reproducibility and stability in production deployments.
"""

from unittest.mock import MagicMock, patch

import pytest

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry
from replicate_mcp.discovery import (
    DiscoveryConfig,
    DiscoveryResult,
    ModelDiscovery,
    VersionPinningMode,
    _is_version_pinned,
    _model_to_metadata,
    _parse_pinned_version,
    _strip_version,
    discover_and_register,
)

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


class TestParsePinnedVersion:
    """Tests for _parse_pinned_version helper."""

    def test_returns_version_from_pinned_string(self) -> None:
        """Extract version hash from pinned model string."""
        assert _parse_pinned_version("meta/llama-2-70b:5c7854e8") == "5c7854e8"

    def test_returns_none_for_unpinned_string(self) -> None:
        """Return None for unpinned model string."""
        assert _parse_pinned_version("meta/llama-2-70b") is None

    def test_handles_multiple_colons(self) -> None:
        """Handle multiple colons in model string - returns everything after first colon."""
        assert _parse_pinned_version("owner/model:version:extra") == "version:extra"


class TestStripVersion:
    """Tests for _strip_version helper."""

    def test_removes_version_hash(self) -> None:
        """Remove version hash from model string."""
        assert _strip_version("meta/llama-2-70b:5c7854e8") == "meta/llama-2-70b"

    def test_returns_unchanged_for_unpinned(self) -> None:
        """Return unchanged for unpinned model string."""
        assert _strip_version("meta/llama-2-70b") == "meta/llama-2-70b"

    def test_handles_multiple_colons(self) -> None:
        """Handle multiple colons correctly."""
        assert _strip_version("owner/model:version:extra") == "owner/model"


class TestIsVersionPinned:
    """Tests for _is_version_pinned helper."""

    def test_returns_false_for_latest_mode(self) -> None:
        """Never pinned when mode is LATEST."""
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.LATEST,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        assert not _is_version_pinned("meta", "llama-2-70b", config)

    def test_returns_true_for_exact_mode(self) -> None:
        """Pinned when mode is EXACT and model is in pinned_versions."""
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        assert _is_version_pinned("meta", "llama-2-70b", config)

    def test_returns_false_for_exact_mode_unpinned_model(self) -> None:
        """Not pinned when model not in pinned_versions."""
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        assert not _is_version_pinned("openai", "gpt-4", config)

    def test_minor_mode_treats_as_pinned(self) -> None:
        """MINOR mode currently behaves like EXACT."""
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.MINOR,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        assert _is_version_pinned("meta", "llama-2-70b", config)


class TestModelToMetadata:
    """Tests for _model_to_metadata with version pinning."""

    def _make_model(self, owner: str = "meta", name: str = "llama-2-70b", tags: list[str] | None = None) -> MagicMock:
        """Create a mock model object."""
        model = MagicMock()
        model.owner = owner
        model.name = name
        model.tags = tags or []
        model.description = "Test model"
        return model

    def test_latest_mode_uses_unpinned_model(self) -> None:
        """LATEST mode returns unpinned model string."""
        model = self._make_model()
        config = DiscoveryConfig(version_pinning=VersionPinningMode.LATEST)
        meta = _model_to_metadata(model, config)

        assert meta is not None
        assert meta.model == "meta/llama-2-70b"
        assert "latest" in meta.tags

    def test_exact_mode_uses_pinned_version(self) -> None:
        """EXACT mode includes pinned version hash in model string."""
        model = self._make_model()
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        meta = _model_to_metadata(model, config)

        assert meta is not None
        assert meta.model == "meta/llama-2-70b:5c7854e8"
        assert "pinned" in meta.tags
        assert "exact-pin" in meta.tags

    def test_minor_mode_uses_pinned_version(self) -> None:
        """MINOR mode includes pinned version hash with minor-pin tag."""
        model = self._make_model()
        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.MINOR,
            pinned_versions={"meta/llama-2-70b": "5c7854e8"},
        )
        meta = _model_to_metadata(model, config)

        assert meta is not None
        assert meta.model == "meta/llama-2-70b:5c7854e8"
        assert "pinned" in meta.tags
        assert "minor-pin" in meta.tags

    def test_provided_version_included_in_model_string(self) -> None:
        """Explicit version parameter included in model string."""
        model = self._make_model()
        config = DiscoveryConfig(version_pinning=VersionPinningMode.LATEST)
        meta = _model_to_metadata(model, config, version="abc123")

        assert meta is not None
        assert meta.model == "meta/llama-2-70b:abc123"
        assert "versioned" in meta.tags

    def test_unpinned_model_skipped_by_owner_filter(self) -> None:
        """Model skipped if owner doesn't match filter."""
        model = self._make_model(owner="openai")
        config = DiscoveryConfig(owner="meta")
        meta = _model_to_metadata(model, config)
        assert meta is None

    def test_unpinned_model_skipped_by_tag_filter(self) -> None:
        """Model skipped if tags don't match filter."""
        model = self._make_model(tags=["nlp"])
        config = DiscoveryConfig(required_tags=["vision"])
        meta = _model_to_metadata(model, config)
        assert meta is None


class TestVersionPinningModeEnum:
    """Tests for VersionPinningMode enum."""

    def test_enum_values(self) -> None:
        """Verify enum values."""
        assert VersionPinningMode.LATEST.value == "latest"
        assert VersionPinningMode.MINOR.value == "minor"
        assert VersionPinningMode.EXACT.value == "exact"

    def test_default_is_latest(self) -> None:
        """LATEST is the default pinning mode."""
        config = DiscoveryConfig()
        assert config.version_pinning == VersionPinningMode.LATEST


class TestDiscoveryConfig:
    """Tests for DiscoveryConfig with version pinning."""

    def test_empty_pinned_versions_by_default(self) -> None:
        """Default pinned_versions is empty dict."""
        config = DiscoveryConfig()
        assert config.pinned_versions == {}

    def test_can_set_pinned_versions(self) -> None:
        """Pinned versions can be set at construction."""
        pinned = {
            "meta/llama-2-70b": "5c7854e8",
            "stability-ai/sdxl": "39ed52f2",
        }
        config = DiscoveryConfig(pinned_versions=pinned)
        assert config.pinned_versions == pinned

    def test_configurable_pinning_mode(self) -> None:
        """Pinning mode is configurable."""
        config = DiscoveryConfig(version_pinning=VersionPinningMode.EXACT)
        assert config.version_pinning == VersionPinningMode.EXACT


class TestModelDiscoveryRefreshPinning:
    """Tests for ModelDiscovery.refresh with version pinning."""

    def _make_model_mock(self, owner: str, name: str, tags: list[str] | None = None) -> MagicMock:
        """Create a mock Replicate model object."""
        model = MagicMock()
        model.owner = owner
        model.name = name
        model.tags = tags or []
        model.description = f"{owner}/{name}"
        return model

    @pytest.mark.asyncio
    async def test_skips_updating_pinned_model_in_exact_mode(self) -> None:
        """Pinned models not updated when mode is EXACT."""
        registry = AgentRegistry()
        registry.register(
            AgentMetadata(
                safe_name="meta__llama",
                description="Llama",
                model="meta/llama:old_version",
                tags=["pinned"],
            )
        )

        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama": "pinned_version"},
        )
        discovery = ModelDiscovery(registry=registry, config=config)

        # Mock the replicate client via import patching
        model = self._make_model_mock("meta", "llama")
        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.return_value = [model]
        mock_replicate.Client.return_value = mock_client

        with patch.dict('sys.modules', {'replicate': mock_replicate}):
            result = await discovery.refresh(api_token="test")  # noqa: S106

        # Model was discovered but not updated (skipped)
        assert result.discovered == 1
        assert result.skipped == 1
        assert result.updated == 0

        # Original pinned version preserved
        meta = registry.get("meta__llama")
        assert meta is not None
        assert meta.model == "meta/llama:old_version"

    @pytest.mark.asyncio
    async def test_updates_unpinned_model_in_exact_mode(self) -> None:
        """Unpinned models are updated when mode is EXACT."""
        registry = AgentRegistry()
        registry.register(
            AgentMetadata(
                safe_name="openai__gpt4",
                description="GPT-4",
                model="openai/gpt-4:old_version",
                tags=["unpinned"],
            )
        )

        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama": "pinned_version"},  # Only meta/llama is pinned
        )
        discovery = ModelDiscovery(registry=registry, config=config)

        model = self._make_model_mock("openai", "gpt4")
        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.return_value = [model]
        mock_replicate.Client.return_value = mock_client

        with patch.dict('sys.modules', {'replicate': mock_replicate}):
            result = await discovery.refresh(api_token="test")  # noqa: S106

        # Unpinned model was updated
        assert result.discovered == 1
        assert result.updated == 1
        assert result.skipped == 0

    @pytest.mark.asyncio
    async def test_registers_new_pinned_model(self) -> None:
        """New pinned models are registered with pinned version."""
        registry = AgentRegistry()

        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.EXACT,
            pinned_versions={"meta/llama": "abc123"},
        )
        discovery = ModelDiscovery(registry=registry, config=config)

        model = self._make_model_mock("meta", "llama")
        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.return_value = [model]
        mock_replicate.Client.return_value = mock_client

        with patch.dict('sys.modules', {'replicate': mock_replicate}):
            result = await discovery.refresh(api_token="test")  # noqa: S106

        # New pinned model was registered
        assert result.discovered == 1
        assert result.registered == 1

        # Pinned version was used
        meta = registry.get("meta__llama")
        assert meta is not None
        assert ":abc123" in meta.model
        assert "pinned" in meta.tags

    @pytest.mark.asyncio
    async def test_latest_mode_updates_all_models(self) -> None:
        """LATEST mode updates all models including previously pinned."""
        registry = AgentRegistry()
        registry.register(
            AgentMetadata(
                safe_name="meta__llama",
                description="Llama",
                model="meta/llama:old_version",
                tags=["pinned"],
            )
        )

        config = DiscoveryConfig(
            version_pinning=VersionPinningMode.LATEST,
            pinned_versions={"meta/llama": "new_version"},
        )
        discovery = ModelDiscovery(registry=registry, config=config)

        model = self._make_model_mock("meta", "llama")
        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.return_value = [model]
        mock_replicate.Client.return_value = mock_client

        with patch.dict('sys.modules', {'replicate': mock_replicate}):
            result = await discovery.refresh(api_token="test")  # noqa: S106

        # Model was updated (not pinned in LATEST mode)
        assert result.updated == 1
        assert result.skipped == 0


class TestDiscoverAndRegister:
    """Tests for discover_and_register with version pinning."""

    @pytest.mark.asyncio
    async def test_discover_and_register_honors_pinning(self) -> None:
        """Convenience function respects pinning configuration."""
        model_mock = MagicMock()
        model_mock.owner = "meta"
        model_mock.name = "llama"
        model_mock.tags = []
        model_mock.description = "Test"

        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.return_value = [model_mock]
        mock_replicate.Client.return_value = mock_client

        with patch.dict('sys.modules', {'replicate': mock_replicate}):
            config = DiscoveryConfig(
                version_pinning=VersionPinningMode.EXACT,
                pinned_versions={"meta/llama": "abc123"},
            )
            registry, result = await discover_and_register(
                api_token="test",  # noqa: S106
                config=config,
            )

        assert result.discovered == 1
        assert result.registered == 1

        meta = registry.get("meta__llama")
        assert meta is not None
        assert meta.model == "meta/llama:abc123"


class TestDiscoveryResultWithPinning:
    """Tests for DiscoveryResult tracking with version pinning."""

    def test_tracks_skipped_pinned_models(self) -> None:
        """DiscoveryResult tracks skipped pinned models."""
        result = DiscoveryResult()
        result.discovered = 1
        result.skipped = 1  # Pinned model skipped
        result.updated = 0

        assert result.total_registered == 0

