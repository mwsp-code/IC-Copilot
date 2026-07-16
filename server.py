from __future__ import annotations

import argparse
import json
import socket
import socketserver
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from equity_research import config
from equity_research.budget import available_budget_modes, budget_allows_paid_data, load_budget_mode_definitions
from equity_research.consensus_import import import_consensus_csv
from equity_research.global_coverage import (
    build_canonical_metric_ontology,
    build_metric_resolution_audit,
    coverage_case_for,
    source_coverage_matrix_for,
)
from equity_research.global_peers import GlobalPeerFinancialProvider
from equity_research.management_sources import (
    build_management_source_package,
    transcript_document_from_payload,
)
from equity_research.models import CompanyIdentity, ConsensusPackage, ResearchSourcePlan, ResearchSourceRequest, ResearchSourceOutcome
from equity_research.pipeline import run_us_equity_research
from equity_research.peers import peer_universe_for
from equity_research.external_evidence import external_evidence_stack_from_config
from equity_research.historical_references import build_historical_references_for_ticker
from equity_research.local_secrets import (
    LocalSecretsManager,
    save_validated_keys,
    validate_provider_keys,
)
from equity_research.llm_vault import (
    build_llm_profile,
    delete_llm_profile_with_secret,
    list_llm_presets,
    profile_to_provider,
    save_llm_profile_with_secret,
    test_llm_profile,
)
from equity_research.network_diagnostics import run_network_diagnostics
from equity_research.news_intelligence import (
    build_corroboration_results,
    claim_from_observation,
    enrich_source_plan_with_news,
    news_claim_from_payload,
    observation_from_payload,
    source_needs_for_claim,
)
from equity_research.providers import StooqPriceClient, build_consensus_provider
from equity_research.research_store import ResearchStore
from equity_research.research_profiles import event_identifier, research_profiles
from equity_research.rigor import build_calibration_report
from equity_research.sample_data import demo_result
from equity_research.sec_client import SecClient
from equity_research.storytelling import demo_cases
from equity_research.thesis_synthesis import UnavailableLlmProvider, provider_from_config


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>US Equity Research Radar</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --ink: #20242a;
      --muted: #667085;
      --line: #d8dde6;
      --panel: #ffffff;
      --accent: #116a59;
      --accent-2: #244c8f;
      --warn: #9a5b00;
      --bad: #9b1c31;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { font-size: 20px; margin: 0; font-weight: 700; }
    h2 { font-size: 18px; margin: 22px 0 12px; }
    h3 { font-size: 15px; margin: 0 0 8px; }
    .controls {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    input, select, textarea {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font-size: 14px;
      background: #fff;
    }
    textarea {
      height: auto;
      min-height: 72px;
      padding: 9px 10px;
      resize: vertical;
    }
    button {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 12px;
      background: #fff;
      color: var(--ink);
      cursor: pointer;
      font-weight: 600;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    details.sources { position: relative; }
    details.sources summary {
      list-style: none;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 12px;
      background: #fff;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
    }
    .source-menu {
      position: absolute;
      right: 0;
      top: 44px;
      width: min(360px, calc(100vw - 32px));
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 10px 30px rgba(32, 36, 42, .14);
      display: grid;
      gap: 9px;
      z-index: 9;
    }
    .source-menu label { color: var(--muted); font-size: 12px; display: grid; gap: 4px; }
    .source-menu label.check { grid-template-columns: 18px 1fr; align-items: center; font-size: 13px; }
    .source-menu input[type="checkbox"] { width: 16px; height: 16px; }
    main { padding: 20px 28px 36px; max-width: 1440px; margin: 0 auto; }
    .status {
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 12px;
      border-radius: 8px;
      margin-bottom: 14px;
      color: var(--muted);
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }
    .metric, .idea, .monitor, .source {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .metric span { color: var(--muted); font-size: 12px; display: block; }
    .metric strong { font-size: 20px; display: block; margin-top: 6px; }
    .tabs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); }
    .tab {
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
      border-bottom-color: transparent;
    }
    .tab.active { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
    .panel { display: none; padding-top: 14px; }
    .panel.active { display: block; }
    .table-scroll {
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      background: var(--panel);
    }
    table {
      width: 100%;
      min-width: 640px;
      border-collapse: collapse;
      background: var(--panel);
    }
    th, td {
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 9px 10px;
      font-size: 13px;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 700; background: #fbfcfe; }
    tr:last-child td { border-bottom: 0; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .demo-gallery {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }
    .demo-card, .story-card, .judge-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .demo-card h3, .story-card h3, .judge-card h3 { margin: 0 0 6px; font-size: 15px; }
    .progress-strip {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
      gap: 8px;
      margin: 12px 0 16px;
    }
    .progress-stage {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      min-height: 72px;
    }
    .progress-stage strong { display: block; font-size: 12px; }
    .progress-stage span { color: var(--muted); font-size: 12px; }
    .story-grid, .judge-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .idea { margin-bottom: 12px; }
    .idea-head { display: flex; justify-content: space-between; gap: 12px; }
    .outcome-form {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 9px;
      margin-top: 10px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
    }
    .outcome-form label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .outcome-form input, .outcome-form select, .outcome-form textarea { width: 100%; }
    .outcome-form .full, .outcome-form .outcome-status { grid-column: 1 / -1; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 2px 9px;
      background: #e8f3ef;
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .pill.warn { background: #fff4df; color: var(--warn); }
    .pill.bad { background: #fde8ec; color: var(--bad); }
    pre {
      white-space: pre-wrap;
      background: #101820;
      color: #f2f5f8;
      padding: 16px;
      border-radius: 8px;
      overflow: auto;
      line-height: 1.45;
    }
    .muted { color: var(--muted); }
    @media (max-width: 900px) {
      header { align-items: stretch; flex-direction: column; }
      .summary, .grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      header { padding: 14px 16px; }
      main { padding: 16px; }
      .controls { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
      .controls #ticker, .controls details { grid-column: 1 / -1; width: 100%; }
      .controls details summary { width: 100%; }
      input { min-width: 0; width: 100%; }
      button { padding: 0 10px; }
      .metric strong { font-size: 18px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>US Equity Research Radar</h1>
    <div class="controls">
      <input id="ticker" value="AAPL" aria-label="Ticker" />
      <select id="research-profile" aria-label="Research profile">
        <option value="fast_screening">Fast Screening</option>
        <option value="adaptive_ic" selected>Adaptive IC Research</option>
        <option value="deep_initiation">Deep Initiation</option>
      </select>
      <select id="event-investigation" aria-label="Event investigation"><option value="">Investigate event...</option></select>
      <button id="investigate-event">Investigate This Event</button>
      <details class="sources">
        <summary>Data Sources</summary>
        <div class="source-menu">
          <label>Alpha Vantage API key<input id="alpha-key" type="password" autocomplete="off" /></label>
          <label>Finnhub API key<input id="finnhub-key" type="password" autocomplete="off" /></label>
          <label>FMP API key<input id="fmp-key" type="password" autocomplete="off" /></label>
          <label>Tiingo API key<input id="tiingo-key" type="password" autocomplete="off" /></label>
          <label>EODHD API key<input id="eodhd-key" type="password" autocomplete="off" title="Recent adjusted EOD prices, event windows, peer reactions, and market-implied expectations" /></label>
          <label>FRED API key<input id="fred-key" type="password" autocomplete="off" /></label>
          <label>BEA API key<input id="bea-key" type="password" autocomplete="off" /></label>
          <label>Census API key<input id="census-key" type="password" autocomplete="off" /></label>
          <label>Wisburg API key<input id="wisburg-key" type="password" autocomplete="off" /></label>
          <label>SEC user agent<input id="sec-user-agent" autocomplete="off" /></label>
          <label>Budget mode<select id="budget-mode"><option>Free</option><option selected>Lean</option><option>Stable</option><option>Premium</option></select></label>
          <label>Primary LLM<select id="llm-primary"></select></label>
          <label>Secondary reader<select id="llm-secondary"></select></label>
          <label>Secondary minimum stage<select id="llm-secondary-min-stage"><option>Research-Ready</option><option>High-Conviction</option></select></label>
          <label>Language policy<select id="llm-language-policy"><option value="bilingual_audit">Bilingual audit</option><option value="english_only">English only</option></select></label>
          <label class="check"><input id="enable-llm" type="checkbox" />LLM thesis synthesis</label>
          <label class="check"><input id="enable-secondary-llm" type="checkbox" checked />Secondary review for Research-Ready+</label>
          <details>
            <summary>LLM Provider Vault</summary>
            <label>Provider preset<select id="llm-profile-preset"></select></label>
            <label>Profile name<input id="llm-profile-name" value="DeepSeek primary" autocomplete="off" /></label>
            <label>Model<input id="llm-profile-model" value="deepseek-v4-pro" autocomplete="off" /></label>
            <label>Base URL<input id="llm-profile-base-url" value="https://api.deepseek.com" autocomplete="off" /></label>
            <label>API key<input id="llm-profile-api-key" type="password" autocomplete="off" /></label>
            <div class="grid">
              <button type="button" id="save-llm-profile">Save LLM Profile</button>
              <button type="button" id="test-llm-profile">Test Profile</button>
            </div>
            <button type="button" id="delete-llm-profile">Delete Selected LLM Profile</button>
            <div id="llm-profile-status" class="muted">Loading LLM profiles...</div>
          </details>
          <label class="check"><input id="enable-nasdaq" type="checkbox" />Nasdaq estimates (unofficial)</label>
          <label class="check"><input id="enable-tradingview" type="checkbox" />TradingView targets (unofficial)</label>
          <label class="check"><input id="enable-default-macro" type="checkbox" checked />Default official macro sources</label>
          <label class="check"><input id="global-macro-mode" type="checkbox" />Global macro mode</label>
          <label class="check"><input id="enable-gdelt" type="checkbox" />GDELT narrative saturation</label>
          <label class="check"><input id="enable-wisburg" type="checkbox" />Wisburg external research</label>
          <label class="check"><input id="refresh-macro-cache" type="checkbox" />Refresh macro cache</label>
          <div class="grid">
            <button type="button" id="test-keys">Test Keys</button>
            <button type="button" id="save-keys">Save Valid Keys</button>
          </div>
          <button type="button" id="clear-keys">Clear Saved Keys</button>
          <div id="secret-status" class="muted">Checking saved key status...</div>
        </div>
      </details>
      <button class="primary" id="run">Run Research</button>
      <button id="demo">Demo</button>
    </div>
  </header>
  <main>
    <section id="demo-gallery" class="demo-gallery"></section>
    <div id="status" class="status">Enter a US ticker and run the workflow.</div>
    <section id="summary" class="summary"></section>
    <nav class="tabs" id="tabs"></nav>
    <section id="content"></section>
  </main>
  <script>
    const tabs = [
      "IC Story",
      "Evidence Trail",
      "Causal Bridge",
      "Market & Expectations",
      "Work Orders",
      "Raw Data"
    ];
    let activeTab = tabs[0];
    let current = null;
    let llmProfiles = [];
    let llmPresets = [];
    let llmSelection = {};

    document.getElementById("run").addEventListener("click", () => load(false, false));
    document.getElementById("investigate-event").addEventListener("click", () => load(false, true));
    document.getElementById("demo").addEventListener("click", () => load(true));
    document.getElementById("test-keys").addEventListener("click", testKeys);
    document.getElementById("save-keys").addEventListener("click", saveKeys);
    document.getElementById("clear-keys").addEventListener("click", clearKeys);
    document.getElementById("save-llm-profile").addEventListener("click", saveLlmProfile);
    document.getElementById("test-llm-profile").addEventListener("click", testLlmProfile);
    document.getElementById("delete-llm-profile").addEventListener("click", deleteLlmProfile);
    loadSecretStatus();
    loadLlmProfiles();
    loadBudgetModes();
    loadDemoCases();

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }

    async function load(demo, investigateEvent = false) {
      const ticker = document.getElementById("ticker").value.trim().toUpperCase() || "AAPL";
      if (investigateEvent && !document.getElementById("event-investigation").value) {
        setStatus("Select a detected event before running an event investigation.");
        return;
      }
      setStatus(demo ? "Loading demo workflow..." : `Running live workflow for ${ticker}...`);
      try {
        const response = demo
          ? await fetch(`/api/demo?ticker=${ticker}`)
          : await fetch("/api/research", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({
                ticker,
                research_profile: investigateEvent ? "investigate_event" : document.getElementById("research-profile").value,
                investigate_event_id: investigateEvent ? document.getElementById("event-investigation").value : "",
                alpha_vantage_key: document.getElementById("alpha-key").value,
                finnhub_key: document.getElementById("finnhub-key").value,
                fmp_key: document.getElementById("fmp-key").value,
                tiingo_key: document.getElementById("tiingo-key").value,
                eodhd_key: document.getElementById("eodhd-key").value,
                fred_key: document.getElementById("fred-key").value,
                bea_key: document.getElementById("bea-key").value,
                census_key: document.getElementById("census-key").value,
                wisburg_key: document.getElementById("wisburg-key").value,
                sec_user_agent: document.getElementById("sec-user-agent").value,
                budget_mode: document.getElementById("budget-mode").value,
                enable_llm: document.getElementById("enable-llm").checked,
                primary_llm_profile_id: document.getElementById("llm-primary").value,
                secondary_llm_profile_id: document.getElementById("llm-secondary").value,
                enable_secondary_llm: document.getElementById("enable-secondary-llm").checked,
                secondary_llm_min_stage: document.getElementById("llm-secondary-min-stage").value,
                llm_language_policy: document.getElementById("llm-language-policy").value,
                enable_nasdaq: document.getElementById("enable-nasdaq").checked,
                enable_tradingview: document.getElementById("enable-tradingview").checked,
                enable_default_macro: document.getElementById("enable-default-macro").checked,
                global_macro_mode: document.getElementById("global-macro-mode").checked,
                enable_gdelt: document.getElementById("enable-gdelt").checked,
                enable_wisburg: document.getElementById("enable-wisburg").checked,
                refresh_macro_cache: document.getElementById("refresh-macro-cache").checked,
                fallback: true
              })
            });
        const payload = await parseJsonResponse(response);
        if (!response.ok && !payload.result) throw new Error(payload.error || "Research failed");
        current = payload.result;
        await hydrateDailySnapshotContext();
        render(payload.warning || `Loaded ${current.identity.ticker}.`);
      } catch (error) {
        setStatus(error.message);
      }
    }

    async function loadDemoCases() {
      try {
        const response = await fetch("/api/demo-cases");
        const payload = await parseJsonResponse(response);
        const target = document.getElementById("demo-gallery");
        const cases = payload.demo_cases || [];
        target.innerHTML = cases.map(item => `
          <article class="demo-card">
            <h3>${esc(item.title)}</h3>
            <p class="muted">${esc(item.lesson)}</p>
            <p><span class="pill">${esc(item.badge || "No API keys")}</span> <span class="pill warn">${esc(item.expected_runtime || "Instant")}</span> <span class="pill">${esc(item.content_version || "Current")}</span></p>
            <p class="muted">${esc(item.research_profile || "Current profile")} · ${esc(item.budget_mode || "Current budget")}</p>
            <p class="muted">${esc((item.enabled_layers || []).join(" · "))}</p>
            <p class="muted">Refreshed ${esc(item.refreshed_at || "Unknown")}</p>
            <button type="button" data-demo-ticker="${esc(item.ticker)}">Load demo</button>
          </article>
        `).join("");
        target.querySelectorAll("[data-demo-ticker]").forEach(button => {
          button.addEventListener("click", () => {
            document.getElementById("ticker").value = button.dataset.demoTicker || "AAPL";
            load(true);
          });
        });
      } catch (error) {
        document.getElementById("demo-gallery").innerHTML = "";
      }
    }

    async function hydrateDailySnapshotContext() {
      if (!current || !current.identity || !current.identity.ticker) return;
      const ticker = encodeURIComponent(current.identity.ticker);
      try {
        const [snapshotResponse, deltaResponse] = await Promise.all([
          fetch(`/api/snapshot-status?ticker=${ticker}`),
          fetch(`/api/wisburg-delta?ticker=${ticker}`)
        ]);
        if (snapshotResponse.ok) {
          const payload = await snapshotResponse.json();
          current.daily_snapshot_status = payload.snapshot_status;
        }
        if (deltaResponse.ok) {
          const payload = await deltaResponse.json();
          current.wisburg_snapshot_delta = payload.wisburg_delta;
        }
      } catch (_error) {
        current.daily_snapshot_status = current.daily_snapshot_status || null;
        current.wisburg_snapshot_delta = current.wisburg_snapshot_delta || null;
      }
    }

    async function loadBudgetModes() {
      try {
        const response = await fetch("/api/budget-modes");
        const payload = await parseJsonResponse(response);
        const modes = payload.modes || ["Free", "Lean", "Stable", "Premium"];
        const select = document.getElementById("budget-mode");
        const currentValue = select.value || "Lean";
        select.innerHTML = modes.map(mode => `<option ${mode === currentValue ? "selected" : ""}>${esc(mode)}</option>`).join("");
        if (!modes.includes(currentValue) && modes.includes("Lean")) select.value = "Lean";
      } catch (error) {
        // Static fallback remains usable.
      }
    }

    function enteredSecrets() {
      return {
        ALPHAVANTAGE_API_KEY: document.getElementById("alpha-key").value,
        FINNHUB_API_KEY: document.getElementById("finnhub-key").value,
        FMP_API_KEY: document.getElementById("fmp-key").value,
        FRED_API_KEY: document.getElementById("fred-key").value,
        BEA_API_KEY: document.getElementById("bea-key").value,
        CENSUS_API_KEY: document.getElementById("census-key").value,
        WISBURG_API_KEY: document.getElementById("wisburg-key").value,
        TIINGO_API_KEY: document.getElementById("tiingo-key").value,
        EODHD_API_KEY: document.getElementById("eodhd-key").value,
        SEC_USER_AGENT: document.getElementById("sec-user-agent").value
      };
    }

    async function loadLlmProfiles() {
      try {
        const response = await fetch("/api/llm-profiles");
        const payload = await parseJsonResponse(response);
        llmProfiles = payload.profiles || [];
        llmPresets = payload.presets || [];
        llmSelection = payload.selection || {};
        renderLlmControls();
      } catch (error) {
        document.getElementById("llm-profile-status").textContent = error.message;
      }
    }

    function renderLlmControls() {
      const presetSelect = document.getElementById("llm-profile-preset");
      presetSelect.innerHTML = llmPresets.map(preset => `<option value="${esc(preset.preset_id)}">${esc(preset.label)}</option>`).join("");
      presetSelect.value = "deepseek";
      presetSelect.onchange = () => {
        const preset = llmPresets.find(item => item.preset_id === presetSelect.value) || {};
        document.getElementById("llm-profile-name").value = `${preset.label || presetSelect.value} primary`;
        document.getElementById("llm-profile-model").value = preset.default_model || "";
        document.getElementById("llm-profile-base-url").value = preset.default_base_url || "";
      };
      const options = [`<option value="">None</option>`].concat(llmProfiles.map(profile =>
        `<option value="${esc(profile.profile_id)}">${esc(profile.display_name)} (${esc(profile.provider_preset)}, ${esc(profile.model)})${profile.key_configured ? " - key saved" : " - no key"}</option>`
      )).join("");
      document.getElementById("llm-primary").innerHTML = options;
      document.getElementById("llm-secondary").innerHTML = options;
      const fallbackPrimary = (llmProfiles.find(profile => profile.key_configured) || llmProfiles[0] || {}).profile_id || "";
      document.getElementById("llm-primary").value = llmSelection.primary_profile_id || fallbackPrimary;
      document.getElementById("llm-secondary").value = llmSelection.secondary_profile_id || "";
      document.getElementById("enable-secondary-llm").checked = llmSelection.enable_secondary !== false;
      document.getElementById("llm-secondary-min-stage").value = llmSelection.secondary_min_stage || "Research-Ready";
      document.getElementById("llm-language-policy").value = llmSelection.language_policy || "bilingual_audit";
      document.getElementById("llm-profile-status").textContent = `${llmProfiles.length} saved LLM profile(s).`;
    }

    function llmProfilePayload() {
      return {
        display_name: document.getElementById("llm-profile-name").value,
        provider_preset: document.getElementById("llm-profile-preset").value,
        model: document.getElementById("llm-profile-model").value,
        base_url: document.getElementById("llm-profile-base-url").value,
        api_key: document.getElementById("llm-profile-api-key").value
      };
    }

    async function saveLlmProfile() {
      const response = await fetch("/api/llm-profiles", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(llmProfilePayload())
      });
      const payload = await parseJsonResponse(response);
      if (!response.ok) throw new Error(payload.error || "Could not save profile");
      document.getElementById("llm-profile-api-key").value = "";
      await loadLlmProfiles();
    }

    async function testLlmProfile() {
      const response = await fetch("/api/llm-profiles/test", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(llmProfilePayload())
      });
      const payload = await parseJsonResponse(response);
      document.getElementById("llm-profile-status").textContent = `${payload.status?.status || "unknown"}: ${payload.status?.message || ""}`;
    }

    async function deleteLlmProfile() {
      const profileId = document.getElementById("llm-primary").value || document.getElementById("llm-secondary").value;
      if (!profileId) return;
      await fetch(`/api/llm-profiles/${encodeURIComponent(profileId)}`, {method: "DELETE"});
      await loadLlmProfiles();
    }

    async function loadSecretStatus() {
      try {
        const response = await fetch("/api/local-secrets/status");
        const payload = await parseJsonResponse(response);
        renderSecretStatus(payload.status || []);
      } catch (error) {
        document.getElementById("secret-status").textContent = error.message;
      }
    }

    async function testKeys() {
      setStatus("Testing provider keys...");
      const response = await fetch("/api/local-secrets/test", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({keys: enteredSecrets()})
      });
      const payload = await parseJsonResponse(response);
      document.getElementById("secret-status").textContent = (payload.results || [])
        .map(item => `${item.label}: ${item.status}`)
        .join(" | ") || "No keys entered.";
      setStatus("Key test complete.");
    }

    async function saveKeys() {
      setStatus("Testing and saving valid keys...");
      const response = await fetch("/api/local-secrets/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({keys: enteredSecrets()})
      });
      const payload = await parseJsonResponse(response);
      document.getElementById("secret-status").textContent =
        `Saved ${payload.saved.length} key(s); skipped ${payload.skipped.length}.`;
      await loadSecretStatus();
      setStatus("Saved valid local keys. Restart if a running workflow captured old settings.");
    }

    async function clearKeys() {
      const response = await fetch("/api/local-secrets", { method: "DELETE" });
      await parseJsonResponse(response);
      await loadSecretStatus();
      setStatus("Cleared saved local keys.");
    }

    function renderSecretStatus(rows) {
      const configured = rows.filter(item => item.configured && item.key !== "SEC_USER_AGENT").length;
      const backend = rows.length
        ? (rows[0].backend_available ? `Secret storage ready (${rows[0].backend || "local"})` : "Secret storage unavailable")
        : "No status";
      document.getElementById("secret-status").textContent = `${configured} saved provider key(s). ${backend}.`;
    }

    async function parseJsonResponse(response) {
      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();
      if (!contentType.includes("application/json")) {
        const preview = text.trim().slice(0, 80);
        throw new Error(`Research API returned ${contentType || "unknown content"} instead of JSON. You may be connected to the Streamlit server or a stale port. Response starts: ${preview}`);
      }
      try {
        return JSON.parse(text);
      } catch (error) {
        throw new Error(`Research API returned invalid JSON: ${error.message}`);
      }
    }

    function setStatus(message) {
      document.getElementById("status").textContent = message;
    }

    function render(message) {
      setStatus(message);
      renderEventInvestigationOptions();
      renderSummary();
      renderTabs();
      renderContent();
    }

    function renderEventInvestigationOptions() {
      const select = document.getElementById("event-investigation");
      const rows = (current && current.events ? current.events : []).slice(0, 20);
      select.innerHTML = '<option value="">Investigate event...</option>' + rows.map(item => {
        const id = (item.metrics || {}).event_id || "";
        const label = `${item.event_date || "Date unknown"} | ${item.title || "Untitled event"}`;
        return `<option value="${esc(id)}">${esc(label)}</option>`;
      }).join("");
    }

    function renderSummary() {
      const identity = current.identity;
      const top = current.ideas[0] || {};
      const score = top.score ? `${top.score.total}/100` : "n/a";
      const capture = top.market_capture ? top.market_capture.category : "Unknown";
      const values = [
        ["Company", identity.name],
        ["Ticker / CIK", `${identity.ticker} / ${identity.cik}`],
        ["Top Idea Score", score],
        ["Market Capture", capture]
      ];
      document.getElementById("summary").innerHTML = values.map(([label, value]) => `
        <div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>
      `).join("");
    }

    function renderTabs() {
      document.getElementById("tabs").innerHTML = tabs.map(tab => `
        <button class="tab ${tab === activeTab ? "active" : ""}" data-tab="${esc(tab)}">${esc(tab)}</button>
      `).join("");
      document.querySelectorAll(".tab").forEach(button => {
        button.addEventListener("click", () => {
          activeTab = button.dataset.tab;
          renderTabs();
          renderContent();
        });
      });
    }

    function renderContent() {
      const target = document.getElementById("content");
      if (activeTab === "IC Story") target.innerHTML = icCopilot();
      if (activeTab === "Evidence Trail") target.innerHTML = researchRadar() + managementSources();
      if (activeTab === "Causal Bridge") target.innerHTML = decisionModels() + ideaFactory() + ideaScorer();
      if (activeTab === "Market & Expectations") target.innerHTML = earningsSurprisePanel() + marketImpliedPanel() + recentMarketContextPanel() + priceMoveAttribution();
      if (activeTab === "Work Orders") target.innerHTML = evidenceClosurePanel() + thesisMonitor();
      if (activeTab === "Raw Data") target.innerHTML = memoPack();
      if (activeTab === "Causal Bridge") bindOutcomeForms();
      if (activeTab === "Work Orders") bindMonitorActions();
      if (activeTab === "Market & Expectations") bindMarketImpliedActions();
    }

    function table(rows, columns) {
      if (!rows.length) return `<p class="muted">No rows.</p>`;
      return `<div class="table-scroll"><table><thead><tr>${columns.map(col => `<th>${esc(col[0])}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(row => `<tr>${columns.map(col => `<td>${esc(row[col[1]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }

    function wisburgLensPanel() {
      const lens = current.wisburg_lens || {};
      const delta = current.wisburg_snapshot_delta || {};
      const narrative = lens.narrative_score || {};
      const debate = lens.debate_map || {};
      const coverage = lens.coverage_audit || {};
      const themes = (lens.themes || []).slice(0, 8).map(theme => ({
        theme: theme.label,
        stance: theme.stance,
        driver: theme.driver,
        evidence: theme.evidence_count,
        language: (theme.source_language_mix || []).join(", ") || "n/a",
        confidence: theme.confidence,
        summary: theme.summary
      }));
      const suggestions = (lens.source_suggestions || []).slice(0, 8).map(item => ({
        priority: item.priority,
        type: item.source_type,
        title: item.title,
        reason: item.reason_to_inspect,
        expected: item.expected_evidence_type,
        checks: item.confirms_or_disproves
      }));
      const excerpts = (lens.excerpts || []).slice(0, 10).map(item => ({
        title: item.title,
        category: item.category,
        language: item.source_language,
        asof: item.source_as_of || "n/a",
        themes: (item.theme_tags || []).join(", "),
        target: item.mentions_target_or_rating ? item.non_consensus_label : "n/a",
        excerpt: item.original_excerpt,
        summary: item.translated_summary || item.generated_summary
      }));
      const caveats = (lens.caveats || []).map(item => ({ item }));
      const entitlementRows = (coverage.tools || []).map(item => ({
        tool: item.tool_name,
        category: item.source_category,
        entitlement: item.status,
        queries: item.query_count,
        items: item.item_count,
        details: item.detail_success_count,
        message: item.message
      }));
      const reports = (lens.reports || []).slice(0, 20).map(item => ({
        report: item.title,
        category: item.category,
        publisher: item.publisher,
        published: item.published_at || "Unknown",
        language: item.source_language,
        detail: item.detail_status,
        scope: item.content_scope,
        tier: item.source_tier
      }));
      const revisions = (lens.revisions || []).slice(0, 12).map(item => ({
        asof: item.source_as_of || "Unknown",
        type: item.revision_type,
        metric: item.metric,
        direction: item.direction,
        prior: item.previous_value ?? "Unknown",
        current: item.current_value ?? "Unknown",
        change: item.change_pct ?? "Unknown",
        period: item.fiscal_period || "Unknown",
        eligibility: item.eligibility,
        statement: item.statement
      }));
      const claims = (lens.structured_claims || []).slice(0, 20).map(item => ({
        claim: item.statement,
        type: item.claim_type,
        driver: item.driver,
        metric: item.metric || "Unknown",
        period: item.fiscal_period || "Unknown",
        tier: item.source_tier,
        check: item.corroboration_status,
        primary: (item.primary_evidence_ids || []).length,
        stage: item.allowed_stage
      }));
      const tasks = (lens.research_tasks || []).slice(0, 16).map(item => ({
        priority: item.priority,
        type: item.source_type,
        action: item.action,
        expected: item.expected_evidence,
        checks: item.confirms_or_disproves,
        status: item.status
      }));
      return `<h3>Outside Analyst Debate</h3>
        <p class="muted">Wisburg is used for outside-analyst debate, narrative crowding, and source suggestions. It cannot independently promote an idea to Research-Ready or High-Conviction.</p>
        <div class="summary">
          <div class="metric"><span>Wisburg Lens</span><strong>${esc(lens.status || "Unavailable")}</strong></div>
          <div class="metric"><span>Research Excerpts</span><strong>${esc((lens.excerpts || []).length)}</strong></div>
          <div class="metric"><span>Themes</span><strong>${esc((lens.themes || []).length)}</strong></div>
          <div class="metric"><span>Narrative</span><strong>${esc(narrative.label || "Unknown")}</strong></div>
        </div>
        ${coverage.status ? `<details><summary>Wisburg entitlement and coverage audit</summary>
        <p>${esc(coverage.status)}. Authentication: ${esc(coverage.authentication_status || "Unknown")}; tool discovery: ${esc(coverage.tool_discovery_status || "Unknown")}; observed items: ${esc(coverage.total_items || 0)}; structured details: ${esc(coverage.detailed_items || 0)}.</p>
        ${table(entitlementRows, [["Tool", "tool"], ["Category", "category"], ["Entitlement", "entitlement"], ["Queries", "queries"], ["Items", "items"], ["Details", "details"], ["Message", "message"]])}
        <h3>Normalized report coverage</h3>${table(reports, [["Report", "report"], ["Category", "category"], ["Publisher", "publisher"], ["Published", "published"], ["Language", "language"], ["Detail status", "detail"], ["Stored scope", "scope"], ["Tier", "tier"]])}</details>` : ""}
        ${delta.status ? `<h3>Point-in-Time Wisburg Change</h3><p>${esc(delta.summary || "")}</p>
        <p class="muted">Status: ${esc(delta.status)}; current: ${esc(delta.observed_at || "Unknown")}; prior: ${esc(delta.prior_observed_at || "First baseline")}; newly observed reports: ${esc((delta.new_report_ids || []).length)}; stance changes: ${esc((delta.theme_stance_changes || []).length)}; external revisions: ${esc((delta.new_revision_ids || []).length)}; corroboration changes: ${esc((delta.corroboration_changes || []).length)}. This covers the capped result set only.</p>` : ""}
        <p class="muted">Debate: ${esc(debate.status || "Unavailable")}; bull: ${esc(debate.strongest_bull_case || "n/a")}; bear: ${esc(debate.strongest_bear_case || "n/a")}. Narrative score: ${esc(narrative.score ?? "n/a")}; topics: ${esc((narrative.repeated_topics || []).join(", ") || "n/a")}.</p>
        <h3>External Revision Observations</h3><p class="muted">Report-level analyst context only; never official consensus or standalone promotion evidence.</p>
        ${table(revisions, [["As of", "asof"], ["Type", "type"], ["Metric", "metric"], ["Direction", "direction"], ["Previous", "prior"], ["Current", "current"], ["Change %", "change"], ["Period", "period"], ["Eligibility", "eligibility"], ["Statement", "statement"]])}
        <h3>Structured Claims and Primary-Source Cross-Check</h3>
        ${table(claims, [["Claim", "claim"], ["Type", "type"], ["Driver", "driver"], ["Metric", "metric"], ["Period", "period"], ["Tier", "tier"], ["Cross-check", "check"], ["Primary matches", "primary"], ["Allowed stage", "stage"]])}
        <h3>Executable Wisburg Research Work Orders</h3>
        ${table(tasks, [["Priority", "priority"], ["Source type", "type"], ["Action", "action"], ["Expected evidence", "expected"], ["Confirm/disprove", "checks"], ["Status", "status"]])}
        ${table(themes, [["Theme", "theme"], ["Stance", "stance"], ["Driver", "driver"], ["Evidence", "evidence"], ["Language", "language"], ["Confidence", "confidence"], ["Summary", "summary"]])}
        <h3>Wisburg Source Suggestions</h3>${table(suggestions, [["Priority", "priority"], ["Source type", "type"], ["Title", "title"], ["Why inspect", "reason"], ["Expected evidence", "expected"], ["Confirm/disprove", "checks"]])}
        <h3>Capped Wisburg Excerpts</h3>${table(excerpts, [["Title", "title"], ["Category", "category"], ["Language", "language"], ["As of", "asof"], ["Themes", "themes"], ["Target/rating", "target"], ["Excerpt", "excerpt"], ["Summary", "summary"]])}
        ${table(caveats, [["External research caveat", "item"]])}`;
    }

    function storyFirstPanel() {
      const brief = current.thesis_brief || {};
      const onePager = current.ic_one_pager || {};
      const demo = current.demo_case || {};
      const progress = current.run_progress || {};
      const judge = current.bull_bear_judge || {};
      const cards = current.story_cards || [];
      const traces = current.formula_traces || [];
      const profile = current.research_profile || {};
      const historyPack = current.historical_research || {};
      const profilePanel = profile.label ? `<h3>Research Profile</h3><div class="summary">
        <div class="metric"><span>Profile</span><strong>${esc(profile.label)}</strong></div>
        <div class="metric"><span>Quarter History</span><strong>${esc(historyPack.analyzed_quarters || 0)}/${esc(historyPack.requested_quarters || profile.quarter_depth || 0)}</strong></div>
        <div class="metric"><span>Annual History</span><strong>${esc(historyPack.analyzed_annual_reports || 0)}/${esc(historyPack.requested_annual_reports || profile.annual_depth || 0)}</strong></div>
        <div class="metric"><span>Call History</span><strong>${esc(historyPack.analyzed_calls || 0)}/${esc(historyPack.requested_calls || profile.call_depth || 0)}</strong></div>
      </div><p class="muted">Adaptive deepening: ${esc((historyPack.adaptive_deepening_reasons || []).join(", ") || "not triggered")}.</p>` : "";
      const demoBanner = demo.title ? `<div class="status"><strong>Demo:</strong> ${esc(demo.title)}. ${esc(demo.lesson || "")} Runtime: ${esc(demo.expected_runtime || "Instant")}; network: ${demo.network_required ? "yes" : "no"}.</div>` : "";
      const pipeline = progress.stages ? `<h3>Research Pipeline</h3><p class="muted">${esc(progress.summary || "")}</p><div class="progress-strip">${progress.stages.map(stage => `
        <details class="progress-stage">
          <summary><strong>${esc(stage.label)}</strong><span>${esc(stage.status)}</span></summary>
          <p>${esc(stage.summary || "")}</p>
          ${table((stage.blockers || []).map(item => ({item})), [["Blocker", "item"]])}
          ${stage.next_action ? `<p class="muted">Next: ${esc(stage.next_action)}</p>` : ""}
        </details>
      `).join("")}</div>` : "";
      const storyCards = `<h3>Story Cards</h3><div class="story-grid">${cards.map(card => `
        <article class="story-card">
          <h3>${esc(card.title)}</h3>
          <p class="muted">${esc(card.status || "")}</p>
          <p>${esc(card.body || card.summary || "")}</p>
          ${card.next_action ? `<p class="muted"><strong>Next:</strong> ${esc(card.next_action)}</p>` : ""}
          ${(card.evidence || []).length ? `<details><summary>Show evidence</summary>${table((card.evidence || []).map(item => ({
            claim: item.claim,
            source: item.source || "Unknown",
            section: item.section || "Unknown",
            metric: item.metric || "n/a",
            value: item.value || "n/a",
            formula: item.formula || "n/a",
            period: item.period || "Unknown",
            confidence: item.confidence || "Unknown",
            excerpt: item.excerpt || ""
          })), [["Claim", "claim"], ["Source", "source"], ["Section", "section"], ["Metric", "metric"], ["Value", "value"], ["Formula", "formula"], ["Period", "period"], ["Confidence", "confidence"], ["Excerpt", "excerpt"]])}</details>` : ""}
        </article>
      `).join("")}</div>`;
      const judgePanel = `<h3>Bull / Bear / Judge</h3><div class="judge-grid">
        <article class="judge-card"><h3>Bull case</h3><p>${esc(judge.bull_case || "n/a")}</p></article>
        <article class="judge-card"><h3>Bear case</h3><p>${esc(judge.bear_case || "n/a")}</p></article>
        <article class="judge-card"><h3>Judge accepts</h3>${table((judge.judge_accepts || []).slice(0, 6).map(item => ({item})), [["Accepted claim", "item"]])}</article>
        <article class="judge-card"><h3>Still unproven</h3>${table((judge.still_unproven || []).slice(0, 8).map(item => ({item})), [["Open item", "item"]])}</article>
      </div>${(judge.resolution_plan || []).length ? `<h3>Resolution Plan</h3>${table((judge.resolution_plan || []).map(item => ({
        type: item.issue_type,
        status: item.status,
        issue: item.issue,
        evidence: item.evidence,
        app: item.app_action,
        user: item.user_action,
        blocks: item.blocking_scope,
        automatic: item.auto_resolvable ? "Yes" : "No"
      })), [["Type", "type"], ["Status", "status"], ["What it means", "issue"], ["Triggering evidence", "evidence"], ["App action", "app"], ["User action", "user"], ["Blocks", "blocks"], ["Automatic", "automatic"]])}` : ""}`;
      const formulas = `<details><summary>Formula transparency</summary>${table(traces.slice(0, 30).map(item => ({
        label: item.label,
        value: item.value,
        sourceField: item.source_field,
        formula: item.formula,
        period: item.period || "Unknown",
        currency: item.currency || "Unknown",
        confidence: item.confidence,
        source: item.source
      })), [["Label", "label"], ["Value", "value"], ["Source field", "sourceField"], ["Formula", "formula"], ["Period", "period"], ["Currency", "currency"], ["Confidence", "confidence"], ["Source", "source"]])}</details>`;
      const playbook = ((current.company_economics || {}).industry_playbook || {});
      const missingMetrics = ((current.metric_resolution_audit || {}).items || []).filter(item => item.status === "metric missing");
      const sourceGaps = ((current.source_coverage_matrix || {}).entries || []).filter(item => ["source unavailable", "source not attempted", "parse failed"].includes(item.status));
      const contributorRows = [
        {item: "Sector playbook", status: playbook.playbook_source ? "Existing / reviewable" : "Draft needed", app: `Prefill ${(playbook.key_kpis || []).length} KPI(s), ${(playbook.leading_indicators || []).length} indicator(s), valuation methods, catalysts, and a fixture specification.`},
        {item: "ADR profile", status: ((current.entity_resolution || {}).reporting_forms || []).some(form => ["20-F", "40-F", "6-K"].includes(form)) ? "Applicable" : "Not currently indicated", app: "Prefill identity, reporting forms, currency, exchange, and source priorities from resolved entity metadata."},
        {item: "Metric aliases", status: `${missingMetrics.length} unresolved metric(s)`, app: "Draft canonical alias rows with source tag, period, unit, and required expected-value fixture."},
        {item: "Source adapters", status: `${sourceGaps.length} source gap(s)`, app: "Draft provider-health, citation, licensing, deterministic-validation, and no-network fixture contracts."},
        {item: "Demo case", status: "Draft ready", app: `Prefill a sanitized ${current.identity ? current.identity.ticker : "ticker"} lesson and no-network regression requirement.`}
      ];
      const contributor = `<details><summary>Improve this project</summary>
        <p class="muted">The app pre-diagnoses contribution opportunities from this run. Authoritative configs and executable adapters still require review and fixture coverage.</p>
        ${table(contributorRows, [["Contribution", "item"], ["Status", "status"], ["What the app can prepare", "app"]])}
      </details>`;
      const implied = current.market_implied_expectations || {};
      const impliedRows = implied.expectations || [];
      const reverseBase = impliedRows.find(item => item.metric === "Reverse DCF base FCF");
      const reverseGrowth = impliedRows.find(item => (item.metric || "").startsWith("Reverse DCF: implied"));
      const fcfYield = impliedRows.find(item => item.metric === "Current free-cash-flow yield");
      const reverseSnapshot = implied.template === "Non-financial" ? `<h3>Reverse FCF / DCF Snapshot</h3>
        <div class="summary">
          <div class="metric"><span>FCF base</span><strong>${reverseBase && reverseBase.implied_value != null ? `${number(reverseBase.implied_value)} ${esc(reverseBase.unit)}` : "Unavailable"}</strong></div>
          <div class="metric"><span>Current FCF yield</span><strong>${fcfYield && fcfYield.implied_value != null ? `${number(fcfYield.implied_value)} ${esc(fcfYield.unit)}` : "Unavailable"}</strong></div>
          <div class="metric"><span>Price-implied FCF growth</span><strong>${reverseGrowth && reverseGrowth.implied_value != null ? `${number(reverseGrowth.implied_value)} ${esc(reverseGrowth.unit)}` : "Unavailable"}</strong></div>
          <div class="metric"><span>Confidence</span><strong>${esc(reverseGrowth ? reverseGrowth.confidence : "Unavailable")}</strong></div>
        </div><p class="muted">${esc(reverseGrowth ? reverseGrowth.interpretation : "Open Market & Expectations for exact missing reverse-DCF inputs.")}</p>` : "";
      return `${demoBanner}<h2>IC Story</h2>
        <div class="summary">
          <div class="metric"><span>Verdict</span><strong>${esc(onePager.verdict || brief.verdict || "n/a")}</strong></div>
          <div class="metric"><span>Stage</span><strong>${esc(onePager.stage || brief.stage || "n/a")}</strong></div>
          <div class="metric"><span>Direction</span><strong>${esc(onePager.direction || brief.direction || "n/a")}</strong></div>
          <div class="metric"><span>Decision</span><strong>${esc(onePager.decision || "Research next")}</strong></div>
        </div>
        <p>${esc(onePager.thesis || brief.thesis || "No thesis generated.")}</p>
        ${onePager.next_best_action ? `<div class="status"><strong>Next action:</strong> ${esc(onePager.next_best_action)}</div>` : ""}
        ${reverseSnapshot}${profilePanel}${pipeline}${storyCards}${judgePanel}${formulas}${contributor}`;
    }

    function icCopilot() {
      const brief = current.thesis_brief || {};
      const onePager = current.ic_one_pager || {};
      const critique = current.thesis_critique || {};
      const sufficiency = current.evidence_sufficiency || {};
      const manifest = current.llm_run_manifest || {};
      const researchManifest = current.llm_research_manifest || {};
      const comparison = current.llm_comparison || {};
      const audit = current.language_audit || {};
      const conviction = current.conviction_audit || {};
      const validation = current.thesis_validation || {};
      const budget = current.budget_policy || {};
      const manual = current.manual_data_status || {};
      const captureReadiness = current.market_capture_readiness || {};
      const economics = current.company_economics || {};
      const creditLens = current.credit_lens || {};
      const coverageExpansion = current.coverage_expansion || {};
      const workOrder = current.evidence_work_order || {};
      const playbook = economics.industry_playbook || {};
      const topIdea = (current.ideas || [])[0] || {};
      const chain = topIdea.conviction_chain || {};
      const chainSteps = (chain.steps || []).map(step => ({
        link: step.label,
        status: step.status,
        statement: step.statement,
        evidence: (step.evidence || []).join("; "),
        gaps: (step.data_gaps || []).join("; ")
      }));
      const mustRows = (chain.what_must_be_true || []).map(item => ({item}));
      const falsifyChainRows = (chain.what_would_falsify || []).map(item => ({item}));
      const nextChainRows = (chain.next_research_actions || []).map(item => ({item}));
      const providerPolicyRows = Object.entries(budget.provider_policy || {}).map(([group, sources]) => ({
        group: group.replaceAll("_", " "),
        sources: (sources || []).join(", ") || "none"
      }));
      const guardrailCheckRows = (manifest.guardrail_checks || []).map(item => ({
        area: item.area,
        status: item.status,
        score: item.score,
        summary: item.summary,
        evidence: (item.evidence || []).join("; "),
        gaps: (item.gaps || []).join("; "),
        enforcement: item.enforcement || "n/a"
      }));
      const onePagerWorkRows = (onePager.work_order_actions || []).map(item => ({item}));
      const onePagerMonitorRows = (onePager.monitor_actions || []).map(item => ({item}));
      const onePagerGapRows = (onePager.evidence_gaps || []).map(item => ({item}));
      const captureActionRows = (captureReadiness.actions || []).map(item => ({
        priority: item.priority,
        area: item.area,
        status: item.status,
        action: item.action,
        why: item.why_it_matters,
        source: item.source_type,
        ideas: (item.related_idea_ids || []).join(", ")
      }));
      const captureSnapshotRows = (captureReadiness.snapshot_needs || []).map(item => ({
        idea: item.idea_id,
        event: item.event_date || "unknown",
        metric: item.metric_family,
        pre: item.pre_event_snapshot,
        post: item.post_event_snapshot,
        sources: (item.accepted_sources || []).slice(0, 4).join("; "),
        hints: (item.csv_row_hints || []).slice(0, 3).join(" | "),
        reason: item.reason,
        status: item.status
      }));
      const captureGapRows = (captureReadiness.data_gaps || []).map(item => ({item}));
      const captureImportPlan = captureReadiness.import_plan || {};
      const captureImportRows = [
        {field: "Status", value: captureImportPlan.status || "n/a"},
        {field: "Minimum viable rows", value: captureImportPlan.minimum_viable_rows ?? captureImportPlan.minimum_required_rows ?? "n/a"},
        {field: "Full revision rows", value: captureImportPlan.full_revision_rows ?? captureImportPlan.minimum_required_rows ?? "n/a"},
        {field: "Minimum required rows", value: captureImportPlan.minimum_required_rows ?? "n/a"},
        {field: "Metric families", value: (captureImportPlan.metric_families || []).join("; ") || "n/a"},
        {field: "Event dates", value: (captureImportPlan.event_dates || []).join("; ") || "n/a"},
        {field: "Required files", value: (captureImportPlan.required_files || []).join("; ") || "n/a"},
        {field: "Optional files", value: (captureImportPlan.optional_files || []).join("; ") || "n/a"},
        {field: "Template command", value: captureImportPlan.template_command || "n/a"},
        {field: "Import command", value: captureImportPlan.import_command || "n/a"},
        {field: "Blocking reason", value: captureImportPlan.blocking_reason || "n/a"}
      ];
      const captureImportSteps = (captureImportPlan.next_steps || []).map(item => ({item}));
      const captureProviderRows = (captureImportPlan.provider_options || []).map(item => ({item}));
      const captureAdvisor = captureReadiness.consensus_advisor || {};
      const captureAdvisorRows = [
        {field: "Status", value: captureAdvisor.status || "n/a"},
        {field: "Blocker", value: captureAdvisor.blocker || "n/a"},
        {field: "Required fix", value: captureAdvisor.required_fix || "n/a"},
        {field: "No-lookahead rule", value: captureAdvisor.no_lookahead_rule || "n/a"}
      ];
      const captureAutofill = captureReadiness.autofill_plan || {};
      const captureAutofillRows = [
        {field: "Status", value: captureAutofill.status || "n/a"},
        {field: "Minimum viable rows", value: captureAutofill.minimum_viable_rows ?? "n/a"},
        {field: "Full revision rows", value: captureAutofill.full_revision_rows ?? "n/a"},
        {field: "Required files", value: (captureAutofill.required_files || []).join("; ") || "n/a"},
        {field: "Optional files", value: (captureAutofill.optional_files || []).join("; ") || "n/a"}
      ];
      const clusterRows = (current.thesis_clusters || []).map(cluster => ({
        cluster: cluster.label,
        status: cluster.status,
        stage: cluster.stage,
        score: cluster.score ?? "n/a",
        chain: cluster.conviction_chain_status || "n/a",
        driver: cluster.driver_name,
        ideas: (cluster.idea_ids || []).length,
        whyNow: cluster.why_now || "n/a",
        priced: cluster.priced_in,
        gaps: (cluster.evidence_gaps || []).join("; ")
      }));
      const scout = current.research_scout || {};
      const scoutAxisRows = [
        ...((scout.company_story_axes || []).slice(0, 4).map(item => ({axis: "Company", item}))),
        ...((scout.sector_story_axes || []).slice(0, 4).map(item => ({axis: "Sector", item}))),
        ...((scout.geography_story_axes || []).slice(0, 4).map(item => ({axis: "Geography", item}))),
        ...((scout.peer_story_axes || []).slice(0, 4).map(item => ({axis: "Peers", item})))
      ];
      const scoutQuestionRows = (scout.questions || []).slice(0, 10).map(item => ({
        priority: item.priority,
        lens: item.lens,
        question: item.question,
        sources: (item.source_types || []).slice(0, 4).join("; "),
        expected: item.expected_evidence || "n/a",
        status: item.current_status || "n/a",
        story: item.story_use || "n/a"
      }));
      const scoutGapRows = (scout.data_gaps || []).map(item => ({item}));
      const questionRows = (current.research_questions || []).map(item => ({
        priority: item.priority,
        question: item.title,
        status: item.status,
        answerability: item.answerability_status || "Unknown",
        answerScore: item.answerability_score ?? "n/a",
        driver: item.driver_name,
        missing: (item.missing_links || []).slice(0, 3).join("; "),
        evidence: (item.required_evidence || []).slice(0, 3).join("; "),
        sources: (item.next_sources || []).slice(0, 3).join("; "),
        capture: (item.market_capture_needs || []).slice(0, 2).join("; ")
      }));
      const driverRows = (economics.drivers || []).map(driver => ({
        driver: driver.name,
        materiality: driver.materiality,
        trend: driver.trend,
        evidence: driver.current_evidence,
        why: driver.why_it_matters,
        source: driver.source
      }));
      const driverCoverageRows = (economics.driver_coverage || []).map(item => ({
        driver: item.driver_name,
        materiality: item.materiality,
        status: item.status,
        evidence: item.current_evidence,
        required: (item.required_evidence || []).slice(0, 2).join("; "),
        missing: (item.missing_evidence || []).slice(0, 3).join("; "),
        next: item.next_source,
        impact: item.stage_impact
      }));
      const playbookQualityRows = (economics.playbook_quality || []).map(item => ({
        area: item.area,
        status: item.status,
        score: item.score,
        summary: item.summary,
        evidence: (item.evidence || []).slice(0, 3).join("; "),
        gaps: (item.gaps || []).slice(0, 3).join("; "),
        next: item.next_action,
        impact: item.stage_impact
      }));
      const creditMetricRows = (creditLens.metrics || []).map(metric => ({
        metric: metric.name,
        value: metric.value === null || metric.value === undefined ? "n/a" : number(metric.value),
        unit: metric.unit || "",
        status: metric.status,
        interpretation: metric.interpretation,
        source: metric.source || "n/a"
      }));
      const creditBridgeRows = (creditLens.credit_bridge || []).map(item => ({
        area: item.area,
        status: item.status,
        question: item.credit_question,
        evidence: item.current_evidence,
        missing: (item.missing_evidence || []).slice(0, 3).join("; "),
        next: item.next_source,
        falsify: item.falsification_test,
        impact: item.stage_impact
      }));
      const creditPositiveRows = (creditLens.positives || []).map(item => ({ item }));
      const creditRiskRows = (creditLens.risks || []).map(item => ({ item }));
      const creditEvidenceRows = (creditLens.required_evidence || []).map(item => ({ item }));
      const creditMonitorRows = (creditLens.monitor_rules || []).map(item => ({ item }));
      const creditCatalystRows = (creditLens.credit_catalysts || []).map(item => ({ item }));
      const creditFalsifyRows = (creditLens.falsification_tests || []).map(item => ({ item }));
      const creditGapRows = (creditLens.data_gaps || []).map(item => ({ item }));
      const coverageReasonRows = (coverageExpansion.why_no_convincing_thesis || []).map(item => ({item}));
      const coverageExpansionRows = (coverageExpansion.recommended_expansions || []).map(item => ({
        priority: item.priority,
        area: item.area,
        source: item.source_type,
        action: item.action,
        why: item.why_it_matters,
        integrity: item.integrity_rule,
        expected: item.expected_output || "n/a",
        cost: item.cost_latency || "n/a"
      }));
      const latencyPolicyRows = (coverageExpansion.latency_policy || []).map(item => ({item}));
      const integrityRows = (coverageExpansion.integrity_notes || []).map(item => ({item}));
      const workOrderRows = (workOrder.items || []).map(item => ({
        priority: item.priority,
        channel: item.channel,
        action: item.action,
        source: item.source_type,
        expected: item.expected_output,
        rr: item.blocks_research_ready ? "Yes" : "No",
        hc: item.blocks_high_conviction ? "Yes" : "No",
        origin: item.origin
      }));
      const workOrderDetailRows = (workOrder.items || []).slice(0, 5).map(item => ({
        action: item.action,
        why: item.why_it_matters,
        acceptance: (item.acceptance_criteria || []).slice(0, 2).join("; "),
        falsify: (item.falsification_tests || []).slice(0, 2).join("; "),
        cost: item.cost_latency || "n/a"
      }));
      const actionRows = (current.action_plan || []).map(item => ({
        criterion: item.criterion,
        metric: item.metric || "n/a",
        operator: item.operator || "n/a",
        threshold: item.threshold ?? "n/a",
        deadline: item.deadline || "n/a",
        source: item.source_field,
        confirm: item.confirm_trigger,
        break: item.break_trigger
      }));
      const evidenceRows = (brief.evidence_chain || []).map(item => ({ item }));
      const uncertaintyRows = (critique.key_uncertainties || []).map(item => ({ item }));
      const falsifyRows = (critique.what_would_falsify || []).map(item => ({ item }));
      const reviewRows = (current.llm_reviews || []).map(item => ({
        provider: item.provider,
        model: item.model,
        status: item.status,
        summary: item.summary || item.message,
        disagreements: (item.disagreements || []).join("; "),
        language: (item.language_quality_issues || []).join("; ")
      }));
      const differenceRows = (comparison.key_differences || []).map(item => ({ item }));
      const languageRows = (audit.excerpts || []).slice(0, 8).map(item => ({
        language: item.source_language,
        source: item.source,
        excerpt: item.original_excerpt
      }));
      const historical = current.historical_references || {};
      const historicalRows = (historical.references || []).map(item => ({
        ticker: item.ticker,
        idea: item.idea_title,
        stage: item.stage,
        direction: item.direction,
        similarity: item.similarity_score,
        outcome: item.outcome_status,
        realized: pct(item.realized_return_pct),
        reasons: (item.match_reasons || []).join("; ")
      }));
      const historicalGaps = (historical.data_gaps || []).map(item => ({ item }));
      const profiling = current.profiling || {};
      const profilingRows = [
        {field: "Status", value: profiling.status || "n/a"},
        {field: "Total runtime", value: profiling.total_ms ? `${(profiling.total_ms / 1000).toFixed(2)}s` : "n/a"},
        {field: "Stages", value: (profiling.steps || []).length}
      ];
      const profilingBottleneckRows = (profiling.bottlenecks || []).map(item => ({item}));
      const profilingTreatmentRows = (profiling.treatments || []).map(item => ({item}));
      const convictionRows = (conviction.items || []).map(item => ({
        check: item.name,
        status: item.status,
        score: item.score,
        evidence: item.evidence,
        gaps: (item.gaps || []).join("; "),
        source: item.source_type
      }));
      const differentiatorRows = (conviction.differentiators || []).map(item => ({ item }));
      const validationRows = (validation.checks || []).map(item => ({
        channel: item.channel,
        status: item.status,
        score: item.score,
        evidence: item.evidence,
        implication: item.implication,
        gaps: (item.gaps || []).join("; "),
        tier: item.source_tier || "n/a",
        citations: item.citation_count
      }));
      const nextEvidenceRows = (validation.required_next_evidence || []).map(item => ({ item }));
      const nextActionRows = (validation.next_evidence_actions || []).map(item => ({
        priority: item.priority,
        channel: item.channel,
        action: item.action,
        source: item.source,
        blocker: item.blocks_high_conviction ? "Yes" : "No",
        why: item.why_it_matters
      }));
      const claimPackage = current.validated_claims || {};
      const claimRows = (claimPackage.claims || []).map(item => ({
        status: item.status,
        category: item.event_category,
        direction: item.direction,
        driver: item.business_driver,
        metric: item.metric || "n/a",
        confidence: item.confidence,
        changed: item.changed_text || item.supporting_quote || "n/a",
        reason: item.reason,
        notGrade: item.not_thesis_grade_reason || ""
      }));
      const sourcePlan = current.source_plan || {};
      const sourcePlanRows = (sourcePlan.requests || []).map(item => ({
        priority: item.priority,
        sourceType: item.source_type,
        title: item.title,
        reason: item.reason_to_inspect,
        expected: item.expected_evidence_type,
        confirms: item.confirms_or_disproves,
        cost: item.cost_latency,
        status: item.status
      }));
      const claimGapRows = (claimPackage.data_gaps || []).map(item => ({ item }));
      const sourcePlanGapRows = (sourcePlan.data_gaps || []).map(item => ({ item }));
      const citationRows = (brief.citations || []).map(item => ({
        source: item.source,
        url: item.url,
        section: item.section || item.form || "n/a",
        excerpt: item.snippet || "n/a"
      }));
      return `${storyFirstPanel()}<h2>IC Audit Trail</h2>
        <div class="summary">
          <div class="metric"><span>IC Verdict</span><strong>${esc(brief.verdict || "n/a")}</strong></div>
          <div class="metric"><span>Evidence Sufficiency</span><strong>${esc(sufficiency.status || brief.status || "n/a")}</strong></div>
          <div class="metric"><span>Sufficiency Score</span><strong>${esc(sufficiency.score ?? "n/a")}/100</strong></div>
          <div class="metric"><span>Synthesis Source</span><strong>${esc(brief.source || "deterministic")}</strong></div>
        </div>
        <h3>IC One-Pager</h3>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(onePager.status || "n/a")}</strong></div>
          <div class="metric"><span>Direction</span><strong>${esc(onePager.direction || brief.direction || "n/a")}</strong></div>
          <div class="metric"><span>Stage</span><strong>${esc(onePager.stage || brief.stage || "n/a")}</strong></div>
          <div class="metric"><span>Source</span><strong>${esc(onePager.source || brief.source || "deterministic")}</strong></div>
        </div>
        <div class="summary">
          <div class="metric"><span>Decision</span><strong>${esc(onePager.decision || "n/a")}</strong></div>
          <div class="metric"><span>Rank Eligible</span><strong>${esc(onePager.rank_eligibility || "n/a")}</strong></div>
          <div class="metric"><span>Why Now</span><strong>${esc(onePager.why_now || "n/a")}</strong></div>
          <div class="metric"><span>Blocking Issue</span><strong>${esc(onePager.blocking_issue || "n/a")}</strong></div>
        </div>
        <p class="muted"><strong>Decision reason:</strong> ${esc(onePager.decision_reason || onePager.go_no_go_reason || "n/a")}</p>
        <p class="muted"><strong>Next best action:</strong> ${esc(onePager.next_best_action || "n/a")}</p>
        <p class="muted"><strong>Go / no-go:</strong> ${esc(onePager.go_no_go_reason || "n/a")}</p>
        <p>${esc(onePager.thesis || brief.thesis || "No thesis generated.")}</p>
        <p class="muted">${esc(onePager.variant_perception || brief.variant_perception || "")}</p>
        ${table([
          {section: "Causal bridge", detail: onePager.causal_bridge || "n/a"},
          {section: "Price move", detail: onePager.price_move || "n/a"},
          {section: "Market capture", detail: onePager.market_capture || "n/a"},
          {section: "Valuation", detail: onePager.valuation || "n/a"},
          {section: "Equity lens", detail: onePager.equity_lens || "n/a"},
          {section: "Credit lens", detail: onePager.credit_lens || "n/a"},
          {section: "Counter-thesis", detail: onePager.counter_thesis || "n/a"}
        ], [["Section", "section"], ["Detail", "detail"]])}
        ${table(onePagerWorkRows, [["Top evidence work order", "item"]])}
        ${table(onePagerMonitorRows, [["Monitor next", "item"]])}
        ${table(onePagerGapRows, [["Evidence gap", "item"]])}
        <h3>Why This Beats Generic Chat</h3>
        <ul>
          <li>Starts from cited filings, prices, consensus snapshots, and source manifests, not model memory.</li>
          <li>Separates facts, gaps, and hypotheses; weak evidence becomes a work order, not a recommendation.</li>
          <li>Builds an auditable IC trail: causal bridge, peer metrics, market capture, valuation, counter-thesis, and monitors.</li>
        </ul>
        <h3>Budget Mode and Data Policy</h3>
        <div class="summary">
          <div class="metric"><span>Mode</span><strong>${esc(budget.mode || "n/a")}</strong></div>
          <div class="metric"><span>Cost Target</span><strong>${esc(budget.cost_target || "n/a")}</strong></div>
          <div class="metric"><span>Manual Data</span><strong>${esc(manual.status || "n/a")}</strong></div>
          <div class="metric"><span>Max Monthly Budget</span><strong>${esc(budget.max_monthly_budget_usd ?? "uncapped / user-defined")}</strong></div>
        </div>
        <p class="muted">${esc(budget.description || "")} ${esc(budget.data_policy || "")} Config: ${esc(budget.config_source || "builtin")}; paid data ${budget.allow_paid_data ? "allowed" : "disabled"}; LLM ${budget.allow_llm ? "allowed" : "disabled"}.</p>
        ${table(providerPolicyRows, [["Provider group", "group"], ["Sources", "sources"]])}
        ${table((budget.enabled_sources || []).map(item => ({item})), [["Enabled source", "item"]])}
        ${table((budget.optional_upgrade_slots || []).map(item => ({item})), [["Upgrade slot", "item"]])}
        <h3>Evidence Work Order</h3>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(workOrder.status || "n/a")}</strong></div>
          <div class="metric"><span>Open Actions</span><strong>${esc((workOrder.items || []).length)}</strong></div>
          <div class="metric"><span>Research-Ready Blockers</span><strong>${esc((workOrder.items || []).filter(item => item.blocks_research_ready).length)}</strong></div>
          <div class="metric"><span>High-Conviction Blockers</span><strong>${esc((workOrder.items || []).filter(item => item.blocks_high_conviction).length)}</strong></div>
        </div>
        <p class="muted">${esc(workOrder.summary || "")}</p>
        ${table(workOrderRows, [["Priority", "priority"], ["Channel", "channel"], ["Action", "action"], ["Source", "source"], ["Expected output", "expected"], ["Blocks RR", "rr"], ["Blocks HC", "hc"], ["Origin", "origin"]])}
        ${table(workOrderDetailRows, [["Action", "action"], ["Why", "why"], ["Acceptance", "acceptance"], ["Falsify if", "falsify"], ["Cost/latency", "cost"]])}
        <h3>Research Scout</h3>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(scout.status || "n/a")}</strong></div>
          <div class="metric"><span>Open Questions</span><strong>${esc((scout.questions || []).length)}</strong></div>
          <div class="metric"><span>Provider</span><strong>${esc(scout.provider || "n/a")}</strong></div>
        </div>
        <p class="muted">${esc(scout.summary || "")}</p>
        ${table(scoutAxisRows, [["Axis", "axis"], ["What to frame", "item"]])}
        ${table(scoutQuestionRows, [["Priority", "priority"], ["Lens", "lens"], ["Question", "question"], ["Source types", "sources"], ["Expected evidence", "expected"], ["Status", "status"], ["Story use", "story"]])}
        ${table(scoutGapRows, [["Research Scout gap", "item"]])}
        <h3>Market Capture Readiness</h3>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(captureReadiness.status || "n/a")}</strong></div>
          <div class="metric"><span>Classified</span><strong>${esc(captureReadiness.classified_ideas ?? 0)}/${esc(captureReadiness.total_ideas ?? 0)}</strong></div>
          <div class="metric"><span>Price-only</span><strong>${esc(captureReadiness.price_only_ideas ?? 0)}</strong></div>
          <div class="metric"><span>Price Coverage</span><strong>${esc(captureReadiness.price_coverage || "n/a")}</strong></div>
          <div class="metric"><span>Consensus Coverage</span><strong>${esc(captureReadiness.consensus_coverage || "n/a")}</strong></div>
        </div>
        <p class="muted">${esc(captureReadiness.summary || "")}</p>
        <p class="muted">${esc(captureReadiness.point_in_time_rule || "")}</p>
        <h4>Consensus Import Plan</h4>
        <p class="muted">${esc(captureImportPlan.summary || "")}</p>
        ${captureImportPlan.practical_next_step ? `<p>${esc(captureImportPlan.practical_next_step)}</p>` : ""}
        ${table(captureImportRows, [["Field", "field"], ["Value", "value"]])}
        <h4>Consensus Coverage Advisor</h4>
        <p class="muted">${esc(captureAdvisor.summary || "")}</p>
        ${table(captureAdvisorRows, [["Field", "field"], ["Value", "value"]])}
        <h4>Market Capture Autofill Plan</h4>
        <p class="muted">${esc(captureAutofill.summary || "")}</p>
        ${table(captureAutofillRows, [["Field", "field"], ["Value", "value"]])}
        ${table(captureProviderRows, [["Cost-effective provider path", "item"]])}
        ${table(captureImportSteps, [["Next step", "item"]])}
        ${table(captureActionRows, [["Priority", "priority"], ["Area", "area"], ["Status", "status"], ["Action", "action"], ["Why", "why"], ["Source type", "source"], ["Ideas", "ideas"]])}
        ${table(captureSnapshotRows, [["Idea", "idea"], ["Event date", "event"], ["Metric family", "metric"], ["Pre-event snapshot", "pre"], ["Post-event snapshot", "post"], ["Accepted sources", "sources"], ["CSV row hints", "hints"], ["Reason", "reason"], ["Status", "status"]])}
        ${table(captureGapRows, [["Market-capture gap", "item"]])}
        <h3>Company Economics + Industry Playbook</h3>
        <p>${esc(economics.business_model || "Company economics model unavailable.")}</p>
        <p class="muted">Industry: ${esc(playbook.industry_label || "n/a")}; source: ${esc(playbook.playbook_source || "built_in")}; quality: ${esc(economics.playbook_quality_score ?? "n/a")}/100; KPIs: ${esc((playbook.key_kpis || []).join(", ") || "n/a")}; valuation: ${esc((playbook.valuation_methods || []).join(", ") || "n/a")}; macro: ${esc((playbook.macro_sensitivities || []).join(", ") || "n/a")}.</p>
        ${table(driverRows, [["Driver", "driver"], ["Materiality", "materiality"], ["Trend", "trend"], ["Evidence", "evidence"], ["Why", "why"], ["Source", "source"]])}
        <h3>Playbook Quality Checklist</h3>${table(playbookQualityRows, [["Area", "area"], ["Status", "status"], ["Score", "score"], ["Summary", "summary"], ["Evidence", "evidence"], ["Gaps", "gaps"], ["Next action", "next"], ["Stage impact", "impact"]])}
        <h3>Driver Coverage Checklist</h3>${table(driverCoverageRows, [["Driver", "driver"], ["Materiality", "materiality"], ["Status", "status"], ["Current evidence", "evidence"], ["Required evidence", "required"], ["Missing evidence", "missing"], ["Next source", "next"], ["Stage impact", "impact"]])}
        <h3>Credit Lens</h3>
        <p class="muted">Status: ${esc(creditLens.status || "Unavailable")}; risk level: ${esc(creditLens.risk_level || "Unknown")}. ${esc(creditLens.summary || "")}</p>
        <p class="muted">${esc(creditLens.source_note || "Derived from available structured financial metrics; not a rating opinion.")}</p>
        ${table(creditMetricRows, [["Metric", "metric"], ["Value", "value"], ["Unit", "unit"], ["Status", "status"], ["Interpretation", "interpretation"], ["Source", "source"]])}
        <h3>Credit Bridge Checklist</h3>${table(creditBridgeRows, [["Area", "area"], ["Status", "status"], ["Credit question", "question"], ["Current evidence", "evidence"], ["Missing evidence", "missing"], ["Next source", "next"], ["Falsification test", "falsify"], ["Stage impact", "impact"]])}
        ${table(creditPositiveRows, [["Credit support", "item"]])}
        ${table(creditRiskRows, [["Credit risk", "item"]])}
        ${table(creditEvidenceRows, [["Required credit evidence", "item"]])}
        ${table(creditMonitorRows, [["Credit monitor rule", "item"]])}
        ${table(creditCatalystRows, [["Credit catalyst", "item"]])}
        ${table(creditFalsifyRows, [["Credit falsification test", "item"]])}
        ${table(creditGapRows, [["Credit data gap", "item"]])}
        <h3>Coverage Expansion Diagnostics</h3>
        <p class="muted">Status: ${esc(coverageExpansion.status || "n/a")}; profile: ${esc(coverageExpansion.coverage_profile || "n/a")}. ${esc(coverageExpansion.summary || "")}</p>
        ${table(coverageReasonRows, [["Why no convincing thesis yet", "item"]])}
        ${table(coverageExpansionRows, [["Priority", "priority"], ["Area", "area"], ["Source", "source"], ["Action", "action"], ["Why", "why"], ["Integrity rule", "integrity"], ["Expected output", "expected"], ["Cost/latency", "cost"]])}
        ${table(integrityRows, [["Integrity note", "item"]])}
        ${table(latencyPolicyRows, [["Latency policy", "item"]])}
        <h3>Thesis Clusters</h3>
        ${table(clusterRows, [["Cluster", "cluster"], ["Status", "status"], ["Stage", "stage"], ["Score", "score"], ["Chain", "chain"], ["Driver", "driver"], ["Ideas", "ideas"], ["Why now", "whyNow"], ["Priced in", "priced"], ["Gaps", "gaps"]])}
        <h3>Research Questions</h3>
        <p class="muted">These are not recommendations. They are open workplans for weak or incomplete thesis chains.</p>
        ${table(questionRows, [["Priority", "priority"], ["Question", "question"], ["Status", "status"], ["Answerability", "answerability"], ["Score", "answerScore"], ["Driver", "driver"], ["Missing links", "missing"], ["Required evidence", "evidence"], ["Next sources", "sources"], ["Market capture needs", "capture"]])}
        <h3>Conviction Chain</h3>
        <p class="muted">Status: ${esc(chain.status || "n/a")}; confidence: ${esc(chain.confidence || "n/a")}. ${esc(chain.summary || "")}</p>
        ${table(chainSteps, [["Link", "link"], ["Status", "status"], ["Statement", "statement"], ["Evidence", "evidence"], ["Gaps", "gaps"]])}
        ${table(mustRows, [["What must be true", "item"]])}
        ${table(falsifyChainRows, [["What would falsify it", "item"]])}
        ${table(nextChainRows, [["Next research action", "item"]])}
        <h3>Conviction Audit</h3>
        <p class="muted">Status: ${esc(conviction.status || "n/a")}; process score: ${esc(conviction.score ?? "n/a")}/100. ${esc(conviction.summary || "")}</p>
        ${table(convictionRows, [["Check", "check"], ["Status", "status"], ["Score", "score"], ["Evidence", "evidence"], ["Gaps", "gaps"], ["Source", "source"]])}
        ${table(differentiatorRows, [["Workflow differentiator", "item"]])}
        <h3>Thesis Validation Matrix</h3>
        <p class="muted">Status: ${esc(validation.status || "n/a")}; score: ${esc(validation.score ?? "n/a")}/100. ${esc(validation.summary || "")}</p>
        ${table(validationRows, [["Channel", "channel"], ["Status", "status"], ["Score", "score"], ["Evidence", "evidence"], ["Implication", "implication"], ["Gaps", "gaps"], ["Tier", "tier"], ["Citations", "citations"]])}
        ${table(nextEvidenceRows, [["Required next evidence", "item"]])}
        <h3>Validated Claims</h3>
        <p class="muted">Status: ${esc(claimPackage.status || "n/a")}; provider: ${esc(claimPackage.provider || "deterministic")}. Guidance, outlook, risk, margin, and management language must pass this layer before becoming thesis-grade.</p>
        ${table(claimRows, [["Status", "status"], ["Category", "category"], ["Direction", "direction"], ["Driver", "driver"], ["Metric", "metric"], ["Confidence", "confidence"], ["What exactly changed", "changed"], ["Reason", "reason"], ["Why not thesis-grade", "notGrade"]])}
        ${table(claimGapRows, [["Claim data gap", "item"]])}
        <h3>Research Source Plan</h3>
        <p class="muted">Status: ${esc(sourcePlan.status || "n/a")}; registry: ${esc(sourcePlan.registry_version || "n/a")}; provider: ${esc(sourcePlan.provider || "deterministic")}. LLM suggestions are limited to registered source types; deterministic adapters perform fetching.</p>
        ${table(sourcePlanRows, [["Priority", "priority"], ["Source type", "sourceType"], ["Title", "title"], ["Why inspect", "reason"], ["Expected evidence", "expected"], ["Confirm/disprove", "confirms"], ["Cost/latency", "cost"], ["Status", "status"]])}
        ${table(sourcePlanGapRows, [["Source-plan data gap", "item"]])}
        ${wisburgLensPanel()}
        <h3>Evidence Action Plan</h3>
        ${table(nextActionRows, [["Priority", "priority"], ["Channel", "channel"], ["Action", "action"], ["Source", "source"], ["Blocks high conviction", "blocker"], ["Why", "why"]])}
        <h3>Thesis</h3><p>${esc(brief.thesis || "No thesis generated.")}</p>
        <h3>Variant Perception</h3><p>${esc(brief.variant_perception || "n/a")}</p>
        <h3>Evidence Chain</h3>${table(evidenceRows, [["Evidence", "item"]])}
        <h3>Strongest Counter-Thesis</h3><p>${esc(critique.strongest_counter_thesis || "n/a")}</p>
        <h3>Key Uncertainties</h3>${table(uncertaintyRows, [["Uncertainty", "item"]])}
        <h3>What Would Falsify This</h3>${table(falsifyRows, [["Break condition", "item"]])}
        <h3>Model Disagreement</h3><p class="muted">Status: ${esc(comparison.status || "n/a")}; agreement: ${esc(comparison.agreement || "n/a")}; primary: ${esc(comparison.primary_provider || "n/a")}; secondary: ${esc(comparison.secondary_provider || "n/a")}.</p>
        ${table(differenceRows, [["Difference", "item"]])}
        <h3>Secondary Reader</h3>${table(reviewRows, [["Provider", "provider"], ["Model", "model"], ["Status", "status"], ["Summary", "summary"], ["Disagreements", "disagreements"], ["Language issues", "language"]])}
        <h3>Language Audit</h3><p class="muted">Policy: ${esc(audit.policy || "n/a")}; source languages: ${esc((audit.source_languages || []).join(", ") || "n/a")}; flags: ${esc((audit.flags || []).join(", ") || "none")}.</p>
        ${table(languageRows, [["Language", "language"], ["Source", "source"], ["Original excerpt", "excerpt"]])}
        <h3>Latency Profile</h3>
        ${table(profilingRows, [["Field", "field"], ["Value", "value"]])}
        ${table(profilingBottleneckRows, [["Slowest stage", "item"]])}
        ${table(profilingTreatmentRows, [["Bottleneck treatment", "item"]])}
        <h3>Historical References</h3>
        <p class="muted">Status: ${esc(historical.status || "Unavailable")}; scope: ${esc(historical.scope || "n/a")}; resolved sample: ${esc(historical.sample_size ?? 0)}/${esc(historical.minimum_sample_size ?? 0)}. ${esc(historical.summary || "")}</p>
        ${table(historicalRows, [["Ticker", "ticker"], ["Idea", "idea"], ["Stage", "stage"], ["Direction", "direction"], ["Similarity", "similarity"], ["Outcome", "outcome"], ["Realized", "realized"], ["Reasons", "reasons"]])}
        ${table(historicalGaps, [["Data gap", "item"]])}
        <h3>Action Plan</h3>${table(actionRows, [["Criterion", "criterion"], ["Metric", "metric"], ["Operator", "operator"], ["Threshold", "threshold"], ["Deadline", "deadline"], ["Source", "source"], ["Confirm", "confirm"], ["Break", "break"]])}
        <h3>Source Citations</h3>${table(citationRows, [["Source", "source"], ["URL", "url"], ["Section", "section"], ["Excerpt", "excerpt"]])}
        <h3>LLM Run Manifest</h3><p class="muted">Status: ${esc(manifest.status || "n/a")}; execution: ${esc(manifest.llm_execution_status || "n/a")}; guardrails: ${esc(manifest.llm_guardrail_status || "n/a")}; provider: ${esc(manifest.provider || "n/a")}; model: ${esc(manifest.model || "n/a")}; prompt: ${esc(manifest.prompt_version || "n/a")}; token estimate: ${esc(manifest.token_estimate || "n/a")}; fingerprint: ${esc(manifest.prompt_hash ? manifest.prompt_hash.slice(0, 16) + "..." : "n/a")}. ${esc(manifest.message || "")}</p>
        <p class="muted">Provider health: ${esc(manifest.provider_health || "n/a")}; failure class: ${esc(manifest.failure_class || "none")}; retryable: ${manifest.retryable ? "yes" : "no"}; timeout: ${esc(manifest.timeout_seconds || "n/a")}s.</p>
        <p class="muted">LLM guardrail score: ${esc(manifest.guardrail_score ?? "n/a")}/100.</p>
        <h3>LLM Guardrail Checklist</h3>
        ${table(guardrailCheckRows, [["Area", "area"], ["Status", "status"], ["Score", "score"], ["Summary", "summary"], ["Evidence", "evidence"], ["Gaps", "gaps"], ["Enforcement", "enforcement"]])}
        ${table(Object.entries(manifest.prompt_context_counts || {}).map(([key, value]) => ({key, value})), [["Context", "key"], ["Count", "value"]])}
        ${table((manifest.guardrail_policy || []).map(item => ({item})), [["LLM guardrail", "item"]])}
        <h3>LLM Research Assistant Policy</h3>
        <p class="muted">Status: ${esc(researchManifest.status || "n/a")}; provider: ${esc(researchManifest.provider || "n/a")}; registry: ${esc(researchManifest.source_registry_version || "n/a")}; executor: ${esc(researchManifest.deterministic_executor || "n/a")}. ${esc(researchManifest.evidence_boundary || "")}</p>
        ${table([
          ...(researchManifest.allowed_roles || []).map(item => ({category: "Allowed role", item})),
          ...(researchManifest.prohibited_actions || []).map(item => ({category: "Prohibited action", item})),
          ...(researchManifest.validation_gates || []).map(item => ({category: "Validation gate", item}))
        ], [["Category", "category"], ["Rule", "item"]])}`;
    }

    function researchRadar() {
      const notes = (current.coverage_notes || []).map(note => `<div class="status">${esc(note)}</div>`).join("");
      const resolution = current.entity_resolution || {};
      const coverage = current.financial_coverage || {};
      const coverageSummary = `<div class="summary">
        <div class="metric"><span>Listing Status</span><strong>${esc(resolution.listing_status || "Unknown")}</strong></div>
        <div class="metric"><span>Financial Coverage</span><strong>${esc(coverage.status || "Unknown")}</strong></div>
        <div class="metric"><span>Exchange</span><strong>${esc(resolution.exchange || "Unknown")}</strong></div>
      </div><p class="muted">${esc(coverage.reason || "")}</p>`;
      const eventRows = current.events.map(event => ({
        category: event.category.replaceAll("_", " "),
        title: event.title,
        direction: event.direction,
        severity: event.severity,
        date: event.event_date,
        summary: event.summary,
        why: event.why_this_matters
      }));
      const assessmentCards = (current.metric_assessments || []).map(item => `<article class="idea">
        <div class="idea-head"><h3>${esc(item.metric_name)}: ${esc(item.interpretation)}</h3><span class="pill warn">${esc(item.polarity)}</span></div>
        <p class="muted">${esc(item.event_label)}</p><p>${esc(item.observed_change)}</p>
        <div class="grid"><div><strong>Constructive hypothesis</strong><p>${esc(item.constructive_hypothesis ? item.constructive_hypothesis.mechanism : "Unknown")}</p></div>
        <div><strong>Adverse hypothesis</strong><p>${esc(item.adverse_hypothesis ? item.adverse_hypothesis.mechanism : "Unknown")}</p></div></div>
        <p class="muted">Historical trend: ${esc(item.historical_trend || "Unknown")}</p>
        <p><strong>Next automatic action:</strong> ${esc(item.next_automatic_action || "Unknown")}</p>
      </article>`).join("");
      const metricRows = current.metrics.map(metric => ({
        metric: metric.name,
        latest: `${number(metric.value)} ${metric.unit}`,
        period: metric.period_end,
        change: metric.yoy_change_pct === null ? "n/a" : `${metric.yoy_change_pct.toFixed(1)}%`
      }));
      const filingRows = current.filings.slice(0, 10).map(filing => ({
        form: filing.form,
        filed: filing.filing_date,
        report: filing.report_date,
        description: filing.description,
        url: filing.url
      }));
      return `${coverageSummary}${notes}${consensusPanel()}${eventWorkflowPanel()}<h2>Detected Changes</h2>${assessmentCards}<details><summary>Raw detected-change table</summary>${table(eventRows, [["Category", "category"], ["Title", "title"], ["Direction", "direction"], ["Severity", "severity"], ["Date", "date"], ["Summary", "summary"], ["Why this matters", "why"]])}</details>
        <h2>Financial Snapshot</h2>${table(metricRows, [["Metric", "metric"], ["Latest", "latest"], ["Period", "period"], ["YoY / Comparable", "change"]])}
        <h2>Source Filings</h2>${table(filingRows, [["Form", "form"], ["Filed", "filed"], ["Report", "report"], ["Description", "description"], ["URL", "url"]])}`;
    }

    function eventWorkflowPanel() {
      const workflow = current.event_workflow || {};
      const rows = (workflow.items || []).map(item => ({
        priority: item.priority,
        type: item.item_type,
        title: item.title,
        due: item.due_date || "n/a",
        source: item.source,
        status: item.status,
        idea: item.related_idea_id || "n/a",
        reason: item.reason
      }));
      return `<h2>Event Calendar + Next Source Workflow</h2>
        <p class="muted">Follow-up queue for filing windows, consensus history, source-plan requests, and monitor rules.</p>
        ${table(rows, [["Priority", "priority"], ["Type", "type"], ["Title", "title"], ["Due", "due"], ["Source", "source"], ["Status", "status"], ["Idea", "idea"], ["Reason", "reason"]])}`;
    }

    function ideaFactory() {
      const wow = current.wow_ideas || [];
      const ready = current.ideas.filter(idea => idea.stage === "Research-Ready");
      const high = current.ideas.filter(idea => idea.stage === "High-Conviction" || idea.stage === "Investable");
      const candidates = current.ideas.filter(idea => idea.stage !== "Research-Ready" && idea.stage !== "High-Conviction" && idea.stage !== "Investable");
      const questions = current.research_questions || [];
      return `<h2>Wow Filter: Changed Evidence, Stale Market Reaction</h2>
        ${wow.length ? wow.map(ideaCard).join("") : `<p class="muted">No uncaptured setup detected from the current data.</p>`}
        <h2>Research-Ready (${ready.length})</h2>${ready.length ? ready.map(ideaCard).join("") : `<p class="muted">No idea passed the practical research-ready gate.</p>`}
        <h2>High-Conviction (${high.length})</h2>${high.length ? high.map(ideaCard).join("") : `<p class="muted">No idea passed every high-conviction gate.</p>`}
        <h2>Research Questions (${questions.length})</h2>${questions.length ? questions.map(researchQuestionCard).join("") : `<p class="muted">No research questions were generated from weak thesis chains.</p>`}
        <h2>Candidates (${candidates.length})</h2>${candidates.map(ideaCard).join("")}`;
    }

    function researchQuestionCard(item) {
      return `<article class="idea">
        <div class="idea-head"><h3>${esc(item.priority || "Medium")}: ${esc(item.title)}</h3><span class="pill warn">${esc(item.status || "Research question")}</span></div>
        <p>${esc(item.source_signal || "")}</p>
        <p><strong>Answerability:</strong> ${esc(item.answerability_status || "Unknown")} (${esc(item.answerability_score ?? "n/a")}/100)</p>
        <p><strong>Decision rule:</strong> ${esc(item.decision_rule || "n/a")}</p>
        <p><strong>Hypothesis to test:</strong> ${esc(item.hypothesis || "n/a")}</p>
        <p><strong>Expected answer format:</strong> ${esc(item.answer_format || "n/a")}</p>
        <p><strong>Stop condition:</strong> ${esc(item.stop_condition || "n/a")}</p>
        <p><strong>Driver:</strong> ${esc(item.driver_name || "Unmapped")}</p>
        <p><strong>Why it matters:</strong> ${esc(item.why_it_matters || "n/a")}</p>
        ${table((item.minimum_evidence_package || []).map(value => ({value})), [["Minimum evidence package", "value"]])}
        ${table((item.answerability_gaps || []).map(value => ({value})), [["Answerability gap", "value"]])}
        ${table((item.missing_links || []).map(value => ({value})), [["Missing link", "value"]])}
        ${table((item.required_evidence || []).map(value => ({value})), [["Required evidence", "value"]])}
        ${table((item.primary_source_types || []).map(value => ({value})), [["Primary source type", "value"]])}
        ${table((item.next_sources || []).map(value => ({value})), [["Next source", "value"]])}
        ${table((item.workplan_steps || []).map(value => ({value})), [["Workplan step", "value"]])}
        ${table((item.acceptance_criteria || []).map(value => ({value})), [["Acceptance criterion", "value"]])}
        ${table((item.falsification_tests || []).map(value => ({value})), [["Falsification test", "value"]])}
        ${table((item.promotion_criteria || []).map(value => ({value})), [["Promotion criterion", "value"]])}
      </article>`;
    }

    function ideaCard(idea) {
      const score = idea.score ? idea.score.total : "n/a";
      const capture = idea.market_capture ? (idea.market_capture.capture_mode || idea.market_capture.category || "Unknown") : "Unknown";
      const ev = expectedValue(idea.scenarios);
      const pillClass = capture === "Uncaptured" ? "" : capture === "Mostly captured" ? "bad" : "warn";
      const driver = idea.driver_analysis ? `<div><strong>Possible causes:</strong> ${esc(idea.driver_analysis.headline)}
        ${(idea.driver_analysis.factors || []).slice(0, 4).map(factor => `
          <p class="muted">${esc(factor.cause)} (${esc(factor.confidence)}, ${esc(factor.magnitude_hint)}): ${esc(factor.explanation)}</p>
        `).join("")}</div>` : "";
      const bridgeRows = idea.driver_analysis ? [
        { area: "Evidence needed", items: (idea.driver_analysis.evidence_needed || []).slice(0, 4).join("; ") },
        { area: "Peer metric checks", items: (idea.driver_analysis.peer_metric_checks || []).slice(0, 4).join("; ") },
        { area: "Falsification tests", items: (idea.driver_analysis.falsification_tests || []).slice(0, 4).join("; ") },
        { area: "Valuation implication", items: idea.driver_analysis.valuation_implication || "" },
        { area: "Credit implication", items: idea.driver_analysis.credit_implication || "" },
        { area: "Bridge gaps", items: (idea.driver_analysis.data_gaps || []).slice(0, 4).join("; ") }
      ].filter(row => row.items) : [];
      const bridgeDetail = idea.driver_analysis ? `<div><strong>Causal bridge detail:</strong>
        <p class="muted">${esc(idea.driver_analysis.bridge_status || "Unknown")} | Driver: ${esc(idea.driver_analysis.primary_driver || "n/a")}</p>
        <p>${esc(idea.driver_analysis.mechanism || "")}</p>
        ${table(bridgeRows, [["Area", "area"], ["Items", "items"]])}
      </div>` : "";
      const causalBridge = idea.causal_bridge_status ? `<p><strong>Causal bridge:</strong> ${esc(idea.causal_bridge_status)}</p>` : "";
      const captureDetail = idea.market_capture ? `<div><strong>Market capture diagnosis:</strong>
        <p class="muted">Capture mode: ${esc(idea.market_capture.capture_mode || "Unclassified")}; category: ${esc(idea.market_capture.category || "Unknown")}.</p>
        <p>${esc(idea.market_capture.diagnosis || idea.market_capture.explanation || "n/a")}</p>
        <p class="muted">Price status: ${esc(idea.market_capture.price_status || "unknown")}; consensus status: ${esc(idea.market_capture.consensus_status || "unknown")}.</p>
        ${table((idea.market_capture.required_inputs || []).map(item => ({item})), [["Required input", "item"]])}
        ${idea.market_capture.point_in_time_note ? `<p class="muted">${esc(idea.market_capture.point_in_time_note)}</p>` : ""}
      </div>` : "";
      const lens = idea.equity_credit_lens || {};
      const equityCredit = (lens.equity || lens.credit) ? `<div><strong>Equity / credit lens:</strong>
        ${lens.equity ? `<p class="muted">${esc(lens.equity)}</p>` : ""}
        ${lens.credit ? `<p class="muted">${esc(lens.credit)}</p>` : ""}
      </div>` : "";
      const llmContribution = idea.llm_contribution ? `<p class="muted"><strong>LLM contribution:</strong> ${esc(Object.entries(idea.llm_contribution).map(([k, v]) => `${k}: ${v}`).join("; "))}</p>` : "";
      const attributionSummary = idea.driver_attribution ? (idea.driver_attribution.attribution_summary || []).slice(0, 4).join("; ") : "";
      const attribution = idea.driver_attribution ? `<div><strong>Price move attribution:</strong> ${esc(idea.driver_attribution.classification)} (${esc(idea.driver_attribution.confidence)}). ${esc(idea.driver_attribution.headline)}
        <p class="muted">Readiness: ${esc(idea.driver_attribution.attribution_readiness || "Unknown")}${attributionSummary ? `; ${esc(attributionSummary)}` : ""}</p>
        <p class="muted">Raw ${esc(idea.driver_attribution.return_window || "n/a")}: ${pct(idea.driver_attribution.raw_return_pct)}; market-relative: ${pct(idea.driver_attribution.market_relative_pct)}; sector-relative: ${pct(idea.driver_attribution.sector_relative_pct)}; beta-adjusted: ${pct(idea.driver_attribution.beta_adjusted_pct)}.</p></div>` : "";
      const sourceEvent = (idea.source_events || [])[0] || {};
      const eventMetrics = sourceEvent.metrics || {};
      const changedText = eventMetrics.changed_text || eventMetrics.supporting_quote || "";
      const notGradeReason = eventMetrics.not_thesis_grade_reason || "";
      const shareRecon = idea.share_reconciliation || null;
      const scoreDims = idea.score ? `<p class="muted"><strong>Score dimensions:</strong> research ${esc(idea.score.research_quality ?? "n/a")}; evidence ${esc(idea.score.evidence_strength_score ?? "n/a")}; valuation ${esc(idea.score.valuation_completeness ?? "n/a")}; market capture ${esc(idea.score.market_capture_confidence ?? "n/a")}; actionability ${esc(idea.score.actionability ?? "n/a")}.</p>` : "";
      const audit = idea.thesis_audit_chain ? `<div><strong>Thesis audit chain:</strong> ${esc(idea.thesis_audit_chain.summary || "")}
        ${table((idea.thesis_audit_chain.steps || []).map(step => ({
          step: step.step,
          status: step.status,
          summary: step.summary,
          evidence: (step.evidence || []).slice(0, 2).join("; "),
          gaps: (step.data_gaps || []).slice(0, 2).join("; ")
        })), [["Step", "step"], ["Status", "status"], ["Summary", "summary"], ["Evidence", "evidence"], ["Gaps", "gaps"]])}</div>` : "";
      const promotion = idea.promotion_decision ? `<details><summary>Promotion evidence audit</summary>
        <p><strong>${esc(idea.promotion_decision.label || "Primary evidence required")}</strong></p>
        <p class="muted">Status: ${esc(idea.promotion_decision.status)}; substituted gate: ${esc(idea.promotion_decision.substituted_gate || "none")}; score cap: ${esc(idea.promotion_decision.score_cap ?? "none")}.</p>
        ${table([
          ...(idea.promotion_decision.checks || []).map(item => ({status: "Passed", item})),
          ...(idea.promotion_decision.failed_checks || []).map(item => ({status: "Failed", item}))
        ], [["Status", "status"], ["Eligibility check", "item"]])}
      </details>` : "";
      return `<article class="idea">
        <div class="idea-head"><h3>${esc(idea.stage || "Candidate")}: ${esc(idea.title)}</h3><span class="pill ${pillClass}">Capture: ${esc(capture)} | Score: ${esc(score)}/100</span></div>
        ${scoreDims}
        ${audit}
        ${driver}
        ${bridgeDetail}
        ${causalBridge}
        ${equityCredit}
        ${llmContribution}
        ${attribution}
        <p>${esc(idea.thesis)}</p>
        <p><strong>Thesis-grade status:</strong> ${esc(idea.thesis_grade_status || "Unvalidated")}</p>
        ${promotion}
        <p><strong>Direction rationale:</strong> ${esc(idea.direction_rationale || "n/a")}</p>
        ${idea.driver_template_summary ? `<p><strong>Driver explanation template:</strong> ${esc(idea.driver_template_summary)}</p>` : ""}
        ${idea.normalization_status ? `<p class="muted"><strong>Normalization status:</strong> ${esc(idea.normalization_status)}</p>` : ""}
        ${shareRecon ? `<p class="muted"><strong>Share reconciliation:</strong> ${esc(shareRecon.status || "Unknown")} | basis: ${esc(shareRecon.basis || "Unknown")} | ADR ratio: ${esc(shareRecon.adr_ratio || "n/a")}${shareRecon.data_gaps && shareRecon.data_gaps.length ? `<br>${esc(shareRecon.data_gaps.join("; "))}` : ""}</p>` : ""}
        ${changedText ? `<p><strong>What exactly changed:</strong> ${esc(changedText)}</p>` : ""}
        ${notGradeReason ? `<p class="muted"><strong>Why not thesis-grade:</strong> ${esc(notGradeReason)}</p>` : ""}
        <p><strong>Variant perception:</strong> ${esc(idea.variant_perception)}</p>
        ${captureDetail}
        <p><strong>Structure:</strong> ${esc(idea.structure)} | <strong>Illustrative EV:</strong> ${ev === null ? "Unavailable" : `${ev.toFixed(1)}%`} | <strong>Probability:</strong> ${esc(idea.probability_provenance ? idea.probability_provenance.status : "Uncalibrated")} | <strong>Horizon:</strong> ${esc(idea.horizon)}</p>
        <p><strong>Catalyst:</strong> ${esc(idea.catalyst)}</p>
        <p><strong>Strongest counter-thesis:</strong> ${esc(idea.strongest_counter_thesis || "Not evaluated")}</p>
        <p><strong>Next source to check:</strong> ${esc(idea.next_source_to_check || "n/a")}</p>
        ${idea.gate_result && idea.gate_result.research_ready_failed && idea.gate_result.research_ready_failed.length ? `<p class="muted"><strong>Research-ready gaps:</strong> ${esc(idea.gate_result.research_ready_failed.join("; "))}</p>` : ""}
        ${idea.gate_result && idea.gate_result.high_conviction_failed && idea.gate_result.high_conviction_failed.length ? `<p class="muted"><strong>High-conviction gaps:</strong> ${esc(idea.gate_result.high_conviction_failed.join("; "))}</p>` : ""}
        ${peerReadthroughTable(idea.peer_readthrough || [])}
        ${peerMetricSummaryBlock(idea.peer_metric_summary || null)}
        ${peerMetricReadthroughTable(idea.peer_metric_readthrough || [])}
        ${globalPeerCoverageTable(idea.global_peer_coverage || [])}
        ${outcomeForm(idea)}
      </article>`;
    }

    function outcomeForm(idea) {
      const ideaId = idea.idea_id || "";
      return `<details class="outcome-panel">
        <summary>Record outcome / post-mortem</summary>
        <div class="outcome-form" data-idea-id="${esc(ideaId)}">
          <label>Realized return %
            <input type="number" step="0.1" data-field="realized_return_pct" placeholder="e.g. 4.2">
          </label>
          <label>Max adverse %
            <input type="number" step="0.1" data-field="max_adverse_excursion_pct" placeholder="e.g. -2.0">
          </label>
          <label>Max favorable %
            <input type="number" step="0.1" data-field="max_favorable_excursion_pct" placeholder="e.g. 8.5">
          </label>
          <label>Outcome
            <select data-field="thesis_outcome">
              <option value="confirmed">Confirmed</option>
              <option value="partially_confirmed">Partially confirmed</option>
              <option value="contradicted">Contradicted</option>
              <option value="inconclusive">Inconclusive</option>
            </select>
          </label>
          <label>Original evidence valid?
            <select data-field="evidence_valid">
              <option value="yes">Yes</option>
              <option value="mixed">Mixed</option>
              <option value="no">No</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
          <label>Closure reason
            <input data-field="closure_reason" placeholder="Why this idea is being closed">
          </label>
          <label class="full">What worked
            <textarea data-field="what_worked" placeholder="Evidence, bridge, timing, or monitoring signal that helped"></textarea>
          </label>
          <label class="full">What failed
            <textarea data-field="what_failed" placeholder="Missing evidence, wrong causal bridge, valuation miss, or timing issue"></textarea>
          </label>
          <label class="full">Lessons / process change
            <textarea data-field="lessons" placeholder="What the process should do differently next time"></textarea>
          </label>
          <button type="button" class="primary outcome-submit">Save Outcome For Calibration</button>
          <span class="muted outcome-status"></span>
        </div>
      </details>`;
    }

    function bindOutcomeForms() {
      document.querySelectorAll(".outcome-submit").forEach(button => {
        button.addEventListener("click", async () => {
          const form = button.closest(".outcome-form");
          if (!form) return;
          const ideaId = form.dataset.ideaId;
          const status = form.querySelector(".outcome-status");
          const payload = {};
          form.querySelectorAll("[data-field]").forEach(input => {
            const value = input.value;
            if (value !== "") payload[input.dataset.field] = value;
          });
          button.disabled = true;
          if (status) status.textContent = "Saving outcome...";
          try {
            const response = await fetch(`/api/ideas/${encodeURIComponent(ideaId)}/outcome`, {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify(payload)
            });
            const result = await parseJsonResponse(response);
            if (!response.ok) throw new Error(result.error || "Outcome save failed");
            if (status) {
              const sample = result.calibration ? result.calibration.sample_size : "updated";
              status.textContent = `Saved. Calibration sample size: ${sample}.`;
            }
            setStatus(`Recorded outcome for ${ideaId}.`);
          } catch (error) {
            if (status) status.textContent = error.message;
          } finally {
            button.disabled = false;
          }
        });
      });
    }

    function peerReadthroughTable(readthroughs) {
      if (!readthroughs.length) return "";
      const rows = readthroughs.map(peer => ({
        peer: peer.peer_ticker,
        evidence: peer.evidence_status,
        relation: peer.relation,
        price: peer.price_reaction_pct === null || peer.price_reaction_pct === undefined ? "n/a" : `${peer.price_reaction_pct.toFixed(1)}%`,
        provider: peer.sympathy_reaction ? peer.sympathy_reaction.source : "n/a",
        status: peer.sympathy_reaction ? peer.sympathy_reaction.status : (peer.failure_status || ""),
        anchor: peer.sympathy_reaction ? (peer.sympathy_reaction.anchor_date || "pending") : "n/a",
        windows: peer.sympathy_reaction ? ["1d", "5d", "20d"].map(window => {
          const value = peer.sympathy_reaction.raw_returns[window];
          return value === null || value === undefined ? (peer.sympathy_reaction.status === "window_pending" ? "pending" : "n/a") : pct(value);
        }).join(" / ") : "n/a",
        reason: peer.failure_reason || (peer.sympathy_reaction ? peer.sympathy_reaction.reason : ""),
        key: (peer.key_metric_changes || []).join("; "),
        conclusion: peer.conclusion
      }));
      return `<h3>Direct peer checks</h3>${table(rows, [["Peer", "peer"], ["Evidence", "evidence"], ["Relation", "relation"], ["Provider", "provider"], ["Status", "status"], ["Anchor", "anchor"], ["1d / 5d / 20d", "windows"], ["Reason", "reason"], ["Key changes", "key"], ["Conclusion", "conclusion"]])}`;
    }

    function peerMetricReadthroughTable(readthroughs) {
      if (!readthroughs.length) return "";
      const rows = readthroughs.map(item => ({
        peer: item.peer_ticker,
        family: item.metric_family,
        status: item.status,
        relation: item.relation,
        alignment: item.fiscal_alignment,
        present: (item.present_metrics || []).join("; "),
        missing: (item.missing_metrics || []).join("; "),
        summary: item.summary,
        acceptance: (item.acceptance_criteria || []).slice(0, 2).join("; "),
        falsifiers: (item.falsification_tests || []).slice(0, 2).join("; "),
        gaps: (item.data_gaps || []).join("; ")
      }));
      return `<h3>Peer Metric Read-Through</h3>${table(rows, [["Peer", "peer"], ["Metric family", "family"], ["Status", "status"], ["Relation", "relation"], ["Fiscal alignment", "alignment"], ["Present metrics", "present"], ["Missing metrics", "missing"], ["Summary", "summary"], ["Acceptance", "acceptance"], ["Falsifiers", "falsifiers"], ["Gaps", "gaps"]])}`;
    }

    function peerMetricSummaryBlock(summary) {
      if (!summary) return "";
      const rows = [
        {field: "Status", value: summary.status || "n/a"},
        {field: "Score", value: `${summary.score ?? "n/a"}/100`},
        {field: "Operating metric peers", value: `${summary.operating_metric_peers ?? 0}/${summary.total_peers ?? 0}`},
        {field: "Missing metric peers", value: summary.missing_metric_peers ?? 0},
        {field: "Stale metric peers", value: summary.stale_metric_peers ?? 0},
        {field: "Price-only peers", value: summary.price_only_peers ?? 0},
        {field: "Global peers", value: summary.global_peer_peers ?? 0},
        {field: "Metric families", value: (summary.metric_families || []).join("; ") || "n/a"},
        {field: "Stage impact", value: summary.stage_impact || "n/a"}
      ];
      const confirms = (summary.confirmations || []).map(item => ({item}));
      const contradicts = (summary.contradictions || []).map(item => ({item}));
      const gaps = (summary.data_gaps || []).map(item => ({item}));
      const actions = (summary.next_actions || []).map(item => ({item}));
      return `<h3>Peer Metric Readiness</h3><p class="muted">${esc(summary.summary || "")}</p>
        ${table(rows, [["Field", "field"], ["Value", "value"]])}
        ${table(confirms, [["Confirming peer evidence", "item"]])}
        ${table(contradicts, [["Contradicting peer evidence", "item"]])}
        ${table(gaps, [["Peer metric gap", "item"]])}
        ${table(actions, [["Next action", "item"]])}`;
    }

    function globalPeerCoverageTable(coverage) {
      if (!coverage.length) return "";
      const rows = coverage.map(item => ({
        peer: item.ticker,
        status: item.status,
        documents: (item.documents || []).length,
        metrics: (item.metrics || []).length,
        gaps: (item.data_gaps || []).join("; ")
      }));
      return `<h3>Global Peer Coverage</h3>${table(rows, [["Peer", "peer"], ["Status", "status"], ["Documents", "documents"], ["Metrics", "metrics"], ["Gaps", "gaps"]])}`;
    }

    function ideaScorer() {
      if (!current.ideas.length) {
        return `<p class="muted">No ideas are available to score. Check Research Radar coverage notes and source filings.</p>`;
      }
      const rows = current.ideas.map(idea => ({
        idea: idea.title,
        total: idea.score ? idea.score.total : "n/a",
        research: idea.score ? (idea.score.research_quality ?? "n/a") : "n/a",
        evidenceScore: idea.score ? (idea.score.evidence_strength_score ?? "n/a") : "n/a",
        valuationComplete: idea.score ? (idea.score.valuation_completeness ?? "n/a") : "n/a",
        marketConfidence: idea.score ? (idea.score.market_capture_confidence ?? "n/a") : "n/a",
        actionability: idea.score ? (idea.score.actionability ?? "n/a") : "n/a",
        evidence: idea.score ? idea.score.evidence_strength : "n/a",
        novelty: idea.score ? idea.score.novelty : "n/a",
        payoff: idea.score ? idea.score.valuation_payoff : "n/a",
        specificity: idea.score ? idea.score.thesis_specificity : "n/a",
        timing: idea.score ? idea.score.catalyst_timing : "n/a",
        reproducibility: idea.score ? idea.score.reproducibility : "n/a",
        stage: idea.stage,
        capture: idea.market_capture ? (idea.market_capture.capture_mode || idea.market_capture.category || "Unknown") : "Unknown",
        price: idea.market_capture && idea.market_capture.price_reaction_pct !== null ? `${idea.market_capture.price_reaction_pct.toFixed(1)}%` : "n/a",
        consensus: idea.market_capture && idea.market_capture.consensus_revision_pct !== null ? `${idea.market_capture.consensus_revision_pct.toFixed(1)}%` : "Not connected",
        priceStatus: idea.market_capture ? (idea.market_capture.price_status || "unknown") : "unknown",
        consensusStatus: idea.market_capture ? (idea.market_capture.consensus_status || "unknown") : "unknown",
        diagnosis: idea.market_capture ? (idea.market_capture.diagnosis || idea.market_capture.explanation || "n/a") : "n/a"
      }));
      const top = current.ideas[0];
      const scenarios = top ? top.scenarios.map(s => ({
        name: s.name,
        probability: `${Math.round(s.probability * 100)}%`,
        entry: number(s.entry_value),
        exit: number(s.exit_value),
        stockMove: pct(stockReturn(s.entry_value, s.exit_value)),
        positionBeforeCosts: pct(s.gross_return_pct),
        payoff: s.net_return_pct === null ? "Incomplete" : `${s.net_return_pct >= 0 ? "+" : ""}${s.net_return_pct.toFixed(1)}%`,
        status: s.probability_status,
        assumptions: s.assumptions.join("; ")
      })) : [];
      const model = top && top.payoff_model ? top.payoff_model : null;
      const ev = model ? model.expected_value_pct : expectedValue(top ? top.scenarios : []);
      const managementRows = current.ideas.flatMap(idea => {
        const event = (idea.source_events || [])[0] || {};
        const metrics = event.metrics || {};
        if (!(metrics.management_claim_id || metrics.meeting_event_id || metrics.sentiment_label)) return [];
        return [{
          idea: idea.title,
          signal: event.category,
          sentiment: metrics.sentiment_label || metrics.cross_check_status || "n/a",
          score: metrics.sentiment_score ?? "n/a",
          specificity: metrics.specificity_score ?? "n/a",
          cross: metrics.cross_check_status || "n/a",
          evasion: (metrics.evasion_terms || []).join(", ") || "n/a",
          uncertainty: (metrics.uncertainty_terms || []).join(", ") || "n/a"
        }];
      });
      const payoffNote = model ? `<p class="muted">Payoff completeness: ${esc(model.payoff_completeness ? model.payoff_completeness.status : model.status)}. Probability source: ${esc(model.probability_provenance ? model.probability_provenance.source : "illustrative_default")}; rank eligible: ${model.rank_eligible ? "yes" : "no"}. ${esc((model.data_gaps || []).join(" "))}</p>
        <div class="summary"><div class="metric"><span>Illustrative EV</span><strong>${ev === null || ev === undefined ? "Unavailable" : `${ev.toFixed(1)}%`}</strong></div><div class="metric"><span>Payoff Status</span><strong>${esc(model.status)}</strong></div><div class="metric"><span>Probability</span><strong>${esc(model.probability_provenance ? model.probability_provenance.status : "Uncalibrated")}</strong></div></div>` : "";
      const scenarioConvention = top && top.direction === "Short"
        ? "<p class='muted'>Scenario labels describe the stock outcome. For Short ideas, lower exits create positive position payoff after borrow, dividends, and costs.</p>"
        : "<p class='muted'>Scenario labels describe the stock outcome; position payoff applies dividends and transaction costs.</p>";
      return `${valuationPanel()}<h2>Idea Quality Scores</h2>${table(rows, [["Idea", "idea"], ["Stage", "stage"], ["Total", "total"], ["Research", "research"], ["Evidence Strength", "evidenceScore"], ["Valuation Completeness", "valuationComplete"], ["Market-Capture Confidence", "marketConfidence"], ["Actionability", "actionability"], ["Evidence /25", "evidence"], ["Novelty /15", "novelty"], ["Valuation /20", "payoff"], ["Specificity /15", "specificity"], ["Timing /10", "timing"], ["Reproducibility /5", "reproducibility"], ["Capture", "capture"], ["Price Status", "priceStatus"], ["Consensus Status", "consensusStatus"], ["Diagnosis", "diagnosis"]])}
        <h2>Management Signal Quality</h2>${table(managementRows, [["Idea", "idea"], ["Signal", "signal"], ["Sentiment", "sentiment"], ["Score", "score"], ["Specificity", "specificity"], ["Cross-check", "cross"], ["Evasion", "evasion"], ["Uncertainty", "uncertainty"]])}
        <h2>Scenario + Payoff</h2>${payoffNote}${scenarioConvention}${table(scenarios, [["Scenario", "name"], ["Probability", "probability"], ["Status", "status"], ["Entry", "entry"], ["Exit", "exit"], ["Stock move", "stockMove"], ["Position before costs", "positionBeforeCosts"], ["Net position payoff", "payoff"], ["Assumptions", "assumptions"]])}`;
    }

    function priceMoveAttribution() {
      const rows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return [{
          idea: idea.title,
          class: attribution.classification,
          confidence: attribution.confidence,
          window: attribution.return_window || "n/a",
          raw: pct(attribution.raw_return_pct),
          market: pct(attribution.market_relative_pct),
          sector: pct(attribution.sector_relative_pct),
          beta: pct(attribution.beta_adjusted_pct),
          peer: pct(attribution.peer_sympathy_pct),
          consensus: pct(attribution.consensus_revision_pct),
          narrative: attribution.narrative_saturation || "Unknown",
          readiness: attribution.attribution_readiness || "Unknown",
          quality: `${attribution.attribution_quality_score ?? 0}/100`,
          summary: (attribution.attribution_summary || []).slice(0, 4).join("; "),
          residual: attribution.residual_explanation || ""
        }];
      });
      const qualityRows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return (attribution.attribution_quality || []).map(item => ({
          idea: idea.title,
          area: item.area,
          status: item.status,
          score: item.score,
          summary: item.summary,
          evidence: (item.evidence || []).slice(0, 3).join("; "),
          gaps: (item.gaps || []).slice(0, 3).join("; "),
          next: item.next_action,
          impact: item.stage_impact
        }));
      });
      const factors = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return (attribution.factors || []).map(factor => ({
          idea: idea.title,
          driver: factor.label,
          type: factor.driver_type,
          direction: factor.direction,
          confidence: factor.confidence,
          magnitude: pct(factor.magnitude_pct),
          tier: factor.source_tier,
          why: factor.explanation,
          disconfirm: factor.disconfirming_evidence
        }));
      });
      const waterfallRows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        const waterfall = attribution ? attribution.waterfall : null;
        if (!waterfall) return [];
        const componentRows = (waterfall.components || []).map(component => ({
          idea: idea.title,
          component: component.label,
          type: component.component_type,
          contribution: pct(component.contribution_pct),
          confidence: component.confidence,
          source: component.source,
          why: component.explanation
        }));
        componentRows.push({
          idea: idea.title,
          component: "Residual company-specific move",
          type: "residual",
          contribution: pct(waterfall.residual_pct),
          confidence: attribution.confidence,
          source: "Waterfall balance",
          why: attribution.residual_explanation || ""
        });
        return componentRows;
      });
      const attributionAuditRows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return [
          ...(attribution.classification_evidence || []).map(item => ({ idea: idea.title, area: "Classification evidence", item })),
          ...(attribution.falsification_tests || []).map(item => ({ idea: idea.title, area: "Falsification test", item })),
          ...(attribution.next_attribution_checks || []).map(item => ({ idea: idea.title, area: "Next attribution check", item }))
        ];
      });
      const factorContextRows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return (attribution.factor_context || []).map(item => ({
          idea: idea.title,
          factor: item.factor_name,
          return: pct(item.factor_return_pct),
          beta: item.beta ?? "n/a",
          contribution: pct(item.contribution_pct),
          window: item.window || "n/a",
          confidence: item.confidence,
          asof: item.source_as_of || "n/a",
          source: item.source
        }));
      });
      const positioningRows = current.ideas.flatMap(idea => {
        const attribution = idea.driver_attribution;
        if (!attribution) return [];
        return [
          ...(attribution.positioning_context || []).map(item => ({
            idea: idea.title,
            provider: item.provider,
            signal: item.label,
            value: item.value ?? "n/a",
            direction: item.direction,
            confidence: item.confidence,
            asof: item.source_as_of || "n/a",
            summary: item.summary
          })),
          ...(attribution.liquidity_context || []).map(item => ({
            idea: idea.title,
            provider: item.source,
            signal: item.label,
            value: item.value ?? "n/a",
            direction: item.direction,
            confidence: item.confidence,
            asof: "event window",
            summary: item.summary
          })),
          ...(attribution.options_context || []).map(item => ({
            idea: idea.title,
            provider: item.provider,
            signal: `Options ${item.status}`,
            value: pct(item.implied_move_pct),
            direction: item.skew_signal || "Unknown",
            confidence: item.confidence,
            asof: item.source_as_of || "n/a",
            summary: item.summary
          }))
        ];
      });
      const evidence = current.external_evidence || { status: "Unavailable", evidence: [], provider_statuses: [] };
      const statuses = (evidence.provider_statuses || []).map(status => ({
        provider: status.provider,
        status: status.status,
        official: status.official,
        entitlement: status.entitlement_status,
        observed: status.observed_at,
        message: status.message
      }));
      const macroItems = (evidence.evidence || []).filter(item => item.source_type === "macro_factor" || item.source_type === "china_macro");
      const macroRow = item => ({
        provider: item.provider,
        series: item.metric_name || "n/a",
        title: item.title,
        asof: item.source_as_of || "n/a",
        release: item.release_date || "n/a",
        vintage: item.vintage_date || "n/a",
        safe: item.lookahead_safe ? "yes" : "no",
        frequency: item.frequency || "n/a",
        change: item.metric_value ?? "n/a",
        unit: item.unit || "n/a",
        direction: item.direction,
        summary: item.summary
      });
      const officialMacroRows = macroItems.filter(item => !["World Bank macro", "IMF macro"].includes(item.provider) && item.source_type !== "china_macro").map(macroRow);
      const globalMacroRows = macroItems.filter(item => ["World Bank macro", "IMF macro"].includes(item.provider) || item.source_type === "china_macro").map(macroRow);
      const narrativeRows = (evidence.evidence || []).filter(item => item.source_type === "narrative_saturation").map(item => ({
        provider: item.provider,
        signal: item.title,
        tier: item.source_tier,
        score: item.metric_value ?? "n/a",
        asof: item.source_as_of || "n/a",
        summary: item.summary
      }));
      const analystRows = (evidence.evidence || []).filter(item => ["external_analyst_context", "management_transcript_context", "external_market_context"].includes(item.source_type)).map(item => ({
        provider: item.provider,
        type: item.source_type,
        title: item.title,
        tier: item.source_tier,
        asof: item.source_as_of || "n/a",
        role: item.disqualifies_high_conviction ? "Context only" : "Can support",
        summary: item.summary
      }));
      const externalRows = (evidence.evidence || []).map(item => ({
        provider: item.provider,
        type: item.source_type,
        title: item.title,
        tier: item.source_tier,
        confidence: item.confidence,
        asof: item.source_as_of || "n/a",
        metric: item.metric_name || "n/a",
        value: item.metric_value ?? "n/a",
        summary: item.summary
      }));
      return `<h2>Price Move Attribution</h2>
        <p class="muted">Driver attribution is a probabilistic research explanation, not a definitive causal claim.</p>
        <h3>Event Return Decomposition</h3>${table(rows, [["Idea", "idea"], ["Class", "class"], ["Readiness", "readiness"], ["Quality", "quality"], ["Confidence", "confidence"], ["Window", "window"], ["Raw", "raw"], ["Market-relative", "market"], ["Sector-relative", "sector"], ["Beta-adjusted", "beta"], ["Peer sympathy", "peer"], ["Consensus", "consensus"], ["Narrative", "narrative"], ["Summary", "summary"], ["Residual", "residual"]])}
        <h3>Attribution Quality Checklist</h3>${table(qualityRows, [["Idea", "idea"], ["Area", "area"], ["Status", "status"], ["Score", "score"], ["Summary", "summary"], ["Evidence", "evidence"], ["Gaps", "gaps"], ["Next action", "next"], ["Stage impact", "impact"]])}
        <h3>Attribution Waterfall</h3>${table(waterfallRows, [["Idea", "idea"], ["Component", "component"], ["Type", "type"], ["Contribution", "contribution"], ["Confidence", "confidence"], ["Source", "source"], ["Why", "why"]])}
        <h3>Attribution Audit Trail</h3>${table(attributionAuditRows, [["Idea", "idea"], ["Area", "area"], ["Item", "item"]])}
        <h3>Style / Factor Context</h3>${table(factorContextRows, [["Idea", "idea"], ["Factor", "factor"], ["Return", "return"], ["Beta", "beta"], ["Contribution", "contribution"], ["Window", "window"], ["Confidence", "confidence"], ["As of", "asof"], ["Source", "source"]])}
        <h3>Positioning, Liquidity, and Options</h3>${table(positioningRows, [["Idea", "idea"], ["Provider", "provider"], ["Signal", "signal"], ["Value", "value"], ["Direction", "direction"], ["Confidence", "confidence"], ["As of", "asof"], ["Summary", "summary"]])}
        <h3>Likely Driver Factors</h3>${table(factors, [["Idea", "idea"], ["Driver", "driver"], ["Type", "type"], ["Direction", "direction"], ["Confidence", "confidence"], ["Magnitude", "magnitude"], ["Tier", "tier"], ["Why", "why"], ["Disconfirm if", "disconfirm"]])}
        <h3>Official Macro Context</h3>${table(officialMacroRows, [["Provider", "provider"], ["Series", "series"], ["Title", "title"], ["As of", "asof"], ["Release", "release"], ["Vintage", "vintage"], ["Lookahead-safe", "safe"], ["Frequency", "frequency"], ["Change", "change"], ["Unit", "unit"], ["Direction", "direction"], ["Summary", "summary"]])}
        <h3>Global / ADR Macro Context</h3>${table(globalMacroRows, [["Provider", "provider"], ["Series", "series"], ["Title", "title"], ["As of", "asof"], ["Lookahead-safe", "safe"], ["Change", "change"], ["Unit", "unit"], ["Summary", "summary"]])}
        <h3>Narrative Saturation</h3>${table(narrativeRows, [["Provider", "provider"], ["Signal", "signal"], ["Tier", "tier"], ["Score", "score"], ["As of", "asof"], ["Summary", "summary"]])}
        ${wisburgLensPanel()}
        <h3>External Analyst Context</h3><p class="muted">Wisburg and similar third-party research can sharpen counter-theses and narrative awareness, but cannot independently create High-Conviction ideas.</p>${table(analystRows, [["Provider", "provider"], ["Type", "type"], ["Title", "title"], ["Tier", "tier"], ["As of", "asof"], ["High-conviction role", "role"], ["Summary", "summary"]])}
        <h3>External Evidence Sources</h3><p class="muted">Status: ${esc(evidence.status || "Unavailable")}; evidence items: ${(evidence.evidence || []).length}. Narrative and third-party evidence are supporting context only.</p>
        ${table(statuses, [["Provider", "provider"], ["Status", "status"], ["Official", "official"], ["Entitlement", "entitlement"], ["Observed", "observed"], ["Message", "message"]])}
        ${table(externalRows, [["Provider", "provider"], ["Type", "type"], ["Title", "title"], ["Tier", "tier"], ["Confidence", "confidence"], ["As of", "asof"], ["Metric", "metric"], ["Value", "value"], ["Summary", "summary"]])}`;
    }

    function evidenceClosurePanel() {
      const report = current.evidence_closure || {};
      const rows = (report.outcomes || []).map(outcome => ({
        work: outcome.work_id,
        outcome: String(outcome.status || "").replaceAll("_", " "),
        finding: outcome.summary,
        adapters: (outcome.attempted_adapters || []).map(item => item.adapter).join("; "),
        next: outcome.next_action
      }));
      return `<h2>Automatic Evidence Closure</h2>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(report.status || "Unavailable")}</strong></div>
          <div class="metric"><span>Resolved</span><strong>${esc(report.resolved_count ?? 0)}</strong></div>
          <div class="metric"><span>Contradicted</span><strong>${esc(report.contradicted_count ?? 0)}</strong></div>
          <div class="metric"><span>Licensed / manual</span><strong>${esc(report.licensed_or_manual_count ?? 0)}</strong></div>
        </div><p class="muted">${esc(report.summary || "Run research to execute registered evidence tasks.")}</p>
        ${table(rows, [["Work order", "work"], ["Outcome", "outcome"], ["Finding", "finding"], ["Adapters tried", "adapters"], ["Next action", "next"]])}`;
    }

    function marketImpliedPanel() {
      const implied = current.market_implied_expectations || {};
      const rows = (implied.expectations || []).map(item => ({
        metric: item.metric,
        value: item.implied_value === null || item.implied_value === undefined ? "Insufficient data" : `${number(item.implied_value)} ${item.unit}`,
        confidence: item.confidence,
        interpretation: item.interpretation,
        formula: item.formula,
        missing: (item.missing_inputs || []).join("; ")
      }));
      const assumptionFields = (implied.assumptions || []).map(item => `
        <label>${esc(item.name)}
          <input class="market-implied-input" data-key="${esc(item.key || item.name)}" type="number"
            value="${esc(item.value ?? 0)}" min="${esc(item.minimum ?? "")}" max="${esc(item.maximum ?? "")}" step="${esc(item.step ?? 0.1)}" />
        </label>`).join("");
      const assumptionForm = assumptionFields ? `<details><summary>Edit reverse-model assumptions</summary>
        <p class="muted">Defaults compute immediately. Saved overrides remain user assumptions and recalculate on the next research run.</p>
        <div class="grid">${assumptionFields}</div>
        <div class="actions"><button id="market-implied-save" type="button">Save Assumptions</button><button id="market-implied-reset" type="button">Reset Defaults</button></div>
        <p id="market-implied-status" class="muted"></p></details>` : "";
      return `<h2>Reverse FCF / DCF and Market-Implied Expectations</h2>
        <p>${esc(implied.summary || "Reverse-model output is unavailable.")}</p>
        <p class="muted">Price source: ${esc(implied.price_source || "Unknown")}; as of ${esc(implied.price_as_of || "Unknown")}. Financial basis: ${esc(implied.financial_basis || "Unknown")}; period ${esc(implied.financial_period || "Unknown")}. These are transparent reverse-engineered assumptions, not analyst consensus.</p>
        ${assumptionForm}
        ${(implied.data_gaps || []).map(gap => `<p class="muted">Gap: ${esc(gap)}</p>`).join("")}
        ${table(rows, [["Variable", "metric"], ["Implied value", "value"], ["Confidence", "confidence"], ["Interpretation", "interpretation"], ["Formula", "formula"], ["Missing inputs", "missing"]])}`;
    }

    function bindMarketImpliedActions() {
      const ticker = current.identity && current.identity.ticker;
      const status = document.getElementById("market-implied-status");
      const save = document.getElementById("market-implied-save");
      const reset = document.getElementById("market-implied-reset");
      if (save) save.addEventListener("click", async () => {
        const assumptions = {};
        document.querySelectorAll(".market-implied-input").forEach(input => {
          assumptions[input.dataset.key] = Number(input.value);
        });
        const response = await fetch("/api/market-implied-assumptions", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ticker, assumptions})
        });
        if (status) status.textContent = response.ok
          ? "Saved. Run research again to recalculate the reverse model with these assumptions."
          : "Could not save assumptions.";
      });
      if (reset) reset.addEventListener("click", async () => {
        const response = await fetch(`/api/market-implied-assumptions?ticker=${encodeURIComponent(ticker)}`, {method: "DELETE"});
        if (status) status.textContent = response.ok
          ? "Defaults restored. Run research again to recalculate."
          : "Could not reset assumptions.";
      });
    }

    function earningsSurprisePanel() {
      const proxy = current.earnings_surprise_proxy || {};
      const rows = (proxy.items || []).map(item => ({
        event: item.event_label,
        date: item.event_date || "Unknown",
        period: item.reporting_period || "Unknown",
        metric: item.metric,
        actual: number(item.actual),
        estimate: number(item.estimate),
        surprise: item.surprise_pct === null || item.surprise_pct === undefined ? "Unknown" : `${number(item.surprise_pct)}%`,
        estimateAsOf: item.estimate_as_of || "Unknown",
        estimateSource: item.estimate_source,
        actualSource: item.actual_source,
        confidence: item.confidence
      }));
      return `<h2>Earnings-Surprise Proxy</h2>
        <p>${esc(proxy.headline || "No reported actual is matched to an eligible contemporaneous estimate.")}</p>
        <p class="muted">${esc(proxy.methodology || "This measures surprise, not subsequent analyst revisions.")}</p>
        ${(proxy.data_gaps || []).map(gap => `<p class="muted">Limitation: ${esc(gap)}</p>`).join("")}
        ${table(rows, [["Event", "event"], ["Date", "date"], ["Period", "period"], ["Metric", "metric"], ["Actual", "actual"], ["Estimate", "estimate"], ["Surprise", "surprise"], ["Estimate as of", "estimateAsOf"], ["Estimate source", "estimateSource"], ["Actual source", "actualSource"], ["Confidence", "confidence"]])}`;
    }

    function recentMarketContextPanel() {
      const context = current.recent_market_context || {};
      const rows = (context.windows || []).map(item => ({
        window: item.label,
        stock: item.return_pct === null || item.return_pct === undefined ? "Unknown" : `${number(item.return_pct)}%`,
        benchmark: item.benchmark_return_pct === null || item.benchmark_return_pct === undefined ? "Unknown" : `${number(item.benchmark_return_pct)}%`,
        relative: item.relative_return_pct === null || item.relative_return_pct === undefined ? "Unknown" : `${number(item.relative_return_pct)}%`,
        status: item.status
      }));
      return `<h2>Recent Market Context</h2>
        <p>${esc(context.summary || "Recent market context is unavailable.")}</p>
        <p class="muted">Source: ${esc(context.source || "Unknown")}; price as of ${esc(context.price_as_of || "Unknown")}; 60-day annualized volatility ${esc(context.annualized_volatility_pct === null || context.annualized_volatility_pct === undefined ? "Unknown" : `${number(context.annualized_volatility_pct)}%`)}; max drawdown ${esc(context.max_drawdown_pct === null || context.max_drawdown_pct === undefined ? "Unknown" : `${number(context.max_drawdown_pct)}%`)}.</p>
        ${table(rows, [["Window", "window"], ["Stock return", "stock"], ["SPY return", "benchmark"], ["Relative return", "relative"], ["Status", "status"]])}
        ${(context.thesis_implications || []).map(item => `<p>${esc(item)}</p>`).join("")}
        ${(context.data_gaps || []).map(gap => `<p class="muted">Gap: ${esc(gap)}</p>`).join("")}`;
    }

    function decisionModels() {
      const graphs = current.causal_thesis_graphs || [];
      const graph = graphs[0] || {};
      const edgeRows = (graph.edges || []).map(edge => ({
        connection: edge.label,
        status: edge.status,
        score: edge.score,
        evidence: (edge.evidence || []).slice(0, 2).join("; ") || "Unknown",
        missing: (edge.missing_evidence || []).slice(0, 2).join("; ") || "None",
        next: edge.next_automatic_action
      }));
      const model = current.company_model || {};
      const caseRows = (model.cases || []).map(item => ({
        case: item.name,
        revenue: number(item.revenue),
        margin: item.operating_margin_pct === null || item.operating_margin_pct === undefined ? "Unknown" : `${number(item.operating_margin_pct)}%`,
        income: number(item.net_income),
        fcf: number(item.free_cash_flow),
        fairValue: number(item.fair_value)
      }));
      const modes = current.research_modes || {};
      const modeRows = (modes.modes || []).sort((a, b) => Number(b.recommended) - Number(a.recommended) || b.score - a.score).map(item => ({
        mode: item.label,
        recommended: item.recommended ? "Yes" : "No",
        readiness: item.status,
        score: item.score,
        available: (item.available_metrics || []).join(", "),
        missing: (item.missing_metrics || []).join(", "),
        next: (item.next_actions || ["Ready for analysis"])[0]
      }));
      return `<h2>Driver-Specific Research Modes</h2>
        ${table(modeRows, [["Mode", "mode"], ["Recommended", "recommended"], ["Readiness", "readiness"], ["Score", "score"], ["Available metrics", "available"], ["Missing metrics", "missing"], ["Next action", "next"]])}
        <h2>Causal Thesis Graph</h2><p>${esc(graph.summary || "No idea graph is available.")}</p>
        ${table(edgeRows, [["Connection", "connection"], ["Status", "status"], ["Score", "score"], ["Evidence", "evidence"], ["Exact missing evidence", "missing"], ["Automatic next action", "next"]])}
        <h2>Company Model Workspace</h2><p class="muted">${esc(model.summary || "No auditable model is available.")}</p>
        ${table(caseRows, [["Case", "case"], ["Revenue", "revenue"], ["Operating margin", "margin"], ["Net income", "income"], ["FCF", "fcf"], ["Fair value", "fairValue"]])}`;
    }

    function managementSources() {
      const mgmt = current.management_sources || {};
      const documents = (mgmt.documents || []).map(doc => ({
        type: doc.source_type,
        provider: doc.provider,
        tier: `Tier ${doc.source_tier}`,
        date: doc.event_date || "n/a",
        title: doc.title,
        policy: doc.raw_payload_policy
      }));
      const claims = (mgmt.claims || []).map(claim => ({
        status: claim.status,
        type: claim.claim_type,
        source: claim.source_type,
        tier: `Tier ${claim.source_tier}`,
        speaker: claim.speaker || "n/a",
        sentiment: claim.sentiment_label || "n/a",
        score: claim.sentiment_score ?? "n/a",
        specificity: claim.specificity_score ?? "n/a",
        evasion: (claim.evasion_terms || []).join(", ") || "n/a",
        statement: claim.statement
      }));
      const checks = (mgmt.cross_checks || []).map(check => ({
        status: check.status,
        type: check.check_type,
        source: check.source_type,
        tier: `Tier ${check.source_tier}`,
        summary: check.summary
      }));
      const meetings = (mgmt.meeting_events || []).map(event => ({
        type: event.event_type,
        status: event.status,
        date: event.event_date || "n/a",
        description: event.description
      }));
      const turns = (mgmt.transcript_turns || []).slice(0, 20).map(turn => ({
        speaker: turn.speaker,
        section: turn.section,
        sentiment: turn.sentiment_label || turn.sentiment || "n/a",
        score: turn.sentiment_score ?? "n/a",
        confidence: turn.sentiment_confidence || "n/a",
        specificity: turn.specificity_score ?? "n/a",
        evasion: (turn.evasion_terms || []).join(", ") || "n/a",
        uncertainty: (turn.uncertainty_terms || []).join(", ") || "n/a",
        text: turn.text
      }));
      const gaps = (mgmt.data_gaps || []).map(message => `<p class="muted">${esc(message)}</p>`).join("");
      return `<h2>Management Sources</h2>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(mgmt.status || "Unavailable")}</strong></div>
          <div class="metric"><span>Documents</span><strong>${documents.length}</strong></div>
          <div class="metric"><span>Claims</span><strong>${claims.length}</strong></div>
          <div class="metric"><span>Cross-checks</span><strong>${checks.length}</strong></div>
        </div>${gaps}
        <h3>Documents</h3>${table(documents, [["Type", "type"], ["Provider", "provider"], ["Tier", "tier"], ["Date", "date"], ["Title", "title"], ["Raw policy", "policy"]])}
        <h3>Management Claims</h3>${table(claims, [["Status", "status"], ["Type", "type"], ["Source", "source"], ["Tier", "tier"], ["Speaker", "speaker"], ["Sentiment", "sentiment"], ["Score", "score"], ["Specificity", "specificity"], ["Evasion", "evasion"], ["Statement", "statement"]])}
        <h3>Cross-Checks</h3>${table(checks, [["Status", "status"], ["Type", "type"], ["Source", "source"], ["Tier", "tier"], ["Summary", "summary"]])}
        <h3>Meeting / Proxy Events</h3>${table(meetings, [["Type", "type"], ["Status", "status"], ["Date", "date"], ["Description", "description"]])}
        <h3>Transcript Turns</h3>${table(turns, [["Speaker", "speaker"], ["Section", "section"], ["Sentiment", "sentiment"], ["Score", "score"], ["Confidence", "confidence"], ["Specificity", "specificity"], ["Evasion", "evasion"], ["Uncertainty", "uncertainty"], ["Text", "text"]])}`;
    }

    function thesisMonitor() {
      const ideas = current.ideas || [];
      const calibration = current.calibration || {};
      const snapshot = current.daily_snapshot_status || {};
      const watchlisted = current.watchlist_status && current.watchlist_status.active;
      const watchlist = `<h2>Watchlist</h2><button id="watchlist-toggle" class="primary">${watchlisted ? "Remove from watchlist" : "Add to watchlist"}</button>
        <p class="muted">Daily snapshots read the same SQLite watchlist.</p>`;
      const dailySnapshot = `<h2>Daily Research Snapshot</h2>${snapshot.run_date ? `<div class="summary">
          <div class="metric"><span>Overall</span><strong>${esc(snapshot.overall_status || "Unknown")}</strong></div>
          <div class="metric"><span>Consensus</span><strong>${esc(snapshot.consensus_status || "Unknown")}</strong></div>
          <div class="metric"><span>Prices</span><strong>${esc(snapshot.price_status || "Unknown")}</strong></div>
          <div class="metric"><span>Wisburg</span><strong>${esc(snapshot.wisburg_status || "Unknown")}</strong></div>
        </div><p class="muted">Run date: ${esc(snapshot.run_date)}; alerts: ${esc(snapshot.alerts_created || 0)}; same-day Wisburg cache: ${snapshot.used_same_day_wisburg_cache ? "reused" : "not reused"}.</p>` : `<p class="muted">No scheduled snapshot has run for this ticker.</p>`}`;
      const alertRows = (current.active_alerts || []).map(alert => ({
        id: alert.alert_id, severity: alert.severity, title: alert.title,
        message: alert.message, created: alert.created_at
      }));
      const alerts = `<h2>Alert Inbox</h2>${table(alertRows, [["ID", "id"], ["Severity", "severity"], ["Alert", "title"], ["Message", "message"], ["Created", "created"]])}
        ${(current.active_alerts || []).map(alert => `<button class="alert-read" data-alert-id="${alert.alert_id}">Mark #${alert.alert_id} read</button>`).join(" ")}`;
      const calibrationRows = (calibration.slices || []).map(item => ({
        slice: item.signal_type,
        status: item.status,
        sample: `${item.sample_size ?? 0}/${calibration.minimum_sample_size ?? "n/a"}`,
        needed: item.outcomes_needed_for_calibration ?? "n/a",
        rank: item.rank_by_ev_allowed ? "Yes" : "No",
        hit: pct(item.hit_rate_pct),
        brier: item.brier_score === null || item.brier_score === undefined ? "n/a" : Number(item.brier_score).toFixed(3),
        action: item.next_action || "n/a"
      }));
      const calibrationCheckRows = (calibration.readiness_checks || []).map(item => ({
        area: item.area,
        status: item.status,
        score: item.score,
        summary: item.summary,
        evidence: (item.evidence || []).join("; "),
        gaps: (item.gaps || []).join("; "),
        action: item.next_action || "n/a",
        impact: item.stage_impact || "n/a"
      }));
      const calibrationPanel = `<h2>Outcome Calibration</h2>
        <div class="summary">
          <div class="metric"><span>Status</span><strong>${esc(calibration.status || "n/a")}</strong></div>
          <div class="metric"><span>Resolved</span><strong>${esc(calibration.sample_size ?? 0)}</strong></div>
          <div class="metric"><span>Needed</span><strong>${esc(calibration.outcomes_needed_for_calibration ?? "n/a")}</strong></div>
          <div class="metric"><span>Rank By EV</span><strong>${calibration.rank_by_ev_allowed ? "Yes" : "No"}</strong></div>
          <div class="metric"><span>Readiness Score</span><strong>${esc(calibration.readiness_score ?? "n/a")}/100</strong></div>
        </div>
        <h3>Calibration Readiness Checklist</h3>
        ${table(calibrationCheckRows, [["Area", "area"], ["Status", "status"], ["Score", "score"], ["Summary", "summary"], ["Evidence", "evidence"], ["Gaps", "gaps"], ["Next action", "action"], ["Stage impact", "impact"]])}
        ${table(calibrationRows, [["Slice", "slice"], ["Status", "status"], ["Sample", "sample"], ["Needed", "needed"], ["Rank", "rank"], ["Hit", "hit"], ["Brier", "brier"], ["Next action", "action"]])}`;
      if (!ideas.length) return `${watchlist}${dailySnapshot}${alerts}${calibrationPanel}<p class="muted">No ideas available for monitoring.</p>`;
      return `${watchlist}${dailySnapshot}${alerts}${calibrationPanel}<h2>Continuous Thesis Monitor</h2><div class="grid">${ideas.slice(0, 4).map(idea => `
        <section class="monitor"><h3>${esc(idea.title)}</h3>
        ${(idea.monitor_items || []).map(item => `<p><strong>${esc(item.criterion)}:</strong> ${esc(item.data_source)} | ${esc(item.cadence)}<br>
        Confirm: ${esc(item.confirm_trigger)}<br>Break: ${esc(item.break_trigger)}</p>`).join("")}
        </section>`).join("")}</div>`;
    }

    function consensusPanel() {
      const packageData = current.consensus || {};
      const target = packageData.target;
      const selectedTarget = target ? (target.target_kind === "aggregate" ? target.target_aggregate : target.target_kind === "median" ? target.target_median : target.target_mean) : null;
      const targetHtml = target ? `<div class="summary">
        <div class="metric"><span>Current Price</span><strong>${number(target.current_price)} ${esc(target.currency)}</strong></div>
        <div class="metric"><span>${esc(target.target_label || "Target")}</span><strong>${number(selectedTarget)} ${esc(target.currency)}</strong></div>
        <div class="metric"><span>Target Kind</span><strong>${esc(target.target_kind || "Unknown")}</strong></div>
        <div class="metric"><span>Implied Upside</span><strong>${pct(target.implied_upside_pct)}</strong></div>
        <div class="metric"><span>Analysts</span><strong>${esc(target.analyst_count ?? "Unknown")}</strong></div>
      </div><p class="muted">Selected provider: ${esc(target.source || packageData.provider)}. High / low: ${number(target.target_high)} / ${number(target.target_low)}. Observed: ${esc(target.observed_at || target.as_of)}. Source as-of: ${esc(target.source_as_of || target.provider_timestamp || "Unknown")}.</p>` : `<p class="muted">Consensus status: ${esc(packageData.status || "Unavailable")}</p>`;
      const targetSources = packageData.provider_targets && packageData.provider_targets.length
        ? packageData.provider_targets : (target ? [target] : []);
      const providerTargetRows = targetSources.map(item => ({
        provider: item.source, official: item.official ? "Yes" : "No", semantic: item.target_kind,
        aggregate: number(item.target_aggregate), mean: number(item.target_mean), median: number(item.target_median),
        high: number(item.target_high), low: number(item.target_low), analysts: item.analyst_count ?? "Unknown",
        observed: item.observed_at || item.as_of, sourceAsOf: item.source_as_of || item.provider_timestamp || "Unknown"
      }));
      const providerStatusRows = (packageData.provider_statuses || []).map(item => ({
        provider: item.provider, status: item.status, official: item.official ? "Yes" : "No",
        entitlement: item.entitlement_status, observed: item.observed_at, message: item.message
      }));
      const providerComparisonRows = (packageData.comparisons || []).map(item => ({
        field: item.field, values: Object.entries(item.values || {}).map(([key, value]) => `${key}: ${value}`).join("; "),
        spread: pct(item.spread_pct), interpretation: item.interpretation
      }));
      const estimateRows = (packageData.estimates || []).slice(0, 16).map(item => ({
        metric: item.metric, period: item.period_end, type: item.period_type,
        average: number(item.average), high: number(item.high), low: number(item.low), analysts: item.analyst_count ?? "Unknown",
        provider: item.source, currency: item.currency, precision: item.period_precision,
        revisions: `${item.revisions_up ?? "n/a"} / ${item.revisions_down ?? "n/a"}`
      }));
      const revisionRows = (packageData.revisions || []).map(item => ({
        metric: item.metric, window: `${item.window_days}d`, provider: item.provider || "n/a",
        status: item.status || "n/a", start: item.start_date || "n/a",
        end: item.end_date || "n/a", change: pct(item.change_pct),
        reason: item.reason || "n/a"
      }));
      const trendByPeriod = {};
      (packageData.observations || []).forEach(item => {
        if (!String(item.field || "").startsWith("recommendation_trend_")) return;
        const period = item.source_as_of || item.observed_at || "Unknown";
        if (!trendByPeriod[period]) trendByPeriod[period] = {period, provider: item.provider, analysts: item.analyst_count ?? "Unknown"};
        const label = item.field.replace("recommendation_trend_", "");
        trendByPeriod[period][label] = item.value_text ?? item.value_numeric ?? "n/a";
      });
      const trendRows = Object.values(trendByPeriod).sort((a, b) => String(b.period).localeCompare(String(a.period)));
      const bridge = current.expectations_bridge || {};
      const rec = packageData.recommendations;
      const recommendationRows = rec ? [{strongBuy: rec.strong_buy, buy: rec.buy, hold: rec.hold, sell: rec.sell, strongSell: rec.strong_sell, consensus: rec.consensus_label || "n/a"}] : [];
      const comparisonRows = (bridge.comparisons || []).map(item => ({
        metric: item.metric, period: item.period_end, expected: number(item.expected),
        actual: number(item.actual), surprise: pct(item.surprise_pct), revision: pct(item.post_event_revision_pct)
      }));
      const eventAuditRows = (bridge.event_audits || []).map(item => ({
        event: item.event_label,
        form: item.form || "Unknown",
        accession: item.accession || "Unknown",
        filed: item.filing_date || "Unknown",
        period: item.reporting_period || "Unknown",
        metrics: (item.actual_metrics_checked || []).join("; ") || "None",
        pre: item.eligible_pre_event_snapshots,
        post: item.eligible_post_event_snapshots,
        status: item.status,
        reason: item.reason
      }));
      return `<h2>Consensus and Expectations</h2>${targetHtml}
        <p>${esc(bridge.headline || "")}</p>
        <h3>Event-Specific Expectations Audit</h3>${table(eventAuditRows, [["Event", "event"], ["Form", "form"], ["Accession", "accession"], ["Filed", "filed"], ["Reporting period", "period"], ["Actual metrics checked", "metrics"], ["Pre snapshots", "pre"], ["Post snapshots", "post"], ["Status", "status"], ["Exact reason", "reason"]])}
        <h3>Provider Targets</h3>${table(providerTargetRows, [["Provider", "provider"], ["Official", "official"], ["Semantic", "semantic"], ["Aggregate", "aggregate"], ["Mean", "mean"], ["Median", "median"], ["High", "high"], ["Low", "low"], ["Analysts", "analysts"], ["Observed", "observed"], ["Source as-of", "sourceAsOf"]])}
        <h3>Provider Health</h3>${table(providerStatusRows, [["Provider", "provider"], ["Status", "status"], ["Official", "official"], ["Entitlement", "entitlement"], ["Observed", "observed"], ["Message", "message"]])}
        <h3>Provider Comparison</h3>${table(providerComparisonRows, [["Field", "field"], ["Values", "values"], ["Spread", "spread"], ["Interpretation", "interpretation"]])}
        ${table(revisionRows, [["Metric", "metric"], ["Window", "window"], ["Provider", "provider"], ["Status", "status"], ["Start", "start"], ["End", "end"], ["Change", "change"], ["Reason", "reason"]])}
        <h3>Recommendation Trend</h3>${table(trendRows, [["Period", "period"], ["Provider", "provider"], ["Strong Buy", "strong_buy"], ["Buy", "buy"], ["Hold", "hold"], ["Sell", "sell"], ["Strong Sell", "strong_sell"], ["Consensus", "consensus"], ["Analysts", "analysts"]])}
        ${table(recommendationRows, [["Strong Buy", "strongBuy"], ["Buy", "buy"], ["Hold", "hold"], ["Sell", "sell"], ["Strong Sell", "strongSell"], ["Consensus", "consensus"]])}
        ${table(estimateRows, [["Metric", "metric"], ["Period", "period"], ["Type", "type"], ["Average", "average"], ["High", "high"], ["Low", "low"], ["Analysts", "analysts"], ["Provider", "provider"], ["Currency", "currency"], ["Precision", "precision"], ["Revisions up/down", "revisions"]])}
        ${table(comparisonRows, [["Metric", "metric"], ["Period", "period"], ["Expected", "expected"], ["Actual", "actual"], ["Surprise", "surprise"], ["Post-event Revision", "revision"]])}
        <p class="muted">${esc(bridge.point_in_time_note || "")}</p>`;
    }

    function valuationPanel() {
      const valuation = current.valuation || {};
      if (valuation.status !== "Available") {
        return `<h2>Valuation Triangulation</h2><p class="muted">${esc(valuation.template || "Unknown")}: ${esc(valuation.status || "Unavailable")}. ${esc((valuation.missing_data || []).join(" "))}</p>`;
      }
      const caseRows = (valuation.cases || []).map(item => ({
        name: item.name, probability: `${Math.round(item.probability * 100)}%`,
        fair: `${number(item.fair_value)} ${valuation.currency}`, method: item.method,
        assumptions: (item.assumptions || []).join("; ")
      }));
      return `<h2>Valuation Triangulation</h2><div class="summary">
        <div class="metric"><span>Template</span><strong>${esc(valuation.template)}</strong></div>
        <div class="metric"><span>Weighted Value</span><strong>${number(valuation.probability_weighted_value)} ${esc(valuation.currency)}</strong></div>
        <div class="metric"><span>Expected Return</span><strong>${pct(valuation.expected_return_pct)}</strong></div>
        <div class="metric"><span>Vs Consensus</span><strong>${pct(valuation.disagreement_pct)}</strong></div>
      </div>${table(caseRows, [["Case", "name"], ["Probability", "probability"], ["Fair Value", "fair"], ["Method", "method"], ["Assumptions", "assumptions"]])}
      <p class="muted">${esc((valuation.normalization_notes || []).join(" "))}</p>
      <p class="muted">${esc((valuation.missing_data || []).join(" "))}</p>`;
    }

    function bindMonitorActions() {
      const watchButton = document.getElementById("watchlist-toggle");
      if (watchButton) watchButton.addEventListener("click", async () => {
        const active = current.watchlist_status && current.watchlist_status.active;
        await fetch(`/api/watchlist?ticker=${current.identity.ticker}`, { method: active ? "DELETE" : "POST" });
        current.watchlist_status.active = !active;
        renderContent();
      });
      document.querySelectorAll(".alert-read").forEach(button => button.addEventListener("click", async () => {
        await fetch(`/api/alerts/${button.dataset.alertId}/read`, { method: "POST" });
        current.active_alerts = current.active_alerts.filter(item => String(item.alert_id) !== button.dataset.alertId);
        renderContent();
      }));
    }

    function memoPack() {
      return `<h2>DD Memo + Investment Committee Pack</h2><pre>${esc(current.memo_markdown)}</pre>`;
    }

    function number(value) {
      if (value === null || value === undefined) return "n/a";
      const abs = Math.abs(value);
      if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
      if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
      if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
      return value.toFixed(1);
    }

    function pct(value) {
      return value === null || value === undefined ? "n/a" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
    }

    function stockReturn(entry, exitValue) {
      if (entry === null || entry === undefined || exitValue === null || exitValue === undefined || entry <= 0 || exitValue <= 0) return null;
      return ((exitValue - entry) / entry) * 100;
    }

    function expectedValue(scenarios) {
      if (!scenarios || !scenarios.length || scenarios.some(s => s.exit_value !== null && s.net_return_pct === null)) return null;
      const total = scenarios.reduce((sum, s) => sum + s.probability, 0);
      if (!total) return null;
      return scenarios.reduce((sum, s) => sum + s.probability * (s.net_return_pct ?? s.upside_downside_pct), 0) / total;
    }
  </script>
