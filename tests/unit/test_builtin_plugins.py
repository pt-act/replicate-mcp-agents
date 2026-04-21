"""Tests for built-in guardrail plugins — PIIMaskPlugin, ContentFilterPlugin, CostCapPlugin."""

from __future__ import annotations

from typing import Any

import pytest

from replicate_mcp.plugins.base import BasePlugin
from replicate_mcp.plugins.builtin import (
    ContentFilterPlugin,
    CostCapPlugin,
    PIIMaskPlugin,
)

# ---------------------------------------------------------------------------
# PIIMaskPlugin
# ---------------------------------------------------------------------------


class TestPIIMaskPluginMetadata:
    def test_metadata_name(self) -> None:
        p = PIIMaskPlugin()
        assert p.metadata.name == "pii_mask"

    def test_metadata_version(self) -> None:
        p = PIIMaskPlugin()
        assert p.metadata.version == "1.0.0"

    def test_metadata_description_mentions_pii(self) -> None:
        p = PIIMaskPlugin()
        assert "PII" in p.metadata.description

    def test_name_property(self) -> None:
        p = PIIMaskPlugin()
        assert p.name == "pii_mask"

    def test_repr(self) -> None:
        p = PIIMaskPlugin()
        assert "pii_mask" in repr(p)


class TestPIIMaskPluginLifecycle:
    def test_setup_is_noop(self) -> None:
        p = PIIMaskPlugin()
        p.setup()  # should not raise

    def test_teardown_is_noop(self) -> None:
        p = PIIMaskPlugin()
        p.teardown()  # should not raise

    def test_is_base_plugin_subclass(self) -> None:
        assert issubclass(PIIMaskPlugin, BasePlugin)


class TestPIIMaskPluginMaskText:
    """Test the _mask_text static method against each PII pattern."""

    def test_ssn_masked(self) -> None:
        assert "[SSN]" in PIIMaskPlugin._mask_text("My SSN is 123-45-6789")

    def test_ssn_with_dots(self) -> None:
        assert "[SSN]" in PIIMaskPlugin._mask_text("123.45.6789")

    def test_email_masked(self) -> None:
        assert "[EMAIL]" in PIIMaskPlugin._mask_text("Contact user@example.com now")

    def test_phone_masked(self) -> None:
        assert "[PHONE]" in PIIMaskPlugin._mask_text("Call 555-123-4567")

    def test_credit_card_masked(self) -> None:
        assert "[CREDIT_CARD]" in PIIMaskPlugin._mask_text("Card 4111111111111111")

    def test_no_pii_unchanged(self) -> None:
        text = "Hello world, no secrets here"
        assert PIIMaskPlugin._mask_text(text) == text

    def test_multiple_pii_types_in_one_string(self) -> None:
        result = PIIMaskPlugin._mask_text(
            "SSN 123-45-6789 email user@example.com phone 555-123-4567"
        )
        assert "[SSN]" in result
        assert "[EMAIL]" in result
        assert "[PHONE]" in result

    def test_mask_preserves_surrounding_text(self) -> None:
        result = PIIMaskPlugin._mask_text("My SSN is 123-45-6789 end")
        assert result.startswith("My SSN is ")
        assert result.endswith(" end")


class TestPIIMaskPluginMaskPayload:
    """Test the _mask_payload classmethod."""

    def test_string_value_with_pii_masked(self) -> None:
        payload = {"prompt": "My SSN is 123-45-6789"}
        result = PIIMaskPlugin._mask_payload(payload)
        assert "[SSN]" in result["prompt"]

    def test_non_string_value_passes_through(self) -> None:
        payload = {"count": 42, "flag": True}
        result = PIIMaskPlugin._mask_payload(payload)
        assert result["count"] == 42
        assert result["flag"] is True

    def test_no_pii_returns_same_object(self) -> None:
        """When nothing changes, the original dict is returned (identity check)."""
        payload = {"prompt": "clean text"}
        result = PIIMaskPlugin._mask_payload(payload)
        assert result is payload  # same object, not a copy

    def test_mixed_keys_some_masked(self) -> None:
        payload = {"prompt": "SSN 123-45-6789", "safe": "hello", "num": 5}
        result = PIIMaskPlugin._mask_payload(payload)
        assert "[SSN]" in result["prompt"]
        assert result["safe"] == "hello"
        assert result["num"] == 5

    def test_empty_payload(self) -> None:
        payload: dict[str, Any] = {}
        result = PIIMaskPlugin._mask_payload(payload)
        assert result is payload


