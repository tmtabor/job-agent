from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' own env_file loading (below) only populates this Settings
# object — it never writes into os.environ. But the model provider SDKs
# (pydantic_ai.providers.google/anthropic/openai, and every other provider)
# read their API key env vars directly via os.getenv(...), bypassing Settings
# entirely. Without this, a provider key set only in .env is invisible to
# those SDKs even though Settings itself validates successfully.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Validated at startup: if the selected model's provider is one of the
    common cloud providers and its API key is missing, Settings() raises
    immediately with a clear error instead of failing cryptically at the
    first API call.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Agent-specific vars are prefixed (AGENT_MODEL, AGENT_LOG_LEVEL, …) so
        # a generic name like MODEL in the user's shell can't silently change
        # the provider. Fields with an explicit validation_alias are exempt.
        env_prefix="AGENT_",
        # A workflow that sets AGENT_MODEL: ${{ vars.AGENT_MODEL }} passes an
        # empty string when that repo variable doesn't exist. Without this,
        # pydantic-settings treats "" as an explicit value instead of falling
        # back to the field default, and infer_model("") then blows up.
        env_ignore_empty=True,
    )

    # Provider API keys. Any model string Pydantic AI accepts works for
    # AGENT_MODEL / AGENT_JUDGE_MODEL — these three are the ones check_provider_key
    # gives an early, friendly error for; other providers (groq, mistral,
    # bedrock, ollama, …) just need their own standard env var set, which their
    # SDK reads directly. Keys keep their standard, unprefixed names for that
    # reason.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    google_api_key: str | None = Field(default=None, validation_alias="GOOGLE_API_KEY")

    # Model selection — model-agnostic (any Pydantic AI model string). Defaults
    # to a small/fast Gemini model: Tier 2 scoring is a high-volume structured
    # classification task, not a general assistant, so a lightweight default
    # fits and keeps per-run cost low.
    model: str = "google:gemini-3.1-flash-lite"

    # Judge model for the LLM-as-judge evals. A more capable tier than `model`
    # (a weak judge grading a strong agent introduces its own bias), and
    # ideally a different model family so grading doesn't depend on the same
    # model being evaluated. Defaults to Gemini Pro so a single GOOGLE_API_KEY
    # runs everything, including `-m eval`; override via AGENT_JUDGE_MODEL.
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
        """Fail fast if the selected model's provider key is obviously missing.

        Only covers the three common cloud providers and only the agent model
        (the judge model is used only by evals, which need a real key at
        runtime anyway). Any other provider prefix is left alone — its SDK
        raises its own error at call time if misconfigured.
        """
        provider = self.model.split(":", 1)[0]
        required = {
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
            "google": ("GOOGLE_API_KEY", self.google_api_key),
        }.get(provider)
        if required and not required[1]:
            raise ValueError(
                f"AGENT_MODEL uses the '{provider}' provider but {required[0]} is not set. "
                "Add it to .env or the environment."
            )
        return self


settings = Settings()
