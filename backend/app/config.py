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
    # 뉴스 검색: Claude CLI 웹검색(가벼운 모델). 하루 검색 횟수 상한(검색 1회 = 기업 1곳 또는 주제 1개)
    news_search_model: str = Field(default="sonnet", alias="NEWS_SEARCH_MODEL")
    news_daily_search_limit: int = Field(default=40, alias="NEWS_DAILY_SEARCH_LIMIT")
    ai_provider: str = Field(default="none", alias="AI_PROVIDER")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    ai_model: str = Field(default="claude-opus-5-5", alias="AI_MODEL")
    # Claude CLI takes model aliases (opus / sonnet / haiku) rather than API model IDs.
    claude_cli_model: str = Field(default="opus", alias="CLAUDE_CLI_MODEL")
    # Read-only access to the separate dart-market-monitor database (shared raw source).
    dart_monitor_database_url: str = Field(default="", alias="DART_MONITOR_DATABASE_URL")
    # The API keys are shared with dart-market-monitor; cap ValueScope's own usage per day.
    dart_daily_call_limit: int = Field(default=3000, alias="DART_DAILY_CALL_LIMIT")
    krx_daily_call_limit: int = Field(default=1000, alias="KRX_DAILY_CALL_LIMIT")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    # PDF 출력용 브라우저(Edge/Chrome) 경로. 비우면 기본 설치 경로에서 자동으로 찾음.
    pdf_browser_path: str = Field(default="", alias="PDF_BROWSER_PATH")

    # Local, git-ignored storage for raw downloaded documents / caches.
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="DATA_DIR")

    def integration_status(self) -> dict:
        """Which integrations are configured — booleans only, never values."""
        return {
            "database": bool(self.database_url),
            "dart": bool(self.dart_api_key),
            "krx": bool(self.krx_api_key),
            "news": (self.ai_provider or "").lower() == "claude_cli",
            "dart_monitor": bool(self.dart_monitor_database_url),
            "ai_provider": self.ai_provider,
            "ai_ready": self.ai_provider == "claude_cli"
            or (self.ai_provider == "anthropic" and bool(self.ai_api_key)),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
