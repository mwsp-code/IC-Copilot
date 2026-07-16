from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
_SYSTEM_ENV_KEYS = set(os.environ)


def _load_local_env_files() -> None:
    for path, override in (
        (PROJECT_ROOT / ".env", False),
        (PROJECT_ROOT / ".env.local", True),
    ):
        if not path.exists():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
                parsed = _parse_env_line(raw_line)
                if not parsed:
                    continue
                key, value = parsed
                if key in _SYSTEM_ENV_KEYS:
                    continue
                if override or key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


_load_local_env_files()


def _load_keyring_env() -> None:
    try:
        from .local_secrets import LocalSecretsManager

        LocalSecretsManager().load_into_environment(_SYSTEM_ENV_KEYS)
    except Exception:
        pass


_load_keyring_env()


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_VALUES


def optional_env_flag(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


APP_NAME = "US Equity Research Radar"
APP_VERSION = "0.1.0"

DEFAULT_SEC_USER_AGENT = (
    "US Equity Research Radar/0.1 research prototype; "
    "set SEC_USER_AGENT with your name and email"
)

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT)
REQUEST_TIMEOUT_SECONDS = int(os.getenv("EQUITY_RESEARCH_TIMEOUT", "25"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", str(max(120, REQUEST_TIMEOUT_SECONDS))))
LLM_SECONDARY_TIMEOUT_SECONDS = int(os.getenv("LLM_SECONDARY_TIMEOUT_SECONDS", str(max(90, REQUEST_TIMEOUT_SECONDS))))
ENABLE_RESEARCH_PROFILING = env_flag("ENABLE_RESEARCH_PROFILING", "true")
RESEARCH_IO_WORKERS = max(1, int(os.getenv("RESEARCH_IO_WORKERS", "4")))
EXTERNAL_EVIDENCE_WORKERS = max(1, int(os.getenv("EXTERNAL_EVIDENCE_WORKERS", "6")))
CACHE_DIR = Path(os.getenv("EQUITY_RESEARCH_CACHE", ".cache/equity_research"))
IDEA_MEMORY_PATH = Path(os.getenv("IDEA_MEMORY_PATH", "data/idea_memory.json"))
RESEARCH_DB_PATH = Path(os.getenv("RESEARCH_DB_PATH", "data/research.db"))
BUDGET_MODE = os.getenv("BUDGET_MODE", "Lean")
BUDGET_POLICY_PATH = Path(os.getenv("BUDGET_POLICY_PATH", "data/budget_modes.json"))
BYOD_DATA_DIR = Path(os.getenv("BYOD_DATA_DIR", "data/manual_import"))
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE_URL = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
CONSENSUS_CSV_DIR = Path(os.getenv("CONSENSUS_CSV_DIR", "data/consensus_import"))
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")
ALPHAVANTAGE_BASE_URL = os.getenv("ALPHAVANTAGE_BASE_URL", "https://www.alphavantage.co/query")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE_URL = os.getenv("FINNHUB_BASE_URL", "https://finnhub.io/api/v1")
ENABLE_YAHOO_CONSENSUS = env_flag("ENABLE_YAHOO_CONSENSUS")
ENABLE_NASDAQ_CONSENSUS = env_flag("ENABLE_NASDAQ_CONSENSUS")
ENABLE_TRADINGVIEW_CONSENSUS = env_flag("ENABLE_TRADINGVIEW_CONSENSUS")
ENABLE_YAHOO_PRICE_FALLBACK = env_flag("ENABLE_YAHOO_PRICE_FALLBACK", "true")
PRICE_CSV_DIR = Path(os.getenv("PRICE_CSV_DIR", "data/price_import"))
TRANSCRIPT_CSV_DIR = Path(os.getenv("TRANSCRIPT_CSV_DIR", "data/transcripts"))
ISSUER_IR_SOURCES_CSV = Path(os.getenv("ISSUER_IR_SOURCES_CSV", "data/issuer_ir_sources.csv"))
ISSUER_IR_MAX_DOCUMENTS_PER_SEED = int(os.getenv("ISSUER_IR_MAX_DOCUMENTS_PER_SEED", "2"))
ISSUER_IR_METADATA_LIMIT_PER_SEED = int(os.getenv("ISSUER_IR_METADATA_LIMIT_PER_SEED", "8"))
ISSUER_IR_TEXT_CACHE_CHARS = int(os.getenv("ISSUER_IR_TEXT_CACHE_CHARS", "200000"))
ADR_PROFILE_CSV = Path(os.getenv("ADR_PROFILE_CSV", "data/adr_profiles.csv"))
GLOBAL_PEER_PROFILE_CSV = Path(os.getenv("GLOBAL_PEER_PROFILE_CSV", "data/global_peer_profiles.csv"))
PEER_UNIVERSE_CSV = Path(os.getenv("PEER_UNIVERSE_CSV", "data/peer_universes.csv"))
INDUSTRY_PLAYBOOK_CSV = Path(os.getenv("INDUSTRY_PLAYBOOK_CSV", "data/industry_playbooks.csv"))
COVERAGE_UNIVERSE_CSV = Path(os.getenv("COVERAGE_UNIVERSE_CSV", "data/coverage_universe.csv"))
JURISDICTION_SOURCES_CSV = Path(os.getenv("JURISDICTION_SOURCES_CSV", "data/jurisdiction_sources.csv"))
METRIC_ONTOLOGY_CSV = Path(os.getenv("METRIC_ONTOLOGY_CSV", "data/metric_ontology.csv"))
SECTOR_KPI_PLAYBOOK_CSV = Path(os.getenv("SECTOR_KPI_PLAYBOOK_CSV", "data/sector_kpi_playbooks.csv"))
SECURITY_TYPE_PROFILES_CSV = Path(os.getenv("SECURITY_TYPE_PROFILES_CSV", "data/security_type_profiles.csv"))
SOURCE_REQUIREMENTS_CSV = Path(os.getenv("SOURCE_REQUIREMENTS_CSV", "data/source_requirements.csv"))
GEOGRAPHY_EXPOSURE_PLAYBOOK_CSV = Path(os.getenv("GEOGRAPHY_EXPOSURE_PLAYBOOK_CSV", "data/geography_exposure_playbooks.csv"))
ENABLE_GLOBAL_PEER_LIVE_EXTRACTION = env_flag("ENABLE_GLOBAL_PEER_LIVE_EXTRACTION", "true")
RULE_SENTIMENT_ENABLED = env_flag("RULE_SENTIMENT_ENABLED", "true")
LLM_SENTIMENT_ENABLED = env_flag("LLM_SENTIMENT_ENABLED")
ENABLE_GDELT = env_flag("ENABLE_GDELT")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_MACRO_OVERRIDE = optional_env_flag("ENABLE_FRED_MACRO")
ENABLE_FRED_MACRO = env_flag("ENABLE_FRED_MACRO")
BLS_API_KEY = os.getenv("BLS_API_KEY", "")
BLS_MACRO_OVERRIDE = optional_env_flag("ENABLE_BLS_MACRO")
ENABLE_BLS_MACRO = env_flag("ENABLE_BLS_MACRO")
BEA_API_KEY = os.getenv("BEA_API_KEY", "")
BEA_MACRO_OVERRIDE = optional_env_flag("ENABLE_BEA_MACRO")
ENABLE_BEA_MACRO = env_flag("ENABLE_BEA_MACRO")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
CENSUS_MACRO_OVERRIDE = optional_env_flag("ENABLE_CENSUS_MACRO")
ENABLE_CENSUS_MACRO = env_flag("ENABLE_CENSUS_MACRO")
TREASURY_MACRO_OVERRIDE = optional_env_flag("ENABLE_TREASURY_MACRO")
ENABLE_TREASURY_MACRO = env_flag("ENABLE_TREASURY_MACRO")
OFR_MACRO_OVERRIDE = optional_env_flag("ENABLE_OFR_MACRO")
ENABLE_OFR_MACRO = env_flag("ENABLE_OFR_MACRO")
WORLD_BANK_MACRO_OVERRIDE = optional_env_flag("ENABLE_WORLD_BANK_MACRO")
ENABLE_WORLD_BANK_MACRO = env_flag("ENABLE_WORLD_BANK_MACRO")
IMF_MACRO_OVERRIDE = optional_env_flag("ENABLE_IMF_MACRO")
ENABLE_IMF_MACRO = env_flag("ENABLE_IMF_MACRO")
ENABLE_DEFAULT_MACRO = env_flag("ENABLE_DEFAULT_MACRO", "true")
GLOBAL_MACRO_MODE = env_flag("GLOBAL_MACRO_MODE")
WISBURG_API_KEY = os.getenv("WISBURG_API_KEY", "")
ENABLE_WISBURG = env_flag("ENABLE_WISBURG")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "")
EODHD_API_KEY = os.getenv("EODHD_API_KEY", "")
EODHD_MAX_CALLS_PER_RUN = int(os.getenv("EODHD_MAX_CALLS_PER_RUN", "18"))
CALIBRATION_MIN_SAMPLE = int(os.getenv("CALIBRATION_MIN_SAMPLE", "30"))
ENABLE_LLM_THESIS = env_flag("ENABLE_LLM_THESIS")
ENABLE_LLM_CLAIM_VALIDATION = env_flag("ENABLE_LLM_CLAIM_VALIDATION")
ENABLE_LLM_SOURCE_AGENT = env_flag("ENABLE_LLM_SOURCE_AGENT")
ENABLE_SECONDARY_LLM_REVIEW = env_flag("ENABLE_SECONDARY_LLM_REVIEW", "true")
SECONDARY_LLM_MIN_STAGE = os.getenv("SECONDARY_LLM_MIN_STAGE", "Research-Ready")
LLM_LANGUAGE_POLICY = os.getenv("LLM_LANGUAGE_POLICY", "bilingual_audit")
LLM_PRIMARY_PROFILE_ID = os.getenv("LLM_PRIMARY_PROFILE_ID", "")
LLM_SECONDARY_PROFILE_ID = os.getenv("LLM_SECONDARY_PROFILE_ID", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
RESEARCH_PROFILE = os.getenv("RESEARCH_PROFILE", "adaptive_ic")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")


def refresh_runtime_secrets() -> None:
    _load_keyring_env()
    globals()["SEC_USER_AGENT"] = os.getenv("SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT)
    globals()["FMP_API_KEY"] = os.getenv("FMP_API_KEY", "")
    globals()["ALPHAVANTAGE_API_KEY"] = os.getenv("ALPHAVANTAGE_API_KEY", "")
    globals()["FINNHUB_API_KEY"] = os.getenv("FINNHUB_API_KEY", "")
    globals()["FRED_API_KEY"] = os.getenv("FRED_API_KEY", "")
    globals()["BLS_API_KEY"] = os.getenv("BLS_API_KEY", "")
    globals()["BEA_API_KEY"] = os.getenv("BEA_API_KEY", "")
    globals()["CENSUS_API_KEY"] = os.getenv("CENSUS_API_KEY", "")
    globals()["WISBURG_API_KEY"] = os.getenv("WISBURG_API_KEY", "")
    globals()["POLYGON_API_KEY"] = os.getenv("POLYGON_API_KEY", "")
    globals()["TIINGO_API_KEY"] = os.getenv("TIINGO_API_KEY", "")
    globals()["EODHD_API_KEY"] = os.getenv("EODHD_API_KEY", "")
    globals()["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    globals()["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY", "")
    globals()["QWEN_API_KEY"] = os.getenv("QWEN_API_KEY", "")
    globals()["KIMI_API_KEY"] = os.getenv("KIMI_API_KEY", "")
    globals()["DEEPSEEK_API_KEY"] = os.getenv("DEEPSEEK_API_KEY", "")
    globals()["LLM_PRIMARY_PROFILE_ID"] = os.getenv("LLM_PRIMARY_PROFILE_ID", "")
    globals()["LLM_SECONDARY_PROFILE_ID"] = os.getenv("LLM_SECONDARY_PROFILE_ID", "")
    globals()["ENABLE_SECONDARY_LLM_REVIEW"] = env_flag("ENABLE_SECONDARY_LLM_REVIEW", "true")
    globals()["SECONDARY_LLM_MIN_STAGE"] = os.getenv("SECONDARY_LLM_MIN_STAGE", "Research-Ready")
    globals()["LLM_LANGUAGE_POLICY"] = os.getenv("LLM_LANGUAGE_POLICY", "bilingual_audit")
    globals()["LLM_PROVIDER"] = os.getenv("LLM_PROVIDER", "deepseek")
    globals()["LLM_MODEL"] = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    globals()["LLM_BASE_URL"] = os.getenv("LLM_BASE_URL", "")
