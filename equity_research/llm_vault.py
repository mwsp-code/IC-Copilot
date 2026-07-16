from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from socket import timeout as SocketTimeout
from urllib.error import HTTPError, URLError

from . import config
from .local_secrets import LLM_PROFILE_SECRET_PREFIX, LocalSecretsManager
from .models import LlmProviderPreset, LlmProviderProfile, ProviderStatus
from .research_store import ResearchStore
from .thesis_synthesis import AnthropicProvider, LlmProvider, OpenAICompatibleProvider


PRESETS: dict[str, LlmProviderPreset] = {
    "deepseek": LlmProviderPreset(
        "deepseek",
        "DeepSeek",
        "openai_compatible",
        "deepseek-v4-pro",
        "https://api.deepseek.com",
        notes="OpenAI-compatible Pro-class default for evidence-grounded IC synthesis and research-assistant reads.",
    ),
    "openai": LlmProviderPreset(
        "openai",
        "OpenAI",
        "openai_compatible",
        "gpt-4.1-mini",
        "https://api.openai.com/v1",
    ),
    "anthropic": LlmProviderPreset(
        "anthropic",
        "Anthropic",
        "anthropic",
        "claude-3-5-sonnet-latest",
        "https://api.anthropic.com/v1",
    ),
    "qwen": LlmProviderPreset(
        "qwen",
        "Qwen",
        "openai_compatible",
        "qwen-plus",
        "",
        requires_base_url=True,
        notes="Enter the OpenAI-compatible Model Studio base URL for your region/account.",
    ),
    "kimi": LlmProviderPreset(
        "kimi",
        "Kimi",
        "openai_compatible",
        "kimi-k2-0711-preview",
        "",
        requires_base_url=True,
        notes="Enter the OpenAI-compatible Kimi/Moonshot base URL for your account.",
    ),
    "ollama": LlmProviderPreset(
        "ollama",
        "Local Ollama",
        "openai_compatible",
        "llama3.1",
        "http://localhost:11434/v1",
        notes="Local OpenAI-compatible endpoint; API key is optional.",
    ),
    "custom_openai_compatible": LlmProviderPreset(
        "custom_openai_compatible",
        "Custom OpenAI-compatible",
        "openai_compatible",
        "",
        "",
        requires_base_url=True,
    ),
}


def list_llm_presets() -> list[LlmProviderPreset]:
    return list(PRESETS.values())


def get_llm_preset(preset_id: str | None) -> LlmProviderPreset:
    return PRESETS.get((preset_id or "").strip().lower(), PRESETS["custom_openai_compatible"])


def llm_profile_secret_ref(profile_id: str) -> str:
    return f"{LLM_PROFILE_SECRET_PREFIX}{profile_id}"


def build_llm_profile(
    *,
    display_name: str,
    provider_preset: str,
    model: str = "",
    base_url: str = "",
    role_eligibility: str = "primary_secondary",
    profile_id: str | None = None,
    key_configured: bool = False,
    secret_ref: str = "",
) -> LlmProviderProfile:
    preset = get_llm_preset(provider_preset)
    clean_id = profile_id or _profile_id(display_name, preset.preset_id)
    clean_model = (model or preset.default_model).strip()
    clean_base_url = (base_url or preset.default_base_url).strip().rstrip("/")
    return LlmProviderProfile(
        profile_id=clean_id,
        display_name=(display_name or preset.label).strip() or preset.label,
        provider_preset=preset.preset_id,
        model=clean_model,
        base_url=clean_base_url,
        role_eligibility=role_eligibility or "primary_secondary",
        key_configured=key_configured,
        secret_ref=secret_ref or llm_profile_secret_ref(clean_id),
    )


def save_llm_profile_with_secret(
    store: ResearchStore,
    manager: LocalSecretsManager,
    *,
    display_name: str,
    provider_preset: str,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    role_eligibility: str = "primary_secondary",
    profile_id: str | None = None,
) -> LlmProviderProfile:
    existing = store.get_llm_profile(profile_id) if profile_id else None
    profile = build_llm_profile(
        display_name=display_name,
        provider_preset=provider_preset,
        model=model,
        base_url=base_url,
        role_eligibility=role_eligibility,
        profile_id=profile_id,
        key_configured=bool(api_key.strip()) or bool(existing and existing.key_configured),
        secret_ref=existing.secret_ref if existing else "",
    )
    _validate_profile_metadata(profile)
    if api_key.strip():
        manager.set(profile.secret_ref, api_key.strip())
        profile.key_configured = True
    elif _profile_allows_missing_key(profile):
        profile.key_configured = bool(manager.get(profile.secret_ref))
    elif manager.get(profile.secret_ref):
        profile.key_configured = True
    return store.save_llm_profile(profile)


def delete_llm_profile_with_secret(store: ResearchStore, manager: LocalSecretsManager, profile_id: str) -> bool:
    profile = store.get_llm_profile(profile_id)
    if profile:
        manager.delete(profile.secret_ref)
    return store.delete_llm_profile(profile_id)


