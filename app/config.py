"""
Configuration module using Pydantic Settings.

Loads all configuration from environment variables following 12-Factor App principles.
This ensures configuration is separated from code and can be easily changed
across different environments (development, staging, production).
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    All settings have sensible defaults for development.
    Production deployments should override via environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Flask Configuration
    # -------------------------------------------------------------------------
    flask_env: str = Field(default="development")
    secret_key: str = Field(default="dev-secret-key-change-in-production")

    # -------------------------------------------------------------------------
    # Database Configuration
    # -------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/sentiment_intel"
    )

    # -------------------------------------------------------------------------
    # OpenAI Configuration
    # -------------------------------------------------------------------------
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")

    # -------------------------------------------------------------------------
    # Worker Configuration
    # -------------------------------------------------------------------------
    worker_id: str = Field(default="worker-1")
    worker_poll_interval: int = Field(default=5, ge=1)
    worker_batch_size: int = Field(default=10, ge=1)
    worker_max_retries: int = Field(default=3, ge=0)
    worker_lock_timeout: int = Field(default=600, ge=60)

    # -------------------------------------------------------------------------
    # Sentiment Analysis Configuration
    # -------------------------------------------------------------------------
    sentiment_tb_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    sentiment_llm_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    sentiment_positive_threshold: float = Field(default=0.1)
    sentiment_negative_threshold: float = Field(default=-0.1)

    # -------------------------------------------------------------------------
    # Alert Configuration
    # -------------------------------------------------------------------------
    alert_negative_ratio_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    slack_webhook_url: Optional[str] = Field(default=None)

    # -------------------------------------------------------------------------
    # Logging Configuration
    # -------------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log level is valid."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v_upper

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Ensure log format is valid."""
        valid_formats = {"json", "console"}
        v_lower = v.lower()
        if v_lower not in valid_formats:
            raise ValueError(f"log_format must be one of {valid_formats}")
        return v_lower

    # -------------------------------------------------------------------------
    # Computed Properties
    # -------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.flask_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.flask_env.lower() == "development"

    @property
    def has_openai_key(self) -> bool:
        """Check if OpenAI API key is configured."""
        return bool(self.openai_api_key and self.openai_api_key.startswith("sk-"))


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    Uses @lru_cache to ensure settings are loaded only once.
    This is thread-safe and efficient.
    """
    return Settings()


__all__ = ["Settings", "get_settings"]