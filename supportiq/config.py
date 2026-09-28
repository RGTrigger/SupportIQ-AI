from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    vector_path: Path
    mode: str
    groq_api_key: str | None = field(repr=False)
    llm_model: str
    github_repo: str | None = None
    email_provider: str = "demo"
    email_from: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_starttls: bool = True
    sms_provider: str = "demo"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = field(default=None, repr=False)
    twilio_from: str | None = None

    @property
    def ai_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def email_live_ready(self) -> bool:
        return (self.email_provider=="smtp" and bool(self.smtp_host and self.email_from and self.smtp_username and self.smtp_password)
                and 1 <= self.smtp_port <= 65535 and bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",self.email_from or "")))

    @property
    def sms_live_ready(self) -> bool:
        return self.sms_provider=="twilio" and bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from)


def _path(name: str, default: str, configured: str | None = None) -> Path:
    path = Path(configured or os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


def get_settings() -> Settings:
    secrets={}
    try:
        import streamlit as st
        secrets=dict(st.secrets)
    except Exception:
        pass
    env=lambda name,default=None: os.getenv(name) or secrets.get(name) or default
    return Settings(
        db_path=_path("SUPPORTIQ_DB_PATH", "data/supportiq.sqlite3",str(env("SUPPORTIQ_DB_PATH","data/supportiq.sqlite3"))),
        vector_path=_path("SUPPORTIQ_VECTOR_PATH", "data/chroma",str(env("SUPPORTIQ_VECTOR_PATH","data/chroma"))),
        mode=str(env("SUPPORTIQ_MODE", "demo")).strip().lower(),
        groq_api_key=env("GROQ_API_KEY"),
        llm_model=env("LLM_MODEL","openai/gpt-oss-20b"),
        github_repo=env("SUPPORTIQ_GITHUB_REPO"),
        email_provider=str(env("EMAIL_PROVIDER","demo")).lower(),
        email_from=env("EMAIL_FROM"),
        smtp_host=env("SMTP_HOST"),
        smtp_port=int(env("SMTP_PORT",587)),
        smtp_username=env("SMTP_USERNAME"),
        smtp_password=env("SMTP_PASSWORD"),
        smtp_starttls=str(env("SMTP_STARTTLS","true")).lower() in {"true","1","yes"},
        sms_provider=str(env("SMS_PROVIDER","demo")).lower(),
        twilio_account_sid=env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=env("TWILIO_AUTH_TOKEN"),
        twilio_from=env("TWILIO_FROM"),
    )