class TestPIIMaskPluginOnAgentRun:
    def test_returns_masked_payload_when_pii_present(self) -> None:
        p = PIIMaskPlugin()
        result = p.on_agent_run("agent", {"prompt": "SSN 123-45-6789"})
        assert result is not None
        assert "[SSN]" in result["prompt"]

    def test_returns_none_when_no_pii(self) -> None:
        p = PIIMaskPlugin()
        result = p.on_agent_run("agent", {"prompt": "clean prompt"})
        assert result is None

    def test_preserves_non_string_values(self) -> None:
        p = PIIMaskPlugin()
        result = p.on_agent_run("agent", {"prompt": "SSN 123-45-6789", "temp": 0.7})
        assert result is not None
        assert result["temp"] == 0.7


class TestPIIMaskPluginOnAgentResult:
    def test_returns_masked_chunks_when_pii_present(self) -> None:
        p = PIIMaskPlugin()
        chunks = [{"text": "Email user@example.com"}]
        result = p.on_agent_result("agent", chunks, 100.0)
        assert result is not None
        assert "[EMAIL]" in result[0]["text"]

    def test_returns_none_when_no_pii(self) -> None:
        p = PIIMaskPlugin()
        chunks = [{"text": "safe output"}]
        result = p.on_agent_result("agent", chunks, 100.0)
        assert result is None

    def test_mixed_chunks_some_masked(self) -> None:
        p = PIIMaskPlugin()
        chunks = [
            {"text": "safe"},
            {"text": "SSN 123-45-6789"},
        ]
        result = p.on_agent_result("agent", chunks, 100.0)
        assert result is not None
        assert result[0]["text"] == "safe"
        assert "[SSN]" in result[1]["text"]

    def test_empty_chunks(self) -> None:
        p = PIIMaskPlugin()
        result = p.on_agent_result("agent", [], 100.0)
        # No chunks → no masking → changed=False → returns None
        assert result is None


# ---------------------------------------------------------------------------
# ContentFilterPlugin
# ---------------------------------------------------------------------------


class TestContentFilterPluginMetadata:
    def test_metadata_name(self) -> None:
        p = ContentFilterPlugin()
        assert p.metadata.name == "content_filter"

    def test_metadata_version(self) -> None:
        p = ContentFilterPlugin()
        assert p.metadata.version == "1.0.0"

    def test_metadata_description(self) -> None:
        p = ContentFilterPlugin()
        assert "deny" in p.metadata.description.lower()

    def test_name_property(self) -> None:
        p = ContentFilterPlugin()
        assert p.name == "content_filter"

    def test_repr(self) -> None:
        p = ContentFilterPlugin()
        assert "content_filter" in repr(p)


