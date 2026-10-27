"""Simplified configuration management."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


# Resolve the runtime data directory; OPENPOKE_DATA_DIR overrides the default server/data
def _resolve_data_dir() -> Path:
    override = os.getenv("OPENPOKE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "data"


DATA_DIR = _resolve_data_dir()


DEFAULT_APP_NAME = "OpenPoke Server"
DEFAULT_APP_VERSION = "0.3.0"


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except (TypeError, ValueError):
        return fallback


DEFAULT_MODEL = "anthropic/claude-haiku-5.5"


# Resolve a model slot: OPENPOKE_<SLOT>_MODEL, then OPENPOKE_MODEL, then DEFAULT_MODEL
def _env_model(slot: str) -> str:
    for name in (f"OPENPOKE_{slot}_MODEL", "OPENPOKE_MODEL"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return DEFAULT_MODEL


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    server_host: str = Field(default=os.getenv("OPENPOKE_HOST", "0.0.0.0"))
    server_port: int = Field(default=_env_int("OPENPOKE_PORT", 8001))

    # LLM model selection (read when Settings is built, so each slot is overridable via env)
    interaction_agent_model: str = Field(default_factory=lambda: _env_model("INTERACTION_AGENT"))
    execution_agent_model: str = Field(default_factory=lambda: _env_model("EXECUTION_AGENT"))
    execution_agent_search_model: str = Field(default_factory=lambda: _env_model("SEARCH"))
    summarizer_model: str = Field(default_factory=lambda: _env_model("SUMMARIZER"))
    email_classifier_model: str = Field(default_factory=lambda: _env_model("EMAIL_CLASSIFIER"))

    # Credentials / integrations
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))

    # HTTP behaviour
    cors_allow_origins_raw: str = Field(default=os.getenv("OPENPOKE_CORS_ALLOW_ORIGINS", "*"))
    enable_docs: bool = Field(default=os.getenv("OPENPOKE_ENABLE_DOCS", "1") != "0")
    docs_url: Optional[str] = Field(default=os.getenv("OPENPOKE_DOCS_URL", "/docs"))

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=100)
    conversation_summary_tail_size: int = Field(default=10)

    # --- Agent loops and timeouts -------------------------------------------------------------
    interaction_max_tool_rounds: int = 8  # model calls per interaction-agent turn
    execution_max_tool_rounds: int = 8  # model calls per execution-agent run
    email_search_max_rounds: int = 8  # model calls inside one email search task
    execution_timeout_seconds: int = 90  # a run taking longer is reported as failed
    trigger_poll_interval_seconds: float = 10.0  # how often due reminders are checked

    # --- Important-email watcher --------------------------------------------------------------
    email_watcher_poll_interval_seconds: float = 60.0
    email_watcher_lookback_minutes: int = 10
    email_watcher_max_results: int = 50
    email_watcher_seen_limit: int = 300  # message IDs remembered to avoid alerting twice

    # --- Notifications to the user (P7) -----------------------------------------------------
    digest_interval_minutes: int = 60  # held updates go out together once the oldest has waited this long
    quiet_hours: str = "22:00-07:00"  # user's local time; the digest waits until they end ("" turns them off)
    notification_check_interval_seconds: float = 60.0  # how often the digest is checked
    event_stream_heartbeat_seconds: float = 15.0  # keep-alive on the browser's live stream (P18)

    # --- Routing: which agents the interaction agent sees (P2, P4) ------------------------------
    working_set_recent: int = 5  # most recently used agents always shown
    working_set_matches: int = 5  # best search matches shown on top of those
    routing_context_chars: int = 600  # recent conversation added to the search query
    search_name_weight: float = 3.0  # how much a query word counts when found in an agent's name...
    search_fact_weight: float = 2.0  # ...its facts (people, addresses, IDs)...
    search_summary_weight: float = 1.0  # ...or its card summary (all scaled by the word's rarity)
    search_id_mention_score: float = 100.0  # a message quoting an agent's ID is about that agent
    duplicate_name_threshold: float = 2 / 3  # share of meaningful name words that makes a likely duplicate
    duplicate_max_candidates: int = 3
    unknown_agent_suggestions: int = 3  # agents suggested when the model uses an unknown ID

    # --- Agent cards (P1) -----------------------------------------------------------------------
    initial_card_chars: int = 500  # a new agent's card starts as its first instructions, this long
    card_max_chars: int = 600  # the card summary the refresh may write
    facts_per_call_limit: int = 20  # facts of one kind recorded from a single tool call

    # --- Agent lifecycle (P3) ------------------------------------------------------------------
    # Agents idle this many days with nothing open (no open drafts, no live reminders) are archived:
    # hidden from the working set and duplicate check, still searchable, revived on use. 0 turns it off.
    agent_archive_after_days: int = Field(default=_env_int("OPENPOKE_ARCHIVE_AFTER_DAYS", 30))

    # --- Worker memory (P5, P6) ------------------------------------------------------------------
    # How much of its own past runs an execution agent sees in its prompt (estimated tokens)
    execution_history_token_budget: int = Field(default=_env_int("OPENPOKE_EXECUTION_HISTORY_TOKENS", 4000))
    chars_per_token: int = 4  # rough token estimate; avoids a tokenizer dependency
    digest_snippet_chars: int = 150  # per email in a search digest
    digest_generic_chars: int = 400  # for tools without a dedicated digest
    digest_max_listed: int = 20  # emails, triggers or facts listed before "… N more"
    recall_max_matches: int = 8
    recall_match_chars: int = 800  # per match summary (the matched passage carries the detail)
    recall_passage_chars: int = 500  # excerpt of a tool call's full text around the matched words
    recall_passage_before: int = 150  # how much of that excerpt comes before the first match
    recall_record_chars: int = 20_000  # a full record returned by ref (the journal itself is never cut)

    @property
    def cors_allow_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allow_origins_raw.strip() in {"", "*"}:
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins_raw.split(",") if origin.strip()]

    @property
    def resolved_docs_url(self) -> Optional[str]:
        """Return documentation URL when docs are enabled."""
        return (self.docs_url or "/docs") if self.enable_docs else None

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
