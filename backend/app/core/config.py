"""Application configuration.

All values are read from environment variables (optionally seeded from a
``.env`` file). The Ollama model can be swapped without touching code by
changing ``OLLAMA_MODEL``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> <repo root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration for CivicBridge AI."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application -------------------------------------------------------
    app_name: str = "CivicBridge AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    support_languages: str = "en,ta"

    # -- PostgreSQL --------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "civicbridge_app"
    postgres_password: str = "civicbridge_dev_password"
    postgres_db: str = "civicbridge"
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")

    # -- Ollama ------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:latest"
    ollama_health_timeout: float = 5.0
    ollama_llm_timeout: float = 180.0
    ollama_temperature: float = 0.0
    ollama_num_predict: int = 128
    ollama_keep_alive: str = "5m"
    # Layers offloaded to the GPU. Left unset (None) so Ollama decides how many
    # layers to place on the GPU - on this host that offloads the bulk of the
    # model (verified ~25% CPU / 75% GPU on the RTX 3050). Set to 0 to force
    # CPU-only inference, or to a layer count to cap the offload.
    ollama_num_gpu: int | None = None
    # Optional vision-capable model for the evidence agent. Leave unset (or
    # empty) to run image analysis with OpenCV features only. When a
    # vision-capable model is pulled and named here, its description is
    # recorded as a model opinion - never as evidence.
    ollama_vision_model: str | None = None

    # -- Agents ------------------------------------------------------------
    # Every knob below exists to keep CPU inference affordable: the pipeline
    # makes at most two model calls per complaint and degrades to deterministic
    # extraction when the model is unavailable instead of retrying.
    agent_use_llm: bool = True
    # Below this confidence the classification agent flags the complaint for a
    # human officer instead of routing it automatically.
    agent_confidence_threshold: float = 0.60
    # Extra attempts to obtain schema-valid JSON after the first response.
    agent_max_repair_attempts: int = 1
    # Structured output is longer than a chat reply, so the agent budget is
    # deliberately larger than the Phase 1 smoke-test budget.
    agent_num_predict: int = 512
    # Bounded in-process cache of identical prompts; avoids duplicate inference
    # when the same complaint text is analysed twice.
    agent_response_cache_size: int = 256

    # -- LangGraph orchestration (Phase 6) ---------------------------------
    # The graph is pure Python and cheap; these knobs only bound how long a
    # single node may run and how many times a transient failure is retried.
    # Inference itself is still bounded by ``ollama_llm_timeout``.
    orchestration_enabled: bool = True
    # Wall-clock ceiling for one node call. The agents degrade internally, so
    # this only fires when a call genuinely hangs (e.g. Ollama stalls).
    orchestration_node_timeout_seconds: float = 240.0
    # Total attempts (first try + retries) for one node before it is failed.
    # Retries only apply to transient agent/timeout errors, never to a node
    # that produced a valid result - so a retry can never duplicate a write.
    orchestration_max_node_attempts: int = 2
    # Upper bound on the whole run; checked between nodes.
    orchestration_total_timeout_seconds: float = 900.0
    # When true the graph is compiled with an in-process checkpointer so a
    # human-review interrupt can be resumed. The checkpointer is not durable
    # across processes (see the risk register) - it is a prototype aid.
    orchestration_checkpointing_enabled: bool = True
    # Default for whether the human-review checkpoint actually interrupts.
    # Off by default so the synchronous endpoint returns a complete result in
    # one call; a caller may opt in per request.
    orchestration_interrupt_for_review: bool = False

    # -- Authentication and authorisation (Phase 7) ------------------------
    # The prototype ships with real server-side sessions because a role check
    # performed only in the browser is not a role check at all. Every officer
    # action endpoint resolves the caller from the signed session cookie.
    auth_enabled: bool = True
    #: HMAC key for the signed session cookie. The default keeps the prototype
    #: runnable out of the box; set a long random value in any real deployment.
    session_secret_key: str = "civicbridge-prototype-session-key-change-me"
    session_cookie_name: str = "civicbridge_session"
    session_ttl_seconds: int = 8 * 60 * 60
    #: Send the cookie only over HTTPS. Left off for local http:// development.
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    #: Known demo passwords for the synthetic accounts, so the reviewer can sign
    #: in without a real account. They are not secrets and must be changed
    #: before any non-prototype use.
    auth_citizen_password: str = "citizen-demo-password"
    auth_officer_password: str = "officer-demo-password"
    auth_admin_password: str = "admin-demo-password"

    # -- Uploads (Phase 7) -------------------------------------------------
    #: Hard ceiling on one uploaded image, enforced before the bytes reach the
    #: evidence agent.
    max_upload_bytes: int = 8 * 1024 * 1024
    #: Image types the prototype accepts; the declared type must also match the
    #: bytes actually sniffed from the file.
    allowed_upload_types: str = "image/jpeg,image/png,image/webp"
    #: Browsers allowed to call the API with credentials. Kept as an explicit
    #: list - never "*" - because cookies are sent with the request.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """SQLAlchemy connection string (``DATABASE_URL`` wins when set)."""
        if self.database_url_override:
            return self.database_url_override
        # Percent-encode the credentials: a password containing URL delimiters
        # such as ``@`` or ``/`` would otherwise be parsed as part of the host.
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg2://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def safe_database_url(self) -> str:
        """Connection string with the password masked, for logs and CLI output."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg2://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def languages(self) -> list[str]:
        return [code.strip() for code in self.support_languages.split(",") if code.strip()]

    @property
    def allowed_upload_mime_types(self) -> list[str]:
        """The MIME types accepted for an uploaded image."""
        return [item.strip() for item in self.allowed_upload_types.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        """Explicit browser origins allowed to send credentialed requests."""
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor so ``.env`` is parsed once per process."""
    return Settings()


settings = get_settings()