</body>
</html>
"""


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML)
            return
        if parsed.path == "/api/demo-cases":
            self._send_json({"demo_cases": _jsonable(demo_cases())})
            return
        if parsed.path == "/api/research-profiles":
            self._send_json({"profiles": _jsonable(research_profiles()), "default": config.RESEARCH_PROFILE})
            return
        if parsed.path == "/api/demo":
            ticker = _ticker_from_query(parsed.query)
            result = demo_result(ticker)
            self._send_json({"demo_case": _jsonable(result.demo_case), "result": _jsonable(result)})
            return
        if parsed.path == "/api/research":
            query = parse_qs(parsed.query)
            ticker = _ticker_from_query(parsed.query)
            fallback = query.get("fallback", ["0"])[0] == "1"
            research_profile = query.get("profile", [config.RESEARCH_PROFILE])[0]
            investigate_event_id = query.get("event_id", [None])[0]
            try:
                result = run_us_equity_research(
                    ticker,
                    research_profile=research_profile,
                    investigate_event_id=investigate_event_id,
                )
                self._send_json({"result": _jsonable(result)})
            except Exception as exc:  # pragma: no cover - server boundary
                if fallback:
                    self._send_json(
                        {
                            "warning": f"Live data unavailable ({exc}). Showing demo workflow.",
                            "result": _jsonable(demo_result(ticker)),
                        }
                    )
                else:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/consensus":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({"consensus": _jsonable(_stored_consensus(ticker))})
            return
        if parsed.path == "/api/watchlist":
            query = parse_qs(parsed.query)
            list_name = query.get("list", ["default"])[0]
            self._send_json({"watchlist": _jsonable(ResearchStore().list_watchlist(list_name))})
            return
        if parsed.path == "/api/alerts":
            query = parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            self._send_json({"alerts": _jsonable(ResearchStore().list_alerts(status=status))})
            return
        if parsed.path in {"/api/provider-comparison", "/api/providers"}:
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            self._send_json({
                "ticker": ticker,
                "observations": _jsonable(store.list_provider_observations(ticker, latest_only=True)),
                "comparisons": _provider_comparison_payload(store, ticker),
                "health": store.list_provider_health(),
            })
            return
        if parsed.path == "/api/provider-health":
            self._send_json({"health": ResearchStore().list_provider_health()})
            return
        if parsed.path == "/api/network-health":
            query = parse_qs(parsed.query)
            timeout = int(query.get("timeout", ["8"])[0] or "8")
            include_powershell = query.get("powershell", ["true"])[0].lower() not in {"0", "false", "no"}
            report = run_network_diagnostics(
                timeout_seconds=max(1, min(timeout, 30)),
                include_powershell=include_powershell,
            )
            self._send_json({"network": _jsonable(report)})
            return
        if parsed.path == "/api/budget-modes":
            self._send_json({
                "modes": available_budget_modes(),
                "definitions": load_budget_mode_definitions(),
                "policy_path": str(config.BUDGET_POLICY_PATH),
            })
            return
        if parsed.path == "/api/local-secrets/status":
            self._send_json({"status": _local_secret_status()})
            return
        if parsed.path == "/api/llm-profiles":
            store = ResearchStore()
            self._send_json({
                "profiles": _jsonable(store.list_llm_profiles()),
                "presets": _jsonable(list_llm_presets()),
                "selection": store.get_llm_selection(),
            })
            return
        if parsed.path == "/api/llm-health":
            store = ResearchStore()
            self._send_json({
                "profiles": _jsonable(store.list_llm_profiles()),
                "selection": store.get_llm_selection(),
            })
            return
        if parsed.path == "/api/macro-health":
            query = parse_qs(parsed.query)
            ticker = query.get("ticker", [None])[0]
            self._send_json({"macro": ResearchStore().macro_health(ticker)})
            return
        if parsed.path == "/api/price-health":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            self._send_json({
                "ticker": ticker,
                "statuses": store.list_price_provider_statuses(ticker),
                "cached_bars": store.load_price_bars(ticker),
            })
            return
        if parsed.path == "/api/market-implied-assumptions":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "assumptions": ResearchStore().latest_market_implied_assumptions(ticker),
            })
            return
        if parsed.path == "/api/event-reactions":
            ticker = _ticker_from_query(parsed.query)
            query = parse_qs(parsed.query)
            event_id = query.get("event_id", [None])[0]
            self._send_json({
                "ticker": ticker,
                "event_reactions": ResearchStore().list_event_reactions(ticker, event_id),
            })
            return
        if parsed.path == "/api/attribution":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "attributions": ResearchStore().latest_attributions(ticker),
            })
            return
        decision_routes = {
            "/api/evidence-closure": "evidence_closure",
            "/api/causal-thesis-graph": "causal_thesis_graph",
            "/api/market-implied": "market_implied",
            "/api/company-model": "company_model",
            "/api/research-modes": "research_modes",
            "/api/historical-research": "historical_research",
            "/api/change-analysis": "metric_assessments",
            "/api/promotion-evidence": "promotion_evidence",
            "/api/playbook-portfolio": "playbook_portfolio",
            "/api/expectation-events": "expectation_event_audits",
            "/api/earnings-surprise-proxy": "earnings_surprise_proxy",
            "/api/recent-market-context": "recent_market_context",
        }
        if parsed.path in decision_routes:
            ticker = _ticker_from_query(parsed.query)
            artifact_type = decision_routes[parsed.path]
            payload = ResearchStore().latest_decision_artifact(ticker, artifact_type)
            if payload is None:
                self._send_json(
                    {
                        "ticker": ticker,
                        artifact_type: None,
                        "message": "No stored decision artifact. Run research first.",
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
            else:
                self._send_json({"ticker": ticker, artifact_type: payload})
            return
        if parsed.path == "/api/external-research":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            self._send_json({
                "ticker": ticker,
                "excerpts": store.list_external_research_excerpts(ticker),
                "wisburg_lens": store.latest_wisburg_lens(ticker),
                "wisburg_delta": store.latest_wisburg_delta(ticker),
            })
            return
        if parsed.path == "/api/wisburg-delta":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "wisburg_delta": ResearchStore().latest_wisburg_delta(ticker),
            })
            return
        if parsed.path == "/api/snapshot-status":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "snapshot_status": ResearchStore().latest_daily_snapshot_status(ticker),
            })
            return
        if parsed.path == "/api/wisburg-themes":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            lens = store.latest_wisburg_lens(ticker) or {}
            self._send_json({
                "ticker": ticker,
                "themes": store.list_wisburg_themes(ticker),
                "debate_map": lens.get("debate_map"),
                "narrative_score": lens.get("narrative_score"),
                "caveats": lens.get("caveats", []),
            })
            return
        if parsed.path == "/api/wisburg-source-suggestions":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "source_suggestions": ResearchStore().list_wisburg_source_suggestions(ticker),
            })
            return
        wisburg_intelligence_routes = {
            "/api/wisburg-coverage": ("coverage", "latest_wisburg_coverage"),
            "/api/wisburg-reports": ("reports", "list_wisburg_reports"),
            "/api/wisburg-claims": ("claims", "list_wisburg_claims"),
            "/api/wisburg-revisions": ("revisions", "list_wisburg_revisions"),
            "/api/wisburg-work-orders": ("work_orders", "list_wisburg_research_tasks"),
        }
        if parsed.path in wisburg_intelligence_routes:
            ticker = _ticker_from_query(parsed.query)
            field_name, method_name = wisburg_intelligence_routes[parsed.path]
            payload = getattr(ResearchStore(), method_name)(ticker)
            self._send_json({"ticker": ticker, field_name: payload})
            return
        if parsed.path == "/api/historical-references":
            ticker = _ticker_from_query(parsed.query)
            references = build_historical_references_for_ticker(ticker, ResearchStore())
            self._send_json({
                "ticker": ticker,
                "historical_references": _jsonable(references),
            })
            return
        if parsed.path in {"/api/management-sources", "/api/management-claims", "/api/cross-checks", "/api/transcripts"}:
            ticker = _ticker_from_query(parsed.query)
            payload = ResearchStore().latest_management_sources(ticker)
            if parsed.path == "/api/management-claims":
                self._send_json({"ticker": ticker, "claims": payload["claims"]})
            elif parsed.path == "/api/cross-checks":
                self._send_json({"ticker": ticker, "cross_checks": payload["cross_checks"]})
            elif parsed.path == "/api/transcripts":
                self._send_json({"ticker": ticker, "documents": payload["documents"], "transcript_turns": payload["transcript_turns"]})
            else:
                self._send_json({"management_sources": payload})
            return
        if parsed.path == "/api/validated-claims":
            ticker = _ticker_from_query(parsed.query)
            claims = ResearchStore().latest_validated_claims(ticker)
            self._send_json({"ticker": ticker, "claims": claims})
            return
        if parsed.path == "/api/source-plan":
            ticker = _ticker_from_query(parsed.query)
            plan = ResearchStore().latest_source_plan(ticker)
            if plan:
                self._send_json({"ticker": ticker, "source_plan": plan})
            else:
                self._send_json(
                    {"ticker": ticker, "source_plan": None, "message": "No stored source plan. Run research first."},
                    status=HTTPStatus.NOT_FOUND,
                )
            return
        if parsed.path == "/api/news-claims":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            self._send_json({
                "ticker": ticker,
                "observations": store.latest_news_observations(ticker),
                "claims": store.latest_news_claims(ticker),
            })
            return
        if parsed.path == "/api/source-corroboration":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            self._send_json({
                "ticker": ticker,
                "corroboration": store.latest_source_corroboration(ticker),
                "primary_source_observations": store.latest_primary_source_observations(ticker),
                "causal_bridges": store.latest_causal_bridges(ticker),
            })
            return
        if parsed.path == "/api/primary-source-plan":
            ticker = _ticker_from_query(parsed.query)
            store = ResearchStore()
            claims = [
                news_claim_from_payload(payload)
                for payload in store.latest_news_claims(ticker)
            ]
            needs = []
            for claim in claims:
                needs.extend(source_needs_for_claim(claim))
            source_plan = store.latest_source_plan(ticker)
            if source_plan:
                plan_obj = _source_plan_from_payload(source_plan)
                if plan_obj:
                    source_plan = _jsonable(enrich_source_plan_with_news(plan_obj, claims))
            self._send_json({
                "ticker": ticker,
                "source_needs": _jsonable(needs),
                "source_plan": source_plan or None,
                "message": "News and external claims are source leads only until primary-source corroboration is attached.",
            })
            return
        if parsed.path in {"/api/entity", "/api/financial-coverage"}:
            ticker = _ticker_from_query(parsed.query)
            payload = ResearchStore().latest_entity_coverage(ticker)
            if not payload:
                self._send_json(
                    {"error": "No stored research coverage for this ticker. Run research first."},
                    status=HTTPStatus.NOT_FOUND,
                )
            elif parsed.path == "/api/entity":
                self._send_json({"entity_resolution": payload["entity_resolution"], "observed_at": payload["observed_at"]})
            else:
                self._send_json({"financial_coverage": payload["financial_coverage"], "observed_at": payload["observed_at"]})
            return
        if parsed.path == "/api/peers":
            ticker = _ticker_from_query(parsed.query)
            stored = ResearchStore().latest_peer_universe(ticker)
            self._send_json({"peer_universe": stored or _jsonable(peer_universe_for(ticker))})
            return
        if parsed.path == "/api/global-peer-coverage":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "global_peer_coverage": ResearchStore().latest_global_peer_coverage(ticker),
            })
            return
        if parsed.path == "/api/global-coverage":
            ticker = _ticker_from_query(parsed.query)
            stored = ResearchStore().latest_entity_coverage(ticker)
            identity = None
            filings = []
            if stored:
                entity = stored.get("entity_resolution", {})
                identity = CompanyIdentity(
                    ticker=str(entity.get("ticker") or ticker),
                    cik=str(entity.get("cik") or ""),
                    name=str(entity.get("name") or ticker),
                    exchange=str(entity.get("exchange") or "Unknown"),
                    sic=entity.get("sic"),
                    sic_description=entity.get("sic_description"),
                )
            case = coverage_case_for(ticker, identity, None, filings)
            self._send_json({
                "ticker": ticker,
                "coverage_case": _jsonable(case),
                "source_coverage_matrix": _jsonable(source_coverage_matrix_for(case, filings)),
                "message": "This endpoint maps the ticker to the registered global coverage testbed. Run research for metric-resolution audit values.",
            })
            return
        if parsed.path == "/api/metric-ontology":
            self._send_json({"metric_ontology": _jsonable(build_canonical_metric_ontology())})
            return
        if parsed.path == "/api/metric-resolution":
            ticker = _ticker_from_query(parsed.query)
            result = demo_result(ticker)
            self._send_json({
                "ticker": ticker,
                "metric_resolution_audit": _jsonable(build_metric_resolution_audit(
                    ticker,
                    result.metrics,
                    result.coverage_case,
                    build_canonical_metric_ontology(),
                )),
                "message": "Metric-resolution API uses the current result payload when available; demo fallback is static.",
            })
            return
        if parsed.path == "/api/peer-metrics":
            ticker = _ticker_from_query(parsed.query)
            self._send_json({
                "ticker": ticker,
                "peer_metric_readthrough": ResearchStore().latest_peer_metric_readthroughs(ticker),
            })
            return
        if parsed.path == "/api/llm-research-manifest":
            ticker = _ticker_from_query(parsed.query)
            manifest = ResearchStore().latest_llm_research_manifest(ticker)
            self._send_json({
                "ticker": ticker,
                "llm_research_manifest": manifest,
                "message": "LLM assistant lanes are provisional and cannot override deterministic promotion gates.",
            })
            return
        if parsed.path.startswith("/api/ideas/") and parsed.path.endswith("/claim-audit"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                audit = ResearchStore().idea_audit(parts[2])
                if not audit:
                    self._send_json({"error": "Idea not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                latest = audit["versions"][0]
                payload = latest.get("payload", {})
                ticker = latest.get("ticker") or payload.get("ticker") or ""
                claim_ids = set(payload.get("validated_claim_ids") or [])
                stored_claims = ResearchStore().latest_validated_claims(str(ticker))
                matched_claims = [
                    claim for claim in stored_claims
                    if claim.get("claim_id") in claim_ids
                ]
                self._send_json({
                    "idea_id": parts[2],
                    "ticker": ticker,
                    "thesis_grade_status": payload.get("thesis_grade_status", "Unvalidated"),
                    "direction_rationale": payload.get("direction_rationale", ""),
                    "validated_claim_ids": list(claim_ids),
                    "claims": matched_claims,
                    "gate_result": payload.get("gate_result"),
                })
                return
        if parsed.path.startswith("/api/ideas/") and parsed.path.endswith("/audit"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                audit = ResearchStore().idea_audit(parts[2])
                if audit:
                    self._send_json({"audit": audit})
                else:
                    self._send_json({"error": "Idea not found"}, status=HTTPStatus.NOT_FOUND)
                return
        if parsed.path == "/api/calibration":
            query = parse_qs(parsed.query)
            ticker = query.get("ticker", [None])[0]
            self._send_json({"calibration": _jsonable(build_calibration_report(ResearchStore(), ticker))})
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/llm-profiles/") and parsed.path.endswith("/test"):
            profile_id = unquote(parsed.path.strip("/").split("/")[2])
            store = ResearchStore()
            profile = store.get_llm_profile(profile_id)
            if not profile:
                self._send_json({"error": "LLM profile not found"}, status=HTTPStatus.NOT_FOUND)
                return
            status = test_llm_profile(profile, manager=LocalSecretsManager())
            store.update_llm_profile_test_status(
                profile.profile_id,
                status.status,
                status.message,
                key_configured=profile.key_configured,
            )
            self._send_json({"status": _jsonable(status), "profile": _jsonable(store.get_llm_profile(profile_id))})
            return
        if parsed.path == "/api/llm-profiles":
            payload = self._read_json()
            store = ResearchStore()
            manager = LocalSecretsManager()
            try:
                profile = save_llm_profile_with_secret(
                    store,
                    manager,
                    display_name=str(payload.get("display_name") or ""),
                    provider_preset=str(payload.get("provider_preset") or "deepseek"),
                    model=str(payload.get("model") or ""),
                    base_url=str(payload.get("base_url") or ""),
                    api_key=str(payload.get("api_key") or ""),
                    role_eligibility=str(payload.get("role_eligibility") or "primary_secondary"),
                    profile_id=str(payload.get("profile_id") or "") or None,
                )
                self._send_json({
                    "profile": _jsonable(profile),
                    "profiles": _jsonable(store.list_llm_profiles()),
                    "selection": store.get_llm_selection(),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/llm-profiles/test":
            payload = self._read_json()
            try:
                candidate = build_llm_profile(
                    display_name=str(payload.get("display_name") or ""),
                    provider_preset=str(payload.get("provider_preset") or "deepseek"),
                    model=str(payload.get("model") or ""),
                    base_url=str(payload.get("base_url") or ""),
                    key_configured=bool(payload.get("api_key")),
                )
                status = test_llm_profile(
                    candidate,
                    manager=LocalSecretsManager(),
                    api_key=str(payload.get("api_key") or ""),
                )
                self._send_json({"status": _jsonable(status)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/llm-selection":
            payload = self._read_json()
            store = ResearchStore()
            selection = store.save_llm_selection(
                str(payload.get("primary_profile_id") or ""),
                str(payload.get("secondary_profile_id") or ""),
                bool(payload.get("enable_secondary", True)),
                str(payload.get("secondary_min_stage") or config.SECONDARY_LLM_MIN_STAGE),
                str(payload.get("language_policy") or config.LLM_LANGUAGE_POLICY),
            )
            self._send_json({"selection": selection})
            return
        if parsed.path == "/api/local-secrets/test":
            payload = self._read_json()
            results = validate_provider_keys(_managed_keys_from_payload(payload))
            self._send_json({"results": _validation_results_payload(results)})
            return
        if parsed.path == "/api/local-secrets/save":
            payload = self._read_json()
            keys = _managed_keys_from_payload(payload)
            results = validate_provider_keys(keys)
            manager = LocalSecretsManager()
            try:
                outcome = save_validated_keys(manager, keys, results)
                config.refresh_runtime_secrets()
                self._send_json({
                    "saved": outcome["saved"],
                    "skipped": outcome["skipped"],
                    "results": _validation_results_payload(results),
                    "status": _local_secret_status(),
                    "restart_recommended": True,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/consensus/import-csv":
            payload = self._read_json()
            directory_value = str(payload.get("directory") or "").strip()
            raw_tickers = payload.get("tickers")
            if isinstance(raw_tickers, str):
                tickers = [item.strip().upper() for item in raw_tickers.replace(",", " ").split() if item.strip()]
            elif isinstance(raw_tickers, list):
                tickers = [str(item).strip().upper() for item in raw_tickers if str(item).strip()]
            else:
                tickers = None
            try:
                result = import_consensus_csv(
                    Path(directory_value) if directory_value else None,
                    tickers,
                    ResearchStore(),
                )
                self._send_json({"import": _jsonable(result)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/market-implied-assumptions":
            payload = self._read_json()
            ticker = str(payload.get("ticker") or "").strip().upper()
            if not ticker:
                self._send_json({"error": "ticker is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            assumptions = payload.get("assumptions")
            if not isinstance(assumptions, dict):
                assumptions = {
                    key: value for key, value in payload.items()
                    if key != "ticker"
                }
            saved = ResearchStore().save_market_implied_assumptions(ticker, assumptions)
            self._send_json({
                "ticker": ticker,
                "assumptions": saved,
                "recalculate_on_next_research_run": True,
            })
            return
        if parsed.path == "/api/news/import":
            payload = self._read_json()
            if not str(payload.get("ticker") or "").strip():
                self._send_json({"error": "ticker is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not str(payload.get("headline") or payload.get("title") or "").strip():
                self._send_json({"error": "headline or title is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            observation = observation_from_payload(payload)
            claim = claim_from_observation(
                observation,
                company=str(payload.get("company") or observation.ticker),
                event_type=str(payload.get("event_type") or ""),
                affected_driver=str(payload.get("affected_driver") or ""),
                claimed_fact=str(payload.get("claimed_fact") or payload.get("summary") or payload.get("excerpt") or ""),
                confidence=str(payload.get("confidence") or "Medium"),
            )
            store = ResearchStore()
            store.save_news_observation(observation, claim)
            primary = store.latest_primary_source_observations(observation.ticker)
            corroboration = build_corroboration_results(observation.ticker, [claim], [])
            store.save_source_corroboration_results(corroboration)
            self._send_json({
                "imported": True,
                "observation": _jsonable(observation),
                "claim": _jsonable(claim),
                "source_needs": _jsonable(source_needs_for_claim(claim)),
                "corroboration": _jsonable(corroboration),
                "stored_full_text": bool(observation.may_store_full_text),
                "primary_source_observation_count": len(primary),
            })
            return
        if parsed.path == "/api/global-peer-refresh":
            payload = self._read_json()
            ticker = str(payload.get("ticker") or "AAPL").strip().upper() or "AAPL"
            universe = peer_universe_for(ticker)
            provider = GlobalPeerFinancialProvider()
            coverage = {}
            for peer in universe.peers:
                result = provider.fetch(peer.ticker)
                if result.identity is not None or result.status != "unsupported_global_peer":
                    coverage[peer.ticker.upper()] = result
            manifest = provider.research_manifest(next(iter(coverage.values()))) if coverage else None
            store = ResearchStore()
            run_id = f"global-peer-refresh-{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
            store.save_global_peer_coverage(run_id, ticker, coverage)
            if manifest:
                store.save_llm_research_manifest(run_id, ticker, manifest)
            self._send_json({
                "ticker": ticker,
                "peer_universe": _jsonable(universe),
                "global_peer_coverage": _jsonable(list(coverage.values())),
                "llm_research_manifest": _jsonable(manifest) if manifest else None,
            })
            return
        if parsed.path == "/api/source-plan/run":
            payload = self._read_json()
            ticker = str(payload.get("ticker") or "AAPL").strip().upper() or "AAPL"
            refresh = bool(payload.get("refresh", False))
            store = ResearchStore()
            if refresh:
                try:
                    result = run_us_equity_research(ticker, store=store)
                    self._send_json({
                        "ticker": ticker,
                        "ran_research": True,
                        "source_plan": _jsonable(result.source_plan),
                        "validated_claims": _jsonable(result.validated_claims),
                    })
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            plan = store.latest_source_plan(ticker)
            if plan:
                self._send_json({"ticker": ticker, "ran_research": False, "source_plan": plan})
            else:
                self._send_json(
                    {
                        "ticker": ticker,
                        "ran_research": False,
                        "source_plan": None,
                        "message": "No stored source plan. Set refresh=true or run research first.",
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
            return
        if parsed.path in {"/api/research", "/api/event-investigation"}:
            payload = self._read_json()
            if parsed.path == "/api/event-investigation":
                payload["research_profile"] = "investigate_event"
            ticker = str(payload.get("ticker") or "AAPL").strip().upper() or "AAPL"
            fallback = bool(payload.get("fallback", False))
            budget_mode = str(payload.get("budget_mode") or config.BUDGET_MODE)
            fmp_key = str(payload.get("fmp_key") or "") if budget_allows_paid_data(budget_mode) else ""
            store = ResearchStore()
            provider = build_consensus_provider(
                store=store,
                alpha_vantage_key=str(payload.get("alpha_vantage_key") or "") or None,
                finnhub_key=str(payload.get("finnhub_key") or "") or None,
                fmp_key=fmp_key or None,
                enable_nasdaq=bool(payload.get("enable_nasdaq", False)),
                enable_tradingview=bool(payload.get("enable_tradingview", False)),
                enable_yahoo=False,
            )
            external_provider = external_evidence_stack_from_config(
                fred_api_key=str(payload.get("fred_key") or "") or None,
                bea_api_key=str(payload.get("bea_key") or "") or None,
                census_api_key=str(payload.get("census_key") or "") or None,
                enable_default_macro=bool(payload.get("enable_default_macro", True)),
                global_macro_mode=bool(payload.get("global_macro_mode", False)),
                enable_gdelt=bool(payload.get("enable_gdelt", False)),
                wisburg_api_key=str(payload.get("wisburg_key") or "") or None,
                enable_wisburg=bool(payload.get("enable_wisburg", False)),
                refresh_cache=bool(payload.get("refresh_macro_cache", False)),
                store=store,
            )
            enable_llm = bool(payload.get("enable_llm", False))
            primary_profile_id = str(payload.get("primary_llm_profile_id") or "")
            secondary_profile_id = str(payload.get("secondary_llm_profile_id") or "")
            enable_secondary_llm = bool(payload.get("enable_secondary_llm", True))
            secondary_min_stage = str(payload.get("secondary_llm_min_stage") or config.SECONDARY_LLM_MIN_STAGE)
            language_policy = str(payload.get("llm_language_policy") or config.LLM_LANGUAGE_POLICY)
            research_profile = str(payload.get("research_profile") or config.RESEARCH_PROFILE)
            investigate_event_id = str(payload.get("investigate_event_id") or "") or None
            if primary_profile_id or secondary_profile_id:
                store.save_llm_selection(
                    primary_profile_id,
                    secondary_profile_id,
                    enable_secondary_llm,
                    secondary_min_stage,
                    language_policy,
                )
            manager = LocalSecretsManager()
            primary_profile = store.get_llm_profile(primary_profile_id)
            secondary_profile = store.get_llm_profile(secondary_profile_id)
            llm_provider = profile_to_provider(primary_profile, manager, enabled=enable_llm)
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
                manager,
                enabled=enable_llm and enable_secondary_llm,
            )
            try:
                result = run_us_equity_research(
                    ticker,
                    sec_client=SecClient(user_agent=str(payload.get("sec_user_agent") or config.SEC_USER_AGENT)),
                    price_client=StooqPriceClient(
                        store=store,
                        tiingo_key=str(payload.get("tiingo_key") or "") or None,
                        eodhd_key=str(payload.get("eodhd_key") or "") or None,
                    ),
                    consensus=provider,
                    external_evidence_provider=external_provider,
                    llm_provider=llm_provider,
                    secondary_llm_provider=secondary_llm_provider,
                    enable_secondary_llm_review=enable_secondary_llm,
                    secondary_llm_min_stage=secondary_min_stage,
                    llm_language_policy=language_policy,
                    budget_mode=budget_mode,
                    store=store,
                    research_profile=research_profile,
                    investigate_event_id=investigate_event_id,
                )
                self._send_json({"result": _jsonable(result)})
            except Exception as exc:  # pragma: no cover - server boundary
                if fallback:
                    self._send_json({
                        "warning": f"Live data unavailable ({exc}). Showing demo workflow.",
                        "result": _jsonable(demo_result(ticker)),
                    })
                else:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/watchlist":
            query = parse_qs(parsed.query)
            payload = self._read_json()
            ticker = (query.get("ticker", [payload.get("ticker", "")])[0] or "").upper()
            list_name = query.get("list", [payload.get("list_name", "default")])[0]
            if not ticker:
                self._send_json({"error": "ticker is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            store = ResearchStore()
            store.add_watchlist(ticker, list_name)
            self._send_json({"watchlist": _jsonable(store.list_watchlist(list_name))})
            return
        if parsed.path.startswith("/api/alerts/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4 and parts[0:2] == ["api", "alerts"]:
                try:
                    alert_id = int(parts[2])
                except ValueError:
                    self._send_json({"error": "invalid alert id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                action = parts[3]
                status = "read" if action == "read" else "dismissed" if action == "dismiss" else None
                if not status:
                    self._send_json({"error": "invalid alert action"}, status=HTTPStatus.BAD_REQUEST)
                    return
                updated = ResearchStore().update_alert_status(alert_id, status)
                self._send_json({"updated": updated})
                return
        if parsed.path.startswith("/api/ideas/") and parsed.path.endswith("/promote"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                result = ResearchStore().promote_idea_with_audit(parts[2])
                status = HTTPStatus.OK if result.get("promoted") else HTTPStatus.CONFLICT
                self._send_json(result, status=status)
                return
        if parsed.path.startswith("/api/ideas/") and parsed.path.endswith("/assumptions"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                payload = self._read_json()
                updated = ResearchStore().update_idea_assumptions(parts[2], payload)
                if updated is None:
                    self._send_json({"error": "Idea not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"idea_id": parts[2], "assumptions": payload})
                return
        if parsed.path.startswith("/api/ideas/") and parsed.path.endswith("/outcome"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                payload = self._read_json()
                store = ResearchStore()
                outcome = store.record_idea_post_mortem(parts[2], payload)
                if outcome is None:
                    self._send_json({"error": "Idea not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({
                        "idea_id": parts[2],
                        "outcome": outcome,
                        "audit": store.idea_audit(parts[2]),
                        "calibration": _jsonable(build_calibration_report(store)),
                    })
                return
        if parsed.path == "/api/transcripts/import":
            payload = self._read_json()
            ticker = str(payload.get("ticker") or "").strip().upper()
            text = str(payload.get("text") or payload.get("transcript") or "").strip()
            if not ticker or not text:
                self._send_json({"error": "ticker and text are required"}, status=HTTPStatus.BAD_REQUEST)
                return
            observed_at = _utc_now_server()
            document, turns = transcript_document_from_payload(
                ticker,
                {
                    "quarter": payload.get("fiscal_period") or payload.get("quarter"),
                    "date": payload.get("event_date"),
                    "transcript": [{"speaker": payload.get("speaker") or "Imported", "content": text}],
                },
                "Manual transcript import",
                str(payload.get("source_url") or "manual:transcript-import"),
                observed_at,
                official=False,
            )
            if not document:
                self._send_json({"error": "Transcript could not be normalized"}, status=HTTPStatus.BAD_REQUEST)
                return
            package = build_management_source_package(
                ticker, [], {}, [document], turns, [], [], [], [],
            )
            ResearchStore().save_management_sources(f"manual-{ticker}-{observed_at}", package)
            self._send_json({"imported": True, "management_sources": _jsonable(package)})
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/market-implied-assumptions":
            ticker = _ticker_from_query(parsed.query)
            deleted = ResearchStore().clear_market_implied_assumptions(ticker)
            self._send_json({"ticker": ticker, "deleted": deleted})
            return
        if parsed.path.startswith("/api/llm-profiles/"):
            profile_id = unquote(parsed.path.rsplit("/", 1)[-1])
            deleted = delete_llm_profile_with_secret(ResearchStore(), LocalSecretsManager(), profile_id)
            self._send_json({
                "deleted": deleted,
                "profiles": _jsonable(ResearchStore().list_llm_profiles()),
            })
            return
        if parsed.path == "/api/local-secrets":
            manager = LocalSecretsManager()
            deleted = manager.delete_many()
            config.refresh_runtime_secrets()
            self._send_json({"deleted": deleted, "status": _local_secret_status()})
            return
        if parsed.path == "/api/watchlist":
            query = parse_qs(parsed.query)
            ticker = (query.get("ticker", [""])[0] or "").strip().upper()
            if not ticker:
                self._send_json({"error": "ticker is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            list_name = query.get("list", ["default"])[0]
            store = ResearchStore()
            store.remove_watchlist(ticker, list_name)
            self._send_json({"watchlist": _jsonable(store.list_watchlist(list_name))})
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, value: str) -> None:
        body = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8501, type=int)
    parser.add_argument(
        "--max-port",
        default=8510,
        type=int,
        help="Highest port to try when the requested port is already in use.",
    )
    args = parser.parse_args()

    httpd, port = _bind_server(args.host, args.port, args.max_port)
    with httpd:
        print(f"US Equity Research Radar running at http://{args.host}:{port}")
        httpd.serve_forever()


def _ticker_from_query(query: str) -> str:
    values = parse_qs(query)
    return values.get("ticker", ["AAPL"])[0].strip().upper() or "AAPL"


def _stored_consensus(ticker: str) -> ConsensusPackage:
    store = ResearchStore()
    target = store.latest_target(ticker)
    estimates = store.latest_estimates(ticker)
    recommendations = store.latest_recommendations(ticker)
    surprises = store.surprises(ticker)
    has_data = bool(target or estimates or recommendations or surprises)
    package = ConsensusPackage(
        ticker=ticker.upper(),
        provider=target.source if target else "Local store",
        status="Available" if has_data else "Unavailable",
        target=target,
        estimates=estimates,
        recommendations=recommendations,
        surprises=surprises,
        revisions=store.revisions(ticker),
        data_gaps=[] if has_data else ["No consensus snapshots have been recorded."],
    )
    return package


def _provider_comparison_payload(store: ResearchStore, ticker: str) -> list[dict]:
    grouped: dict[str, dict[str, float | str | None]] = {}
    for observation in store.list_provider_observations(ticker, latest_only=True):
        if not (
            observation.field.startswith("target_")
            or observation.field.startswith("estimate_")
            or observation.field == "analyst_count"
        ):
            continue
        grouped.setdefault(observation.field, {})[observation.provider] = (
            observation.value_numeric
            if observation.value_numeric is not None else observation.value_text
        )
    rows: list[dict] = []
    for field, values in sorted(grouped.items()):
        numeric = [float(value) for value in values.values() if isinstance(value, (int, float))]
        average = sum(numeric) / len(numeric) if numeric else None
        spread = (
            (max(numeric) - min(numeric)) / abs(average) * 100
            if len(numeric) >= 2 and average not in (None, 0) else None
        )
        rows.append({
            "field": field,
            "values": values,
            "spread_pct": spread,
            "interpretation": (
                "Material provider disagreement; inspect semantics and timestamps."
                if spread is not None and spread >= 10
                else "Providers are broadly aligned."
                if spread is not None
                else "Only one provider currently supplies this semantic field."
            ),
        })
    return rows


def _local_secret_status() -> list[dict]:
    return LocalSecretsManager().redacted_status(getattr(config, "_SYSTEM_ENV_KEYS", set()))


def _managed_keys_from_payload(payload: dict) -> dict[str, str]:
    raw = payload.get("keys") if isinstance(payload.get("keys"), dict) else payload
    mapping = {
        "ALPHAVANTAGE_API_KEY": raw.get("ALPHAVANTAGE_API_KEY") or raw.get("alpha_vantage_key"),
        "FINNHUB_API_KEY": raw.get("FINNHUB_API_KEY") or raw.get("finnhub_key"),
        "FMP_API_KEY": raw.get("FMP_API_KEY") or raw.get("fmp_key"),
        "FRED_API_KEY": raw.get("FRED_API_KEY") or raw.get("fred_key"),
        "BEA_API_KEY": raw.get("BEA_API_KEY") or raw.get("bea_key"),
        "CENSUS_API_KEY": raw.get("CENSUS_API_KEY") or raw.get("census_key"),
        "WISBURG_API_KEY": raw.get("WISBURG_API_KEY") or raw.get("wisburg_key"),
        "TIINGO_API_KEY": raw.get("TIINGO_API_KEY") or raw.get("tiingo_key"),
        "EODHD_API_KEY": raw.get("EODHD_API_KEY") or raw.get("eodhd_key"),
        "OPENAI_API_KEY": raw.get("OPENAI_API_KEY") or raw.get("openai_key"),
        "ANTHROPIC_API_KEY": raw.get("ANTHROPIC_API_KEY") or raw.get("anthropic_key"),
        "QWEN_API_KEY": raw.get("QWEN_API_KEY") or raw.get("qwen_key"),
        "KIMI_API_KEY": raw.get("KIMI_API_KEY") or raw.get("kimi_key"),
        "DEEPSEEK_API_KEY": raw.get("DEEPSEEK_API_KEY") or raw.get("deepseek_key"),
        "SEC_USER_AGENT": raw.get("SEC_USER_AGENT") or raw.get("sec_user_agent"),
    }
    return {key: str(value or "").strip() for key, value in mapping.items()}


def _validation_results_payload(results) -> list[dict]:
    return [
        {
            "key": item.key,
            "label": item.label,
            "status": item.status,
            "message": item.message,
        }
        for item in results
    ]


def _utc_now_server() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bind_server(host: str, start_port: int, max_port: int) -> tuple[ReusableThreadingTCPServer, int]:
    for port in range(start_port, max_port + 1):
        if _port_has_listener(port):
            continue
        try:
            return ReusableThreadingTCPServer((host, port), Handler), port
        except OSError as exc:
            if exc.errno not in {13, 48, 98, 10013, 10048}:
                raise
    raise OSError(
        f"No available port from {start_port} to {max_port}. "
        "Stop an existing server or pass --port with a different value."
    )


def _port_has_listener(port: int) -> bool:
    for host in ("127.0.0.1", "localhost"):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            continue
    return False


def _jsonable(value):
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _source_plan_from_payload(payload: dict) -> ResearchSourcePlan | None:
    try:
        requests = [
            ResearchSourceRequest(**{key: value for key, value in item.items() if key in ResearchSourceRequest.__dataclass_fields__})
            for item in payload.get("requests", [])
            if isinstance(item, dict)
        ]
        outcomes = [
            ResearchSourceOutcome(**{key: value for key, value in item.items() if key in ResearchSourceOutcome.__dataclass_fields__})
            for item in payload.get("outcomes", [])
            if isinstance(item, dict)
        ]
        fields = {
            key: value for key, value in payload.items()
            if key in ResearchSourcePlan.__dataclass_fields__ and key not in {"requests", "outcomes"}
        }
        fields["requests"] = requests
        fields["outcomes"] = outcomes
        return ResearchSourcePlan(**fields)
    except Exception:
        return None


if __name__ == "__main__":
    main()
