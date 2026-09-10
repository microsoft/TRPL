"""
Configuration module for lia-agent.
Loads and manages application settings from environment variables.
"""

import os
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
import logging

# Load environment variables
load_dotenv()


logger = logging.getLogger(f"lia.{__name__}")

RuntimeMode = Literal["cloud", "deterministic"]
_runtime_mode = os.getenv("LIA_RUNTIME_MODE", "cloud").strip().lower()
if _runtime_mode not in {"cloud", "deterministic"}:
    raise RuntimeError("LIA_RUNTIME_MODE must be 'cloud' or 'deterministic'")


def _cloud_value(var_name: str, deterministic_default: str = "") -> str:
    value = os.getenv(var_name, "").strip()
    if value:
        return value
    if _runtime_mode == "deterministic":
        return deterministic_default
    raise RuntimeError(f"{var_name} is required when LIA_RUNTIME_MODE=cloud")


def _parse_csv_env(var_name: str) -> list[str]:
    raw = os.getenv(var_name, "")
    return [value.strip() for value in raw.split(",") if value.strip()]


def _default_allowed_origins() -> list[str]:
    configured = _parse_csv_env("ALLOWED_ORIGINS")
    if configured:
        return configured

    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    if app_env in {"dev", "development", "local", "test"}:
        # Safe local defaults for browser-based dev clients.
        return [
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]

    # Non-local environments are strict by default.
    return []


class VoicePreset(BaseModel):
    """Configuration for a named TTS voice option."""
    provider: str  # "azure" or "elevenlabs"
    # Azure fields
    azure_api_key: str | None = None
    azure_region: str | None = None
    azure_voice: str | None = None
    azure_deployment: str | None = None
    # ElevenLabs fields
    eleven_api_key: str | None = None
    eleven_voice_id: str | None = None
    eleven_model_id: str | None = None
    eleven_output_format: str | None = None
    eleven_base_url: str | None = None


class LLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_type: Literal["openai"] = "openai"
    base_url: str
    api_key: str
    model: str
    price: list[str] = Field(min_length=2, max_length=2)
    stream: bool = False
    reasoning_effort: str | None = Field(default=None, exclude=True)