def profile_to_provider(
    profile: LlmProviderProfile | None,
    manager: LocalSecretsManager | None = None,
    *,
    enabled: bool = True,
    api_key: str | None = None,
    fetch_json=None,
) -> LlmProvider | None:
    if not enabled or not profile:
        return None
    preset = get_llm_preset(profile.provider_preset)
    key = api_key if api_key is not None else ""
    if not key and manager is not None:
        key = manager.get(profile.secret_ref) or ""
    if not key:
        key = _env_key_for_preset(preset.preset_id)
    if not key and not _profile_allows_missing_key(profile):
        return None
    if preset.adapter == "anthropic":
        return AnthropicProvider(
            api_key=key,
            model=profile.model or preset.default_model,
            base_url=profile.base_url or preset.default_base_url,
            fetch_json=fetch_json,
        )
    return OpenAICompatibleProvider(
        api_key=key,
        model=profile.model or preset.default_model,
        base_url=profile.base_url or preset.default_base_url,
        provider_name=preset.preset_id,
        fetch_json=fetch_json,
    )


def test_llm_profile(
    profile: LlmProviderProfile,
    *,
    manager: LocalSecretsManager | None = None,
    api_key: str | None = None,
    fetch_json=None,
) -> ProviderStatus:
    observed_at = _utc_now()
    if not profile.base_url and get_llm_preset(profile.provider_preset).requires_base_url:
        return ProviderStatus(profile.display_name, "missing_base_url", False, "missing_base_url", observed_at, "Base URL is required for this provider preset.")
    key = api_key if api_key is not None else ""
    if not key and manager is not None:
        key = manager.get(profile.secret_ref) or ""
    if not key:
        key = _env_key_for_preset(profile.provider_preset)
    if not key and not _profile_allows_missing_key(profile):
        return ProviderStatus(profile.display_name, "not_configured", False, "missing_key", observed_at, "API key is not configured.")
    provider = profile_to_provider(profile, manager, enabled=True, api_key=key, fetch_json=fetch_json)
    if provider is None:
        return ProviderStatus(profile.display_name, "not_configured", False, "missing_key", observed_at, "Provider could not be constructed.")
    try:
        payload = provider.complete_json({
            "prompt_version": "llm-healthcheck-v1",
            "task": "Return JSON only with verdict='ok' and evidence_chain=[].",
            "citations": {},
            "evidence": {},
            "rules": ["Do not include secrets."],
        })
        if not isinstance(payload, dict):
            return ProviderStatus(profile.display_name, "malformed_response", False, "malformed_response", observed_at, "Provider returned a non-object JSON payload.")
        return ProviderStatus(profile.display_name, "valid", False, "available", observed_at, "LLM provider returned JSON successfully.")
    except TimeoutError:
        return ProviderStatus(profile.display_name, "timeout", False, "timeout", observed_at, "Provider validation timed out.")
    except HTTPError as exc:
        status = "invalid_key" if exc.code in {401, 403} else "network_error"
        return ProviderStatus(profile.display_name, status, False, "http_error", observed_at, f"Provider returned HTTP {exc.code}.")
    except (URLError, OSError, SocketTimeout) as exc:
        return ProviderStatus(profile.display_name, "network_error", False, "network_error", observed_at, _redact(str(exc), key))
    except Exception as exc:
        message = _redact(str(exc), key)
        status = "invalid_key" if "401" in message or "403" in message or "unauthorized" in message.lower() else "malformed_response"
        return ProviderStatus(profile.display_name, status, False, status, observed_at, message)


def selected_llm_providers_from_store(
    store: ResearchStore,
    manager: LocalSecretsManager,
    *,
    enable_llm: bool,
) -> tuple[LlmProvider | None, LlmProvider | None, dict]:
    selection = store.get_llm_selection()
    primary = store.get_llm_profile(selection.get("primary_profile_id"))
    secondary = store.get_llm_profile(selection.get("secondary_profile_id"))
    return (
        profile_to_provider(primary, manager, enabled=enable_llm),
        profile_to_provider(secondary, manager, enabled=enable_llm and bool(selection.get("enable_secondary", True))),
        selection,
    )


def _validate_profile_metadata(profile: LlmProviderProfile) -> None:
    preset = get_llm_preset(profile.provider_preset)
    if preset.requires_base_url and not profile.base_url:
        raise ValueError(f"{preset.label} requires an OpenAI-compatible base URL.")
    if not profile.model:
        raise ValueError("LLM model is required.")


def _profile_allows_missing_key(profile: LlmProviderProfile) -> bool:
    return profile.provider_preset in {"ollama", "local"} or profile.base_url.startswith("http://localhost")


def _env_key_for_preset(preset_id: str) -> str:
    if preset_id == "anthropic":
        return config.ANTHROPIC_API_KEY
    if preset_id == "qwen":
        return config.QWEN_API_KEY
    if preset_id == "kimi":
        return config.KIMI_API_KEY
    if preset_id == "deepseek":
        return config.DEEPSEEK_API_KEY
    if preset_id == "openai":
        return config.OPENAI_API_KEY
    return config.OPENAI_API_KEY


def _profile_id(display_name: str, preset_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (display_name or preset_id).strip().lower()).strip("-")
    return f"{slug or preset_id}-{uuid.uuid4().hex[:8]}"


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
