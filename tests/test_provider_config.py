"""Integration tests for the multi-provider configuration layer.

All tests in this module carry ``@pytest.mark.integration`` because they
exercise the configuration pipeline that drives every ZGraph run. They do
NOT make any real network calls — they only assert that ``Settings.from_env``
and ``build_chat_model`` produce the expected in-process objects.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from zgraph.config import ProviderConfig, Settings
from zgraph.core.provider import (
    PROVIDER_PRESETS,
    build_chat_model,
    list_supported_providers,
    resolve_provider,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. Legacy backward compatibility — no ZGRAPH_PROVIDERS, only old env vars.
# ---------------------------------------------------------------------------


class TestLegacySingleProvider:
    """When ZGRAPH_PROVIDERS is not set, fall back to the old fields."""

    def test_synthesizes_default_provider_from_legacy_env(self, set_env):
        set_env(
            BASE_URL="https://legacy.example.com/v1",
            APIKEY="sk-legacy",
            LLM_MODEL_NAME="legacy-model",
        )
        s = Settings.from_env()

        assert list(s.providers.keys()) == ["default"]
        cfg = s.providers["default"]
        assert cfg.name == "default"
        assert cfg.api_key == "sk-legacy"
        assert cfg.base_url == "https://legacy.example.com/v1"
        assert cfg.model == "legacy-model"
        assert s.default_provider == "default"

    def test_top_level_fields_mirror_default_provider(self, set_env):
        set_env(
            BASE_URL="https://legacy.example.com/v1",
            APIKEY="sk-legacy",
            LLM_MODEL_NAME="legacy-model",
        )
        s = Settings.from_env()

        # runtime.py / planner.py still read these flat fields; they must
        # remain consistent with the default provider.
        assert s.base_url == "https://legacy.example.com/v1"
        assert s.api_key == "sk-legacy"
        assert s.model_name == "legacy-model"

    def test_api_key_alias_API_KEY_also_works(self, set_env):
        set_env(BASE_URL="https://x", API_KEY="sk-from-alias", LLM_MODEL_NAME="m")
        s = Settings.from_env()
        assert s.providers["default"].api_key == "sk-from-alias"

    def test_no_env_at_all_returns_empty_legacy_provider(self):
        s = Settings.from_env()
        # providers dict is non-empty (the synthetic "default"), but everything
        # inside is empty. This is the documented legacy fallback shape.
        assert list(s.providers.keys()) == ["default"]
        cfg = s.providers["default"]
        assert cfg.api_key == ""
        assert cfg.base_url == ""
        assert cfg.model == "gpt-4o-mini"  # legacy default


# ---------------------------------------------------------------------------
# 2. Multi-provider mode — ZGRAPH_PROVIDERS enables each preset.
# ---------------------------------------------------------------------------


class TestMultiProviderParsing:
    """ZGRAPH_PROVIDERS=deepseek,kimi,minimax should produce three configs."""

    def test_all_three_presets_get_default_base_url_and_model(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi,minimax",
            DEEPSEEK_API_KEY="sk-d",
            KIMI_API_KEY="sk-k",
            MINIMAX_API_KEY="sk-m",
        )
        s = Settings.from_env()

        assert set(s.providers.keys()) == {"deepseek", "kimi", "minimax"}
        # presets are applied when no per-provider override is given
        assert s.providers["deepseek"].base_url == "https://api.deepseek.com/v1"
        assert s.providers["deepseek"].model == "deepseek-chat"
        assert s.providers["kimi"].base_url == "https://api.moonshot.cn/v1"
        assert s.providers["kimi"].model == "moonshot-v1-8k"
        assert s.providers["minimax"].base_url == "https://api.minimax.chat/v1"
        assert s.providers["minimax"].model == "MiniMax-Text-01"

    def test_per_provider_model_override(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="kimi",
            KIMI_API_KEY="sk-k",
            KIMI_MODEL="moonshot-v1-128k",
        )
        s = Settings.from_env()
        assert s.providers["kimi"].model == "moonshot-v1-128k"
        # base_url still falls back to preset
        assert s.providers["kimi"].base_url == "https://api.moonshot.cn/v1"

    def test_per_provider_base_url_override(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="minimax",
            MINIMAX_API_KEY="sk-m",
            MINIMAX_BASE_URL="https://internal.minimax.example.com/v1",
        )
        s = Settings.from_env()
        assert s.providers["minimax"].base_url == "https://internal.minimax.example.com/v1"
        # model still falls back to preset
        assert s.providers["minimax"].model == "MiniMax-Text-01"

    def test_provider_without_api_key_is_still_configured(self, set_env):
        # Missing key is allowed at parse time; build_chat_model enforces it.
        set_env(ZGRAPH_PROVIDERS="deepseek")
        s = Settings.from_env()
        assert s.providers["deepseek"].api_key == ""
        assert s.providers["deepseek"].is_configured is False

    def test_names_are_lowercased_and_whitespace_trimmed(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS=" DeepSeek , KIMI , MINIMAX ",
            DEEPSEEK_API_KEY="x",
            KIMI_API_KEY="y",
            MINIMAX_API_KEY="z",
        )
        s = Settings.from_env()
        assert set(s.providers.keys()) == {"deepseek", "kimi", "minimax"}

    def test_unknown_provider_in_ZGRAPH_PROVIDERS_yields_empty_preset(self, set_env):
        # An unknown provider is accepted at parse time but with empty
        # base_url/model. resolve_provider will still find it but
        # build_chat_model will fail to actually call anything.
        set_env(ZGRAPH_PROVIDERS="unknown-llm", UNKNOWN_LLM_API_KEY="sk")
        s = Settings.from_env()
        cfg = s.providers["unknown-llm"]
        assert cfg.base_url == ""
        assert cfg.model == ""


# ---------------------------------------------------------------------------
# 3. default_provider resolution
# ---------------------------------------------------------------------------


class TestDefaultProviderResolution:
    """ZGRAPH_DEFAULT_PROVIDER overrides; otherwise first sorted key wins."""

    def test_explicit_default(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi,minimax",
            ZGRAPH_DEFAULT_PROVIDER="kimi",
            DEEPSEEK_API_KEY="d",
            KIMI_API_KEY="k",
            MINIMAX_API_KEY="m",
        )
        s = Settings.from_env()
        assert s.default_provider == "kimi"

    def test_explicit_default_case_insensitive(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi",
            ZGRAPH_DEFAULT_PROVIDER="KIMI",
            DEEPSEEK_API_KEY="d",
            KIMI_API_KEY="k",
        )
        s = Settings.from_env()
        assert s.default_provider == "kimi"

    def test_default_falls_back_to_first_sorted(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="minimax,deepseek,kimi",
            DEEPSEEK_API_KEY="d",
            KIMI_API_KEY="k",
            MINIMAX_API_KEY="m",
        )
        s = Settings.from_env()
        # sorted order is: deepseek, kimi, minimax — first is "deepseek"
        assert s.default_provider == "deepseek"

    def test_explicit_default_outside_providers_is_ignored(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek",
            ZGRAPH_DEFAULT_PROVIDER="kimi",  # not enabled
            DEEPSEEK_API_KEY="d",
        )
        s = Settings.from_env()
        # Falls back to first sorted (which is "deepseek" since only one).
        assert s.default_provider == "deepseek"

    def test_top_level_fields_follow_default(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi",
            ZGRAPH_DEFAULT_PROVIDER="kimi",
            DEEPSEEK_API_KEY="sk-d",
            DEEPSEEK_MODEL="deepseek-chat",
            KIMI_API_KEY="sk-k",
            KIMI_MODEL="moonshot-v1-128k",
        )
        s = Settings.from_env()
        assert s.api_key == "sk-k"
        assert s.model_name == "moonshot-v1-128k"
        assert s.base_url == "https://api.moonshot.cn/v1"


# ---------------------------------------------------------------------------
# 4. resolve_provider
# ---------------------------------------------------------------------------


class TestResolveProvider:
    """The lookup function used by build_chat_model and downstream code."""

    def test_resolve_by_name(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi",
            DEEPSEEK_API_KEY="d",
            KIMI_API_KEY="k",
        )
        s = Settings.from_env()
        assert resolve_provider(s, "kimi").model == "moonshot-v1-8k"

    def test_resolve_with_none_uses_default(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi",
            ZGRAPH_DEFAULT_PROVIDER="kimi",
            DEEPSEEK_API_KEY="d",
            KIMI_API_KEY="k",
        )
        s = Settings.from_env()
        assert resolve_provider(s).name == "kimi"

    def test_resolve_unknown_provider_raises(self, set_env):
        set_env(ZGRAPH_PROVIDERS="deepseek", DEEPSEEK_API_KEY="d")
        s = Settings.from_env()
        with pytest.raises(ValueError, match="not configured"):
            resolve_provider(s, "openai")
        # Error message lists what's actually available.
        with pytest.raises(ValueError, match="deepseek"):
            resolve_provider(s, "openai")

    def test_resolve_with_empty_default_raises(self):
        s = Settings.from_env()
        # In legacy fallback mode default is "default"; with NO env at all the
        # synthetic default is empty-keyed. Cover the edge case explicitly.
        s_no_default = Settings(
            base_url="",
            api_key="",
            model_name="",
            provider="",
            timeout=120,
            temperature=None,
            top_p=None,
            max_tokens=None,
            reasoning_effort="",
            structured_output=False,
            max_rounds=50,
            stream=True,
            system_prompt="",
            host="127.0.0.1",
            port=8001,
            zgraph_home=s.zgraph_home,
            data_dir=s.data_dir,
            layer_config=s.layer_config,
            skills_dir=s.skills_dir,
            offline=False,
            skill_search=True,
            skill_top_k=4,
            skill_min_score=0.18,
            skill_context_char_limit=1200,
            tokenizer_strategy="word",
            rerank_model_name="",
            rerank_base_url="",
            rerank_api_key="",
            rerank_timeout=30,
            rerank_document_char_limit=1024,
            rerank_batch_size=4,
            tool_top_k=4,
            tool_min_score=0.18,
            memory_summary_max_tokens=256,
            memory_summary_temperature=0.2,
            log_level="INFO",
            log_enabled=True,
            whitelist=set(),
            run_ttl_seconds=86400,
            allow_bash=False,
            auto_approve_interrupts=False,
            providers={},
            default_provider="",
            legacy_provider=None,
            tmp_store_path=Path("./storage"),
            media_ttl_seconds=3600,
        )
        with pytest.raises(ValueError, match="No default provider"):
            resolve_provider(s_no_default)


# ---------------------------------------------------------------------------
# 5. build_chat_model
# ---------------------------------------------------------------------------


class TestBuildChatModel:
    """Constructs ChatOpenAI without making any network call."""

    def test_builds_with_default_provider(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="kimi",
            KIMI_API_KEY="sk-k",
        )
        s = Settings.from_env()
        m = build_chat_model(s)
        assert m.model_name == "moonshot-v1-8k"
        assert "moonshot.cn" in (m.openai_api_base or "")

    def test_builds_with_explicit_provider_name(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek,kimi",
            ZGRAPH_DEFAULT_PROVIDER="deepseek",
            DEEPSEEK_API_KEY="sk-d",
            KIMI_API_KEY="sk-k",
        )
        s = Settings.from_env()
        m = build_chat_model(s, "kimi")
        assert m.model_name == "moonshot-v1-8k"

    def test_missing_api_key_raises_with_actionable_message(self, set_env):
        set_env(ZGRAPH_PROVIDERS="deepseek")  # no DEEPSEEK_API_KEY
        s = Settings.from_env()
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            build_chat_model(s, "deepseek")

    def test_temperature_and_top_p_are_propagated(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek",
            DEEPSEEK_API_KEY="sk-d",
            LLM_TEMPERATURE="0.3",
            LLM_TOP_P="0.9",
        )
        s = Settings.from_env()
        m = build_chat_model(s)
        assert m.temperature == 0.3
        assert m.top_p == 0.9

    def test_max_tokens_and_reasoning_effort_are_propagated(self, set_env):
        set_env(
            ZGRAPH_PROVIDERS="deepseek",
            DEEPSEEK_API_KEY="sk-d",
            LLM_MAX_TOKENS="1024",
            LLM_REASONING_EFFORT="medium",
        )
        s = Settings.from_env()
        m = build_chat_model(s)
        assert m.max_tokens == 1024
        assert m.reasoning_effort == "medium"


# ---------------------------------------------------------------------------
# 6. Public introspection
# ---------------------------------------------------------------------------


class TestSupportedProviders:
    """``list_supported_providers`` is the public source of truth."""

    def test_returns_three_canonical_names(self):
        names = list_supported_providers()
        assert names == ["deepseek", "kimi", "minimax"]

    def test_presets_dict_is_internally_consistent(self):
        # Each preset's name field must match its key.
        for key, preset in PROVIDER_PRESETS.items():
            assert preset.name == key
            assert preset.base_url.startswith("https://")
            assert preset.default_model  # non-empty