class Config(BaseModel):
    """Application configuration from environment variables."""

    model_config = ConfigDict(frozen=True)

    # Server Configuration
    # Bind to loopback by default: this "brain" service exposes session-control
    # endpoints and must not face the internet directly. Set HOST=0.0.0.0
    # explicitly only when it sits behind a firewall / the token-server edge.
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    app_env: str = os.getenv("APP_ENV", "dev").strip().lower()
    allowed_origins: list[str] = _default_allowed_origins()
    runtime_mode: RuntimeMode = _runtime_mode

    # LLM Configuration
    llm_timeout: int = 120
    default_max_steps: int = int(os.getenv("DEFAULT_MAX_ROUNDS", "20"))
    opposing_camp_split_threshold_pct: int = int(
        os.getenv("OPPOSING_CAMP_SPLIT_THRESHOLD_PCT", "40")
    )

    llm_base_url: str = os.getenv("LLM_BASE_URL", "").strip()
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_reasoning_effort: str | None = os.getenv(
        "LLM_REASONING_EFFORT", ""
    ).strip() or None
    llm_config_small: LLMConfig = LLMConfig(
        base_url=_cloud_value("LLM_BASE_URL"),
        api_key=_cloud_value("LLM_API_KEY"),
        model=_cloud_value("LLM_SMALL_MODEL"),
        price=[
            _cloud_value("LLM_SMALL_INPUT_PRICE_1K", "0"),
            _cloud_value("LLM_SMALL_OUTPUT_PRICE_1K", "0"),
        ],
        reasoning_effort=os.getenv("LLM_SMALL_REASONING_EFFORT", "").strip()
        or llm_reasoning_effort,
    )
    llm_config_mid: LLMConfig = LLMConfig(
        base_url=_cloud_value("LLM_BASE_URL"),
        api_key=_cloud_value("LLM_API_KEY"),
        model=_cloud_value("LLM_MID_MODEL"),
        price=[
            _cloud_value("LLM_MID_INPUT_PRICE_1K", "0"),
            _cloud_value("LLM_MID_OUTPUT_PRICE_1K", "0"),
        ],
        reasoning_effort=os.getenv("LLM_MID_REASONING_EFFORT", "").strip()
        or llm_reasoning_effort,
    )
    llm_config_large: LLMConfig = LLMConfig(
        base_url=_cloud_value("LLM_BASE_URL"),
        api_key=_cloud_value("LLM_API_KEY"),
        model=_cloud_value("LLM_LARGE_MODEL"),
        price=[
            _cloud_value("LLM_LARGE_INPUT_PRICE_1K", "0"),
            _cloud_value("LLM_LARGE_OUTPUT_PRICE_1K", "0"),
        ],
        reasoning_effort=os.getenv("LLM_LARGE_REASONING_EFFORT", "").strip()
        or llm_reasoning_effort,
    )

    # Debug Configuration
    print_agent_json: bool = os.getenv("PRINT_AGENT_JSON", "False").lower() == "true"
    seed: int = int(os.getenv("SEED", "42"))
    debug_agent_audio_idle_timeout: float = float(
        os.getenv("DEBUG_AGENT_AUDIO_IDLE_TIMEOUT", "15.0")
    )

    # Pledges Configuration
    pledge_options_count: int = int(os.getenv("PLEDGE_OPTIONS_COUNT", "6"))
    pledge_model_tier: Literal["nano", "mini", "full"] = os.getenv(
        "PLEDGE_MODEL_TIER", "full"
    )

    # Slogan Configuration
    slogan_max_line_length: int = int(os.getenv("SLOGAN_MAX_LINE_LENGTH", "25"))
    slogan_max_lines: int = int(os.getenv("SLOGAN_MAX_LINES", "2"))
    slogan_model_tier: Literal["nano", "mini", "full"] = os.getenv(
        "SLOGAN_MODEL_TIER", "full"
    )

    # Hand Raise Configuration
    hand_raise_enabled: bool = os.getenv("HAND_RAISE_ENABLED", "True").lower() == "true"

    hand_raise_hints_enabled: bool = (
        os.getenv("HAND_RAISE_HINTS_ENABLED", "False").lower() == "true"
    )

    # Storys phase time limits. When disabled, the node never hits the
    # hard/soft pressure branches, so there's no automatic "wrap up and
    # hand off to next visitor" behavior. Useful for remote/demo use
    # where there is no physical visitor queue behind the current speaker.
    storys_time_pressure_enabled: bool = (
        os.getenv("STORYS_TIME_PRESSURE_ENABLED", "False").lower() == "true"
    )
    # Limits (seconds) applied when someone else is waiting their turn.
    storys_soft_limit_sec: float = float(os.getenv("STORYS_SOFT_LIMIT_SEC", "120"))
    storys_hard_limit_sec: float = float(os.getenv("STORYS_HARD_LIMIT_SEC", "180"))
    # Limits (seconds) applied when the current speaker is alone. More
    # patient — a solo visitor can take longer to think and converse.
    storys_solo_soft_limit_sec: float = float(os.getenv("STORYS_SOLO_SOFT_LIMIT_SEC", "180"))
    storys_solo_hard_limit_sec: float = float(os.getenv("STORYS_SOLO_HARD_LIMIT_SEC", "240"))

    # Two-stage self-routing for the Storys agent. When enabled, the
    # response LLM gates each turn: if it lacks specifics for the visitor's
    # question it emits a brief acknowledgement ("Hmm — let me think on
    # that.") + needs_more_kb=true; the node then runs RAG synchronously
    # and re-calls the LLM in the same stream session. When disabled (the
    # current production behaviour), RAG runs asynchronously and lands in
    # the next turn, so the model answers the current question blind.
    storys_self_routing: bool = (
        os.getenv("STORYS_SELF_ROUTING", "False").lower() == "true"
    )

    # Silence (seconds) inserted after the "let me think" ack and before
    # the real RAG-backed answer. The brain finishes streaming the ack
    # in ~200ms while TTS plays it for ~1s; without this pause the real
    # answer starts the instant TTS finishes the ack, which feels rushed
    # and unnatural ("Hmm — let me think on that. The letter said..."
    # back-to-back). 0 disables. Suggested 1.5–2.0 for natural beat.
    storys_kb_ack_pause_sec: float = float(
        os.getenv("STORYS_KB_ACK_PAUSE_SEC", "0")
    )

    # When self-routing fetches KB, the model produces a short "let me think"
    # ack. We only VOICE that ack this fraction of the time (random per turn);
    # the rest of the time we fetch silently and go straight to the answer.
    # 1.0 = always speak the ack, 0.0 = never. Default 0.23.
    storys_kb_ack_probability: float = float(
        os.getenv("STORYS_KB_ACK_PROBABILITY", "0.23")
    )

    # Camera phase — when enabled, the avatar will speak a gentle nudge if a
    # visitor lingers (VISITOR_HESITATING, ~5s in entry zone) or is idle in
    # the room without approaching the mic (VISITOR_IDLE_IN_ROOM, ~30s).
    # When disabled (default), the avatar stays silent and only greets on
    # BATCH_INVITE / MIC_ZONE_LEFT / HAND_RAISE_RESPONSE.
    camera_urge_visitors: bool = (
        os.getenv("CAMERA_URGE_VISITORS", "False").lower() == "true"
    )

    # TTS Configuration
    tts_enabled: bool = os.getenv("TTS_ENABLED", "True").lower() == "true"
    tts_voice: str = os.getenv("TTS_VOICE", "davis_dragon_hd").strip()
    tts_azure_api_key: str = os.getenv("TTS_AZURE_API_KEY", "").strip()
    tts_azure_region: str = os.getenv("TTS_AZURE_REGION", "eastus").strip()
    tts_azure_voice_id: str = os.getenv(
        "TTS_AZURE_VOICE_ID", "en-US-Davis:DragonHDLatestNeural"
    ).strip()
    tts_audio_ready_wait_seconds: float = float(
        os.getenv("TTS_AUDIO_READY_WAIT_SECONDS", "2.0")
    )
    tts_audio_idle_timeout_seconds: float = float(
        os.getenv("TTS_AUDIO_IDLE_TIMEOUT_SECONDS", "30.0")
    )  # the longest audio output we expect from a single utterance
    tts_audio_flush_grace_seconds: float = float(
        os.getenv("TTS_AUDIO_FLUSH_GRACE_SECONDS", "0.25")
    )
    tts_caption_timing_enabled: bool = (
        os.getenv("TTS_CAPTION_TIMING_ENABLED", "True").lower() == "true"
    )

    # Additional Azure voice credentials (westus fine-tuned voices)
    tts_azure_api_key_westus: str = os.getenv("TTS_AZURE_API_KEY_WESTUS", "").strip()
    joe_2_deployment: str = os.getenv("JOE_2_DEPLOYMENT", "").strip()
    tr_cortina_deployment: str = os.getenv("TR_CORTINA_DEPLOYMENT", "").strip()

    # ElevenLabs TTS credentials
    eleven_api_key: str = os.getenv("ELEVEN_API_KEY", "").strip()
    eleven_voice_id: str = os.getenv("ELEVEN_VOICE_ID", "").strip()
    eleven_model_id: str = os.getenv("ELEVEN_MODEL_ID", "eleven_v3").strip()
    eleven_output_format: str = os.getenv("ELEVEN_OUTPUT_FORMAT", "pcm_24000").strip()
    eleven_base_url: str = os.getenv("ELEVEN_BASE_URL", "https://api.elevenlabs.io").strip()

    # Welcome Phase Configuration
    welcome_max_rounds: int = int(os.getenv("WELCOME_MAX_ROUNDS", "20"))
    welcome_max_rounds_per_visitor: int = int(
        os.getenv("WELCOME_MAX_ROUNDS_PER_VISITOR", "2")
    )
    welcome_first_question_prompt_for_question: bool = (
        os.getenv("WELCOME_FIRST_QUESTION_PROMPT_FOR_QUESTION", "True").lower()
        == "true"
    )
    welcome_prompt_for_question_chance: float = float(
        os.getenv("WELCOME_PROMPT_FOR_QUESTION_CHANCE", "0.3")
    )

    # Knowledge Base Async Search Configuration
    kb_async_search_enabled: bool = (
        os.getenv("KB_ASYNC_SEARCH_ENABLED", "True").lower() == "true"
    )

    # Prompt-injection detector (independent thread that audits visitor
    # input for jailbreak attempts; see debate/services/prompt_injection.py).
    # In "observe" mode (default) detections are logged + auto-filed to
    # /admin/reports without affecting the conversation. "enforce" mode is
    # reserved for a future iteration that sanitizes input + injects a
    # warning to the main agent.
    prompt_injection_enabled: bool = (
        os.getenv("PROMPT_INJECTION_ENABLED", "True").lower() == "true"
    )
    prompt_injection_mode: str = os.getenv(
        "PROMPT_INJECTION_MODE", "observe"
    ).strip().lower()
    prompt_injection_model: str = os.getenv(
        "PROMPT_INJECTION_MODEL", "gpt-4.1-nano"
    ).strip()  # gpt-5-nano is reasoning-tuned and returns empty content
    # via chat.completions; would need Responses API. gpt-4.1-nano works
    # out of the box at ~0.9-1.0s warm / 1.7s cold.
    prompt_injection_use_l1: bool = (
        os.getenv("PROMPT_INJECTION_USE_L1", "True").lower() == "true"
    )
    prompt_injection_use_l2: bool = (
        os.getenv("PROMPT_INJECTION_USE_L2", "True").lower() == "true"
    )
    prompt_injection_auto_report: bool = (
        os.getenv("PROMPT_INJECTION_AUTO_REPORT", "True").lower() == "true"
    )
    prompt_injection_l2_timeout: float = float(
        os.getenv("PROMPT_INJECTION_L2_TIMEOUT", "3.0")
    )

    # L3 Output Reviewer (independent agent that audits TR's response in
    # parallel with streaming; see debate/services/output_reviewer.py).
    # In observe mode (default) detections are logged + auto-filed to
    # /admin/reports without affecting the live stream. In enforce mode,
    # a detection calls safety.kill_utterance which halts the OpenAI
    # stream mid-output.
    #
    # OFF by default — opt in by setting OUTPUT_REVIEWER_ENABLED=true.
    output_reviewer_enabled: bool = (
        os.getenv("OUTPUT_REVIEWER_ENABLED", "False").lower() == "true"
    )
    output_reviewer_mode: str = os.getenv(
        "OUTPUT_REVIEWER_MODE", "observe"
    ).strip().lower()
    output_reviewer_model: str = os.getenv(
        "OUTPUT_REVIEWER_MODEL", "gpt-4.1-mini"
    ).strip()  # nano misclassifies TR's first-person speech as
    # "third person"; mini handles the nuance reliably at ~2x the cost
    # (still trivial).
    output_reviewer_flush_chars: int = int(
        os.getenv("OUTPUT_REVIEWER_FLUSH_CHARS", "160")
    )
    output_reviewer_timeout: float = float(
        os.getenv("OUTPUT_REVIEWER_TIMEOUT", "3.0")
    )
    output_reviewer_auto_report: bool = (
        os.getenv("OUTPUT_REVIEWER_AUTO_REPORT", "True").lower() == "true"
    )
    # Minimum response length to bother reviewing — short canned
    # deflections like "I'm afraid I don't know what you mean by that"
    # are well-known TR safe responses and not worth the LLM cost.
    output_reviewer_min_chars: int = int(
        os.getenv("OUTPUT_REVIEWER_MIN_CHARS", "40")
    )
    # Cap concurrent L3 LLM calls so a high-traffic moment doesn't burn
    # Foundry quota nor starve the main agent. Calls beyond this just
    # wait briefly for a slot; if they wait too long they no-op (the
    # response already streamed anyway).
    output_reviewer_max_concurrent: int = int(
        os.getenv("OUTPUT_REVIEWER_MAX_CONCURRENT", "8")
    )
    # Per-uid buffers older than this many seconds get garbage-collected
    # to handle abnormal session termination (websocket dropped, etc.).
    output_reviewer_buffer_ttl_seconds: int = int(
        os.getenv("OUTPUT_REVIEWER_BUFFER_TTL_SECONDS", "120")
    )

    # Audience Configuration
    audience: Literal["kids", "adults"] = os.getenv("AUDIENCE", "kids")

    # Questions Phase Configuration
    max_questions: int = int(os.getenv("MAX_QUESTIONS", "4"))

    # Authentication Configuration
    basic_auth_username: str = os.getenv("BASIC_AUTH_USERNAME", "").strip()
    basic_auth_password: str = os.getenv("BASIC_AUTH_PASSWORD", "").strip()
    client_api_keys: set[str] = set(
        key.strip() for key in os.getenv("CLIENT_API_KEYS", "").split(",") if key.strip()
    )

    # Debate WebSocket auth configuration.
    # Secure by default: the WS controller can drive the live session, so
    # require a controller/observer token. Set WS_AUTH_ENABLED=False only for
    # trusted local development.
    ws_auth_enabled: bool = os.getenv("WS_AUTH_ENABLED", "True").lower() == "true"
    ws_controller_token_ttl_seconds: int = int(
        os.getenv("WS_CONTROLLER_TOKEN_TTL_SECONDS", "120")
    )
    ws_observer_token_ttl_seconds: int = int(
        os.getenv("WS_OBSERVER_TOKEN_TTL_SECONDS", "120")
    )
    ws_reconnect_token_ttl_seconds: int = int(
        os.getenv("WS_RECONNECT_TOKEN_TTL_SECONDS", "3600")
    )
    ws_join_code_ttl_seconds: int = int(
        os.getenv("WS_JOIN_CODE_TTL_SECONDS", "3600")
    )

    # Search / RAG Configuration
    search_archives_query_key: str = os.getenv("SEARCH_ARCHIVES_QUERY_KEY", "").strip()
    search_archives_url: str = os.getenv("SEARCH_ARCHIVES_URL", "").strip()
    search_archives_index_name: str = os.getenv(
        "SEARCH_ARCHIVES_INDEX_NAME", ""
    ).strip()
    search_archives_semantic_config: str = os.getenv(
        "SEARCH_ARCHIVES_SEMANTIC_CONFIG", ""
    ).strip()
    search_archives_embedding_model: str = os.getenv(
        "SEARCH_ARCHIVES_EMBEDDING_MODEL", ""
    ).strip()
    search_archives_embedding_key: str = os.getenv(
        "SEARCH_ARCHIVES_EMBEDDING_KEY", ""
    ).strip()
    search_archives_embedding_endpoint: str = os.getenv(
        "SEARCH_ARCHIVES_EMBEDDING_ENDPOINT", ""
    ).strip()
    search_archives_embedding_api_version: str = os.getenv(
        "SEARCH_ARCHIVES_EMBEDDING_API_VERSION", ""
    ).strip()

    search_books_query_key: str = os.getenv("SEARCH_BOOKS_QUERY_KEY", "").strip()
    search_books_url: str = os.getenv("SEARCH_BOOKS_URL", "").strip()
    search_books_index_name: str = os.getenv("SEARCH_BOOKS_INDEX_NAME", "").strip()
    search_books_semantic_config: str = os.getenv(
        "SEARCH_BOOKS_SEMANTIC_CONFIG", ""
    ).strip()
    search_books_embedding_model: str = os.getenv(
        "SEARCH_BOOKS_EMBEDDING_MODEL", ""
    ).strip()
    search_books_embedding_key: str = os.getenv(
        "SEARCH_BOOKS_EMBEDDING_KEY", ""
    ).strip()
    search_books_embedding_endpoint: str = os.getenv(
        "SEARCH_BOOKS_EMBEDDING_ENDPOINT", ""
    ).strip()
    search_books_embedding_api_version: str = os.getenv(
        "SEARCH_BOOKS_EMBEDDING_API_VERSION", ""
    ).strip()

    # Application Insights Configuration
    applicationinsights_connection_string: str = os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
    ).strip()

    # Authentication modes (computed based on what's configured)
    @property
    def auth_modes(self) -> list[Literal["basic", "api_key"]]:
        """Return list of enabled authentication modes."""
        modes = []
        if self.basic_auth_username and self.basic_auth_password:
            modes.append("basic")
        if self.client_api_keys:
            modes.append("api_key")
        return modes

    @property
    def provider_status(self) -> dict[str, bool]:
        if self.runtime_mode == "deterministic":
            return {"llm": False, "search": False, "speech": False}

        llm = bool(
            self.llm_base_url
            and self.llm_api_key
            and self.llm_config_small.model
            and self.llm_config_mid.model
            and self.llm_config_large.model
        )
        search = bool(
            self.search_archives_query_key
            and self.search_archives_url
            and self.search_archives_index_name
            and self.search_archives_embedding_model
            and self.search_archives_embedding_key
            and self.search_archives_embedding_endpoint
            and self.search_books_query_key
            and self.search_books_url
            and self.search_books_index_name
            and self.search_books_embedding_model
            and self.search_books_embedding_key
            and self.search_books_embedding_endpoint
        )
        speech = bool(
            self.tts_enabled
            and self.tts_azure_api_key
            and self.tts_azure_region
        )
        return {"llm": llm, "search": search, "speech": speech}

    # For compatibility with existing code that expects these attributes
    @property
    def read_role(self) -> str | None:
        """Read role (not used in simple auth setup)."""
        return None

    @property
    def write_role(self) -> str | None:
        """Write role (not used in simple auth setup)."""
        return None

    @property
    def pledge_llm_config(self) -> LLMConfig:
        """Select the LLM config for pledge generation."""
        return self.pledge_llm_config_for(self.pledge_model_tier)

    def pledge_llm_config_for(self, tier: str) -> LLMConfig:
        """Select the LLM config for pledge generation by tier."""
        if tier == "full":
            return self.llm_config_large
        if tier == "mini":
            return self.llm_config_mid
        return self.llm_config_small

    def slogan_llm_config_for(self, tier: str) -> LLMConfig:
        """Select the LLM config for slogan generation by tier."""
        return self.pledge_llm_config_for(tier)

    @property
    def voice_presets(self) -> dict[str, VoicePreset]:
        """Named voice presets available for selection."""
        return {
            "davis_dragon_hd": VoicePreset(
                provider="azure",
                azure_api_key=self.tts_azure_api_key,
                azure_region=self.tts_azure_region,
                azure_voice=self.tts_azure_voice_id,
            ),
            "tr_cortina": VoicePreset(
                provider="azure",
                azure_api_key=self.tts_azure_api_key_westus,
                azure_region="westus",
                azure_deployment=self.tr_cortina_deployment,
            ),
            "joe_2": VoicePreset(
                provider="azure",
                azure_api_key=self.tts_azure_api_key_westus,
                azure_region="westus",
                azure_deployment=self.joe_2_deployment,
            ),
            "elevenlabs": VoicePreset(
                provider="elevenlabs",
                eleven_api_key=self.eleven_api_key,
                eleven_voice_id=self.eleven_voice_id,
                eleven_model_id=self.eleven_model_id,
                eleven_output_format=self.eleven_output_format,
                eleven_base_url=self.eleven_base_url,
            ),
        }

    def get_voice_preset(self, name: str) -> VoicePreset:
        """Look up a voice preset by name."""
        return self.voice_presets[name]


# Global config instance
config = Config()
