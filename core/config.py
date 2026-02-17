"""Configuration loading and validation.

Loads settings from config.yml with environment variable interpolation.
Secrets (API keys, tokens) come from environment variables; structural
config (which channels are enabled, scheduler jobs, etc.) lives in YAML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ${ENV_VAR} references in strings."""
    if isinstance(obj, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


# --- Config models ---


class AgentConfig(BaseModel):
    name: str = "Clio"
    owner_name: str = "Matteo"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-5-20250514"
    timezone: str = "Europe/Zurich"
    skills_dir: str = "skills/"
    character_file: str = "character.md"
    personalia_file: str = "personalia.md"


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_comma_separated_ints(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return [int(x.strip()) for x in v.split(",") if x.strip()] if v else []
        return v


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    bridge_url: str = "http://localhost:3001"
    allowed_numbers: list[str] = Field(default_factory=list)

    @field_validator("allowed_numbers", mode="before")
    @classmethod
    def parse_comma_separated_strings(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return [x.strip() for x in v.split(",") if x.strip()] if v else []
        return v


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()


class CalendarProvider(BaseModel):
    name: str
    url: str
    username: str = ""
    password: str = ""


class CalendarConfig(BaseModel):
    providers: list[CalendarProvider] = Field(default_factory=list)


class VoiceConfig(BaseModel):
    stt_model: str = "base"
    tts_voice: str = "en-US-GuyNeural"
    tts_enabled: bool = True


class SchedulerJob(BaseModel):
    id: str
    cron: str
    task: str
    channel: str = "telegram"
    type: str = "agent"


class SchedulerConfig(BaseModel):
    jobs: list[SchedulerJob] = Field(default_factory=list)


class AdminConfig(BaseModel):
    enabled: bool = True
    port: int = 8000
    api_key: str = ""


class HistoryConfig(BaseModel):
    db_path: str = "data/agent.db"
    max_turns: int = 20


class Config(BaseModel):
    agent: AgentConfig = AgentConfig()
    channels: ChannelsConfig = ChannelsConfig()
    calendar: CalendarConfig = CalendarConfig()
    voice: VoiceConfig = VoiceConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    admin: AdminConfig = AdminConfig()
    history: HistoryConfig = HistoryConfig()


def load_config(path: str | Path = "config.yml") -> Config:
    """Load and validate config from a YAML file.

    Loads .env file first, then resolves ${VAR_NAME} references in the
    YAML against environment variables.
    """
    load_dotenv()

    path = Path(path)
    if not path.exists():
        return Config()

    raw = yaml.safe_load(path.read_text()) or {}
    resolved = _resolve_env_vars(raw)
    return Config.model_validate(resolved)
