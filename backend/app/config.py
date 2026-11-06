"""Application settings loaded from the repository-root .env file.

Secrets live only in .env (git-ignored) and are never sent to the frontend.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(default="", alias="DATABASE_URL")
    dart_api_key: str = Field(default="", alias="DART_API_KEY")
    krx_api_key: str = Field(default="", alias="KRX_API_KEY")
    news_api_key: str = Field(default="", alias="NEWS_API_KEY")
    ai_provider: str = Field(default="none", alias="AI_PROVIDER")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    ai_model: str = Field(default="claude-opus-5-5", alias="AI_MODEL")
    # Claude CLI takes model aliases (opus / sonnet / haiku) rather than API model IDs.
    claude_cli_model: str = Field(default="opus", alias="CLAUDE_CLI_MODEL")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    # Local, git-ignored storage for raw downloaded documents / caches.
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="DATA_DIR")

    def integration_status(self) -> dict:
        """Which integrations are configured — booleans only, never values."""
        return {
            "database": bool(self.database_url),
            "dart": bool(self.dart_api_key),
            "krx": bool(self.krx_api_key),
            "news": bool(self.news_api_key),
            "ai_provider": self.ai_provider,
            "ai_ready": self.ai_provider == "claude_cli"
            or (self.ai_provider == "anthropic" and bool(self.ai_api_key)),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
