from __future__ import annotations

import html
import json
import os
from pathlib import Path

import streamlit as st

from equity_research import config
from equity_research.analysis import format_number
from equity_research.budget import available_budget_modes, budget_allows_paid_data
from equity_research.consensus_import import import_consensus_csv, write_consensus_csv_templates
from equity_research.contributor_tools import build_contribution_pack, save_contribution_pack
from equity_research.external_evidence import (
    default_macro_source_settings,
    external_evidence_stack_from_config,
)
from equity_research.idea_engine import build_payoff_model, expected_value
from equity_research.local_secrets import (
    LocalSecretsManager,
    save_validated_keys,
    validate_provider_keys,
)
from equity_research.llm_vault import (
    build_llm_profile,
    delete_llm_profile_with_secret,
    get_llm_preset,
    list_llm_presets,
    profile_to_provider,
    save_llm_profile_with_secret,
    test_llm_profile,
)
from equity_research.memory import IdeaMemoryStore
from equity_research.network_diagnostics import run_network_diagnostics
from equity_research.pipeline import ResearchResult, run_us_equity_research
from equity_research.market_implied import build_market_implied_expectations
from equity_research.providers import StooqPriceClient, build_consensus_provider
from equity_research.research_store import ResearchStore
from equity_research.research_profiles import event_identifier, research_profiles
from equity_research.rigor import build_calibration_report
from equity_research.sample_data import demo_result
from equity_research.sec_client import SecClient, SecClientError
from equity_research.storytelling import demo_cases
from equity_research.thesis_synthesis import provider_from_config
from equity_research.thesis_synthesis import UnavailableLlmProvider


st.set_page_config(
    page_title="US Equity Research Radar",
    page_icon="",
    layout="wide",
)


