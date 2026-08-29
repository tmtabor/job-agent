from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' env_file support (below) only populates the Settings
# object, not os.environ — but pydantic-ai's GoogleProvider reads GOOGLE_API_KEY
# directly via os.getenv(...), bypassing Settings entirely. Load .env into
# os.environ here too, so a key set only in .env is visible to the provider.
# override=False (the default) won't clobber real exported env vars.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Validated at startup: if the selected model's provider requires an API
    key and it is missing, Settings() raises immediately with a clear error
    instead of failing cryptically at the first API call.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Agent-specific vars are prefixed (AGENT_MODEL, AGENT_LOG_LEVEL, …) so
        # a generic name like MODEL in the user's shell can't silently change
        # the provider. Fields with an explicit validation_alias are exempt.
        env_prefix="AGENT_",
    )

    # Gemini API key. Keeps its standard, unprefixed name: pydantic-ai's
    # GoogleProvider reads this exact variable directly, so prefixing it would
    # validate one variable while the model client reads another.
    google_api_key: str | None = Field(default=None, validation_alias="GOOGLE_API_KEY")

    # Model selection. Both agents run on Gemini; override per-deployment via
    # AGENT_MODEL / AGENT_JUDGE_MODEL in .env.
    model: str = "google:gemini-3.1-flash-lite"

    # Judge model for LLM-as-judge evals. Deliberately a more capable tier than
    # `model` — a weak judge grading a strong agent introduces its own bias.
    judge_model: str = "google:gemini-3.1-pro-preview"

    # Logfire — optional, falls back to console if not set. Unprefixed:
    # LOGFIRE_TOKEN is the standard name the Logfire SDK and CLI use.
    logfire_token: str | None = Field(default=None, validation_alias="LOGFIRE_TOKEN")

    # Logging
    log_level: str = "INFO"

    # Job-agent-specific: non-model API keys/tokens, unprefixed for the same
    # reason as the model provider keys above (each SDK/client reads its own
    # standard env var name directly).
    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    adzuna_app_id: str | None = Field(default=None, validation_alias="ADZUNA_APP_ID")
    adzuna_api_key: str | None = Field(default=None, validation_alias="ADZUNA_API_KEY")
    postmark_server_token: str | None = Field(
        default=None, validation_alias="POSTMARK_SERVER_TOKEN"
    )

    # Job-agent-specific: digest email sender/recipient. These keep the
    # AGENT_ prefix (picked up automatically via env_prefix, no alias needed)
    # since they're this agent's own setting, not a third-party SDK's var.
    email_from: str | None = None
    email_to: str | None = None

    @model_validator(mode="after")
    def check_provider_key(self) -> "Settings":
        """Fail fast if the selected model's provider key is missing.

        Only the agent model is validated here — the judge model is used
        only by evals, which require a real key at runtime anyway.
        """
        provider = self.model.split(":", 1)[0]
        if provider == "google" and not self.google_api_key:
            raise ValueError(
                "AGENT_MODEL is a Google model but GOOGLE_API_KEY is not set. "
                "Add it to .env or the environment."
            )
        # "ollama" (and other local providers) run locally — no API key needed.
        return self


settings = Settings()
