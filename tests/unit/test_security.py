"""Tests for replicate_mcp.security — secret management and masking."""

from __future__ import annotations

import os

import pytest

from replicate_mcp.security import (
    InsecureConfigError,
    SecretMasker,
    SecretManager,
    SecretNotFoundError,
    assert_no_eval_in_config,
    sanitize_otel_attributes,
)


# ---------------------------------------------------------------------------
# SecretMasker
# ---------------------------------------------------------------------------


class TestSecretMasker:
    def test_mask_value_long(self) -> None:
        masker = SecretMasker()
        result = masker.mask_value("r" + "A" * 38)
        assert "..." in result
        assert len(result) < 20

    def test_mask_value_short(self) -> None:
        masker = SecretMasker()
        assert masker.mask_value("abc") == "***"

    def test_mask_string_replicate_token(self) -> None:
        masker = SecretMasker()
        token = "r" + "A" * 38
        result = masker.mask_string(f"token is {token}")
        assert "<redacted>" in result
        assert token not in result

    def test_mask_string_no_sensitive(self) -> None:
        masker = SecretMasker()
        assert masker.mask_string("hello world") == "hello world"

    def test_sanitize_dict_key_match(self) -> None:
        masker = SecretMasker()
        data = {"api_token": "r" + "A" * 38, "name": "alice"}
        result = masker.sanitize(data)
        assert result["name"] == "alice"
        assert "A" * 38 not in result["api_token"]

    def test_sanitize_nested_dict(self) -> None:
        masker = SecretMasker()
        data = {"outer": {"password": "supersecret123456"}}
        result = masker.sanitize(data)
        assert result["outer"]["password"] != "supersecret123456"

    def test_sanitize_list(self) -> None:
        masker = SecretMasker()
        data = [{"token": "short"}]
        result = masker.sanitize(data)
        assert isinstance(result, list)

    def test_sanitize_non_string_scalar(self) -> None:
        masker = SecretMasker()
        assert masker.sanitize(42) == 42
        assert masker.sanitize(True) is True

    def test_sanitize_dict_sensitive_non_str_value(self) -> None:
        masker = SecretMasker()
        data = {"token": 12345}
        result = masker.sanitize(data)
        assert result["token"] == "***"

    def test_extra_patterns(self) -> None:
        import re
        masker = SecretMasker(extra_patterns=[re.compile(r"MY_SECRET_\w+")])
        result = masker.mask_string("value=MY_SECRET_abc123")
        assert "MY_SECRET_" not in result

    def test_extra_keys(self) -> None:
        masker = SecretMasker(extra_keys={"my_custom_secret"})
        data = {"my_custom_secret": "should-be-masked"}
        result = masker.sanitize(data)
        assert result["my_custom_secret"] != "should-be-masked"


# ---------------------------------------------------------------------------
# SecretManager
# ---------------------------------------------------------------------------


class TestSecretManager:
    def test_get_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "X" * 38)
        mgr = SecretManager()
        token = mgr.get_token()
        assert token == "r" + "X" * 38

    def test_get_token_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        mgr = SecretManager()
        with pytest.raises(SecretNotFoundError):
            mgr.get_token()

    def test_get_token_not_required_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        mgr = SecretManager()
        assert mgr.get_token(required=False) == ""

    def test_masked_token_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        mgr = SecretManager()
        masked = mgr.masked_token()
        assert "..." in masked
        assert len(masked) < 20

    def test_masked_token_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        mgr = SecretManager()
        assert mgr.masked_token() == "<not set>"

    def test_validate_replicate_token_valid(self) -> None:
        mgr = SecretManager()
        assert mgr.validate_replicate_token("r" + "A" * 38) is True

    def test_validate_replicate_token_invalid_short(self) -> None:
        mgr = SecretManager()
        assert mgr.validate_replicate_token("r12345") is False

    def test_validate_replicate_token_wrong_prefix(self) -> None:
        mgr = SecretManager()
        assert mgr.validate_replicate_token("s" + "A" * 38) is False


# ---------------------------------------------------------------------------
# sanitize_otel_attributes
# ---------------------------------------------------------------------------


class TestSanitizeOtelAttributes:
    def test_masks_token_key(self) -> None:
        attrs = {"agent.id": "llama3", "api_token": "r" + "B" * 38}
        result = sanitize_otel_attributes(attrs)
        assert result["agent.id"] == "llama3"
        assert "B" * 38 not in result["api_token"]

    def test_passthrough_safe_attributes(self) -> None:
        attrs = {"model": "meta/llama", "latency_ms": "3200"}
        result = sanitize_otel_attributes(attrs)
        assert result["model"] == "meta/llama"


# ---------------------------------------------------------------------------
# assert_no_eval_in_config
# ---------------------------------------------------------------------------


class TestAssertNoEvalInConfig:
    def test_clean_config_passes(self) -> None:
        config = {"transform": "extract_prompt", "threshold": 0.7}
        assert_no_eval_in_config(config)  # should not raise

    def test_eval_detected(self) -> None:
        config = {"transform": "eval('bad code')"}
        with pytest.raises(InsecureConfigError, match="eval"):
            assert_no_eval_in_config(config)

    def test_exec_detected(self) -> None:
        config = {"init": "exec('import os')"}
        with pytest.raises(InsecureConfigError):
            assert_no_eval_in_config(config)

    def test_nested_eval_detected(self) -> None:
        config = {"outer": {"inner": "eval(x)"}}
        with pytest.raises(InsecureConfigError):
            assert_no_eval_in_config(config)

    def test_list_value_eval_detected(self) -> None:
        config = {"steps": ["eval(malicious)"]}
        with pytest.raises(InsecureConfigError):
            assert_no_eval_in_config(config)

    def test_import_detected(self) -> None:
        config = {"code": "__import__('os').system('rm -rf /')"}
        with pytest.raises(InsecureConfigError):
            assert_no_eval_in_config(config)