def main() -> None:
    st.title("US Equity Research Radar")
    if "result" not in st.session_state:
        st.session_state.result = None
    if "network_diagnostics" not in st.session_state:
        st.session_state.network_diagnostics = None
    pending_demo_preset = st.session_state.pop("_pending_demo_preset", None)
    if pending_demo_preset:
        st.session_state.research_profile_selector = pending_demo_preset.get(
            "research_profile", "deep_initiation"
        )
        st.session_state.budget_mode_selector = pending_demo_preset.get("budget_mode", "Premium")

    with st.sidebar:
        st.header("Research Run")
        secrets_manager = LocalSecretsManager()
        local_secret_status = secrets_manager.redacted_status(getattr(config, "_SYSTEM_ENV_KEYS", set()))
        configured_secret_count = sum(
            1 for item in local_secret_status
            if item["configured"] and item["key"] != "SEC_USER_AGENT"
        )
        local_secret_backend = local_secret_status[0]["backend"] if local_secret_status else "unavailable"
        if configured_secret_count:
            st.caption(f"{configured_secret_count} saved provider key(s) configured via {local_secret_backend}.")
        elif not secrets_manager.backend_available:
            st.warning("OS keychain is unavailable. Install keyring support or use .env.local manually.")
        cases = demo_cases()
        demo_labels = [f"{case.ticker} - {case.title}" for case in cases]
        st.subheader("Load Demo Gallery")
        selected_demo = st.selectbox("Demo case", demo_labels, index=0)
        selected_case = cases[demo_labels.index(selected_demo)]
        freshness = (
            f" Version: {selected_case.content_version}; refreshed {selected_case.refreshed_at}."
            if selected_case.content_version or selected_case.refreshed_at else ""
        )
        st.caption(f"{selected_case.lesson} {selected_case.badge}; {selected_case.expected_runtime}.{freshness}")
        if selected_case.research_profile or selected_case.budget_mode:
            st.caption(
                f"Demo preset: {selected_case.research_profile or 'Current research profile'} | "
                f"{selected_case.budget_mode or 'Current budget mode'}."
            )
        if selected_case.enabled_layers:
            st.caption("Included layers: " + ", ".join(selected_case.enabled_layers) + ".")
        load_demo_clicked = st.button("Load Selected Demo", use_container_width=True)
        ticker = st.text_input("Ticker", value=selected_case.ticker or "AAPL").upper().strip()
        profile_options = research_profiles()
        profile_ids = [item.profile_id for item in profile_options if not item.event_scoped]
        profile_by_id = {item.profile_id: item for item in profile_options}
        default_profile = config.RESEARCH_PROFILE if config.RESEARCH_PROFILE in profile_ids else "adaptive_ic"
        selected_profile_id = st.radio(
            "Research depth",
            profile_ids,
            index=profile_ids.index(default_profile),
            format_func=lambda value: profile_by_id[value].label,
            help="Fast screens one anomaly; Adaptive is the default analyst workflow; Deep builds a fuller initiation pack.",
            key="research_profile_selector",
        )
        selected_profile = profile_by_id[selected_profile_id]
        st.caption(
            f"{selected_profile.quarter_depth} quarters, {selected_profile.annual_depth} annual reports, "
            f"{selected_profile.call_depth} calls."
        )
        investigate_clicked = False
        investigate_event_id = None
        prior_result = st.session_state.get("result")
        if prior_result and getattr(prior_result, "events", None):
            event_options = prior_result.events[:20]
            event_labels = {
                event_identifier(item): f"{item.event_date or 'Date unknown'} | {item.title}"
                for item in event_options
            }
            investigate_event_id = st.selectbox(
                "Investigate an event",
                list(event_labels),
                format_func=lambda value: event_labels[value],
            )
            investigate_clicked = st.button("Investigate This Event", use_container_width=True)
        budget_modes = available_budget_modes()
        budget_mode = st.selectbox(
            "Budget mode",
            list(budget_modes),
            index=list(budget_modes).index(config.BUDGET_MODE) if config.BUDGET_MODE in budget_modes else 1,
            help=(
                "Free uses official/keyless and manual sources. Lean adds low-cost LLM synthesis. "
                "Stable/Premium allow paid provider slots when keys are configured."
            ),
            key="budget_mode_selector",
        )
        sec_user_agent = st.text_input(
            "SEC user agent",
            value=os.getenv("SEC_USER_AGENT", config.DEFAULT_SEC_USER_AGENT),
        )
        st.subheader("Consensus Connection")
        configured_alpha_key = config.ALPHAVANTAGE_API_KEY or _streamlit_secret("ALPHAVANTAGE_API_KEY")
        configured_finnhub_key = config.FINNHUB_API_KEY or _streamlit_secret("FINNHUB_API_KEY")
        configured_fmp_key = config.FMP_API_KEY or _streamlit_secret("FMP_API_KEY")
        configured_fred_key = config.FRED_API_KEY or _streamlit_secret("FRED_API_KEY")
        configured_bls_key = config.BLS_API_KEY or _streamlit_secret("BLS_API_KEY")
        configured_bea_key = config.BEA_API_KEY or _streamlit_secret("BEA_API_KEY")
        configured_census_key = config.CENSUS_API_KEY or _streamlit_secret("CENSUS_API_KEY")
        configured_wisburg_key = config.WISBURG_API_KEY or _streamlit_secret("WISBURG_API_KEY")
        configured_tiingo_key = config.TIINGO_API_KEY or _streamlit_secret("TIINGO_API_KEY")
        configured_eodhd_key = config.EODHD_API_KEY or _streamlit_secret("EODHD_API_KEY")
        llm_store = ResearchStore()
        alpha_session_key = st.text_input(
            "Alpha Vantage API key",
            type="password",
            key="alpha_session_key",
            help="Used only in this Streamlit session. It is never written to SQLite or logs.",
            placeholder="Already configured" if configured_alpha_key else "Free key",
        ).strip()
        finnhub_session_key = st.text_input(
            "Finnhub API key",
            type="password",
            key="finnhub_session_key",
            help="Used only in this Streamlit session. It is never written to SQLite or logs.",
            placeholder="Already configured" if configured_finnhub_key else "Free key",
        ).strip()
        with st.expander("Saved key status"):
            st.dataframe(
                [
                    {
                        "Provider": item["label"],
                        "Configured": "Yes" if item["configured"] else "No",
                        "Source": item["source"],
                    }
                    for item in local_secret_status
                ],
                hide_index=True,
                use_container_width=True,
            )
        with st.expander("Additional sources"):
            fmp_session_key = st.text_input(
                "FMP API key",
                type="password",
                key="fmp_session_key",
                help="Optional legacy provider. Used only in this Streamlit session.",
                placeholder="Already configured" if configured_fmp_key else "Optional",
            ).strip()
            tiingo_session_key = st.text_input(
                "Tiingo API key",
                type="password",
                key="tiingo_session_key",
                help="Session-only unless saved. Used for adjusted EOD prices, event windows, beta, and peer-return attribution.",
                placeholder="Already configured" if configured_tiingo_key else "Optional market data key",
            ).strip()
            eodhd_session_key = st.text_input(
                "EODHD API key",
                type="password",
                key="eodhd_session_key",
                help=(
                    "Session-only unless saved. The free plan supplies recent adjusted EOD prices for "
                    "event windows, peer reactions, market context, and reverse-implied expectations."
                ),
                placeholder="Already configured" if configured_eodhd_key else "Optional EOD price key",
            ).strip()
            enable_nasdaq = st.toggle(
                "Nasdaq estimates (unofficial)",
                value=config.ENABLE_NASDAQ_CONSENSUS,
            )
            enable_tradingview = st.toggle(
                "TradingView targets (unofficial)",
                value=config.ENABLE_TRADINGVIEW_CONSENSUS,
            )
            st.markdown("**Macro / external evidence**")
            fred_session_key = st.text_input(
                "FRED API key",
                type="password",
                key="fred_session_key",
                help="Session-only. Enables FRED/ALFRED macro context when the toggle is on.",
                placeholder="Already configured" if configured_fred_key else "Free key",
            ).strip()
            bls_session_key = st.text_input(
                "BLS API key",
                type="password",
                key="bls_session_key",
                help="Optional session-only BLS key. BLS may allow limited no-key calls.",
                placeholder="Already configured" if configured_bls_key else "Optional",
            ).strip()
            bea_session_key = st.text_input(
                "BEA API key",
                type="password",
                key="bea_session_key",
                help="Session-only. BEA macro calls require a BEA key.",
                placeholder="Already configured" if configured_bea_key else "Free key",
            ).strip()
            census_session_key = st.text_input(
                "Census API key",
                type="password",
                key="census_session_key",
                help="Session-only. Census macro calls require a Census key.",
                placeholder="Already configured" if configured_census_key else "Free key",
            ).strip()
            global_macro_mode = st.toggle("Global macro mode", value=config.GLOBAL_MACRO_MODE)
            enable_default_macro = st.toggle("Default official macro sources", value=config.ENABLE_DEFAULT_MACRO)
            effective_fred_key_preview = fred_session_key or configured_fred_key
            effective_bea_key_preview = bea_session_key or configured_bea_key
            effective_census_key_preview = census_session_key or configured_census_key
            macro_defaults = default_macro_source_settings(
                ticker,
                fred_api_key=effective_fred_key_preview,
                bea_api_key=effective_bea_key_preview,
                census_api_key=effective_census_key_preview,
                enable_default_macro=enable_default_macro,
                global_macro_mode=global_macro_mode,
            )
            enable_fred = st.toggle("FRED / ALFRED macro", value=macro_defaults["fred"])
            enable_bls = st.toggle("BLS macro", value=macro_defaults["bls"])
            enable_bea = st.toggle("BEA macro", value=macro_defaults["bea"])
            enable_census = st.toggle("Census macro", value=macro_defaults["census"])
            enable_treasury = st.toggle("Treasury / Fiscal Data macro", value=macro_defaults["treasury"])
            enable_ofr = st.toggle("OFR financial stress", value=macro_defaults["ofr"])
            enable_world_bank = st.toggle("World Bank macro", value=macro_defaults["world_bank"])
            enable_imf = st.toggle("IMF macro", value=macro_defaults["imf"])
            enable_gdelt = st.toggle("GDELT narrative saturation", value=config.ENABLE_GDELT)
            refresh_macro_cache = st.toggle("Refresh macro cache", value=False)
            wisburg_session_key = st.text_input(
                "Wisburg API key",
                type="password",
                key="wisburg_session_key",
                help="Session-only unless saved to the OS keychain. Used for external analyst/narrative context only.",
                placeholder="Already configured" if configured_wisburg_key else "Optional",
            ).strip()
            enable_wisburg = st.toggle("Wisburg research", value=config.ENABLE_WISBURG)
            st.markdown("**IC Copilot / LLM synthesis**")
            enable_llm = st.toggle(
                "LLM thesis synthesis",
                value=config.ENABLE_LLM_THESIS or budget_mode in {"Lean", "Stable", "Premium"},
                help="Optional. Sends only curated excerpts and structured claims to the selected provider.",
            )
            presets = {preset.preset_id: preset for preset in list_llm_presets()}
            profiles = llm_store.list_llm_profiles()
            selection = llm_store.get_llm_selection()
            profile_labels = {"": "None"}
            profile_labels.update({
                profile.profile_id: (
                    f"{profile.display_name} ({profile.provider_preset}, {profile.model})"
                    + (" - key saved" if profile.key_configured else " - no key")
                )
                for profile in profiles
            })
            profile_ids = list(profile_labels)
            primary_default = selection.get("primary_profile_id") or config.LLM_PRIMARY_PROFILE_ID
            secondary_default = selection.get("secondary_profile_id") or config.LLM_SECONDARY_PROFILE_ID
            if not primary_default:
                first_configured = next((profile.profile_id for profile in profiles if profile.key_configured), "")
                primary_default = first_configured or (profiles[0].profile_id if profiles else "")
            primary_profile_id = st.selectbox(
                "Primary LLM",
                profile_ids,
                index=profile_ids.index(primary_default) if primary_default in profile_ids else 0,
                format_func=lambda value: profile_labels[value],
            )
            secondary_profile_id = st.selectbox(
                "Secondary reader",
                profile_ids,
                index=profile_ids.index(secondary_default) if secondary_default in profile_ids else 0,
                format_func=lambda value: profile_labels[value],
            )
            enable_secondary_review = st.toggle(
                "Secondary review for Research-Ready+",
                value=bool(selection.get("enable_secondary", config.ENABLE_SECONDARY_LLM_REVIEW)),
            )
            secondary_min_stage = st.selectbox(
                "Secondary minimum stage",
                ["Research-Ready", "High-Conviction"],
                index=0 if selection.get("secondary_min_stage", config.SECONDARY_LLM_MIN_STAGE) != "High-Conviction" else 1,
            )
            language_policy = st.selectbox(
                "Language policy",
                ["bilingual_audit", "english_only"],
                index=0 if selection.get("language_policy", config.LLM_LANGUAGE_POLICY) != "english_only" else 1,
            )
            if st.button("Save LLM Selection", use_container_width=True):
                llm_store.save_llm_selection(
                    primary_profile_id,
                    secondary_profile_id,
                    enable_secondary_review,
                    secondary_min_stage,
                    language_policy,
                )
                st.success("Saved LLM primary/secondary selection.")
            with st.expander("Provider Vault"):
                saved_rows = [
                    {
                        "Name": profile.display_name,
                        "Preset": profile.provider_preset,
                        "Model": profile.model,
                        "Base URL": profile.base_url or "n/a",
                        "Key": "Configured" if profile.key_configured else "Missing",
                        "Last test": profile.last_test_status,
                    }
                    for profile in profiles
                ]
                if saved_rows:
                    st.dataframe(saved_rows, hide_index=True, use_container_width=True)
                preset_id = st.selectbox(
                    "Provider preset",
                    list(presets),
                    index=list(presets).index("deepseek") if "deepseek" in presets else 0,
                    format_func=lambda value: presets[value].label,
                )
                preset = get_llm_preset(preset_id)
                profile_name = st.text_input("Profile name", value=f"{preset.label} primary").strip()
                profile_model = st.text_input("Model", value=preset.default_model).strip()
                profile_base_url = st.text_input(
                    "Base URL",
                    value=preset.default_base_url,
                    placeholder="Required for custom, Qwen, and Kimi OpenAI-compatible endpoints",
                ).strip()
                profile_api_key = st.text_input(
                    "API key",
                    type="password",
                    key="llm_profile_api_key",
                    placeholder="Saved to OS keychain only",
                ).strip()
                profile_action_cols = st.columns(2)
                if profile_action_cols[0].button("Save Provider Profile", use_container_width=True):
                    try:
                        saved_profile = save_llm_profile_with_secret(
                            llm_store,
                            secrets_manager,
                            display_name=profile_name,
                            provider_preset=preset_id,
                            model=profile_model,
                            base_url=profile_base_url,
                            api_key=profile_api_key,
                        )
                        st.success(f"Saved {saved_profile.display_name}.")
                    except Exception as exc:
                        st.error(f"Could not save LLM profile: {exc}")
                if profile_action_cols[1].button("Test Unsaved Profile", use_container_width=True):
                    try:
                        candidate = build_llm_profile(
                            display_name=profile_name,
                            provider_preset=preset_id,
                            model=profile_model,
                            base_url=profile_base_url,
                            key_configured=bool(profile_api_key),
                        )
                        status = test_llm_profile(candidate, manager=secrets_manager, api_key=profile_api_key)
                        st.info(f"{status.status}: {status.message}")
                    except Exception as exc:
                        st.error(f"Could not test LLM profile: {exc}")
                if profiles:
                    delete_id = st.selectbox(
                        "Delete saved profile",
                        [profile.profile_id for profile in profiles],
                        format_func=lambda value: profile_labels.get(value, value),
                    )
                    if st.button("Delete Selected LLM Profile", use_container_width=True):
                        delete_llm_profile_with_secret(llm_store, secrets_manager, delete_id)
                        st.warning("Deleted selected LLM profile and its keychain secret.")
            entered_keys = {
                "ALPHAVANTAGE_API_KEY": alpha_session_key,
                "FINNHUB_API_KEY": finnhub_session_key,
                "FMP_API_KEY": fmp_session_key,
                "FRED_API_KEY": fred_session_key,
                "BEA_API_KEY": bea_session_key,
                "CENSUS_API_KEY": census_session_key,
                "WISBURG_API_KEY": wisburg_session_key,
                "TIINGO_API_KEY": tiingo_session_key,
                "EODHD_API_KEY": eodhd_session_key,
                "SEC_USER_AGENT": sec_user_agent,
            }
            key_cols = st.columns(3)
            if key_cols[0].button("Test Keys", use_container_width=True):
                with st.spinner("Testing provider keys..."):
                    st.session_state.key_validation_results = validate_provider_keys(entered_keys)
            if key_cols[1].button("Save Valid Keys", use_container_width=True):
                with st.spinner("Testing and saving valid keys..."):
                    results = validate_provider_keys(entered_keys)
                    st.session_state.key_validation_results = results
                    try:
                        outcome = save_validated_keys(secrets_manager, entered_keys, results)
                        config.refresh_runtime_secrets()
                        st.success(
                            f"Saved {len(outcome['saved'])} key(s). "
                            "Restart the app if a running workflow already captured old settings."
                        )
                    except Exception as exc:
                        st.error(f"Could not save keys to OS keychain: {exc}")
            if key_cols[2].button("Clear Saved Keys", use_container_width=True):
                secrets_manager.delete_many()
                config.refresh_runtime_secrets()
                st.warning("Cleared saved local keys from the OS keychain.")
            if st.session_state.get("key_validation_results"):
                st.dataframe(
                    [
                        {"Provider": item.label, "Status": item.status, "Message": item.message}
                        for item in st.session_state.key_validation_results
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
        effective_alpha_key = alpha_session_key or configured_alpha_key
        effective_finnhub_key = finnhub_session_key or configured_finnhub_key
        effective_fmp_key = (fmp_session_key or configured_fmp_key) if budget_allows_paid_data(budget_mode) else ""
        effective_fred_key = fred_session_key or configured_fred_key
        effective_bls_key = bls_session_key or configured_bls_key
        effective_bea_key = bea_session_key or configured_bea_key
        effective_census_key = census_session_key or configured_census_key
        effective_wisburg_key = wisburg_session_key or configured_wisburg_key
        effective_tiingo_key = tiingo_session_key or configured_tiingo_key
        effective_eodhd_key = eodhd_session_key or configured_eodhd_key
        primary_profile = llm_store.get_llm_profile(primary_profile_id)
        secondary_profile = llm_store.get_llm_profile(secondary_profile_id)
        llm_provider = profile_to_provider(primary_profile, secrets_manager, enabled=enable_llm)
        if enable_llm and primary_profile_id and llm_provider is None:
            llm_provider = UnavailableLlmProvider(
                primary_profile.provider_preset if primary_profile else "selected_profile",
                primary_profile.model if primary_profile else "unknown",
                "Selected primary LLM profile is unavailable. Check that its API key and base URL are saved.",
            )
        elif llm_provider is None:
            llm_provider = provider_from_config(enabled=enable_llm)
        secondary_llm_provider = profile_to_provider(
            secondary_profile,
            secrets_manager,
            enabled=enable_llm and enable_secondary_review,
        )
        external_provider = external_evidence_stack_from_config(
            fred_api_key=effective_fred_key,
            enable_fred=enable_fred,
            bls_api_key=effective_bls_key,
            enable_bls=enable_bls,
            bea_api_key=effective_bea_key,
            enable_bea=enable_bea,
            census_api_key=effective_census_key,
            enable_census=enable_census,
            enable_treasury=enable_treasury,
            enable_ofr=enable_ofr,
            enable_world_bank=enable_world_bank,
            enable_imf=enable_imf,
            enable_gdelt=enable_gdelt,
            wisburg_api_key=effective_wisburg_key,
            enable_wisburg=enable_wisburg,
            enable_default_macro=enable_default_macro,
            global_macro_mode=global_macro_mode,
            refresh_cache=refresh_macro_cache,
        )
        configured_sources = sum(bool(value) for value in (
            effective_alpha_key, effective_finnhub_key, effective_fmp_key,
            enable_nasdaq, enable_tradingview,
        ))
        configured_market_sources = sum(bool(value) for value in (
            effective_tiingo_key,
            effective_eodhd_key,
        ))
        configured_external_sources = sum(bool(value) for value in (
            enable_fred, enable_bls, enable_bea, enable_census, enable_treasury,
            enable_ofr, enable_world_bank, enable_imf, enable_gdelt, enable_wisburg,
        ))
        if configured_sources:
            st.success(f"{configured_sources} consensus source(s) enabled.")
        else:
            st.warning("No consensus source is enabled. SEC research will still run.")
        if (fmp_session_key or configured_fmp_key) and not budget_allows_paid_data(budget_mode):
            st.info(
                "FMP is saved/configured but disabled in this budget mode. "
                "Choose Stable or Premium to use FMP targets, estimates, surprises, and point-in-time snapshots."
            )
        if configured_market_sources:
            st.success(f"{configured_market_sources} paid market data source(s) enabled for price attribution.")
        run_network_check = st.button("Run Network Diagnostics", use_container_width=True)
        test_connection = st.button("Test Consensus Sources", use_container_width=True)
        run_clicked = st.button("Run Live Research", type="primary", use_container_width=True)
        st.caption("US equities MVP. HK support can plug into the same pipeline later.")

    if load_demo_clicked:
        st.session_state.result = demo_result(selected_case.ticker)
        st.session_state._pending_demo_preset = {
            "research_profile": "deep_initiation" if selected_case.budget_mode == "Premium" else default_profile,
            "budget_mode": selected_case.budget_mode or budget_mode,
        }
        st.rerun()

    if run_network_check:
        with st.spinner("Running network diagnostics from the Streamlit process..."):
            st.session_state.network_diagnostics = run_network_diagnostics(timeout_seconds=8)

    if st.session_state.network_diagnostics:
        report = st.session_state.network_diagnostics
        with st.sidebar.expander("Network Diagnostics", expanded=True):
            st.caption(f"Class: {report.network_class}")
            st.caption(report.summary)
            if report.runtime_context:
                st.caption(f"Python: {report.runtime_context.get('python_executable', 'Unknown')}")
                st.caption(f"PID: {report.runtime_context.get('pid', 'Unknown')}")
            for action in report.suggested_actions[:4]:
                st.write(f"- {action}")
            rows = [
                {
                    "Provider": probe.provider,
                    "Check": probe.check_type,
                    "Host": probe.endpoint_host,
                    "Status": probe.status,
                    "Class": probe.failure_class,
                    "Fix": probe.suggested_fix,
                }
                for probe in report.probes
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)

    if test_connection:
        if not configured_sources:
            st.sidebar.error("Enter a free provider key or enable an unofficial fallback.")
        elif not ticker:
            st.sidebar.error("Enter a ticker first.")
        else:
            with st.spinner(f"Testing consensus sources for {ticker}..."):
                package = build_consensus_provider(
                    alpha_vantage_key=effective_alpha_key,
                    finnhub_key=effective_finnhub_key,
                    fmp_key=effective_fmp_key,
                    enable_nasdaq=enable_nasdaq,
                    enable_tradingview=enable_tradingview,
                    enable_yahoo=False,
                ).fetch_package(ticker)
            if package.status == "Unavailable":
                st.sidebar.error("No enabled source returned usable consensus data.")
            elif package.status.startswith("Partial"):
                st.sidebar.warning(package.status)
            else:
                st.sidebar.success("Official consensus data is available.")
            for status in package.provider_statuses:
                st.sidebar.caption(f"{status.provider}: {status.status}")
            for gap in package.data_gaps:
                st.sidebar.caption(gap)

    if (run_clicked or investigate_clicked) and ticker:
        effective_profile = "investigate_event" if investigate_clicked else selected_profile_id
        try:
            with st.spinner(f"Running research workflow for {ticker}..."):
                store = ResearchStore()
                if configured_sources:
                    provider = build_consensus_provider(
                        store=store,
                        alpha_vantage_key=effective_alpha_key,
                        finnhub_key=effective_finnhub_key,
                        fmp_key=effective_fmp_key,
                        enable_nasdaq=enable_nasdaq,
                        enable_tradingview=enable_tradingview,
                        enable_yahoo=False,
                    )
                    st.session_state.result = run_us_equity_research(
                        ticker,
                        sec_client=SecClient(user_agent=sec_user_agent),
                        price_client=StooqPriceClient(
                            store=store,
                            tiingo_key=effective_tiingo_key,
                            eodhd_key=effective_eodhd_key,
                        ),
                        consensus=provider,
                        external_evidence_provider=external_provider,
                        llm_provider=llm_provider,
                        secondary_llm_provider=secondary_llm_provider,
                        enable_secondary_llm_review=enable_secondary_review,
                        secondary_llm_min_stage=secondary_min_stage,
                        llm_language_policy=language_policy,
                        budget_mode=budget_mode,
                        store=store,
                        research_profile=effective_profile,
                        investigate_event_id=investigate_event_id if investigate_clicked else None,
                    )
                else:
                    st.session_state.result = run_us_equity_research(
                        ticker,
                        sec_client=SecClient(user_agent=sec_user_agent),
                        price_client=StooqPriceClient(
                            store=store,
                            tiingo_key=effective_tiingo_key,
                            eodhd_key=effective_eodhd_key,
                        ),
                        external_evidence_provider=external_provider,
                        llm_provider=llm_provider,
                        secondary_llm_provider=secondary_llm_provider,
                        enable_secondary_llm_review=enable_secondary_review,
                        secondary_llm_min_stage=secondary_min_stage,
                        llm_language_policy=language_policy,
                        budget_mode=budget_mode,
                        store=store,
                        research_profile=effective_profile,
                        investigate_event_id=investigate_event_id if investigate_clicked else None,
                    )
        except SecClientError as exc:
            st.error(str(exc))
            st.stop()
        except Exception as exc:  # pragma: no cover - UI guardrail
            st.exception(exc)
            st.stop()

    result: ResearchResult | None = st.session_state.result
    if result is None:
        st.info("Enter a US ticker and run the workflow.")
        return

    render_header(result)
    tabs = st.tabs(
        [
            "IC Story",
            "Evidence Trail",
            "Causal Bridge",
            "Market & Expectations",
            "Work Orders",
            "Raw Data",
        ]
    )
    with tabs[0]:
        render_ic_copilot(result)
    with tabs[1]:
        render_research_radar(result)
        render_management_sources(result)
        render_validated_claims_and_source_plan(result)
    with tabs[2]:
        render_research_modes(result)
        render_causal_thesis_graphs(result)
        render_company_model_workspace(result)
        render_idea_factory(result)
        render_idea_scorer(result)
    with tabs[3]:
        render_earnings_surprise_proxy(result)
        render_market_implied_expectations(result)
        render_recent_market_context(result)
        render_price_move_attribution(result)
        render_market_capture_readiness(result)
    with tabs[4]:
        render_evidence_work_order(result)
        render_research_questions(result)
        render_thesis_monitor(result)
    with tabs[5]:
        render_memo(result)


def _streamlit_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def render_header(result: ResearchResult) -> None:
    identity = result.identity
    top_score = result.ideas[0].score.total if result.ideas and result.ideas[0].score else 0
    capture = (
        result.ideas[0].market_capture.category
        if result.ideas and result.ideas[0].market_capture
        else "Unknown"
    )
    _wrapped_metric_grid([
        ("Company", identity.name.title()),
        ("CIK", identity.cik),
        ("Top Idea Score", f"{top_score}/100" if top_score else "n/a"),
        ("Market Capture", capture),
    ])


def render_story_first(result: ResearchResult) -> None:
    demo_case = getattr(result, "demo_case", None)
    if demo_case:
        st.info(
            f"Demo gallery: **{demo_case.title}**. {demo_case.lesson} "
            f"Runtime: {demo_case.expected_runtime}; network required: {'yes' if demo_case.network_required else 'no'}; "
            f"version: {demo_case.content_version or 'Unversioned'}; refreshed: {demo_case.refreshed_at or 'Unknown'}."
        )
    one_pager = getattr(result, "ic_one_pager", None)
    brief = result.thesis_brief
    st.markdown("### IC Story")
    _wrapped_metric_grid([
        ("Verdict", one_pager.verdict if one_pager else brief.verdict),
        ("Stage", one_pager.stage if one_pager else brief.stage),
        ("Direction", one_pager.direction if one_pager else brief.direction),
        ("Decision", one_pager.decision if one_pager and one_pager.decision else "Research next"),
        ("Rank", one_pager.rank_eligibility if one_pager else "n/a"),
    ])
    if one_pager:
        st.write(one_pager.thesis)
        if one_pager.next_best_action:
            st.success(f"Next action: {one_pager.next_best_action}")
    profile = getattr(result, "research_profile", None)
    history = getattr(result, "historical_research", None)
    if profile and history:
        _wrapped_metric_grid([
            ("Research Profile", profile.label),
            ("Quarter History", f"{history.analyzed_quarters}/{history.requested_quarters}"),
            ("Annual History", f"{history.analyzed_annual_reports}/{history.requested_annual_reports}"),
            ("Call History", f"{history.analyzed_calls}/{history.requested_calls}"),
        ])
        if history.adaptive_deepening_reasons:
            st.caption("Adaptive deepening: " + ", ".join(history.adaptive_deepening_reasons) + ".")
        st.caption(
            f"Discovered before parsing: {history.discovered_quarters} quarterly filings, "
            f"{history.discovered_annual_reports} annual reports, and {history.discovered_calls} calls."
        )
    render_pipeline_progress(result)
    render_story_cards(result)
    render_bull_bear_judge(result)
    with st.expander("Formula transparency", expanded=False):
        render_formula_traces(result)
    render_contributor_surface(result)


def _wrapped_metric_grid(items: list[tuple[str, object]]) -> None:
    cards = []
    for label, value in items:
        safe_label = html.escape(str(label))
        safe_value = html.escape(str(value or "Unknown"))
        cards.append(
            '<div style="min-height:92px;border:1px solid rgba(128,128,128,0.32);border-radius:8px;'
            'padding:0.85rem;overflow-wrap:anywhere;word-break:normal;">'
            f'<div style="font-size:0.78rem;color:#9aa0aa;margin-bottom:0.4rem;">{safe_label}</div>'
            f'<div style="font-size:1.12rem;line-height:1.3;font-weight:650;letter-spacing:0;">{safe_value}</div>'
            '</div>'
        )
    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.75rem;'
        'width:100%;margin-bottom:0.75rem;">' + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


def render_pipeline_progress(result: ResearchResult) -> None:
    progress = getattr(result, "run_progress", None)
    if not progress or not progress.stages:
        return
    st.markdown("#### Research Pipeline")
    st.caption(progress.summary)
    status_icon = {
        "Passed": "[OK]",
        "Partial": "[..]",
        "Blocked": "[!]",
        "Skipped": "[-]",
        "Unavailable": "[?]",
    }
    cols = st.columns(len(progress.stages))
    for col, stage in zip(cols, progress.stages):
        col.markdown(f"**{status_icon.get(stage.status, '[?]')} {stage.label}**")
        col.caption(stage.status)
    with st.expander("Pipeline details", expanded=False):
        st.dataframe(
            [
                {
                    "Stage": stage.label,
                    "Status": stage.status,
                    "Summary": stage.summary,
                    "Evidence": "; ".join(stage.evidence),
                    "Blockers": "; ".join(stage.blockers),
                    "Next action": stage.next_action,
                }
                for stage in progress.stages
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_story_cards(result: ResearchResult) -> None:
    cards = getattr(result, "story_cards", [])
    if not cards:
        return
    st.markdown("#### Story Cards")
    for idx in range(0, len(cards), 2):
        cols = st.columns(2)
        for card, col in zip(cards[idx:idx + 2], cols):
            with col.container(border=True):
                st.markdown(f"**{card.title}**")
                st.caption(card.status)
                st.write(card.body or card.summary)
                if card.next_action:
                    st.caption(f"Next: {card.next_action}")
                if card.evidence:
                    with st.expander("Show evidence", expanded=False):
                        render_evidence_drawers(card.evidence)


def render_evidence_drawers(drawers) -> None:
    for drawer in drawers:
        st.markdown(f"**{drawer.label}**")
        st.write(drawer.claim)
        meta = [
            f"Source: {drawer.source or 'Unknown'}",
            f"Tier: {drawer.source_tier if drawer.source_tier is not None else 'Unknown'}",
            f"Section: {drawer.section or 'Unknown'}",
            f"Period: {drawer.period or 'Unknown'}",
            f"Metric: {drawer.metric or 'n/a'}",
            f"Value: {drawer.value or 'n/a'}",
            f"Formula: {drawer.formula or 'n/a'}",
            f"Parser: {drawer.parser_status or 'Unknown'}",
            f"Confidence: {drawer.confidence or 'Unknown'}",
        ]
        st.caption(" | ".join(meta))
        if drawer.url:
            st.caption(drawer.url)
        if drawer.excerpt:
            st.code(drawer.excerpt[:700])


def render_bull_bear_judge(result: ResearchResult) -> None:
    panel = getattr(result, "bull_bear_judge", None)
    if not panel:
        return
    st.markdown("#### Bull / Bear / Judge")
    cols = st.columns(3)
    with cols[0].container(border=True):
        st.markdown("**Bull case**")
        st.write(panel.bull_case)
    with cols[1].container(border=True):
        st.markdown("**Bear case**")
        st.write(panel.bear_case)
    with cols[2].container(border=True):
        st.markdown("**Judge accepts**")
        for item in panel.judge_accepts[:5]:
            st.write(f"- {item}")
        if panel.still_unproven:
            st.markdown("**Still unproven**")
            for item in panel.still_unproven[:5]:
                st.write(f"- {item}")
    if panel.resolution_plan:
        st.markdown("**How the app will try to resolve open items**")
        st.dataframe(
            [
                {
                    "Type": item.issue_type,
                    "Status": item.status,
                    "What it means": item.issue,
                    "Triggering evidence": item.evidence,
                    "App action": item.app_action,
                    "User action": item.user_action,
                    "Blocks": item.blocking_scope,
                    "Automatic": "Yes" if item.auto_resolvable else "No",
                }
                for item in panel.resolution_plan
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_formula_traces(result: ResearchResult) -> None:
    traces = getattr(result, "formula_traces", [])
    if not traces:
        st.info("No formula traces are attached to this run.")
        return
    st.dataframe(
        [
            {
                "Label": trace.label,
                "Value": trace.value,
                "Source field": trace.source_field,
                "Formula": trace.formula,
                "Period": trace.period or "Unknown",
                "Currency": trace.currency or "Unknown",
                "Confidence": trace.confidence,
                "Source": trace.source,
            }
            for trace in traces[:30]
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_contributor_surface(result: ResearchResult) -> None:
    with st.expander("Improve this project", expanded=False):
        pack = build_contribution_pack(result)
        st.write(
            "The app can prefill safe configuration and test specifications from this run. "
            "It saves a reviewable draft and does not silently alter the authoritative source registry."
        )
        st.dataframe(
            [
                {
                    "Contribution": "Sector playbook",
                    "Status": pack["sector_playbook"]["status"],
                    "App support": "Prefill KPIs, indicators, valuation methods, catalysts, and fixture requirement",
                },
                {
                    "Contribution": "ADR profile",
                    "Status": pack["adr_profile"]["status"],
                    "App support": "Prefill ratio, currency, forms, exchange, and issuer sources",
                },
                {
                    "Contribution": "Metric aliases",
                    "Status": f"{len(pack['metric_alias_drafts'])} unresolved metric(s)",
                    "App support": "Draft canonical metric rows; exact source tags still require validation",
                },
                {
                    "Contribution": "Source adapters",
                    "Status": f"{len(pack['source_adapter_specs'])} source gap(s)",
                    "App support": "Generate adapter contracts and fixture requirements; executable code requires review",
                },
                {
                    "Contribution": "Demo case",
                    "Status": "Draft ready",
                    "App support": "Prefill lesson and no-network regression requirement",
                },
            ],
            use_container_width=True,
            hide_index=True,
        )
        serialized = json.dumps(pack, indent=2, ensure_ascii=False)
        left, right = st.columns(2)
        if left.button(
            "Save local contribution draft",
            key=f"save-contribution-{result.identity.ticker}",
            use_container_width=True,
        ):
            path = save_contribution_pack(pack)
            st.success(f"Saved reviewable contribution draft to {path}.")
        right.download_button(
            "Download contribution pack",
            data=serialized,
            file_name=f"{result.identity.ticker}_contribution_pack.json",
            mime="application/json",
            key=f"download-contribution-{result.identity.ticker}",
            use_container_width=True,
        )


def render_ic_copilot(result: ResearchResult) -> None:
    render_story_first(result)
    render_reverse_fcf_snapshot(result)
    st.subheader("IC Audit Trail")
    brief = result.thesis_brief
    critique = result.thesis_critique
    sufficiency = result.evidence_sufficiency
    manifest = result.llm_run_manifest
    _wrapped_metric_grid([
        ("IC Verdict", brief.verdict),
        ("Evidence Sufficiency", sufficiency.status),
        ("Sufficiency Score", f"{sufficiency.score}/100"),
        ("Synthesis Source", brief.source),
    ])
    st.markdown("#### LLM Guardrail Checklist")
    guard_cols = st.columns(5)
    guard_cols[0].metric("LLM Status", manifest.status)
    guard_cols[1].metric("Execution", manifest.llm_execution_status or "n/a")
    guard_cols[2].metric("Guardrails", manifest.llm_guardrail_status or "n/a")
    guard_cols[3].metric("Guardrail Score", f"{manifest.guardrail_score}/100")
    guard_cols[4].metric("Prompt Fingerprint", manifest.prompt_hash[:12] + "..." if manifest.prompt_hash else "n/a")
    st.caption(manifest.message)
    if manifest.failure_class or manifest.provider_health or manifest.timeout_seconds:
        st.caption(
            "Provider health: "
            f"{manifest.provider_health or 'n/a'}; failure class: {manifest.failure_class or 'none'}; "
            f"retryable: {'yes' if manifest.retryable else 'no'}; timeout: {manifest.timeout_seconds or 'n/a'}s."
        )
    if manifest.guardrail_checks:
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Score": item.score,
                    "Summary": item.summary,
                    "Evidence": "; ".join(item.evidence),
                    "Gaps": "; ".join(item.gaps),
                    "Enforcement": item.enforcement,
                }
                for item in manifest.guardrail_checks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if manifest.llm_execution_status == "timeout":
        st.info("LLM provider timed out before output was returned. This is not an evidence-guardrail rejection; deterministic synthesis was used.")
    one_pager = getattr(result, "ic_one_pager", None)
    if one_pager:
        st.markdown("#### IC One-Pager")
        _wrapped_metric_grid([
            ("One-Pager Status", one_pager.status),
            ("Direction", one_pager.direction),
            ("Stage", one_pager.stage),
            ("Source", one_pager.source),
            ("Decision", one_pager.decision or "n/a"),
            ("Rank Eligible", one_pager.rank_eligibility or "n/a"),
            ("Why Now", one_pager.why_now or "n/a"),
            ("Blocking Issue", one_pager.blocking_issue or "n/a"),
        ])
        st.caption(one_pager.decision_reason or one_pager.go_no_go_reason)
        if one_pager.next_best_action:
            st.info(f"Next best action: {one_pager.next_best_action}")
        if one_pager.go_no_go_reason:
            st.caption(f"Go / no-go: {one_pager.go_no_go_reason}")
        st.write(one_pager.thesis)
        st.caption(one_pager.variant_perception)
        summary_cols = st.columns(2)
        with summary_cols[0]:
            st.markdown("**Causal Bridge**")
            st.write(one_pager.causal_bridge)
            st.markdown("**Price / Capture**")
            st.write(one_pager.price_move)
            st.write(one_pager.market_capture)
            st.markdown("**Valuation**")
            st.write(one_pager.valuation)
        with summary_cols[1]:
            st.markdown("**Equity Lens**")
            st.write(one_pager.equity_lens)
            st.markdown("**Credit Lens**")
            st.write(one_pager.credit_lens)
            st.markdown("**Counter-Thesis**")
            st.write(one_pager.counter_thesis)
        if one_pager.work_order_actions:
            st.markdown("**Top Evidence Work Orders**")
            for item in one_pager.work_order_actions[:4]:
                st.write(f"- {item}")
        if one_pager.monitor_actions:
            st.markdown("**Monitor Next**")
            for item in one_pager.monitor_actions[:4]:
                st.write(f"- {item}")
    st.markdown("#### Why This Beats Generic Chat")
    st.write("- Starts from cited filings, prices, consensus snapshots, and source manifests, not model memory.")
    st.write("- Separates facts, gaps, and hypotheses; weak evidence becomes a work order, not a recommendation.")
    st.write("- Builds an auditable IC trail: causal bridge, peer metrics, market capture, valuation, counter-thesis, and monitors.")
    budget = result.budget_policy
    st.markdown("#### Budget Mode and Data Policy")
    cols = st.columns(3)
    cols[0].metric("Mode", budget.mode)
    cols[1].metric("Cost Target", budget.cost_target)
    cols[2].metric("Manual Data", result.manual_data_status.status)
    st.caption(budget.description)
    st.caption(
        f"Config: {budget.config_source}; max monthly budget "
        f"{budget.max_monthly_budget_usd if budget.max_monthly_budget_usd is not None else 'uncapped / user-defined'}; "
        f"paid data {'allowed' if budget.allow_paid_data else 'disabled'}; "
        f"LLM {'allowed' if budget.allow_llm else 'disabled'}."
    )
    with st.expander("Enabled Sources and Upgrade Slots", expanded=False):
        if budget.provider_policy:
            st.markdown("**Provider policy**")
            for group, sources in budget.provider_policy.items():
                st.write(f"- {group.replace('_', ' ').title()}: {', '.join(sources) or 'none'}")
        for source in budget.enabled_sources:
            st.write(f"- {source}")
        if budget.optional_upgrade_slots:
            st.markdown("**Upgrade slots**")
            for source in budget.optional_upgrade_slots:
                st.write(f"- {source}")
        for warning in budget.warnings:
            st.warning(warning)
    render_research_scout(result)
    render_evidence_work_order(result)
    render_global_coverage(result)
    render_market_capture_readiness(result)
    render_company_economics(result)
    render_credit_lens(result)
    render_coverage_expansion(result)
    render_thesis_clusters(result)
    render_research_questions(result)
    render_validated_claims_and_source_plan(result)
    render_wisburg_research_lens(result)
    render_latency_profile(result)
    render_conviction_chain(result)
    audit_report = result.conviction_audit
    st.markdown("#### Conviction Audit")
    cols = st.columns(3)
    cols[0].metric("Audit Status", audit_report.status)
    cols[1].metric("Process Score", f"{audit_report.score}/100")
    cols[2].metric("Checklist Items", len(audit_report.items))
    st.caption(audit_report.summary)
    if audit_report.items:
        st.dataframe(
            [
                {
                    "Check": item.name,
                    "Status": item.status,
                    "Score": item.score,
                    "Evidence": item.evidence,
                    "Gaps": "; ".join(item.gaps),
                    "Source": item.source_type,
                }
                for item in audit_report.items
            ],
            use_container_width=True,
            hide_index=True,
        )
    if audit_report.differentiators:
        with st.expander("Workflow Differentiators", expanded=False):
            for item in audit_report.differentiators:
                st.write(f"- {item}")
    validation = result.thesis_validation
    st.markdown("#### Thesis Validation Matrix")
    cols = st.columns(3)
    cols[0].metric("Validation Status", validation.status)
    cols[1].metric("Validation Score", f"{validation.score}/100")
    cols[2].metric("Channels", len(validation.checks))
    st.caption(validation.summary)
    if validation.checks:
        st.dataframe(
            [
                {
                    "Channel": check.channel,
                    "Status": check.status,
                    "Score": check.score,
                    "Evidence": check.evidence,
                    "Implication": check.implication,
                    "Gaps": "; ".join(check.gaps),
                    "Tier": check.source_tier or "n/a",
                    "Citations": check.citation_count,
                }
                for check in validation.checks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if validation.required_next_evidence:
        with st.expander("Required Next Evidence", expanded=False):
            for item in validation.required_next_evidence:
                st.write(f"- {item}")
    if validation.next_evidence_actions:
        st.markdown("#### Evidence Action Plan")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Channel": item.channel,
                    "Action": item.action,
                    "Source": item.source,
                    "Blocks High Conviction": "Yes" if item.blocks_high_conviction else "No",
                    "Why": item.why_it_matters,
                }
                for item in validation.next_evidence_actions
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("#### Thesis")
    st.write(brief.thesis)
    st.markdown("#### Variant Perception")
    st.write(brief.variant_perception)
    if brief.evidence_chain:
        st.markdown("#### Evidence Chain")
        for item in brief.evidence_chain:
            st.write(f"- {item}")
    st.markdown("#### Strongest Counter-Thesis")
    st.write(critique.strongest_counter_thesis)
    if critique.key_uncertainties:
        st.markdown("#### Key Uncertainties")
        for item in critique.key_uncertainties:
            st.write(f"- {item}")
    if critique.what_would_falsify:
        st.markdown("#### What Would Falsify This")
        for item in critique.what_would_falsify:
            st.write(f"- {item}")
    st.markdown("#### Model Disagreement")
    comparison = result.llm_comparison
    st.caption(
        f"Status: {comparison.status}; agreement: {comparison.agreement}; "
        f"primary: {comparison.primary_provider or 'n/a'}; secondary: {comparison.secondary_provider or 'n/a'}."
    )
    if comparison.key_differences:
        for item in comparison.key_differences:
            st.write(f"- {item}")
    if comparison.unsupported_claims:
        st.warning("Secondary reader flagged unsupported claims.")
        for item in comparison.unsupported_claims:
            st.write(f"- {item}")
    if result.llm_reviews:
        st.markdown("#### Secondary Reader")
        st.dataframe(
            [
                {
                    "Provider": review.provider,
                    "Model": review.model,
                    "Status": review.status,
                    "Summary": review.summary or review.message,
                    "Disagreements": "; ".join(review.disagreements),
                    "Language issues": "; ".join(review.language_quality_issues),
                }
                for review in result.llm_reviews
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("#### Language Audit")
    audit = result.language_audit
    st.caption(
        f"Policy: {audit.policy}; source languages: {', '.join(audit.source_languages) or 'n/a'}; "
        f"flags: {', '.join(audit.flags) or 'none'}."
    )
    for note in audit.chinese_source_notes:
        st.write(f"- {note}")
    references = result.historical_references
    st.markdown("#### Historical References")
    st.caption(
        f"Status: {references.status}; scope: {references.scope}; "
        f"resolved sample: {references.sample_size}/{references.minimum_sample_size}."
    )
    if references.summary:
        st.write(references.summary)
    if references.hit_rate_pct is not None or references.mean_realized_return_pct is not None:
        cols = st.columns(2)
        cols[0].metric(
            "Analog Hit Rate",
            f"{references.hit_rate_pct:.1f}%" if references.hit_rate_pct is not None else "n/a",
        )
        cols[1].metric(
            "Mean Realized Return",
            f"{references.mean_realized_return_pct:+.1f}%" if references.mean_realized_return_pct is not None else "n/a",
        )
    if references.references:
        st.dataframe(
            [
                {
                    "Ticker": reference.ticker,
                    "Idea": reference.idea_title,
                    "Stage": reference.stage,
                    "Direction": reference.direction,
                    "Similarity": reference.similarity_score,
                    "Outcome": reference.outcome_status,
                    "Realized": (
                        f"{reference.realized_return_pct:+.1f}%"
                        if reference.realized_return_pct is not None else "n/a"
                    ),
                    "Reasons": "; ".join(reference.match_reasons),
                }
                for reference in references.references
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in references.data_gaps:
        st.caption(gap)
    if result.action_plan:
        st.markdown("#### Action Plan")
        st.dataframe(
            [
                {
                    "Criterion": item.criterion,
                    "Metric": item.metric or "n/a",
                    "Operator": item.operator or "n/a",
                    "Threshold": item.threshold if item.threshold is not None else "n/a",
                    "Deadline": item.deadline or "n/a",
                    "Source": item.source_field,
                    "Confirm": item.confirm_trigger,
                    "Break": item.break_trigger,
                }
                for item in result.action_plan
            ],
            use_container_width=True,
            hide_index=True,
        )
    if brief.citations:
        st.markdown("#### Source Citations")
        for citation in brief.citations[:6]:
            st.write(f"- [{citation.source}]({citation.url}) - {citation.snippet or citation.section or citation.form or 'source excerpt'}")
    if brief.data_gaps or sufficiency.data_gaps:
        st.markdown("#### Data Gaps")
        for gap in list(dict.fromkeys(brief.data_gaps + sufficiency.data_gaps)):
            st.caption(gap)
    st.markdown("#### LLM Run Manifest")
    st.caption(
        f"Status: {manifest.status}; provider: {manifest.provider}; model: {manifest.model}; "
        f"prompt: {manifest.prompt_version}; token estimate: {manifest.token_estimate or 'n/a'}."
    )
    if manifest.prompt_hash:
        st.caption(f"Prompt fingerprint: {manifest.prompt_hash[:16]}...")
    if manifest.prompt_context_counts:
        st.caption(
            "Context counts: "
            + ", ".join(f"{key}={value}" for key, value in manifest.prompt_context_counts.items())
        )
    if manifest.guardrail_policy:
        st.caption("Guardrails: " + ", ".join(manifest.guardrail_policy))
    if manifest.message:
        st.caption(manifest.message)
    research_manifest = getattr(result, "llm_research_manifest", None)
    if research_manifest:
        st.markdown("#### LLM Research Assistant Policy")
        cols = st.columns(4)
        cols[0].metric("Assistant Status", research_manifest.status)
        cols[1].metric("Provider", research_manifest.provider)
        cols[2].metric("Registry", research_manifest.source_registry_version or "n/a")
        cols[3].metric("Executor", research_manifest.deterministic_executor or "n/a")
        if research_manifest.allowed_roles or research_manifest.prohibited_actions or research_manifest.validation_gates:
            policy_rows = []
            policy_rows.extend(
                {"Category": "Allowed role", "Rule": item}
                for item in research_manifest.allowed_roles
            )
            policy_rows.extend(
                {"Category": "Prohibited action", "Rule": item}
                for item in research_manifest.prohibited_actions
            )
            policy_rows.extend(
                {"Category": "Validation gate", "Rule": item}
                for item in research_manifest.validation_gates
            )
            st.dataframe(policy_rows, use_container_width=True, hide_index=True)
        st.caption(research_manifest.evidence_boundary)
        for message in research_manifest.messages[:3]:
            st.caption(message)


def render_company_economics(result: ResearchResult) -> None:
    economics = result.company_economics
    st.markdown("#### Company Economics + Industry Playbook")
    cols = st.columns(3)
    cols[0].metric("Economics Status", economics.status)
    cols[1].metric("Industry", economics.industry_playbook.industry_label)
    cols[2].metric("Material Drivers", economics.material_driver_count)
    st.metric("Playbook Quality", f"{economics.playbook_quality_score}/100")
    st.write(economics.business_model)
    playbook = economics.industry_playbook
    st.caption(
        f"KPIs: {', '.join(playbook.key_kpis) or 'n/a'} | "
        f"Valuation: {', '.join(playbook.valuation_methods) or 'n/a'} | "
        f"Macro: {', '.join(playbook.macro_sensitivities) or 'n/a'} | "
        f"Source: {getattr(playbook, 'playbook_source', 'built_in')}"
    )
    portfolio = getattr(result, "playbook_portfolio", None)
    if portfolio:
        st.markdown("**Validated Playbook Portfolio**")
        st.dataframe(
            [
                {
                    "Role": item.role,
                    "Playbook": item.label,
                    "Status": item.status,
                    "Rationale": item.rationale,
                    "Evidence IDs": "; ".join(item.evidence_ids[:4]) or "Unknown",
                }
                for item in [portfolio.primary] + portfolio.secondary
            ],
            use_container_width=True,
            hide_index=True,
        )
    if economics.drivers:
        st.dataframe(
            [
                {
                    "Driver": driver.name,
                    "Materiality": driver.materiality,
                    "Trend": driver.trend,
                    "Evidence": driver.current_evidence,
                    "Why it matters": driver.why_it_matters,
                    "Source": driver.source,
                }
                for driver in economics.drivers
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(economics, "playbook_quality", None):
        st.markdown("**Playbook Quality Checklist**")
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Score": item.score,
                    "Summary": item.summary,
                    "Evidence": "; ".join(item.evidence[:3]),
                    "Gaps": "; ".join(item.gaps[:3]),
                    "Next action": item.next_action,
                    "Stage impact": item.stage_impact,
                }
                for item in economics.playbook_quality
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(economics, "driver_coverage", None):
        st.markdown("**Driver Coverage Checklist**")
        st.dataframe(
            [
                {
                    "Driver": item.driver_name,
                    "Materiality": item.materiality,
                    "Status": item.status,
                    "Current evidence": item.current_evidence,
                    "Required evidence": "; ".join(item.required_evidence[:2]),
                    "Missing evidence": "; ".join(item.missing_evidence[:3]),
                    "Next source": item.next_source,
                    "Stage impact": item.stage_impact,
                }
                for item in economics.driver_coverage
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in economics.data_gaps:
        st.caption(gap)


def render_credit_lens(result: ResearchResult) -> None:
    lens = getattr(result, "credit_lens", None)
    st.markdown("#### Credit Lens")
    if not lens:
        st.info("Credit lens is unavailable for the current run.")
        return
    cols = st.columns(3)
    cols[0].metric("Credit Status", lens.status)
    cols[1].metric("Risk Level", lens.risk_level)
    cols[2].metric("Metrics", len(lens.metrics))
    st.write(lens.summary)
    st.caption(lens.source_note)
    if lens.metrics:
        st.dataframe(
            [
                {
                    "Metric": metric.name,
                    "Value": metric.value if metric.value is not None else "n/a",
                    "Unit": metric.unit,
                    "Status": metric.status,
                    "Interpretation": metric.interpretation,
                    "Source": metric.source,
                }
                for metric in lens.metrics
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(lens, "credit_bridge", None):
        st.markdown("**Credit Bridge Checklist**")
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Credit question": item.credit_question,
                    "Current evidence": item.current_evidence,
                    "Missing evidence": "; ".join(item.missing_evidence[:3]),
                    "Next source": item.next_source,
                    "Falsification test": item.falsification_test,
                    "Stage impact": item.stage_impact,
                }
                for item in lens.credit_bridge
            ],
            use_container_width=True,
            hide_index=True,
        )
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Supports**")
        for item in lens.positives[:6]:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**Risks**")
        for item in lens.risks[:6]:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Evidence Needed**")
        for item in lens.required_evidence[:6]:
            st.write(f"- {item}")
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Monitor Rules**")
        for item in lens.monitor_rules[:6]:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**Credit Catalysts**")
        for item in lens.credit_catalysts[:6]:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Falsification Tests**")
        for item in lens.falsification_tests[:6]:
            st.write(f"- {item}")
    for gap in lens.data_gaps[:8]:
        st.caption(gap)


def render_coverage_expansion(result: ResearchResult) -> None:
    diagnostics = result.coverage_expansion
    st.markdown("#### Coverage Expansion Diagnostics")
    cols = st.columns(3)
    cols[0].metric("Thesis Status", diagnostics.status)
    cols[1].metric("Coverage Profile", diagnostics.coverage_profile)
    cols[2].metric("Next Expansions", len(diagnostics.recommended_expansions))
    st.caption(diagnostics.summary)
    if diagnostics.why_no_convincing_thesis:
        with st.expander("Why no convincing thesis yet", expanded=diagnostics.status == "No convincing thesis yet"):
            for reason in diagnostics.why_no_convincing_thesis:
                st.write(f"- {reason}")
    if diagnostics.recommended_expansions:
        st.dataframe(
            [
                {
                    "Priority": action.priority,
                    "Area": action.area,
                    "Source": action.source_type,
                    "Action": action.action,
                    "Why": action.why_it_matters,
                    "Integrity rule": action.integrity_rule,
                    "Expected output": action.expected_output,
                    "Cost / latency": action.cost_latency,
                }
                for action in diagnostics.recommended_expansions
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Integrity and latency policy", expanded=False):
        for note in diagnostics.integrity_notes:
            st.write(f"- {note}")
        for policy in diagnostics.latency_policy:
            st.write(f"- {policy}")


def render_global_coverage(result: ResearchResult) -> None:
    coverage_case = getattr(result, "coverage_case", None)
    matrix = getattr(result, "source_coverage_matrix", None)
    audit = getattr(result, "metric_resolution_audit", None)
    if not coverage_case and not matrix and not audit:
        return
    st.markdown("#### Global Coverage and Metric Resolution")
    cols = st.columns(4)
    cols[0].metric("Geography", coverage_case.geography if coverage_case else "Unknown")
    cols[1].metric("Security Type", coverage_case.security_type if coverage_case else "Unknown")
    cols[2].metric("Sources", len(matrix.entries) if matrix else 0)
    cols[3].metric("Metric Audit", audit.status if audit else "Unknown")
    if coverage_case:
        st.caption(
            f"{coverage_case.company_name}; filing regime {coverage_case.filing_regime}; "
            f"reporting standard {coverage_case.reporting_standard}; currency {coverage_case.currency}."
        )
        if coverage_case.primary_sources:
            st.write("Primary source stack: " + ", ".join(coverage_case.primary_sources))
        for gap in coverage_case.data_gaps[:4]:
            st.warning(gap)
    if matrix:
        with st.expander("Source Coverage Matrix", expanded=False):
            st.caption(matrix.summary)
            st.dataframe(
                [
                    {
                        "Source": item.source_type,
                        "Family": item.source_family,
                        "Status": item.status,
                        "Priority": item.priority,
                        "Official": "Yes" if item.official else "No",
                        "Tier": item.source_tier,
                        "Access": item.access_mode,
                        "Licensing": item.licensing_policy,
                        "Blocker": item.blocker,
                    }
                    for item in matrix.entries
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in matrix.data_gaps[:6]:
                st.warning(gap)
    if audit:
        with st.expander("Canonical Metric Resolution Audit", expanded=False):
            st.caption(audit.summary)
            st.dataframe(
                [
                    {
                        "Metric": item.metric,
                        "Status": item.status,
                        "Method": item.resolution_method,
                        "Value": item.value if item.value is not None else "Unknown",
                        "Unit": item.unit,
                        "Period": item.period_end or "Unknown",
                        "Source metric": item.source_metric,
                        "Formula": item.formula,
                        "Blocker": item.blocker,
                    }
                    for item in audit.items[:24]
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in audit.data_gaps[:8]:
                st.warning(gap)


def render_evidence_work_order(result: ResearchResult) -> None:
    work_order = getattr(result, "evidence_work_order", None)
    if not work_order:
        return
    st.markdown("#### Evidence Work Order")
    _wrapped_metric_grid([
        ("Status", work_order.status),
        ("Open Actions", len(work_order.items)),
        ("Research-Ready Blockers", sum(1 for item in work_order.items if item.blocks_research_ready)),
        ("High-Conviction Blockers", sum(1 for item in work_order.items if item.blocks_high_conviction)),
    ])
    st.caption(work_order.summary)
    if work_order.items:
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Channel": item.channel,
                    "Action": item.action,
                    "Source": item.source_type,
                    "Expected output": item.expected_output,
                    "Blocks RR": "Yes" if item.blocks_research_ready else "No",
                    "Blocks HC": "Yes" if item.blocks_high_conviction else "No",
                    "Origin": item.origin,
                }
                for item in work_order.items[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
        top = work_order.items[0]
        with st.expander("Top Evidence Work Order Detail", expanded=False):
            st.markdown(f"**Action:** {top.action}")
            st.write(top.why_it_matters)
            st.caption(f"Source type: {top.source_type}; origin: {top.origin}; cost/latency: {top.cost_latency or 'n/a'}")
            if top.acceptance_criteria:
                st.markdown("**Acceptance criteria**")
                for item in top.acceptance_criteria:
                    st.write(f"- {item}")
            if top.falsification_tests:
                st.markdown("**Falsification tests**")
                for item in top.falsification_tests:
                    st.write(f"- {item}")
    for gap in work_order.data_gaps:
        st.warning(gap)
    render_evidence_closure(result)


def render_evidence_closure(result: ResearchResult) -> None:
    report = getattr(result, "evidence_closure", None)
    if not report:
        return
    st.markdown("##### Automatic Evidence Closure")
    cols = st.columns(4)
    cols[0].metric("Resolved", report.resolved_count)
    cols[1].metric("Contradicted", report.contradicted_count)
    cols[2].metric("Unavailable", report.unavailable_count)
    cols[3].metric("Licensed / manual", report.licensed_or_manual_count)
    st.caption(report.summary)
    if report.outcomes:
        st.dataframe(
            [
                {
                    "Work order": outcome.work_id,
                    "Outcome": outcome.status.replace("_", " ").title(),
                    "Finding": outcome.summary,
                    "Adapters tried": "; ".join(attempt.adapter for attempt in outcome.attempted_adapters),
                    "Next action": outcome.next_action,
                }
                for outcome in report.outcomes
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_causal_thesis_graphs(result: ResearchResult) -> None:
    graphs = getattr(result, "causal_thesis_graphs", [])
    if not graphs:
        return
    st.markdown("#### Causal Thesis Graph")
    st.caption("Each connection is scored independently: source event → driver → KPI → earnings/FCF → valuation → catalyst.")
    graph = graphs[0]
    _wrapped_metric_grid([
        ("Graph status", graph.status),
        ("Connection score", f"{graph.overall_score}/100"),
        ("Weakest link", graph.weakest_link),
    ])
    st.info(graph.summary)
    st.dataframe(
        [
            {
                "Connection": edge.label,
                "Status": edge.status,
                "Score": edge.score,
                "Evidence": "; ".join(edge.evidence[:2]) or "Unknown",
                "Exact missing evidence": "; ".join(edge.missing_evidence[:2]) or "None",
                "Automatic next action": edge.next_automatic_action,
            }
            for edge in graph.edges
        ],
        use_container_width=True,
        hide_index=True,
    )
    if len(graphs) > 1:
        with st.expander("Other idea graphs", expanded=False):
            st.dataframe(
                [
                    {
                        "Idea": item.idea_id,
                        "Status": item.status,
                        "Score": item.overall_score,
                        "Weakest link": item.weakest_link,
                        "Diagnosis": item.summary,
                    }
                    for item in graphs[1:]
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_market_implied_expectations(result: ResearchResult) -> None:
    implied = getattr(result, "market_implied_expectations", None)
    if not implied:
        return
    st.markdown("#### Reverse FCF / DCF and Market-Implied Expectations")
    _wrapped_metric_grid([
        ("Status", implied.status),
        ("Template", implied.template),
        ("Current price", f"{implied.current_price:,.2f} {implied.currency}" if implied.current_price else "Unknown"),
    ])
    st.caption(implied.summary)
    st.caption(
        f"Price source: {implied.price_source or 'Unknown'} | Price as of: {implied.price_as_of or 'Unknown'} | "
        f"Financial basis: {implied.financial_basis} | Period: {implied.financial_period or 'Unknown'}"
    )
    if implied.assumptions:
        with st.expander("Edit reverse-model assumptions", expanded=False):
            st.caption(
                "Implied variables are computed first from sensible screening defaults. Saved overrides remain "
                "user assumptions, not source facts, calibrated forecasts, or promotion evidence."
            )
            with st.form(f"market_implied_assumptions_{implied.ticker}"):
                input_columns = st.columns(min(4, len(implied.assumptions)))
                edited_assumptions: dict[str, float] = {}
                for index, assumption in enumerate(implied.assumptions):
                    key = assumption.key or assumption.name.lower().replace(" ", "_")
                    edited_assumptions[key] = input_columns[index % len(input_columns)].number_input(
                        assumption.name,
                        min_value=float(assumption.minimum) if assumption.minimum is not None else None,
                        max_value=float(assumption.maximum) if assumption.maximum is not None else None,
                        value=float(assumption.value or 0.0),
                        step=float(assumption.step or 0.1),
                        help=f"{assumption.source}. Current provenance: {assumption.provenance}.",
                    )
                save_assumptions = st.form_submit_button("Recalculate and Save", use_container_width=True)
                reset_assumptions = st.form_submit_button("Reset Sensible Defaults", use_container_width=True)
            if save_assumptions or reset_assumptions:
                store = ResearchStore()
                if reset_assumptions:
                    store.clear_market_implied_assumptions(implied.ticker)
                    overrides = {}
                else:
                    overrides = store.save_market_implied_assumptions(implied.ticker, edited_assumptions)
                result.market_implied_expectations = build_market_implied_expectations(
                    result.identity,
                    result.metrics,
                    implied.current_price,
                    result.valuation,
                    result.company_model,
                    price_source=implied.price_source,
                    price_as_of=implied.price_as_of,
                    assumption_overrides=overrides,
                )
                st.session_state.result = result
                st.rerun()
            st.caption(
                "Defaults are for screening. Test sensitivities before using the output in an IC decision."
            )
    if implied.expectations:
        st.dataframe(
            [
                {
                    "Market-implied variable": row.metric,
                    "Value": f"{row.implied_value:,.2f} {row.unit}" if row.implied_value is not None else "Insufficient data",
                    "Confidence": row.confidence,
                    "Interpretation": row.interpretation,
                    "Formula": row.formula,
                    "Missing inputs": "; ".join(row.missing_inputs),
                }
                for row in implied.expectations
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Assumption provenance", expanded=False):
        for assumption in implied.assumptions:
            value = "Unknown" if assumption.value is None else f"{assumption.value:g} {assumption.unit}"
            st.write(f"- **{assumption.name}:** {value} · {assumption.provenance} · {assumption.source}")


    for gap in implied.data_gaps:
        st.info(gap)


def render_reverse_fcf_snapshot(result: ResearchResult) -> None:
    implied = getattr(result, "market_implied_expectations", None)
    if not implied or implied.template != "Non-financial":
        return
    rows = implied.expectations or []
    base = next((row for row in rows if row.metric == "Reverse DCF base FCF"), None)
    growth = next((row for row in rows if row.metric.startswith("Reverse DCF: implied")), None)
    fcf_yield = next((row for row in rows if row.metric == "Current free-cash-flow yield"), None)
    st.markdown("#### Reverse FCF / DCF Snapshot")
    _wrapped_metric_grid([
        ("FCF base", _implied_row_value(base)),
        ("Current FCF yield", _implied_row_value(fcf_yield)),
        ("Price-implied FCF growth", _implied_row_value(growth)),
        ("Confidence", growth.confidence if growth else "Unavailable"),
    ])
    if growth:
        st.caption(growth.interpretation)
    else:
        missing = next((row for row in rows if row.metric == "Reverse DCF"), None)
        st.warning(
            "Reverse FCF cannot be solved yet. Missing: "
            + ("; ".join(missing.missing_inputs) if missing and missing.missing_inputs else "price, normalized shares, cash/debt, or positive FCF inputs")
        )
    if "not verified as TTM" in (implied.financial_basis or ""):
        st.caption(
            f"Screening basis: {implied.financial_basis}. Open Market & Expectations to edit discount rate, terminal growth, forecast horizon, and revenue-growth assumptions."
        )


def _implied_row_value(row) -> str:
    if not row or row.implied_value is None:
        return "Unavailable"
    return f"{row.implied_value:,.2f} {row.unit}".strip()


def render_earnings_surprise_proxy(result: ResearchResult) -> None:
    proxy = getattr(result, "earnings_surprise_proxy", None)
    if not proxy:
        return
    st.markdown("#### Earnings-Surprise Proxy")
    _wrapped_metric_grid([
        ("Status", proxy.status),
        ("Comparable observations", str(len(proxy.items))),
        ("Revision follow-through", "Available" if proxy.revision_follow_through_available else "Not measured"),
    ])
    st.write(proxy.headline)
    st.caption(proxy.methodology)
    if proxy.items:
        st.dataframe(
            [
                {
                    "Event": item.event_label,
                    "Event date": item.event_date or "Unknown",
                    "Period": item.reporting_period or "Unknown",
                    "Metric": item.metric,
                    "Actual": item.actual if item.actual is not None else "Unknown",
                    "Estimate": item.estimate if item.estimate is not None else "Unknown",
                    "Surprise %": item.surprise_pct if item.surprise_pct is not None else "Unknown",
                    "Estimate as of": item.estimate_as_of or "Unknown",
                    "Estimate source": item.estimate_source,
                    "Actual source": item.actual_source,
                    "Confidence": item.confidence,
                }
                for item in proxy.items
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in proxy.data_gaps:
        st.info(gap)


def render_recent_market_context(result: ResearchResult) -> None:
    context = getattr(result, "recent_market_context", None)
    if not context:
        return
    st.markdown("#### Recent Market Context")
    _wrapped_metric_grid([
        ("Status", context.status),
        ("Price source", context.source),
        ("Price as of", context.price_as_of or "Unknown"),
        ("60d volatility", f"{context.annualized_volatility_pct:.1f}%" if context.annualized_volatility_pct is not None else "Unknown"),
        ("Max drawdown", f"{context.max_drawdown_pct:.1f}%" if context.max_drawdown_pct is not None else "Unknown"),
        ("Recent volume ratio", f"{context.recent_volume_ratio:.2f}x" if context.recent_volume_ratio is not None else "Unknown"),
    ])
    st.caption(context.summary)
    if context.windows:
        st.dataframe(
            [
                {
                    "Window": window.label,
                    "Stock return %": window.return_pct if window.return_pct is not None else "Unknown",
                    "SPY return %": window.benchmark_return_pct if window.benchmark_return_pct is not None else "Unknown",
                    "Relative return %": window.relative_return_pct if window.relative_return_pct is not None else "Unknown",
                    "Status": window.status,
                }
                for window in context.windows
            ],
            use_container_width=True,
            hide_index=True,
        )
    for implication in context.thesis_implications:
        st.write(f"- {implication}")
    for gap in context.data_gaps:
        st.info(gap)


def render_company_model_workspace(result: ResearchResult) -> None:
    model = getattr(result, "company_model", None)
    if not model:
        return
    st.markdown("#### Company Model Workspace")
    st.caption(f"{model.status}. {model.summary}")
    if model.cases:
        st.dataframe(
            [
                {
                    "Case": case.name,
                    "Revenue": case.revenue if case.revenue is not None else "Unknown",
                    "Operating margin %": case.operating_margin_pct if case.operating_margin_pct is not None else "Unknown",
                    "Net income": case.net_income if case.net_income is not None else "Unknown",
                    "FCF": case.free_cash_flow if case.free_cash_flow is not None else "Unknown",
                    "Fair value": case.fair_value if case.fair_value is not None else "Unknown",
                }
                for case in model.cases
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Historical model and formula audit", expanded=False):
        st.dataframe(
            [
                {
                    "Statement": row.statement,
                    "Metric": row.metric,
                    "Period": row.period,
                    "Value": row.value if row.value is not None else "Unknown",
                    "Unit": row.unit,
                    "Provenance": row.provenance,
                    "Formula": row.formula,
                    "Source": row.source,
                }
                for row in model.historicals
            ],
            use_container_width=True,
            hide_index=True,
        )
        for gap in model.data_gaps[:8]:
            st.warning(gap)


def render_research_modes(result: ResearchResult) -> None:
    suite = getattr(result, "research_modes", None)
    if not suite:
        return
    st.markdown("#### Driver-Specific Research Modes")
    st.caption("Choose the workflow that matches the decision; recommended modes are inferred from current events and drivers.")
    st.dataframe(
        [
            {
                "Mode": mode.label,
                "Recommended": "Yes" if mode.recommended else "No",
                "Readiness": mode.status,
                "Score": mode.score,
                "Available metrics": ", ".join(mode.available_metrics),
                "Missing metrics": ", ".join(mode.missing_metrics),
                "Next action": mode.next_actions[0] if mode.next_actions else "Ready for analysis",
            }
            for mode in sorted(suite.modes, key=lambda item: (not item.recommended, -item.score))
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_market_capture_readiness(result: ResearchResult) -> None:
    readiness = getattr(result, "market_capture_readiness", None)
    if not readiness:
        return
    st.markdown("#### Market Capture Readiness")
    cols = st.columns(6)
    cols[0].metric("Status", readiness.status)
    cols[1].metric("Classified", f"{readiness.classified_ideas}/{readiness.total_ideas}")
    cols[2].metric("Price-only", getattr(readiness, "price_only_ideas", 0))
    cols[3].metric("Unknown", readiness.unknown_ideas)
    cols[4].metric("Price", readiness.price_coverage)
    cols[5].metric("Consensus", readiness.consensus_coverage)
    st.caption(readiness.summary)
    st.caption(readiness.point_in_time_rule)
    import_plan = getattr(readiness, "import_plan", None)
    if import_plan:
        st.markdown("**Consensus Import Plan**")
        plan_cols = st.columns(4)
        plan_cols[0].metric("Import status", import_plan.status)
        plan_cols[1].metric("Minimum viable rows", getattr(import_plan, "minimum_viable_rows", import_plan.minimum_required_rows))
        plan_cols[2].metric("Full revision rows", getattr(import_plan, "full_revision_rows", import_plan.minimum_required_rows))
        plan_cols[3].metric("Event dates", len(import_plan.event_dates))
        st.caption(import_plan.summary)
        if getattr(import_plan, "practical_next_step", ""):
            st.info(import_plan.practical_next_step)
        if import_plan.blocking_reason:
            st.warning(import_plan.blocking_reason)
        if getattr(import_plan, "provider_options", None):
            st.markdown("**Cost-effective provider paths**")
            for option in import_plan.provider_options:
                st.write(f"- {option}")
        if import_plan.required_files:
            st.write("Required files: " + ", ".join(import_plan.required_files))
        if import_plan.optional_files:
            st.caption("Optional files: " + ", ".join(import_plan.optional_files))
        if import_plan.template_command:
            st.code(import_plan.template_command, language="bash")
        if import_plan.import_command:
            st.code(import_plan.import_command, language="bash")
        for step in import_plan.next_steps:
            st.write(f"- {step}")
    advisor = getattr(readiness, "consensus_advisor", None)
    if advisor:
        st.markdown("**Consensus Coverage Advisor**")
        advisor_cols = st.columns(3)
        advisor_cols[0].metric("Advisor status", advisor.status)
        advisor_cols[1].metric("Blocker", advisor.blocker)
        advisor_cols[2].metric("Required fix", advisor.required_fix[:48] + ("..." if len(advisor.required_fix) > 48 else ""))
        st.caption(advisor.summary)
        st.caption(advisor.no_lookahead_rule)
    autofill = getattr(readiness, "autofill_plan", None)
    if autofill:
        st.markdown("**Market Capture Autofill Plan**")
        autofill_cols = st.columns(3)
        autofill_cols[0].metric("Status", autofill.status)
        autofill_cols[1].metric("Minimum viable rows", autofill.minimum_viable_rows)
        autofill_cols[2].metric("Full revision rows", autofill.full_revision_rows)
        st.caption(autofill.summary)
    if readiness.actions:
        st.dataframe(
            [
                {
                    "Priority": action.priority,
                    "Area": action.area,
                    "Status": action.status,
                    "Action": action.action,
                    "Why it matters": action.why_it_matters,
                    "Source type": action.source_type,
                    "Ideas": ", ".join(action.related_idea_ids),
                }
                for action in readiness.actions
            ],
            use_container_width=True,
            hide_index=True,
        )
    if readiness.snapshot_needs:
        st.markdown("**Point-in-time Snapshot Checklist**")
        st.dataframe(
            [
                {
                    "Idea": item.idea_id,
                    "Event date": item.event_date or "unknown",
                    "Metric family": item.metric_family,
                    "Pre-event snapshot": item.pre_event_snapshot,
                    "Post-event snapshot": item.post_event_snapshot,
                    "Accepted sources": "; ".join(item.accepted_sources[:4]),
                    "CSV row hints": " | ".join(item.csv_row_hints[:3]),
                    "Reason": item.reason,
                    "Status": item.status,
                }
                for item in readiness.snapshot_needs
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in readiness.data_gaps:
        st.warning(gap)


def render_latency_profile(result: ResearchResult) -> None:
    profiling = getattr(result, "profiling", None)
    if not profiling:
        return
    st.markdown("#### Latency Profile")
    cols = st.columns(3)
    cols[0].metric("Status", profiling.status)
    cols[1].metric("Total runtime", f"{profiling.total_ms / 1000:.2f}s" if profiling.total_ms else "n/a")
    cols[2].metric("Stages", len(profiling.steps))
    if profiling.bottlenecks:
        st.markdown("**Slowest stages**")
        for item in profiling.bottlenecks[:5]:
            st.write(f"- {item}")
    if getattr(profiling, "treatments", None):
        st.markdown("**Bottleneck treatments**")
        for item in profiling.treatments[:8]:
            st.write(f"- {item}")
    for note in profiling.notes:
        st.caption(note)


def render_thesis_clusters(result: ResearchResult) -> None:
    st.markdown("#### Thesis Clusters")
    if not result.thesis_clusters:
        st.info("No thesis clusters were generated.")
        return
    st.dataframe(
        [
            {
                "Cluster": cluster.label,
                "Status": cluster.status,
                "Stage": cluster.stage,
                "Score": cluster.score if cluster.score is not None else "n/a",
                "Chain": cluster.conviction_chain_status,
                "Driver": cluster.driver_name,
                "Ideas": len(cluster.idea_ids),
                "Priced in": cluster.priced_in,
                "Gaps": "; ".join(cluster.evidence_gaps),
            }
            for cluster in result.thesis_clusters[:8]
        ],
        use_container_width=True,
        hide_index=True,
    )
    top = result.thesis_clusters[0]
    with st.expander("Top Cluster IC One-Pager", expanded=False):
        st.write(top.thesis)
        if top.why_now:
            st.markdown(f"**Why now:** {top.why_now}")
        st.markdown(f"**Counter-thesis:** {top.counter_thesis}")
        if top.what_must_be_true:
            st.markdown("**What must be true**")
            for item in top.what_must_be_true:
                st.write(f"- {item}")
        if top.what_would_falsify:
            st.markdown("**What would falsify it**")
            for item in top.what_would_falsify:
                st.write(f"- {item}")
        st.markdown("**Valuation bridge**")
        for item in top.valuation_bridge:
            st.write(f"- {item}")
        st.markdown("**Monitoring checklist**")
        for item in top.monitor_checklist:
            st.write(f"- {item}")
        if top.next_research_actions:
            st.markdown("**Next research actions**")
            for item in top.next_research_actions:
                st.write(f"- {item}")


def render_research_questions(result: ResearchResult) -> None:
    st.markdown("#### Research Questions")
    questions = getattr(result, "research_questions", [])
    if not questions:
        st.info("No open research questions were generated. Either the evidence chain is complete, or no source-linked signal was strong enough to investigate.")
        return
    st.dataframe(
        [
            {
                "Question": item.title,
                "Priority": item.priority,
                "Status": item.status,
                "Answerability": item.answerability_status,
                "Score": item.answerability_score,
                "Driver": item.driver_name,
                "Missing links": "; ".join(item.missing_links[:3]),
                "Market capture needs": "; ".join(item.market_capture_needs[:2]),
            }
            for item in questions[:8]
        ],
        use_container_width=True,
        hide_index=True,
    )
    top = questions[0]
    with st.expander("Top Research Question Workplan", expanded=False):
        st.write(top.source_signal)
        st.markdown(f"**Why it matters:** {top.why_it_matters}")
        st.markdown(
            f"**Answerability:** {top.answerability_status} "
            f"({top.answerability_score}/100)"
        )
        if top.decision_rule:
            st.write(top.decision_rule)
        if top.hypothesis:
            st.markdown("**Hypothesis to test**")
            st.write(top.hypothesis)
        if top.minimum_evidence_package:
            st.markdown("**Minimum evidence package**")
            for item in top.minimum_evidence_package[:5]:
                st.write(f"- {item}")
        if top.answer_format:
            st.markdown("**Expected answer format**")
            st.write(top.answer_format)
        if top.stop_condition:
            st.markdown("**Stop condition**")
            st.write(top.stop_condition)
        if top.answerability_gaps:
            st.markdown("**Answerability gaps**")
            for item in top.answerability_gaps[:6]:
                st.write(f"- {item}")
        if top.required_evidence:
            st.markdown("**Required evidence**")
            for item in top.required_evidence[:6]:
                st.write(f"- {item}")
        if top.primary_source_types:
            st.markdown("**Primary source types**")
            for item in top.primary_source_types[:6]:
                st.write(f"- {item}")
        if top.next_sources:
            st.markdown("**Next sources to check**")
            for item in top.next_sources[:6]:
                st.write(f"- {item}")
        if top.workplan_steps:
            st.markdown("**Workplan steps**")
            for item in top.workplan_steps[:6]:
                st.write(f"- {item}")
        if top.acceptance_criteria:
            st.markdown("**Acceptance criteria**")
            for item in top.acceptance_criteria[:6]:
                st.write(f"- {item}")
        if top.falsification_tests:
            st.markdown("**Falsification tests**")
            for item in top.falsification_tests[:6]:
                st.write(f"- {item}")
        if top.promotion_criteria:
            st.markdown("**Promotion criteria**")
            for item in top.promotion_criteria:
                st.write(f"- {item}")
        if top.equity_lens or top.credit_lens:
            st.markdown("**Equity / credit lens**")
            if top.equity_lens:
                st.write(top.equity_lens)
            if top.credit_lens:
                st.write(top.credit_lens)


def render_conviction_chain(result: ResearchResult) -> None:
    if not result.ideas or not result.ideas[0].conviction_chain:
        st.markdown("#### Conviction Chain")
        st.info("No conviction chain was generated for the current run.")
        return
    chain = result.ideas[0].conviction_chain
    st.markdown("#### Conviction Chain")
    cols = st.columns(3)
    cols[0].metric("Chain Status", chain.status)
    cols[1].metric("Confidence", chain.confidence)
    cols[2].metric("Links Complete", f"{sum(1 for step in chain.steps if step.status == 'Complete')}/{len(chain.steps)}")
    st.caption(chain.summary)
    st.dataframe(
        [
            {
                "Link": step.label,
                "Status": step.status,
                "Statement": step.statement,
                "Evidence": "; ".join(step.evidence),
                "Gaps": "; ".join(step.data_gaps),
            }
            for step in chain.steps
        ],
        use_container_width=True,
        hide_index=True,
    )
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**What must be true**")
        for item in chain.what_must_be_true:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**What would falsify it**")
        for item in chain.what_would_falsify:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Next research actions**")
        for item in chain.next_research_actions or ["No immediate chain gaps."]:
                st.write(f"- {item}")


def render_research_scout(result: ResearchResult) -> None:
    scout = getattr(result, "research_scout", None)
    if not scout:
        return
    st.markdown("#### Research Scout")
    cols = st.columns(3)
    cols[0].metric("Status", scout.status)
    cols[1].metric("Open questions", len(scout.questions))
    cols[2].metric("Provider", scout.provider)
    st.caption(scout.summary)
    axis_rows = []
    for label, rows in (
        ("Company", scout.company_story_axes),
        ("Sector", scout.sector_story_axes),
        ("Geography", scout.geography_story_axes),
        ("Peers", scout.peer_story_axes),
    ):
        for item in rows[:4]:
            axis_rows.append({"Story axis": label, "What to frame": item})
    if axis_rows:
        st.dataframe(axis_rows, use_container_width=True, hide_index=True)
    if scout.questions:
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Lens": item.lens,
                    "Question": item.question,
                    "Source types": "; ".join(item.source_types[:4]),
                    "Expected evidence": item.expected_evidence,
                    "Status": item.current_status,
                    "Story use": item.story_use,
                }
                for item in scout.questions[:10]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if scout.data_gaps:
        with st.expander("Research Scout gaps", expanded=False):
            for gap in scout.data_gaps:
                st.warning(gap)


def render_validated_claims_and_source_plan(result: ResearchResult) -> None:
    st.markdown("#### Validated Claims")
    claims = result.validated_claims
    cols = st.columns(3)
    cols[0].metric("Claim Status", claims.status)
    cols[1].metric("Claims", len(claims.claims))
    cols[2].metric("Provider", claims.provider)
    if claims.claims:
        st.dataframe(
            [
                {
                    "Status": claim.status,
                    "Category": claim.event_category,
                    "Direction": claim.direction,
                    "Driver": claim.business_driver,
                    "Metric": claim.metric or "n/a",
                    "Confidence": claim.confidence,
                    "What changed": claim.changed_text or claim.supporting_quote,
                    "Reason": claim.reason,
                    "Why not thesis-grade": claim.not_thesis_grade_reason,
                }
                for claim in claims.claims[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in claims.data_gaps:
        st.caption(gap)
    plan = result.source_plan
    st.markdown("#### Research Source Plan")
    st.caption(
        f"Status: {plan.status}; registry: {plan.registry_version}; provider: {plan.provider}. "
        "The LLM may recommend source types, but deterministic adapters must fetch them."
    )
    if plan.requests:
        st.dataframe(
            [
                {
                    "Priority": request.priority,
                    "Source type": request.source_type,
                    "Title": request.title,
                    "Why inspect": request.reason_to_inspect,
                    "Expected evidence": request.expected_evidence_type,
                    "Confirms/disproves": request.confirms_or_disproves,
                    "Cost/latency": request.cost_latency,
                    "Status": request.status,
                }
                for request in plan.requests[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in plan.data_gaps:
        st.caption(gap)
    render_news_intelligence(result)


def render_news_intelligence(result: ResearchResult) -> None:
    claims = getattr(result, "news_claims", []) or []
    corroboration = getattr(result, "source_corroboration_results", []) or []
    bridges = getattr(result, "causal_bridges", []) or []
    st.markdown("#### News Intelligence & Primary Source Corroboration")
    st.caption(
        "Credible news is used for event discovery and source leads only. "
        "It cannot independently create Research-Ready or High-Conviction ideas."
    )
    cols = st.columns(3)
    cols[0].metric("News Claims", len(claims))
    cols[1].metric("Corroboration Checks", len(corroboration))
    missing = sum(1 for item in corroboration if item.status == "Primary corroboration missing")
    cols[2].metric("Primary Gaps", missing)
    if claims:
        st.dataframe(
            [
                {
                    "Status": claim.status,
                    "Source family": claim.source_family,
                    "Driver": claim.affected_driver,
                    "Event": claim.event_type,
                    "Confidence": claim.confidence,
                    "Claim": claim.claimed_fact,
                    "Required corroboration": "; ".join(claim.required_corroboration[:2]),
                }
                for claim in claims[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if corroboration:
        st.dataframe(
            [
                {
                    "Status": item.status,
                    "Driver": item.driver_family,
                    "Explanation": item.explanation,
                    "Gaps": "; ".join(item.gaps[:2]),
                }
                for item in corroboration[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if bridges:
        st.caption(
            f"Causal bridge records attached: {len(bridges)}. "
            "Bridge status is shown on idea cards when relevant."
        )


def render_wisburg_research_lens(result: ResearchResult) -> None:
    lens = getattr(result, "wisburg_lens", None)
    st.markdown("#### Outside Analyst Debate")
    if not lens or lens.status == "Unavailable":
        caveats = lens.caveats if lens else ["Wisburg research lens was not attached."]
        st.caption("; ".join(caveats))
        return

    narrative = lens.narrative_score
    debate = lens.debate_map
    cols = st.columns(4)
    cols[0].metric("Wisburg Lens", lens.status)
    cols[1].metric("Research Excerpts", len(lens.excerpts))
    cols[2].metric("Themes", len(lens.themes))
    cols[3].metric("Narrative", narrative.label if narrative else "Unknown")
    st.caption(
        "Wisburg is used for outside-analyst debate, narrative crowding, and source suggestions. "
        "It cannot independently promote an idea to Research-Ready or High-Conviction."
    )
    coverage = getattr(lens, "coverage_audit", None)
    if coverage:
        with st.expander("Wisburg entitlement and coverage audit", expanded=False):
            st.write(
                f"**{coverage.status}**. Authentication: {coverage.authentication_status}. "
                f"Tool discovery: {coverage.tool_discovery_status}. "
                f"Observed {coverage.total_items} item(s); fetched structured detail for "
                f"{coverage.detailed_items} item(s)."
            )
            st.dataframe(
                [
                    {
                        "Tool": item.tool_name,
                        "Category": item.source_category,
                        "Entitlement": item.status,
                        "Queries": item.query_count,
                        "Items": item.item_count,
                        "Details": item.detail_success_count,
                        "Message": item.message,
                    }
                    for item in coverage.tools
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in coverage.data_gaps:
                st.caption(gap)
            if lens.reports:
                st.markdown("**Normalized report coverage**")
                st.dataframe(
                    [
                        {
                            "Report": item.title,
                            "Category": item.category,
                            "Publisher": item.publisher,
                            "Published": item.published_at or "Unknown",
                            "Language": item.source_language,
                            "Detail status": item.detail_status,
                            "Stored scope": item.content_scope,
                            "Tier": item.source_tier,
                        }
                        for item in lens.reports[:20]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
    delta = ResearchStore().latest_wisburg_delta(result.identity.ticker)
    if delta:
        st.markdown("**Point-in-Time Wisburg Change**")
        st.write(delta.get("summary") or "No delta summary is available.")
        st.caption(
            f"Status: {delta.get('status', 'Unknown')}; current snapshot: {delta.get('observed_at', 'Unknown')}; "
            f"prior snapshot: {delta.get('prior_observed_at') or 'First baseline'}; "
            f"new reports: {len(delta.get('new_report_ids') or [])}; "
            f"stance changes: {len(delta.get('theme_stance_changes') or [])}; "
            f"external revisions: {len(delta.get('new_revision_ids') or [])}; "
            f"corroboration changes: {len(delta.get('corroboration_changes') or [])}."
        )
        if delta.get("new_report_titles"):
            with st.expander("Newly observed Wisburg items", expanded=False):
                for title in delta["new_report_titles"]:
                    st.write(f"- {title}")
    if lens.revisions:
        st.markdown("**External Revision Observations**")
        st.caption(
            "These are report-level analyst observations from Wisburg. They are not official "
            "consensus snapshots and cannot establish that expectations moved."
        )
        st.dataframe(
            [
                {
                    "As of": item.source_as_of or "Unknown",
                    "Type": item.revision_type,
                    "Metric": item.metric,
                    "Direction": item.direction,
                    "Previous": item.previous_value,
                    "Current": item.current_value,
                    "Change %": item.change_pct,
                    "Period": item.fiscal_period or "Unknown",
                    "Eligibility": item.eligibility,
                    "Statement": item.statement,
                }
                for item in lens.revisions[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.structured_claims:
        st.markdown("**Structured Claims and Primary-Source Cross-Check**")
        st.dataframe(
            [
                {
                    "Claim": item.statement,
                    "Type": item.claim_type,
                    "Driver": item.driver,
                    "Metric": item.metric or "Unknown",
                    "Period": item.fiscal_period or "Unknown",
                    "Tier": item.source_tier,
                    "Cross-check": item.corroboration_status,
                    "Primary matches": len(item.primary_evidence_ids),
                    "Allowed stage": item.allowed_stage,
                }
                for item in lens.structured_claims[:20]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.research_tasks:
        st.markdown("**Executable Wisburg Research Work Orders**")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Source type": item.source_type,
                    "Action": item.action,
                    "Expected evidence": item.expected_evidence,
                    "Confirm/disprove": item.confirms_or_disproves,
                    "Status": item.status,
                }
                for item in lens.research_tasks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if debate:
        st.markdown("**Bull / Bear Debate Map**")
        st.write(
            f"Status: {debate.status}. "
            f"Bull case: {debate.strongest_bull_case or 'n/a'} "
            f"Bear case: {debate.strongest_bear_case or 'n/a'}"
        )
    if lens.themes:
        st.dataframe(
            [
                {
                    "Theme": theme.label,
                    "Stance": theme.stance,
                    "Driver": theme.driver,
                    "Evidence": theme.evidence_count,
                    "Language": ", ".join(theme.source_language_mix) or "n/a",
                    "Confidence": theme.confidence,
                    "Summary": theme.summary,
                }
                for theme in lens.themes[:8]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if narrative:
        st.markdown("**Narrative Crowding**")
        st.caption(
            f"{narrative.label}; score {narrative.score if narrative.score is not None else 'n/a'}; "
            f"items {narrative.item_count}; repeated topics: {', '.join(narrative.repeated_topics) or 'n/a'}."
        )
    if lens.source_suggestions:
        st.markdown("**Wisburg Source Suggestions**")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Source type": item.source_type,
                    "Title": item.title,
                    "Why inspect": item.reason_to_inspect,
                    "Expected evidence": item.expected_evidence_type,
                    "Confirm/disprove": item.confirms_or_disproves,
                }
                for item in lens.source_suggestions[:8]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.excerpts:
        with st.expander("Capped Wisburg excerpts and caveats", expanded=False):
            st.dataframe(
                [
                    {
                        "Title": item.title,
                        "Category": item.category,
                        "Language": item.source_language,
                        "As of": item.source_as_of or "n/a",
                        "Themes": ", ".join(item.theme_tags),
                        "Target/rating": item.non_consensus_label if item.mentions_target_or_rating else "n/a",
                        "Excerpt": item.original_excerpt,
                        "Summary": item.translated_summary or item.generated_summary,
                    }
                    for item in lens.excerpts[:10]
                ],
                use_container_width=True,
                hide_index=True,
            )
            for caveat in lens.caveats:
                st.caption(caveat)


def render_research_radar(result: ResearchResult) -> None:
    resolution = result.entity_resolution
    coverage = result.financial_coverage
    status_cols = st.columns(3)
    status_cols[0].metric("Listing Status", resolution.listing_status)
    status_cols[1].metric("Financial Coverage", coverage.status.replace("_", " ").title())
    status_cols[2].metric("Exchange", resolution.exchange or "Unknown")
    if resolution.warning:
        st.warning(resolution.warning)
    st.caption(coverage.reason)
    if result.coverage_notes:
        for note in result.coverage_notes:
            st.info(note)

    render_consensus(result)
    render_event_workflow(result)

    st.subheader("Detected Changes")
    if not result.events:
        st.warning("No material changes detected from the loaded filings and facts.")
    else:
        assessment_by_event = {
            item.event_id: item for item in getattr(result, "metric_assessments", [])
        }
        for event in result.events[:8]:
            assessment = assessment_by_event.get(event_identifier(event))
            if not assessment:
                continue
            with st.container(border=True):
                st.markdown(f"**{assessment.metric_name}: {assessment.interpretation}**")
                st.caption(f"{assessment.event_label} | Polarity: {assessment.polarity}")
                st.write(assessment.observed_change)
                hypothesis_cols = st.columns(2)
                with hypothesis_cols[0]:
                    st.markdown("**Constructive hypothesis**")
                    st.write(assessment.constructive_hypothesis.mechanism if assessment.constructive_hypothesis else "Unknown")
                with hypothesis_cols[1]:
                    st.markdown("**Adverse hypothesis**")
                    st.write(assessment.adverse_hypothesis.mechanism if assessment.adverse_hypothesis else "Unknown")
                st.caption(f"Historical trend: {assessment.historical_trend}")
                st.info(f"Next automatic research action: {assessment.next_automatic_action}")
        event_rows = [
            {
                "Category": event.category.replace("_", " ").title(),
                "Title": event.title,
                "Direction": event.direction,
                "Severity": event.severity,
                "Date": event.event_date,
                "Summary": event.summary,
                "Why this matters": event.why_this_matters,
            }
            for event in result.events[:20]
        ]
        with st.expander("Raw detected-change table", expanded=False):
            st.dataframe(event_rows, use_container_width=True, hide_index=True)

    st.subheader("Financial Snapshot")
    metric_rows = [
        {
            "Metric": metric.name,
            "Latest": f"{format_number(metric.value)} {metric.unit}",
            "Period": metric.period_end,
            "Filed": metric.filed,
            "YoY / comparable": (
                f"{metric.yoy_change_pct:+.1f}%"
                if metric.yoy_change_pct is not None
                else "n/a"
            ),
        }
        for metric in result.metrics
    ]
    if metric_rows:
        st.dataframe(metric_rows, use_container_width=True, hide_index=True)
    else:
        st.info(f"Financial snapshot unavailable [{coverage.status}]: {coverage.reason}")
        for gap in coverage.data_gaps:
            st.caption(gap)

    st.subheader("Source Filings")
    filing_rows = [
        {
            "Form": filing.form,
            "Filed": filing.filing_date,
            "Report": filing.report_date,
            "Description": filing.description,
            "URL": filing.url,
        }
        for filing in result.filings[:12]
    ]
    st.dataframe(filing_rows, use_container_width=True, hide_index=True)


def render_event_workflow(result: ResearchResult) -> None:
    workflow = result.event_workflow
    st.subheader("Event Calendar + Next Source Workflow")
    st.caption(
        "This queue turns the research output into follow-up checks: upcoming filing windows, "
        "consensus seeding, source-plan requests, and monitor rules."
    )
    if not workflow.items:
        st.info("No workflow items are available yet.")
        for gap in workflow.data_gaps:
            st.caption(gap)
        return
    st.dataframe(
        [
            {
                "Priority": item.priority,
                "Type": item.item_type,
                "Title": item.title,
                "Due": item.due_date or "n/a",
                "Source": item.source,
                "Status": item.status,
                "Related idea": item.related_idea_id or "n/a",
                "Reason": item.reason,
            }
            for item in workflow.items
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_idea_factory(result: ResearchResult) -> None:
    st.subheader("Wow Filter: Changed Evidence, Stale Market Reaction")
    if result.wow_ideas:
        for idea in result.wow_ideas[:5]:
            render_idea_summary(idea)
    else:
        st.info(
            "No uncaptured or partially captured setup was detected. "
            "Connect consensus and richer price data to sharpen this filter."
        )

    st.subheader("Generated Ideas")
    if not result.ideas:
        st.warning("No trade ideas generated.")
        return
    ready = [idea for idea in result.ideas if idea.stage == "Research-Ready"]
    high = [idea for idea in result.ideas if idea.stage in {"High-Conviction", "Investable"}]
    candidates = [
        idea for idea in result.ideas
        if idea.stage not in {"Research-Ready", "High-Conviction", "Investable"}
    ]
    questions = getattr(result, "research_questions", [])
    ready_tab, high_tab, question_tab, candidate_tab = st.tabs([
        f"Research-Ready ({len(ready)})",
        f"High-Conviction ({len(high)})",
        f"Research Questions ({len(questions)})",
        f"Candidates ({len(candidates)})",
    ])
    with ready_tab:
        if not ready:
            st.info("No idea passed the practical research-ready gate.")
        for idea in ready[:8]:
            render_idea_summary(idea)
    with high_tab:
        if not high:
            st.info("No idea passed every high-conviction gate.")
        for idea in high[:8]:
            render_idea_summary(idea)
    with question_tab:
        if not questions:
            st.info("No research questions were generated from weak thesis chains.")
        for question in questions[:8]:
            with st.expander(f"{question.priority} | {question.title}", expanded=False):
                st.write(question.source_signal)
                st.markdown(f"**Status:** {question.status}")
                st.markdown(
                    f"**Answerability:** {question.answerability_status} "
                    f"({question.answerability_score}/100)"
                )
                st.markdown(f"**Driver:** {question.driver_name}")
                st.markdown(f"**Why it matters:** {question.why_it_matters}")
                if question.decision_rule:
                    st.write(question.decision_rule)
                if question.answerability_gaps:
                    st.markdown("**Answerability gaps**")
                    for item in question.answerability_gaps[:5]:
                        st.write(f"- {item}")
                if question.missing_links:
                    st.markdown("**Missing links**")
                    for item in question.missing_links[:5]:
                        st.write(f"- {item}")
                if question.required_evidence:
                    st.markdown("**Required evidence**")
                    for item in question.required_evidence[:5]:
                        st.write(f"- {item}")
                if question.primary_source_types:
                    st.markdown("**Primary source types**")
                    for item in question.primary_source_types[:5]:
                        st.write(f"- {item}")
                if question.next_sources:
                    st.markdown("**Next sources**")
                    for item in question.next_sources[:5]:
                        st.write(f"- {item}")
                if question.workplan_steps:
                    st.markdown("**Workplan steps**")
                    for item in question.workplan_steps[:5]:
                        st.write(f"- {item}")
                if question.acceptance_criteria:
                    st.markdown("**Acceptance criteria**")
                    for item in question.acceptance_criteria[:5]:
                        st.write(f"- {item}")
                if question.falsification_tests:
                    st.markdown("**Falsification tests**")
                    for item in question.falsification_tests[:5]:
                        st.write(f"- {item}")
    with candidate_tab:
        for idea in candidates[:8]:
            render_idea_summary(idea)


def _idea_capture_heading(idea) -> str:
    capture = idea.market_capture
    if not capture:
        return "Capture: not evaluated"
    if capture.capture_mode == "Unavailable":
        price_status = (capture.price_status or "price unavailable").replace("_", " ")
        return f"Capture: unavailable ({price_status})"
    return f"Capture: {capture.capture_mode.lower()}"


def render_idea_summary(idea) -> None:
    score = idea.score.total if idea.score else "n/a"
    capture = idea.market_capture.category if idea.market_capture else "Unknown"
    capture_mode = idea.market_capture.capture_mode if idea.market_capture else "Unavailable"
    capture_heading = _idea_capture_heading(idea)
    ev = expected_value(idea.scenarios)
    with st.expander(f"{idea.stage} | {idea.title} | Score {score}/100 | {capture_heading}", expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("Direction", idea.direction)
        col2.metric("Illustrative EV", f"{ev:+.1f}%" if ev is not None else "Unavailable")
        col3.metric("Horizon", idea.horizon)
        probability_status = idea.probability_provenance.status if idea.probability_provenance else "Uncalibrated"
        st.caption(
            f"Probability source: {probability_status}. Illustrative values are excluded from ranking until calibrated."
        )
        if idea.market_capture:
            st.caption(
                f"Market-capture mode: {capture_mode}. "
                f"{idea.market_capture.diagnosis or idea.market_capture.explanation}"
            )
        if idea.score:
            st.markdown("**Score dimensions**")
            st.dataframe(
                [{
                    "Research quality": idea.score.research_quality,
                    "Evidence strength": idea.score.evidence_strength_score,
                    "Valuation completeness": idea.score.valuation_completeness,
                    "Market-capture confidence": idea.score.market_capture_confidence,
                    "Actionability": idea.score.actionability,
                }],
                use_container_width=True,
                hide_index=True,
            )
        if idea.thesis_audit_chain:
            st.markdown("**Thesis audit chain**")
            st.write(idea.thesis_audit_chain.summary)
            st.dataframe(
                [
                    {
                        "Step": step.step,
                        "Status": step.status,
                        "Summary": step.summary,
                        "Evidence": "; ".join(step.evidence[:2]),
                        "Gaps": "; ".join(step.data_gaps[:2]),
                    }
                    for step in idea.thesis_audit_chain.steps
                ],
                use_container_width=True,
                hide_index=True,
            )
            if idea.thesis_audit_chain.next_actions:
                st.caption("Next audit actions: " + "; ".join(idea.thesis_audit_chain.next_actions[:4]))
        if idea.driver_analysis:
            st.markdown("**Possible causes**")
            st.write(idea.driver_analysis.headline)
            if idea.driver_analysis.bridge_status or idea.driver_analysis.mechanism:
                st.markdown("**Causal bridge detail**")
                st.caption(
                    f"{idea.driver_analysis.bridge_status or 'Unknown'}"
                    + (
                        f" | Driver: {idea.driver_analysis.primary_driver}"
                        if idea.driver_analysis.primary_driver else ""
                    )
                )
                if idea.driver_analysis.mechanism:
                    st.write(idea.driver_analysis.mechanism)
                bridge_rows = []
                if idea.driver_analysis.evidence_needed:
                    bridge_rows.append({
                        "Area": "Evidence needed",
                        "Items": "; ".join(idea.driver_analysis.evidence_needed[:4]),
                    })
                if idea.driver_analysis.peer_metric_checks:
                    bridge_rows.append({
                        "Area": "Peer metric checks",
                        "Items": "; ".join(idea.driver_analysis.peer_metric_checks[:4]),
                    })
                if idea.driver_analysis.falsification_tests:
                    bridge_rows.append({
                        "Area": "Falsification tests",
                        "Items": "; ".join(idea.driver_analysis.falsification_tests[:4]),
                    })
                if bridge_rows:
                    st.dataframe(bridge_rows, use_container_width=True, hide_index=True)
                if idea.driver_analysis.valuation_implication:
                    st.caption("Valuation implication: " + idea.driver_analysis.valuation_implication)
                if idea.driver_analysis.credit_implication:
                    st.caption("Credit implication: " + idea.driver_analysis.credit_implication)
                for gap in idea.driver_analysis.data_gaps[:4]:
                    st.warning(gap)
            for factor in idea.driver_analysis.factors:
                st.write(
                    f"- {factor.cause} ({factor.confidence} confidence, "
                    f"{factor.magnitude_hint}): {factor.explanation}"
                )
                for note in factor.missing_data_notes:
                    st.caption(note)
        if idea.causal_bridge_status:
            st.markdown("**Causal bridge**")
            st.write(idea.causal_bridge_status)
        if idea.equity_credit_lens:
            st.markdown("**Equity / credit lens**")
            st.write(idea.equity_credit_lens.get("equity", ""))
            st.write(idea.equity_credit_lens.get("credit", ""))
        if idea.llm_contribution:
            st.markdown("**LLM contribution**")
            st.caption(
                "; ".join(f"{key}: {value}" for key, value in idea.llm_contribution.items())
            )
        if idea.driver_attribution:
            attribution = idea.driver_attribution
            st.markdown("**Price move attribution**")
            st.write(f"{attribution.classification} ({attribution.confidence}). {attribution.headline}")
            st.caption(
                f"Raw {attribution.return_window or 'n/a'}: {_percent(attribution.raw_return_pct)}; "
                f"market-relative: {_percent(attribution.market_relative_pct)}; "
                f"sector-relative: {_percent(attribution.sector_relative_pct)}; "
                f"beta-adjusted: {_percent(attribution.beta_adjusted_pct)}."
            )
        st.write(idea.thesis)
        st.markdown(f"**Thesis-grade status:** {idea.thesis_grade_status}")
        if idea.promotion_decision:
            with st.expander("Promotion evidence audit", expanded=False):
                decision = idea.promotion_decision
                st.markdown(f"**{decision.label}**")
                st.caption(
                    f"Status: {decision.status}; substituted gate: {decision.substituted_gate or 'none'}; "
                    f"score cap: {decision.score_cap if decision.score_cap is not None else 'none'}."
                )
                for item in decision.checks:
                    st.write(f"- Passed: {item}")
                for item in decision.failed_checks:
                    st.write(f"- Failed: {item}")
                if decision.source_ids:
                    st.caption("Eligible source IDs: " + ", ".join(decision.source_ids))
        st.markdown(f"**Direction rationale:** {idea.direction_rationale or 'n/a'}")
        if idea.driver_template_summary:
            st.markdown("**Driver explanation template**")
            st.write(idea.driver_template_summary)
        if idea.normalization_status:
            st.markdown(f"**Normalization status:** {idea.normalization_status}")
        if idea.share_reconciliation:
            st.markdown("**Share reconciliation**")
            st.write(f"Status: {idea.share_reconciliation.status}; basis: {idea.share_reconciliation.basis}")
            if idea.share_reconciliation.adr_ratio:
                st.caption(f"ADR ratio: {idea.share_reconciliation.adr_ratio}")
            for gap in idea.share_reconciliation.data_gaps:
                st.caption(gap)
        if idea.source_events:
            event = idea.source_events[0]
            changed_text = event.metrics.get("changed_text") or event.metrics.get("supporting_quote")
            if changed_text:
                st.markdown("**What exactly changed**")
                st.write(changed_text)
            if event.metrics.get("not_thesis_grade_reason"):
                st.warning(str(event.metrics["not_thesis_grade_reason"]))
        st.markdown(f"**Variant perception:** {idea.variant_perception}")
        if idea.market_capture:
            st.markdown("**Market capture diagnosis**")
            st.caption(f"Capture mode: {idea.market_capture.capture_mode}; category: {capture}.")
            st.write(idea.market_capture.diagnosis or idea.market_capture.explanation)
            st.caption(
                f"Price status: {idea.market_capture.price_status}; "
                f"consensus status: {idea.market_capture.consensus_status}."
            )
            for item in idea.market_capture.required_inputs[:5]:
                st.write(f"- {item}")
            if idea.market_capture.point_in_time_note:
                st.caption(idea.market_capture.point_in_time_note)
        st.markdown(f"**Catalyst:** {idea.catalyst}")
        st.markdown(f"**Strongest counter-thesis:** {idea.strongest_counter_thesis}")
        st.markdown(f"**Next source to check:** {idea.next_source_to_check or 'n/a'}")
        if idea.gate_result and idea.gate_result.research_ready_failed:
            st.markdown("**Research-ready gaps**")
            for failure in idea.gate_result.research_ready_failed:
                st.write(f"- {failure}")
        if idea.gate_result and idea.gate_result.high_conviction_failed:
            st.markdown("**High-conviction gaps**")
            for failure in idea.gate_result.high_conviction_failed:
                st.write(f"- {failure}")
        if idea.peer_readthrough:
            st.markdown("**Direct peer checks**")
            st.dataframe(
                [
                    {
                        "Peer": readthrough.peer_ticker,
                        "Evidence": readthrough.evidence_status,
                        "Relation": readthrough.relation,
                        "Price reaction": (
                            f"{readthrough.price_reaction_pct:+.1f}%"
                            if readthrough.price_reaction_pct is not None
                            else "n/a"
                        ),
                        "Provider": (
                            readthrough.sympathy_reaction.source
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "Status": (
                            readthrough.sympathy_reaction.status
                            if readthrough.sympathy_reaction else readthrough.failure_status or ""
                        ),
                        "Anchor": (
                            readthrough.sympathy_reaction.anchor_date or "pending"
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "1d / 5d / 20d": (
                            " / ".join(
                                (
                                    "pending"
                                    if readthrough.sympathy_reaction.status == "window_pending"
                                    else "n/a"
                                )
                                if readthrough.sympathy_reaction.raw_returns.get(window) is None
                                else f"{readthrough.sympathy_reaction.raw_returns[window]:+.1f}%"
                                for window in ("1d", "5d", "20d")
                            )
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "Reason": (
                            readthrough.failure_reason
                            or (readthrough.sympathy_reaction.reason if readthrough.sympathy_reaction else "")
                        ),
                        "Key changes": "; ".join(readthrough.key_metric_changes),
                        "Conclusion": readthrough.conclusion,
                    }
                    for readthrough in idea.peer_readthrough
                ],
                use_container_width=True,
                hide_index=True,
            )
        if idea.peer_metric_summary:
            summary = idea.peer_metric_summary
            st.markdown("**Peer metric readiness**")
            summary_cols = st.columns(5)
            summary_cols[0].metric("Status", summary.status)
            summary_cols[1].metric("Score", f"{summary.score}/100")
            summary_cols[2].metric("Operating peers", f"{summary.operating_metric_peers}/{summary.total_peers}")
            summary_cols[3].metric("Stale", summary.stale_metric_peers)
            summary_cols[4].metric("Price-only", summary.price_only_peers)
            st.caption(summary.summary)
            if summary.stage_impact:
                st.caption(summary.stage_impact)
            if summary.confirmations:
                st.write("Confirming peer metric evidence: " + "; ".join(summary.confirmations))
            if summary.contradictions:
                st.write("Contradicting peer metric evidence: " + "; ".join(summary.contradictions))
            for gap in summary.data_gaps:
                st.warning(gap)
            for action in summary.next_actions:
                st.write(f"- {action}")
        if idea.peer_metric_readthrough:
            st.markdown("**Peer metric read-through**")
            st.dataframe(
                [
                    {
                        "Peer": readthrough.peer_ticker,
                        "Metric family": readthrough.metric_family,
                        "Status": readthrough.status,
                        "Relation": readthrough.relation,
                        "Fiscal alignment": readthrough.fiscal_alignment,
                        "Present metrics": "; ".join(readthrough.present_metrics),
                        "Missing metrics": "; ".join(readthrough.missing_metrics),
                        "Summary": readthrough.summary,
                        "Acceptance": "; ".join(readthrough.acceptance_criteria[:2]),
                        "Falsifiers": "; ".join(readthrough.falsification_tests[:2]),
                        "Gaps": "; ".join(readthrough.data_gaps),
                    }
                    for readthrough in idea.peer_metric_readthrough
                ],
                use_container_width=True,
                hide_index=True,
            )
        if idea.global_peer_coverage:
            with st.expander("Global peer coverage"):
                st.dataframe(
                    [
                        {
                            "Peer": coverage.ticker,
                            "Status": coverage.status,
                            "Documents": len(coverage.documents),
                            "Metrics": len(coverage.metrics),
                            "Gaps": "; ".join(coverage.data_gaps),
                        }
                        for coverage in idea.global_peer_coverage
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
        if idea.citations:
            st.markdown("**Source evidence**")
            for citation in idea.citations[:3]:
                st.write(f"- [{citation.source}]({citation.url}) - {citation.snippet or citation.section}")


def render_price_move_attribution(result: ResearchResult) -> None:
    st.subheader("Price Move Attribution")
    rows = []
    waterfall_rows = []
    factor_context_rows = []
    positioning_rows = []
    liquidity_rows = []
    options_rows = []
    factor_rows = []
    audit_rows = []
    quality_rows = []
    for idea in result.ideas:
        attribution = idea.driver_attribution
        if not attribution:
            continue
        rows.append({
            "Idea": idea.title,
            "Class": attribution.classification,
            "Confidence": attribution.confidence,
            "Readiness": attribution.attribution_readiness,
            "Window": attribution.return_window or "n/a",
            "Raw": _percent(attribution.raw_return_pct),
            "Market-relative": _percent(attribution.market_relative_pct),
            "Sector-relative": _percent(attribution.sector_relative_pct),
            "Beta-adjusted": _percent(attribution.beta_adjusted_pct),
            "Peer sympathy": _percent(attribution.peer_sympathy_pct),
            "Consensus revision": _percent(attribution.consensus_revision_pct),
            "Narrative": attribution.narrative_saturation,
            "Quality score": f"{attribution.attribution_quality_score}/100",
            "Summary": "; ".join(attribution.attribution_summary[:4]),
            "Residual": attribution.residual_explanation,
        })
        for item in attribution.attribution_quality:
            quality_rows.append({
                "Idea": idea.title,
                "Area": item.area,
                "Status": item.status,
                "Score": item.score,
                "Summary": item.summary,
                "Evidence": "; ".join(item.evidence[:3]),
                "Gaps": "; ".join(item.gaps[:3]),
                "Next action": item.next_action,
                "Stage impact": item.stage_impact,
            })
        for item in attribution.classification_evidence:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Classification evidence",
                "Item": item,
            })
        for item in attribution.falsification_tests:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Falsification test",
                "Item": item,
            })
        for item in attribution.next_attribution_checks:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Next attribution check",
                "Item": item,
            })
        if attribution.waterfall:
            waterfall = attribution.waterfall
            waterfall_rows.append({
                "Idea": idea.title,
                "Component": "Residual company-specific move",
                "Type": "residual",
                "Contribution": _percent(waterfall.residual_pct),
                "Confidence": attribution.confidence,
                "Source": "Waterfall balance",
                "Why": attribution.residual_explanation,
            })
            for component in waterfall.components:
                waterfall_rows.append({
                    "Idea": idea.title,
                    "Component": component.label,
                    "Type": component.component_type,
                    "Contribution": _percent(component.contribution_pct),
                    "Confidence": component.confidence,
                    "Source": component.source,
                    "Why": component.explanation,
                })
        for exposure in attribution.factor_context:
            factor_context_rows.append({
                "Idea": idea.title,
                "Factor": exposure.factor_name,
                "Return": _percent(exposure.factor_return_pct),
                "Beta": exposure.beta if exposure.beta is not None else "n/a",
                "Contribution": _percent(exposure.contribution_pct),
                "Window": exposure.window or "n/a",
                "Confidence": exposure.confidence,
                "As of": exposure.source_as_of or "n/a",
                "Source": exposure.source,
            })
        for item in attribution.positioning_context:
            positioning_rows.append({
                "Idea": idea.title,
                "Provider": item.provider,
                "Signal": item.label,
                "Value": item.value if item.value is not None else "n/a",
                "Direction": item.direction,
                "Confidence": item.confidence,
                "As of": item.source_as_of or "n/a",
                "Summary": item.summary,
            })
        for item in attribution.liquidity_context:
            liquidity_rows.append({
                "Idea": idea.title,
                "Signal": item.label,
                "Value": item.value if item.value is not None else "n/a",
                "Direction": item.direction,
                "Confidence": item.confidence,
                "Source": item.source,
                "Summary": item.summary,
            })
        for item in attribution.options_context:
            options_rows.append({
                "Idea": idea.title,
                "Provider": item.provider,
                "Status": item.status,
                "Implied move": _percent(item.implied_move_pct),
                "IV change": _percent(item.implied_volatility_change_pct),
                "Skew": item.skew_signal,
                "Confidence": item.confidence,
                "Summary": item.summary,
            })
        for factor in attribution.factors:
            factor_rows.append({
                "Idea": idea.title,
                "Driver": factor.label,
                "Type": factor.driver_type,
                "Direction": factor.direction,
                "Confidence": factor.confidence,
                "Magnitude": _percent(factor.magnitude_pct),
                "Tier": factor.source_tier,
                "Why": factor.explanation,
                "Disconfirm if": factor.disconfirming_evidence,
            })
    if rows:
        st.markdown("#### Event Return Decomposition")
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No price move attribution is available yet. Check event-window price health and detected events.")
    if factor_rows:
        st.markdown("#### Likely Driver Factors")
        st.dataframe(factor_rows, use_container_width=True, hide_index=True)
    if quality_rows:
        st.markdown("#### Attribution Quality Checklist")
        st.dataframe(quality_rows, use_container_width=True, hide_index=True)
    if audit_rows:
        st.markdown("#### Attribution Audit Trail")
        st.dataframe(audit_rows, use_container_width=True, hide_index=True)
    if waterfall_rows:
        st.markdown("#### Attribution Waterfall")
        st.dataframe(waterfall_rows, use_container_width=True, hide_index=True)
    if factor_context_rows:
        st.markdown("#### Style / Factor Context")
        st.dataframe(factor_context_rows, use_container_width=True, hide_index=True)
    if positioning_rows or liquidity_rows or options_rows:
        st.markdown("#### Positioning, Liquidity, and Options")
        if positioning_rows:
            st.dataframe(positioning_rows, use_container_width=True, hide_index=True)
        if liquidity_rows:
            st.dataframe(liquidity_rows, use_container_width=True, hide_index=True)
        if options_rows:
            st.dataframe(options_rows, use_container_width=True, hide_index=True)
    evidence = result.external_evidence
    macro_items = [item for item in evidence.evidence if item.source_type in {"macro_factor", "china_macro"}]
    official_macro_rows = [
        {
            "Provider": item.provider,
            "Series": item.metric_name or "n/a",
            "Title": item.title,
            "As of": item.source_as_of or "n/a",
            "Release": item.release_date or "n/a",
            "Vintage": item.vintage_date or "n/a",
            "Lookahead-safe": item.lookahead_safe,
            "Frequency": item.frequency or "n/a",
            "Change": item.metric_value,
            "Unit": item.unit or "n/a",
            "Direction": item.direction,
            "Summary": item.summary,
        }
        for item in macro_items
        if item.provider not in {"World Bank macro", "IMF macro"} and item.source_type != "china_macro"
    ]
    global_macro_rows = [
        {
            "Provider": item.provider,
            "Series": item.metric_name or "n/a",
            "Title": item.title,
            "As of": item.source_as_of or "n/a",
            "Lookahead-safe": item.lookahead_safe,
            "Change": item.metric_value,
            "Unit": item.unit or "n/a",
            "Summary": item.summary,
        }
        for item in macro_items
        if item.provider in {"World Bank macro", "IMF macro"} or item.source_type == "china_macro"
    ]
    narrative_rows = [
        {
            "Provider": item.provider,
            "Signal": item.title,
            "Tier": item.source_tier,
            "Score": item.metric_value,
            "As of": item.source_as_of or "n/a",
            "Summary": item.summary,
        }
        for item in evidence.evidence
        if item.source_type == "narrative_saturation"
    ]
    analyst_context_rows = [
        {
            "Provider": item.provider,
            "Type": item.source_type,
            "Title": item.title,
            "Tier": item.source_tier,
            "As of": item.source_as_of or "n/a",
            "Language": next((tag for tag in item.tags if tag in {"en", "zh"}), "n/a"),
            "Summary": item.summary,
            "High-conviction role": "Context only" if item.disqualifies_high_conviction else "Can support",
        }
        for item in evidence.evidence
        if item.source_type in {"external_analyst_context", "management_transcript_context", "external_market_context"}
    ]
    if official_macro_rows:
        st.markdown("#### Official Macro Context")
        st.dataframe(official_macro_rows, use_container_width=True, hide_index=True)
    if global_macro_rows:
        st.markdown("#### Global / ADR Macro Context")
        st.dataframe(global_macro_rows, use_container_width=True, hide_index=True)
    if narrative_rows:
        st.markdown("#### Narrative Saturation")
        st.dataframe(narrative_rows, use_container_width=True, hide_index=True)
    render_wisburg_research_lens(result)
    if analyst_context_rows:
        st.markdown("#### External Analyst Context")
        st.caption(
            "Wisburg and similar third-party research can sharpen counter-theses and narrative awareness, "
            "but cannot independently create High-Conviction ideas."
        )
        st.dataframe(analyst_context_rows, use_container_width=True, hide_index=True)
    st.markdown("#### External Evidence Sources")
    st.caption(
        f"Status: {evidence.status}. Evidence items: {len(evidence.evidence)}. "
        "Narrative and third-party evidence are supporting context only."
    )
    if evidence.provider_statuses:
        st.dataframe(
            [
                {
                    "Provider": status.provider,
                    "Status": status.status,
                    "Official": status.official,
                    "Entitlement": status.entitlement_status,
                    "Observed": status.observed_at,
                    "Message": status.message,
                }
                for status in evidence.provider_statuses
            ],
            use_container_width=True,
            hide_index=True,
        )
    if evidence.evidence:
        st.dataframe(
            [
                {
                    "Provider": item.provider,
                    "Type": item.source_type,
                    "Title": item.title,
                    "Tier": item.source_tier,
                    "Confidence": item.confidence,
                    "As of": item.source_as_of or "n/a",
                    "Metric": item.metric_name or "n/a",
                    "Value": item.metric_value,
                    "Summary": item.summary,
                }
                for item in evidence.evidence
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_idea_scorer(result: ResearchResult) -> None:
    st.subheader("Idea Quality Scores")
    render_valuation(result)
    if not result.ideas:
        st.warning(
            "No ideas are available to score. This usually means no supported filings, "
            "sections, or financial metrics were extracted for the ticker."
        )
        return

    rows = []
    for idea in result.ideas:
        score = idea.score
        capture = idea.market_capture
        if not score:
            continue
        rows.append(
            {
                "Idea": idea.title,
                "Total": score.total,
                "Research Quality": score.research_quality,
                "Evidence Strength Score": score.evidence_strength_score,
                "Valuation Completeness": score.valuation_completeness,
                "Market-Capture Confidence": score.market_capture_confidence,
                "Actionability": score.actionability,
                "Evidence": score.evidence_strength,
                "Novelty": score.novelty,
                "Valuation / Payoff": score.valuation_payoff,
                "Thesis Specificity": score.thesis_specificity,
                "Catalyst Timing": score.catalyst_timing,
                "Market Capture": score.market_capture,
                "Reproducibility": score.reproducibility,
                "Stage": idea.stage,
                "Capture Class": capture.category if capture else "Unknown",
                "Price Reaction": (
                    f"{capture.price_reaction_pct:+.1f}%"
                    if capture and capture.price_reaction_pct is not None
                    else "n/a"
                ),
                "Consensus Revision": (
                    f"{capture.consensus_revision_pct:+.1f}%"
                    if capture and capture.consensus_revision_pct is not None
                    else "Not connected"
                ),
                "Price Status": capture.price_status if capture else "unknown",
                "Consensus Status": capture.consensus_status if capture else "unknown",
                "Capture Diagnosis": capture.diagnosis if capture else "No market-capture object attached.",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    quality_rows = _management_signal_quality_rows(result.ideas)
    if quality_rows:
        st.markdown("#### Management Signal Quality")
        st.dataframe(quality_rows, use_container_width=True, hide_index=True)

    selected = st.selectbox("Scenario simulator", [idea.title for idea in result.ideas])
    idea = next(item for item in result.ideas if item.title == selected)
    st.markdown("#### Scenario + Payoff")
    if idea.payoff_model:
        col_status, col_probability, col_rank = st.columns(3)
        completeness = idea.payoff_model.payoff_completeness
        col_status.metric(
            "Payoff completeness",
            completeness.status if completeness else idea.payoff_model.status,
        )
        col_probability.metric(
            "Probability source",
            idea.payoff_model.probability_provenance.status
            if idea.payoff_model.probability_provenance else "Uncalibrated",
        )
        col_rank.metric("Rank eligible", "Yes" if idea.payoff_model.rank_eligible else "No")
        for gap in idea.payoff_model.data_gaps:
            st.caption(gap)
        render_payoff_assumption_inputs(idea, result.valuation)
    st.caption(
        "Scenario labels describe the stock outcome. Position payoff applies the idea direction, "
        "so a lower stock exit is profitable for a Short after borrow, dividends, and transaction costs."
        if idea.direction == "Short"
        else "Scenario labels describe the stock outcome; position payoff applies dividends and transaction costs."
    )
    cols = st.columns(3)
    for idx, scenario in enumerate(idea.scenarios):
        with cols[idx]:
            st.markdown(f"**{scenario.name}**")
            st.metric("Probability", f"{scenario.probability * 100:.1f}%")
            st.metric("Entry", f"{scenario.entry_value:.2f}" if scenario.entry_value is not None else "n/a")
            st.metric("Exit value", f"{scenario.exit_value:.2f}" if scenario.exit_value is not None else "n/a")
            stock_move = _stock_outcome_return(scenario.entry_value, scenario.exit_value)
            st.metric("Stock move", f"{stock_move:+.1f}%" if stock_move is not None else "Incomplete")
            payoff = scenario.net_return_pct
            st.metric("Position payoff", f"{payoff:+.1f}%" if payoff is not None else "Incomplete")
    normalized_ev = idea.payoff_model.expected_value_pct if idea.payoff_model else expected_value(idea.scenarios)
    status = idea.probability_provenance.status if idea.probability_provenance else "Uncalibrated"
    st.metric(
        f"Illustrative expected value ({status})",
        f"{normalized_ev:+.1f}%" if normalized_ev is not None else "Unavailable",
    )


def render_payoff_assumption_inputs(idea, valuation) -> None:
    payoff = idea.payoff_model
    if not payoff:
        return
    existing = getattr(idea, "user_assumptions", {}) or {}
    scenario_by_name = {scenario.name: scenario for scenario in payoff.scenarios}
    with st.expander("Assumption inputs"):
        st.caption(
            "Use this when the scenario bridge says entry price, exit anchors, or costs are missing. "
            "Saved values are labelled user assumptions and do not override source evidence, citations, or conviction gates."
        )
        with st.form(f"payoff_assumptions_{idea.idea_id}"):
            entry_default = _existing_assumption_number(existing, "entry_price", payoff.entry_price or 0.0)
            entry_price = st.number_input(
                "Current entry price",
                min_value=0.0,
                value=entry_default,
                step=1.0,
                help="Use the price you would underwrite as the entry point for the idea.",
            )
            cols = st.columns(3)
            exits = {}
            for idx, name in enumerate(("Bear", "Base", "Bull")):
                scenario = scenario_by_name.get(name)
                default = existing.get(f"{name.lower()}_exit")
                if default is None and scenario and scenario.exit_value is not None:
                    default = scenario.exit_value
                exits[f"{name.lower()}_exit"] = cols[idx].number_input(
                    f"{name} exit anchor",
                    min_value=0.0,
                    value=float(default or 0.0),
                    step=1.0,
                    help="Exit price or fair-value anchor for this scenario.",
                )
            probability_cols = st.columns(3)
            probabilities = {}
            for idx, name in enumerate(("Bear", "Base", "Bull")):
                scenario = scenario_by_name.get(name)
                default_probability = existing.get(f"{name.lower()}_probability_pct")
                if default_probability is None and scenario:
                    default_probability = scenario.probability * 100
                probabilities[f"{name.lower()}_probability_pct"] = probability_cols[idx].number_input(
                    f"{name} probability %",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_probability if default_probability is not None else {"Bear": 25, "Base": 50, "Bull": 25}[name]),
                    step=5.0,
                    help="Illustrative probability. The app normalizes all three values to 100%.",
                )
            cost_cols = st.columns(4)
            transaction_cost_pct = cost_cols[0].number_input(
                "Transaction cost %",
                min_value=0.0,
                value=_existing_assumption_number(existing, "transaction_cost_pct", payoff.transaction_cost_pct or 0.10),
                step=0.05,
            )
            dividend_return_pct = cost_cols[1].number_input(
                "Dividend return %",
                value=_existing_assumption_number(existing, "dividend_return_pct", payoff.dividend_return_pct or 0.0),
                step=0.10,
            )
            borrow_cost_pct = cost_cols[2].number_input(
                "Borrow cost %",
                min_value=0.0,
                value=_existing_assumption_number(existing, "borrow_cost_pct", payoff.borrow_cost_pct or 0.0),
                step=0.25,
                help="Relevant for short ideas.",
            )
            hedge_ratio = cost_cols[3].number_input(
                "Hedge ratio",
                min_value=0.0,
                value=_existing_assumption_number(existing, "hedge_ratio", payoff.hedge_ratio or 0.0),
                step=0.05,
                help="Relevant for relative-value ideas.",
            )
            note = st.text_area(
                "Assumption note",
                value=str(existing.get("note") or ""),
                placeholder="Example: Base exit uses 18x next-year EPS; bear assumes margin reversion.",
            )
            preview_rows = _payoff_assumption_preview(
                idea.direction,
                entry_price,
                exits,
                transaction_cost_pct,
                dividend_return_pct,
                borrow_cost_pct,
                hedge_ratio,
                probabilities,
            )
            st.dataframe(preview_rows, hide_index=True, use_container_width=True)
            submitted = st.form_submit_button("Save Assumptions", use_container_width=True)
        if submitted:
            if sum(probabilities.values()) <= 0:
                st.error("At least one scenario probability must be greater than zero.")
                return
            payload = {
                "entry_price": entry_price,
                **exits,
                **probabilities,
                "transaction_cost_pct": transaction_cost_pct,
                "dividend_return_pct": dividend_return_pct,
                "borrow_cost_pct": borrow_cost_pct,
                "hedge_ratio": hedge_ratio,
                "note": note.strip(),
            }
            saved = ResearchStore().update_idea_assumptions(idea.idea_id, payload)
            idea.user_assumptions = payload
            probability_values = {
                name.title(): probabilities[f"{name}_probability_pct"] / 100
                for name in ("bear", "base", "bull")
            }
            idea.payoff_model = build_payoff_model(
                idea,
                valuation,
                entry_price,
                borrow_cost_pct=borrow_cost_pct,
                transaction_cost_pct=transaction_cost_pct,
                dividend_return_pct=dividend_return_pct,
                hedge_ratio=hedge_ratio or None,
                scenario_exit_values={name.replace("_exit", ""): value for name, value in exits.items()},
                scenario_probabilities=probability_values,
            )
            idea.scenarios = idea.payoff_model.scenarios
            idea.probability_provenance = idea.payoff_model.probability_provenance
            if saved is None:
                st.warning("Assumptions were captured for this session, but the idea version is not saved in SQLite yet. Run research once more to persist it.")
                st.session_state[f"user_assumptions_{idea.idea_id}"] = payload
            else:
                st.success(
                    "Assumptions saved and the illustrative payoff was recalculated. "
                    "The probabilities remain uncalibrated and excluded from EV ranking."
                )


def _existing_assumption_number(existing: dict, key: str, fallback: float) -> float:
    value = existing.get(key)
    if value in (None, ""):
        return float(fallback or 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback or 0.0)


def _payoff_assumption_preview(
    direction: str,
    entry_price: float,
    exits: dict[str, float],
    transaction_cost_pct: float,
    dividend_return_pct: float,
    borrow_cost_pct: float,
    hedge_ratio: float,
    probability_inputs: dict[str, float],
) -> list[dict[str, str]]:
    raw_probabilities = {
        key: max(0.0, probability_inputs.get(key.replace("_exit", "_probability_pct"), 0.0))
        for key in ("bear_exit", "base_exit", "bull_exit")
    }
    probability_total = sum(raw_probabilities.values())
    probabilities = {
        key: value / probability_total for key, value in raw_probabilities.items()
    } if probability_total > 0 else {"bear_exit": 0.25, "base_exit": 0.50, "bull_exit": 0.25}
    rows: list[dict[str, str]] = []
    ev_terms: list[float] = []
    for key, probability in probabilities.items():
        exit_value = exits.get(key)
        gross = _preview_directional_return(direction, entry_price, exit_value)
        net = None
        if gross is not None and direction == "Long":
            net = gross + dividend_return_pct - transaction_cost_pct
        elif gross is not None and direction == "Short":
            net = gross - borrow_cost_pct - dividend_return_pct - transaction_cost_pct
        elif gross is not None and direction == "Relative Value" and hedge_ratio > 0:
            net = gross - transaction_cost_pct
        if net is not None:
            ev_terms.append(probability * net)
        rows.append({
            "Scenario": key.replace("_exit", "").title(),
            "Probability": f"{probability * 100:.0f}%",
            "Entry": f"{entry_price:.2f}" if entry_price > 0 else "Missing",
            "Exit": f"{exit_value:.2f}" if exit_value and exit_value > 0 else "Missing",
            "Stock move": f"{_preview_stock_return(entry_price, exit_value):+.1f}%" if _preview_stock_return(entry_price, exit_value) is not None else "Incomplete",
            "Position before costs": f"{gross:+.1f}%" if gross is not None else "Incomplete",
            "Net position payoff": f"{net:+.1f}%" if net is not None else "Incomplete",
        })
    rows.append({
        "Scenario": "Illustrative EV",
        "Probability": "/".join(f"{probabilities[key] * 100:.0f}" for key in ("bear_exit", "base_exit", "bull_exit")),
        "Entry": "",
        "Exit": "",
        "Stock move": "",
        "Position before costs": "",
        "Net position payoff": f"{sum(ev_terms):+.1f}%" if len(ev_terms) == 3 else "Incomplete",
    })
    return rows


def _stock_outcome_return(entry_value: float | None, exit_value: float | None) -> float | None:
    if entry_value is None or exit_value is None or entry_value <= 0 or exit_value <= 0:
        return None
    return (exit_value - entry_value) / entry_value * 100


def _preview_stock_return(entry_price: float, exit_value: float | None) -> float | None:
    if entry_price <= 0 or not exit_value or exit_value <= 0:
        return None
    return (exit_value - entry_price) / entry_price * 100


def _preview_directional_return(
    direction: str,
    entry_price: float,
    exit_value: float | None,
) -> float | None:
    if entry_price <= 0 or not exit_value or exit_value <= 0:
        return None
    if direction == "Long":
        return (exit_value - entry_price) / entry_price * 100
    if direction == "Short":
        return (entry_price - exit_value) / entry_price * 100
    if direction == "Relative Value":
        return (exit_value - entry_price) / entry_price * 100
    return None


def render_management_sources(result: ResearchResult) -> None:
    st.subheader("Management Sources")
    package = result.management_sources
    col_status, col_docs, col_claims, col_checks = st.columns(4)
    col_status.metric("Status", package.status)
    col_docs.metric("Documents", len(package.documents))
    col_claims.metric("Claims", len(package.claims))
    col_checks.metric("Cross-checks", len(package.cross_checks))
    for gap in package.data_gaps:
        st.caption(gap)

    st.markdown("**Documents**")
    st.dataframe(
        [
            {
                "Type": doc.source_type,
                "Provider": doc.provider,
                "Tier": doc.source_tier,
                "Date": doc.event_date,
                "Title": doc.title,
                "Raw policy": doc.raw_payload_policy,
            }
            for doc in package.documents
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Management Claims**")
    st.dataframe(
        [
            {
                "Status": claim.status,
                "Type": claim.claim_type,
                "Source": claim.source_type,
                "Tier": claim.source_tier,
                "Speaker": claim.speaker or "n/a",
                "Sentiment": claim.sentiment_label or "n/a",
                "Score": claim.sentiment_score,
                "Specificity": claim.specificity_score,
                "Evasion": ", ".join(claim.evasion_terms) or "n/a",
                "Statement": claim.statement,
            }
            for claim in package.claims
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Cross-Checks**")
    st.dataframe(
        [
            {
                "Status": check.status,
                "Type": check.check_type,
                "Source": check.source_type,
                "Tier": check.source_tier,
                "Materiality": check.materiality,
                "Summary": check.summary,
            }
            for check in package.cross_checks
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Meeting / Proxy Events**")
    st.dataframe(
        [
            {
                "Type": event.event_type,
                "Status": event.status,
                "Date": event.event_date,
                "Description": event.description,
            }
            for event in package.meeting_events
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Transcript Turns**")
    st.dataframe(
        [
            {
                "Speaker": turn.speaker,
                "Section": turn.section,
                "Sentiment": turn.sentiment_label or turn.sentiment or "n/a",
                "Score": turn.sentiment_score,
                "Confidence": turn.sentiment_confidence or "n/a",
                "Specificity": turn.specificity_score,
                "Evasion": ", ".join(turn.evasion_terms) or "n/a",
                "Uncertainty": ", ".join(turn.uncertainty_terms) or "n/a",
                "Text": turn.text,
            }
            for turn in package.transcript_turns[:40]
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_thesis_monitor(result: ResearchResult) -> None:
    store = IdeaMemoryStore()
    research_store = ResearchStore()
    st.subheader("Watchlist")
    current_watchlist = research_store.list_watchlist()
    watchlisted = any(item.ticker == result.identity.ticker for item in current_watchlist)
    watch_col1, watch_col2 = st.columns(2)
    if not watchlisted:
        if watch_col1.button("Add To Default Watchlist", use_container_width=True):
            research_store.add_watchlist(result.identity.ticker)
            st.success(f"{result.identity.ticker} added to the daily snapshot watchlist.")
            st.rerun()
    else:
        watch_col1.success(f"{result.identity.ticker} is on the default watchlist.")
        if watch_col2.button("Remove From Watchlist", use_container_width=True):
            research_store.remove_watchlist(result.identity.ticker)
            st.rerun()
    watchlist = research_store.list_watchlist()
    if watchlist:
        st.dataframe(
            [
                {
                    "Ticker": item.ticker,
                    "Last Snapshot": item.last_snapshot_at or "Not yet collected",
                }
                for item in watchlist
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Daily Research Snapshot")
    daily_status = research_store.latest_daily_snapshot_status(result.identity.ticker)
    if daily_status:
        daily_cols = st.columns(4)
        daily_cols[0].metric("Overall", daily_status.get("overall_status", "Unknown"))
        daily_cols[1].metric("Consensus", daily_status.get("consensus_status", "Unknown"))
        daily_cols[2].metric("Prices", daily_status.get("price_status", "Unknown"))
        daily_cols[3].metric("Wisburg", daily_status.get("wisburg_status", "Unknown"))
        st.caption(
            f"Run date: {daily_status.get('run_date', 'Unknown')}; "
            f"alerts created: {daily_status.get('alerts_created', 0)}; "
            f"same-day Wisburg cache: {'reused' if daily_status.get('used_same_day_wisburg_cache') else 'not reused'}."
        )
        if daily_status.get("data_gaps"):
            with st.expander("Daily snapshot gaps", expanded=False):
                for gap in daily_status["data_gaps"]:
                    st.write(f"- {gap}")
    else:
        st.info(
            "No daily snapshot has run for this ticker. Add it to the watchlist and run the scheduled collector; "
            "the dashboard does not need to stay open."
        )

    st.subheader("Alert Inbox")
    alerts = research_store.list_alerts(limit=50)
    if alerts:
        st.dataframe(
            [
                {
                    "ID": alert.alert_id,
                    "Ticker": alert.ticker,
                    "Severity": alert.severity,
                    "Status": alert.status,
                    "Alert": alert.title,
                    "Created": alert.created_at,
                }
                for alert in alerts
            ],
            use_container_width=True,
            hide_index=True,
        )
        selected_alert = st.selectbox(
            "Alert action",
            alerts,
            format_func=lambda alert: f"#{alert.alert_id} {alert.ticker} - {alert.title}",
        )
        action_col1, action_col2 = st.columns(2)
        if action_col1.button("Mark Read", use_container_width=True):
            research_store.update_alert_status(selected_alert.alert_id, "read")
            st.rerun()
        if action_col2.button("Dismiss", use_container_width=True):
            research_store.update_alert_status(selected_alert.alert_id, "dismissed")
            st.rerun()
    else:
        st.info("No alerts yet. Daily consensus, price, and external-research snapshots accumulate monitor history.")

    st.subheader("Continuous Thesis Monitor")
    if not result.ideas:
        st.warning("No ideas available for monitoring.")
        return

    selected_title = st.selectbox("Idea to monitor", [idea.title for idea in result.ideas])
    idea = next(item for item in result.ideas if item.title == selected_title)
    for item in idea.monitor_items:
        with st.container(border=True):
            st.markdown(f"**{item.criterion}**")
            st.write(f"Source: {item.data_source}")
            st.write(f"Cadence: {item.cadence}")
            st.write(f"Confirm: {item.confirm_trigger}")
            st.write(f"Break: {item.break_trigger}")

    st.subheader("Outcome + Post-Mortem")
    calibration = build_calibration_report(research_store, result.identity.ticker)
    cols = st.columns(3)
    cols[0].metric("Calibration", calibration.status)
    cols[1].metric("Resolved Outcomes", calibration.sample_size)
    cols[2].metric("Threshold", calibration.minimum_sample_size)
    readiness_cols = st.columns(3)
    readiness_cols[0].metric(
        "Nearest Slice",
        calibration.nearest_calibration_slice or "n/a",
    )
    readiness_cols[1].metric("Outcomes Needed", calibration.outcomes_needed_for_calibration)
    readiness_cols[2].metric("Rank By EV", "Yes" if calibration.rank_by_ev_allowed else "No")
    st.metric("Calibration Readiness Score", f"{calibration.readiness_score}/100")
    if calibration.readiness_checks:
        with st.expander("Calibration Readiness Checklist", expanded=False):
            st.dataframe(
                [
                    {
                        "Area": item.area,
                        "Status": item.status,
                        "Score": item.score,
                        "Summary": item.summary,
                        "Evidence": "; ".join(item.evidence),
                        "Gaps": "; ".join(item.gaps),
                        "Next action": item.next_action,
                        "Stage impact": item.stage_impact,
                    }
                    for item in calibration.readiness_checks
                ],
                use_container_width=True,
                hide_index=True,
            )
    if calibration.calibration_actions:
        with st.expander("Calibration Readiness Actions", expanded=False):
            for item in calibration.calibration_actions:
                st.write(f"- {item}")
            st.markdown("**Required outcome fields**")
            for item in calibration.required_outcome_fields:
                st.write(f"- {item}")
    if calibration.slices:
        st.markdown("**Calibration Slice Scoreboard**")
        st.dataframe(
            [
                {
                    "Slice": item.signal_type,
                    "Status": item.status,
                    "Sample": item.sample_size,
                    "Needed": item.outcomes_needed_for_calibration,
                    "Rank by EV": "Yes" if item.rank_by_ev_allowed else "No",
                    "Hit rate": f"{item.hit_rate_pct:.1f}%" if item.hit_rate_pct is not None else "n/a",
                    "Brier": f"{item.brier_score:.3f}" if item.brier_score is not None else "n/a",
                    "Expected": f"{item.mean_expected_return_pct:.1f}%" if item.mean_expected_return_pct is not None else "n/a",
                    "Realized": f"{item.mean_realized_return_pct:.1f}%" if item.mean_realized_return_pct is not None else "n/a",
                    "Next action": item.next_action,
                }
                for item in calibration.slices
            ],
            use_container_width=True,
            hide_index=True,
        )
    process_cols = st.columns(3)
    process_cols[0].metric("Post-Mortems", calibration.post_mortem_count)
    process_cols[1].metric(
        "Review Coverage",
        f"{calibration.post_mortem_coverage_pct:.0f}%"
        if calibration.post_mortem_coverage_pct is not None else "n/a",
    )
    process_cols[2].metric(
        "Evidence Valid",
        f"{calibration.evidence_valid_rate_pct:.0f}%"
        if calibration.evidence_valid_rate_pct is not None else "n/a",
    )
    quality_cols = st.columns(3)
    quality_cols[0].metric("Post-Mortem Quality", calibration.post_mortem_quality_status)
    quality_cols[1].metric("Complete Reviews", calibration.complete_post_mortem_count)
    quality_cols[2].metric(
        "Complete Coverage",
        f"{calibration.complete_post_mortem_coverage_pct:.0f}%"
        if calibration.complete_post_mortem_coverage_pct is not None else "n/a",
    )
    for gap in calibration.post_mortem_quality_gaps[:3]:
        st.caption(f"Post-mortem quality gap: {gap}")
    if calibration.recurring_failure_modes or calibration.recurring_lessons or calibration.process_improvement_actions:
        with st.expander("Calibration Learning Loop", expanded=False):
            if calibration.recurring_failure_modes:
                st.markdown("**Recurring failure modes**")
                for item in calibration.recurring_failure_modes[:5]:
                    st.write(f"- {item}")
            if calibration.recurring_lessons:
                st.markdown("**Recurring lessons**")
                for item in calibration.recurring_lessons[:5]:
                    st.write(f"- {item}")
            if calibration.process_improvement_actions:
                st.markdown("**Process changes**")
                for item in calibration.process_improvement_actions[:5]:
                    st.write(f"- {item}")
    for gap in calibration.data_gaps[:3]:
        st.caption(gap)
    with st.form(f"post_mortem_{idea.idea_id}"):
        outcome_cols = st.columns(3)
        realized_return = outcome_cols[0].number_input("Realized return %", value=0.0, step=0.5)
        adverse = outcome_cols[1].number_input("Max adverse excursion %", value=0.0, step=0.5)
        favorable = outcome_cols[2].number_input("Max favorable excursion %", value=0.0, step=0.5)
        thesis_outcome = st.selectbox(
            "Thesis outcome",
            ["confirmed", "partly_confirmed", "contradicted", "inconclusive", "stopped_out", "expired"],
        )
        evidence_valid = st.selectbox(
            "Was the original evidence valid?",
            ["yes", "partly", "no", "unknown"],
        )
        closure_reason = st.text_input("Closure reason", value="")
        what_worked = st.text_area("What worked", value="", height=80)
        what_failed = st.text_area("What failed", value="", height=80)
        lessons = st.text_area("Lessons", value="", height=80)
        next_process_change = st.text_area("Process change for next time", value="", height=80)
        submitted = st.form_submit_button("Record Outcome For Calibration", use_container_width=True)
    if submitted:
        outcome = research_store.record_idea_post_mortem(
            idea.idea_id,
            {
                "horizon": idea.horizon,
                "realized_return_pct": realized_return,
                "max_adverse_excursion_pct": adverse,
                "max_favorable_excursion_pct": favorable,
                "thesis_outcome": thesis_outcome,
                "closure_reason": closure_reason,
                "evidence_valid": evidence_valid,
                "what_worked": what_worked,
                "what_failed": what_failed,
                "lessons": lessons,
                "next_process_change": next_process_change,
            },
        )
        if outcome:
            st.success("Outcome recorded. Calibration and historical analogs will include it after refresh.")
            st.rerun()
        else:
            st.error("Could not find a frozen idea version for this idea. Run research once, then retry.")

    note = st.text_area("Memory note", value="")
    if st.button("Save Idea To Memory", use_container_width=True):
        store.save_idea(result.identity.ticker, idea, note)
        st.success("Idea saved to local memory.")

    st.subheader("Idea Memory + Post-Mortem")
    records = store.list_records()
    if not records:
        st.info("No saved ideas yet.")
        return
    st.dataframe(
        [
            {
                "Ticker": record.get("ticker"),
                "Idea": record.get("title"),
                "Score": record.get("score"),
                "Capture": record.get("market_capture"),
                "EV": record.get("expected_value_pct"),
                "Status": record.get("status"),
                "Saved": record.get("saved_at"),
            }
            for record in records
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_memo(result: ResearchResult) -> None:
    st.subheader("DD Memo + Investment Committee Pack")
    st.download_button(
        "Download Markdown Memo",
        result.memo_markdown,
        file_name=f"{result.identity.ticker.lower()}_ic_memo.md",
        mime="text/markdown",
        use_container_width=True,
    )
    st.markdown(result.memo_markdown)


def render_consensus(result: ResearchResult) -> None:
    st.subheader("Consensus and Expectations")
    package = result.consensus
    if package.target:
        target = package.target
        columns = st.columns(6)
        columns[0].metric("Current Price", _money(target.current_price, target.currency))
        columns[1].metric(target.target_label, _money(_target_value(target), target.currency))
        columns[2].metric("Target Kind", target.target_kind.title())
        columns[3].metric("High / Low", f"{_plain(target.target_high)} / {_plain(target.target_low)}")
        columns[4].metric("Implied Upside", _percent(target.implied_upside_pct))
        columns[5].metric("Analysts", target.analyst_count if target.analyst_count is not None else "n/a")
        st.caption(
            f"Selected provider: {target.source or package.provider}. "
            f"Dispersion: {_percent(target.dispersion_pct)}. Observed {target.observed_at or target.as_of}; "
            f"source as-of: {target.source_as_of or target.provider_timestamp or 'Unknown'}; freshness: "
            f"{target.freshness_days if target.freshness_days is not None else 'n/a'} days."
        )
    else:
        st.info(f"Consensus status: {package.status}.")
    if package.data_gaps:
        for gap in package.data_gaps:
            st.caption(gap)

    provider_targets = package.provider_targets or ([package.target] if package.target else [])
    if provider_targets:
        st.markdown("#### Provider Targets")
        st.dataframe(
            [
                {
                    "Provider": item.source,
                    "Official": item.official,
                    "Semantic": item.target_kind,
                    "Aggregate": item.target_aggregate,
                    "Mean": item.target_mean,
                    "Median": item.target_median,
                    "High": item.target_high,
                    "Low": item.target_low,
                    "Analysts": item.analyst_count if item.analyst_count is not None else "Unknown",
                    "Observed": item.observed_at or item.as_of,
                    "Source as-of": item.source_as_of or item.provider_timestamp or "Unknown",
                }
                for item in provider_targets
            ],
            use_container_width=True,
            hide_index=True,
        )

    if package.provider_statuses:
        st.markdown("#### Provider Health")
        st.dataframe(
            [
                {
                    "Provider": item.provider,
                    "Status": item.status,
                    "Official": item.official,
                    "Entitlement": item.entitlement_status,
                    "Observed": item.observed_at,
                    "Message": item.message,
                }
                for item in package.provider_statuses
            ],
            use_container_width=True,
            hide_index=True,
        )

    if package.comparisons:
        st.markdown("#### Provider Comparison")
        st.dataframe(
            [
                {
                    "Field": item.field,
                    "Values": "; ".join(f"{key}: {value}" for key, value in item.values.items()),
                    "Spread": _percent(item.spread_pct),
                    "Interpretation": item.interpretation,
                }
                for item in package.comparisons
            ],
            use_container_width=True,
            hide_index=True,
        )

    revision_rows = [
        {
            "Metric": revision.metric,
            "Window": f"{revision.window_days}d",
            "Provider": revision.provider or "n/a",
            "Status": revision.status,
            "Start": revision.start_date or "n/a",
            "End": revision.end_date or "n/a",
            "Change": _percent(revision.change_pct),
            "Reason": revision.reason or "n/a",
        }
        for revision in package.revisions
    ]
    if revision_rows:
        st.markdown("#### Revision History")
        st.dataframe(revision_rows, use_container_width=True, hide_index=True)

    trend_rows = _recommendation_trend_rows(package)
    if trend_rows:
        st.markdown("#### Recommendation Trend")
        st.dataframe(trend_rows, use_container_width=True, hide_index=True)

    if package.recommendations:
        rec = package.recommendations
        st.markdown("#### Recommendation Mix")
        st.dataframe(
            [{
                "Strong Buy": rec.strong_buy, "Buy": rec.buy, "Hold": rec.hold,
                "Sell": rec.sell, "Strong Sell": rec.strong_sell,
                "Consensus": rec.consensus_label or "n/a",
            }],
            use_container_width=True,
            hide_index=True,
        )

    if package.estimates:
        st.markdown("#### Forward Estimates")
        st.dataframe(
            [
                {
                    "Metric": item.metric,
                    "Period": item.period_end,
                    "Type": item.period_type,
                    "Average": item.average,
                    "High": item.high,
                    "Low": item.low,
                    "Analysts": item.analyst_count,
                    "Currency": item.currency,
                    "Provider": item.source,
                    "Date precision": item.period_precision,
                    "Revisions up/down": f"{item.revisions_up if item.revisions_up is not None else 'n/a'} / {item.revisions_down if item.revisions_down is not None else 'n/a'}",
                }
                for item in package.estimates[:24]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Import consensus CSV snapshots", expanded=False):
        st.caption(
            "Use this to seed point-in-time targets, estimates, recommendations, and surprises. "
            "Rows are stored in local SQLite and immediately support 7/30/90-day revision calculations."
        )
        directory = st.text_input(
            "CSV directory",
            value=str(config.CONSENSUS_CSV_DIR),
            help="Directory containing targets.csv, estimates.csv, recommendations.csv, surprises.csv, and optional revision CSVs.",
        )
        if st.button("Create CSV Templates", use_container_width=True):
            try:
                template_result = write_consensus_csv_templates(
                    Path(directory),
                    ticker=result.identity.ticker,
                    overwrite=False,
                )
                if template_result.files_written:
                    st.success("Created templates: " + ", ".join(template_result.files_written))
                if template_result.files_existing:
                    st.info("Already existed: " + ", ".join(template_result.files_existing))
                for message in template_result.messages:
                    st.caption(message)
            except Exception as exc:
                st.error(f"Consensus CSV template creation failed: {exc}")
        tickers = st.text_input(
            "Tickers to import",
            value=result.identity.ticker,
            help="Comma or space separated. Leave blank to import every ticker found in the CSV files.",
        )
        if st.button("Import Consensus CSV To SQLite", use_container_width=True):
            ticker_list = [item.strip().upper() for item in tickers.replace(",", " ").split() if item.strip()] or None
            try:
                outcome = import_consensus_csv(Path(directory), ticker_list, ResearchStore())
                st.success(f"Imported {outcome.imported} ticker(s); skipped {outcome.skipped}.")
                cols = st.columns(3)
                cols[0].metric("Metadata observations", outcome.metadata_observations)
                cols[1].metric("Revision windows", outcome.revision_windows_available)
                cols[2].metric("Incomplete windows", outcome.revision_windows_incomplete)
                if outcome.rows_by_file:
                    st.dataframe(
                        [
                            {"File": filename, "Rows": count}
                            for filename, count in outcome.rows_by_file.items()
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                for message in outcome.messages[:8]:
                    st.caption(message)
                st.info("Run the research workflow again to refresh the consensus panel from the imported snapshots.")
            except Exception as exc:
                st.error(f"Consensus CSV import failed: {exc}")

    bridge = result.expectations_bridge
    st.markdown("#### Expectations Bridge")
    st.write(bridge.headline)
    if bridge.event_audits:
        st.dataframe(
            [
                {
                    "Event": item.event_label,
                    "Form": item.form or "Unknown",
                    "Accession": item.accession or "Unknown",
                    "Filed": item.filing_date or "Unknown",
                    "Reporting period": item.reporting_period or "Unknown",
                    "Actual metrics checked": "; ".join(item.actual_metrics_checked) or "None",
                    "Pre-event snapshots": item.eligible_pre_event_snapshots,
                    "Post-event snapshots": item.eligible_post_event_snapshots,
                    "Status": item.status,
                    "Exact reason": item.reason,
                }
                for item in bridge.event_audits[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if bridge.comparisons:
        st.dataframe(
            [
                {
                    "Metric": item.metric,
                    "Period": item.period_end,
                    "Expected": item.expected,
                    "Actual": item.actual,
                    "Surprise": _percent(item.surprise_pct),
                    "Post-event revision": _percent(item.post_event_revision_pct),
                    "Interpretation": item.interpretation,
                }
                for item in bridge.comparisons
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.caption(bridge.point_in_time_note)


def render_valuation(result: ResearchResult) -> None:
    valuation = result.valuation
    st.markdown("#### Valuation Triangulation")
    if valuation.status != "Available":
        st.info(
            f"{valuation.template}: {valuation.status}. "
            f"{' '.join(valuation.missing_data)}"
        )
        return
    cols = st.columns(4)
    cols[0].metric("Template", valuation.template)
    cols[1].metric("Weighted Value", _money(valuation.probability_weighted_value, valuation.currency))
    cols[2].metric("Expected Return", _percent(valuation.expected_return_pct))
    cols[3].metric("Vs Consensus", _percent(valuation.disagreement_pct))
    st.dataframe(
        [
            {
                "Case": case.name,
                "Probability": f"{case.probability:.0%}",
                "Fair Value": _money(case.fair_value, valuation.currency),
                "Method": case.method,
                "Assumptions": "; ".join(case.assumptions),
            }
            for case in valuation.cases
        ],
        use_container_width=True,
        hide_index=True,
    )
    if valuation.normalization_notes:
        st.caption(" ".join(valuation.normalization_notes))
    if valuation.missing_data:
        st.caption("Unavailable methods: " + " ".join(valuation.missing_data))
    st.caption(f"Confidence: {valuation.confidence}. {valuation.methodology}")


def _plain(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def _money(value: float | None, currency: str) -> str:
    return "n/a" if value is None else f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _target_value(target) -> float | None:
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _recommendation_trend_rows(package) -> list[dict]:
    by_period: dict[str, dict] = {}
    for observation in package.observations:
        if not observation.field.startswith("recommendation_trend_"):
            continue
        period = observation.source_as_of or observation.observed_at or "Unknown"
        row = by_period.setdefault(period, {"Period": period, "Provider": observation.provider})
        label = observation.field.replace("recommendation_trend_", "").replace("_", " ").title()
        row[label] = (
            observation.value_text
            if observation.value_text is not None
            else observation.value_numeric
        )
        if observation.analyst_count is not None:
            row["Analysts"] = observation.analyst_count
    return [by_period[key] for key in sorted(by_period.keys(), reverse=True)]


def _management_signal_quality_rows(ideas) -> list[dict]:
    rows = []
    for idea in ideas:
        event = idea.source_events[0] if idea.source_events else None
        if not event:
            continue
        metrics = event.metrics or {}
        if not (
            metrics.get("management_claim_id")
            or metrics.get("meeting_event_id")
            or metrics.get("sentiment_label")
        ):
            continue
        rows.append({
            "Idea": idea.title,
            "Signal": event.category,
            "Sentiment": metrics.get("sentiment_label") or metrics.get("cross_check_status") or "n/a",
            "Score": metrics.get("sentiment_score"),
            "Specificity": metrics.get("specificity_score"),
            "Cross-check": metrics.get("cross_check_status") or "n/a",
            "Evasion terms": ", ".join(metrics.get("evasion_terms") or []) or "n/a",
            "Uncertainty terms": ", ".join(metrics.get("uncertainty_terms") or []) or "n/a",
        })
    return rows


if __name__ == "__main__":
    main()