class TestContentFilterPluginLifecycle:
    def test_setup_is_noop(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        p.setup()

    def test_teardown_is_noop(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        p.teardown()

    def test_is_base_plugin_subclass(self) -> None:
        assert issubclass(ContentFilterPlugin, BasePlugin)


class TestContentFilterPluginDenyList:
    def test_default_deny_list_is_empty(self) -> None:
        p = ContentFilterPlugin()
        assert p._deny_list == []

    def test_custom_deny_list(self) -> None:
        p = ContentFilterPlugin(deny_list=["forbidden", "blocked"])
        assert p._deny_list == ["forbidden", "blocked"]


class TestContentFilterPluginMatches:
    def test_matches_denied_term(self) -> None:
        p = ContentFilterPlugin(deny_list=["badword"])
        assert p._matches({"prompt": "this has badword in it"})

    def test_case_insensitive_match(self) -> None:
        p = ContentFilterPlugin(deny_list=["badword"])
        assert p._matches({"prompt": "BADWORD here"})

    def test_no_match_returns_false(self) -> None:
        p = ContentFilterPlugin(deny_list=["badword"])
        assert not p._matches({"prompt": "clean content"})

    def test_empty_deny_list_never_matches(self) -> None:
        p = ContentFilterPlugin()
        assert not p._matches({"prompt": "anything goes"})

    def test_match_on_any_value(self) -> None:
        """_matches joins all values, so a deny term in a non-prompt key matches."""
        p = ContentFilterPlugin(deny_list=["secret"])
        assert p._matches({"system": "top secret info"})

    def test_multiple_denied_terms(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad", "evil"])
        assert p._matches({"prompt": "this is evil"})
        assert p._matches({"prompt": "this is bad"})
        assert not p._matches({"prompt": "this is fine"})


class TestContentFilterPluginOnAgentRun:
    def test_blocked_payload_replaced(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        result = p.on_agent_run("agent", {"prompt": "do bad things", "model": "x"})
        assert result is not None
        assert result["prompt"] == "[CONTENT BLOCKED]"
        assert result["model"] == "x"  # other keys preserved

    def test_clean_payload_returns_none(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        result = p.on_agent_run("agent", {"prompt": "all good"})
        assert result is None

    def test_empty_deny_list_never_blocks(self) -> None:
        p = ContentFilterPlugin()
        result = p.on_agent_run("agent", {"prompt": "anything"})
        assert result is None


class TestContentFilterPluginOnAgentResult:
    def test_blocked_chunks_replaced(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        chunks = [{"text": "bad output"}]
        result = p.on_agent_result("agent", chunks, 50.0)
        assert result is not None
        assert result[0]["text"] == "[CONTENT BLOCKED]"

    def test_clean_chunks_returns_none(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        chunks = [{"text": "good output"}]
        result = p.on_agent_result("agent", chunks, 50.0)
        assert result is None

    def test_mixed_chunks_all_replaced_if_any_match(self) -> None:
        """If ANY chunk matches, ALL chunks get [CONTENT BLOCKED]."""
        p = ContentFilterPlugin(deny_list=["bad"])
        chunks = [
            {"text": "good"},
            {"text": "bad stuff"},
        ]
        result = p.on_agent_result("agent", chunks, 50.0)
        assert result is not None
        # All chunks get blocked (not just the matching one)
        assert result[0]["text"] == "[CONTENT BLOCKED]"
        assert result[1]["text"] == "[CONTENT BLOCKED]"

    def test_preserves_non_text_keys_in_blocked_chunks(self) -> None:
        p = ContentFilterPlugin(deny_list=["bad"])
        chunks = [{"text": "bad", "cost_usd": 0.05}]
        result = p.on_agent_result("agent", chunks, 50.0)
        assert result is not None
        assert result[0]["text"] == "[CONTENT BLOCKED]"
        assert result[0]["cost_usd"] == 0.05


# ---------------------------------------------------------------------------
# CostCapPlugin
# ---------------------------------------------------------------------------


class TestCostCapPluginMetadata:
    def test_metadata_name(self) -> None:
        p = CostCapPlugin()
        assert p.metadata.name == "cost_cap"

    def test_metadata_version(self) -> None:
        p = CostCapPlugin()
        assert p.metadata.version == "1.0.0"

    def test_metadata_description(self) -> None:
        p = CostCapPlugin()
        assert "cost" in p.metadata.description.lower()

    def test_name_property(self) -> None:
        p = CostCapPlugin()
        assert p.name == "cost_cap"

    def test_repr(self) -> None:
        p = CostCapPlugin()
        assert "cost_cap" in repr(p)


class TestCostCapPluginLifecycle:
    def test_setup_resets_session_spend(self) -> None:
        p = CostCapPlugin()
        p._session_spend = 5.0
        p.setup()
        assert p.session_spend == 0.0

    def test_teardown_resets_session_spend(self) -> None:
        p = CostCapPlugin()
        p._session_spend = 5.0
        p.teardown()
        assert p.session_spend == 0.0

    def test_is_base_plugin_subclass(self) -> None:
        assert issubclass(CostCapPlugin, BasePlugin)


class TestCostCapPluginDefaults:
    def test_default_per_invocation_cap(self) -> None:
        p = CostCapPlugin()
        assert p.per_invocation_cap == 1.0

    def test_default_session_cap(self) -> None:
        p = CostCapPlugin()
        assert p.session_cap == 10.0

    def test_default_session_spend_is_zero(self) -> None:
        p = CostCapPlugin()
        assert p.session_spend == 0.0


class TestCostCapPluginCustomCaps:
    def test_custom_per_invocation_cap(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.5)
        assert p.per_invocation_cap == 0.5

    def test_custom_session_cap(self) -> None:
        p = CostCapPlugin(session_cap=5.0)
        assert p.session_cap == 5.0

    def test_both_caps_custom(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.25, session_cap=2.0)
        assert p.per_invocation_cap == 0.25
        assert p.session_cap == 2.0


class TestCostCapPluginOnAgentRun:
    def test_per_invocation_cap_exceeded(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.5, session_cap=100.0)
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.75, "prompt": "test"})
        assert result is not None
        assert "[COST CAP EXCEEDED — INVOCATION BLOCKED]" in result["prompt"]

    def test_session_cap_exceeded(self) -> None:
        p = CostCapPlugin(per_invocation_cap=100.0, session_cap=1.0)
        p._session_spend = 0.8
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.5, "prompt": "test"})
        assert result is not None
        assert "[COST CAP EXCEEDED — SESSION LIMIT]" in result["prompt"]

    def test_under_both_caps_returns_none(self) -> None:
        p = CostCapPlugin(per_invocation_cap=1.0, session_cap=10.0)
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.5, "prompt": "test"})
        assert result is None

    def test_no_estimated_cost_returns_none(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.01)
        result = p.on_agent_run("agent", {"prompt": "test"})
        assert result is None

    def test_zero_estimated_cost_returns_none(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.01)
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.0, "prompt": "test"})
        assert result is None

    def test_exactly_at_cap_returns_none(self) -> None:
        p = CostCapPlugin(per_invocation_cap=1.0, session_cap=10.0)
        result = p.on_agent_run("agent", {"estimated_cost_usd": 1.0, "prompt": "test"})
        assert result is None  # exactly at cap, not over

    def test_per_invocation_blocked_preserves_other_keys(self) -> None:
        p = CostCapPlugin(per_invocation_cap=0.01)
        result = p.on_agent_run("agent", {"estimated_cost_usd": 5.0, "model": "big"})
        assert result is not None
        assert result["model"] == "big"


