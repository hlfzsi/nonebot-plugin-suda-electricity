__all__ = ["Config", "APP_CONFIG"]
from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    database_url: str | None = Field(
        default=None,
        description="Database connection URL. Falls back to the local SQLite file when unset.",
    )

    suda_secret_key: str = Field(
        ...,
        description="Secret key for encrypting sensitive data in the database.",
    )

    scheduler_interval_hours: int = Field(
        default=8,
        ge=1,
        description="Fixed check interval for each dormitory, in hours.",
    )
    scheduler_tick_seconds: int = Field(
        default=60,
        ge=1,
        description="How often the scheduler scans for due dormitories.",
    )
    scheduler_due_limit: int = Field(
        default=10,
        ge=1,
        description="Maximum number of due dormitories handled in one scheduler tick.",
    )


APP_CONFIG = get_plugin_config(Config)