class TestCostCapPluginOnAgentResult:
    def test_accumulates_cost_from_chunks(self) -> None:
        p = CostCapPlugin()
        chunks = [{"cost_usd": 0.5}, {"cost_usd": 0.3}]
        p.on_agent_result("agent", chunks, 100.0)
        assert p.session_spend == pytest.approx(0.8)

    def test_no_cost_key_in_chunks(self) -> None:
        p = CostCapPlugin()
        chunks = [{"text": "output"}]
        p.on_agent_result("agent", chunks, 100.0)
        assert p.session_spend == 0.0

    def test_partial_cost_key_in_chunks(self) -> None:
        p = CostCapPlugin()
        chunks = [{"cost_usd": 0.3}, {"text": "no cost here"}]
        p.on_agent_result("agent", chunks, 100.0)
        assert p.session_spend == pytest.approx(0.3)

    def test_zero_cost_accumulated(self) -> None:
        p = CostCapPlugin()
        chunks = [{"cost_usd": 0.0}]
        p.on_agent_result("agent", chunks, 100.0)
        assert p.session_spend == 0.0

    def test_returns_none(self) -> None:
        """on_agent_result never transforms chunks, just accumulates cost."""
        p = CostCapPlugin()
        chunks = [{"cost_usd": 1.0}]
        result = p.on_agent_result("agent", chunks, 100.0)
        assert result is None

    def test_empty_chunks(self) -> None:
        p = CostCapPlugin()
        p.on_agent_result("agent", [], 100.0)
        assert p.session_spend == 0.0


class TestCostCapPluginSessionSpendIntegration:
    def test_sequential_invocations_accumulate(self) -> None:
        p = CostCapPlugin(per_invocation_cap=10.0, session_cap=1.0)
        # First invocation: spend accumulates
        p.on_agent_result("agent", [{"cost_usd": 0.6}], 100.0)
        assert p.session_spend == pytest.approx(0.6)
        # Second invocation: spend grows
        p.on_agent_result("agent", [{"cost_usd": 0.3}], 100.0)
        assert p.session_spend == pytest.approx(0.9)
        # Now next run would exceed session cap
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.2, "prompt": "hi"})
        assert result is not None
        assert "SESSION LIMIT" in result["prompt"]

    def test_setup_resets_spend_mid_session(self) -> None:
        p = CostCapPlugin(session_cap=1.0)
        p.on_agent_result("agent", [{"cost_usd": 0.9}], 100.0)
        assert p.session_spend == pytest.approx(0.9)
        # Reset via setup
        p.setup()
        assert p.session_spend == 0.0
        # Now run is allowed again
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.5, "prompt": "hi"})
        assert result is None

    def test_per_invocation_then_session_cap(self) -> None:
        """Per-invocation cap is checked first, then session cap."""
        p = CostCapPlugin(per_invocation_cap=0.5, session_cap=100.0)
        # This exceeds per-invocation cap
        result = p.on_agent_run("agent", {"estimated_cost_usd": 1.0, "prompt": "hi"})
        assert result is not None
        assert "INVOCATION BLOCKED" in result["prompt"]
        # This is under per-invocation but session is not near limit
        result = p.on_agent_run("agent", {"estimated_cost_usd": 0.3, "prompt": "hi"})
        assert result is None
