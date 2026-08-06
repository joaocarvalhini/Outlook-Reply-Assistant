"""Environment-driven configuration.

Values resolve in this order: CLI overrides -> process environment -> `.env`
file -> documented default. Unlike a CLI tool, this daemon runs unattended, so
every setting is validated at startup: a bad value must fail before the first
poll, not halfway through a mailbox.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

__all__ = ["Config", "ConfigError"]

DEFAULT_CLASSIFIER_MODEL: Final[str] = "claude-haiku-4-5"
DEFAULT_REPLY_MODEL: Final[str] = "claude-haiku-4-5"
DEFAULT_KNOWLEDGE_DIR: Final[str] = "knowledge"
DEFAULT_BLOCKLIST_FILE: Final[str] = "blocklist.txt"
DEFAULT_STATE_FILE: Final[str] = "state.json"
DEFAULT_MAX_TOKENS: Final[int] = 1024
DEFAULT_POLL_SECONDS: Final[int] = 300
DEFAULT_MAX_BODY_CHARS: Final[int] = 4000
DEFAULT_LOG_LEVEL: Final[str] = "INFO"
DEFAULT_COMPANY_NAME: Final[str] = "A Loja"
DEFAULT_ASSISTANT_NAME: Final[str] = "Assistente"
DEFAULT_SIGNATURE: Final[str] = "Equipa de Apoio ao Cliente"
DEFAULT_DRAFTED_CATEGORY: Final[str] = "IA-Rascunhado"
DEFAULT_ESCALATED_CATEGORY: Final[str] = "Precisa de humano"

VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "y", "on", "sim"})


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


def _lookup(name: str, overrides: Mapping[str, str | None] | None) -> str | None:
    if overrides is not None:
        value = overrides.get(name)
        if value is not None:
            return value
    return os.environ.get(name)


def _as_int(name: str, raw: str | None, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def _required(name: str, raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise ConfigError(f"{name} is required and must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime settings for one daemon process."""

    api_key: str
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str

    classifier_model: str = DEFAULT_CLASSIFIER_MODEL
    reply_model: str = DEFAULT_REPLY_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS

    knowledge_dir: Path = Path(DEFAULT_KNOWLEDGE_DIR)
    blocklist_file: Path = Path(DEFAULT_BLOCKLIST_FILE)
    state_file: Path = Path(DEFAULT_STATE_FILE)

    poll_seconds: int = DEFAULT_POLL_SECONDS
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS
    # True means: run the whole pipeline, log what it would have done, write
    # nothing to the mailbox. This is the default in .env.example on purpose.
    dry_run: bool = True

    company_name: str = DEFAULT_COMPANY_NAME
    assistant_name: str = DEFAULT_ASSISTANT_NAME
    signature: str = DEFAULT_SIGNATURE

    drafted_category: str = DEFAULT_DRAFTED_CATEGORY
    escalated_category: str = DEFAULT_ESCALATED_CATEGORY

    log_level: str = DEFAULT_LOG_LEVEL
    log_file: Path | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Fail fast on values that would break the daemon mid-poll."""
        if "@" not in self.mailbox:
            raise ConfigError(f"MAILBOX must be an email address, got {self.mailbox!r}")
        if not self.classifier_model.strip():
            raise ConfigError("CLASSIFIER_MODEL must not be empty")
        if not self.reply_model.strip():
            raise ConfigError("REPLY_MODEL must not be empty")
        if self.max_tokens < 256:
            raise ConfigError("MAX_TOKENS must be at least 256")
        if self.poll_seconds < 30:
            # Below this the Graph throttling budget is spent on empty polls.
            raise ConfigError("POLL_SECONDS must be at least 30")
        if self.max_body_chars < 500:
            raise ConfigError("MAX_BODY_CHARS must be at least 500")
        if self.log_level not in VALID_LOG_LEVELS:
            raise ConfigError(
                f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {self.log_level!r}"
            )
        if self.drafted_category == self.escalated_category:
            raise ConfigError("DRAFTED_CATEGORY and ESCALATED_CATEGORY must differ")

    @property
    def mailbox_domain(self) -> str:
        _, _, domain = self.mailbox.partition("@")
        return domain.lower()

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path | None = None,
        overrides: Mapping[str, str | None] | None = None,
    ) -> Config:
        """Build a `Config` from `.env`, the process environment and overrides."""
        load_dotenv(dotenv_path=env_file, override=False)

        log_file_raw = _lookup("LOG_FILE", overrides)

        return cls(
            api_key=_required("ANTHROPIC_API_KEY", _lookup("ANTHROPIC_API_KEY", overrides)),
            tenant_id=_required("GRAPH_TENANT_ID", _lookup("GRAPH_TENANT_ID", overrides)),
            client_id=_required("GRAPH_CLIENT_ID", _lookup("GRAPH_CLIENT_ID", overrides)),
            client_secret=_required(
                "GRAPH_CLIENT_SECRET", _lookup("GRAPH_CLIENT_SECRET", overrides)
            ),
            mailbox=_required("MAILBOX", _lookup("MAILBOX", overrides)).lower(),
            classifier_model=(
                _lookup("CLASSIFIER_MODEL", overrides) or DEFAULT_CLASSIFIER_MODEL
            ).strip(),
            reply_model=(_lookup("REPLY_MODEL", overrides) or DEFAULT_REPLY_MODEL).strip(),
            max_tokens=_as_int("MAX_TOKENS", _lookup("MAX_TOKENS", overrides), DEFAULT_MAX_TOKENS),
            knowledge_dir=Path(
                (_lookup("KNOWLEDGE_DIR", overrides) or DEFAULT_KNOWLEDGE_DIR).strip()
            ),
            blocklist_file=Path(
                (_lookup("BLOCKLIST_FILE", overrides) or DEFAULT_BLOCKLIST_FILE).strip()
            ),
            state_file=Path((_lookup("STATE_FILE", overrides) or DEFAULT_STATE_FILE).strip()),
            poll_seconds=_as_int(
                "POLL_SECONDS", _lookup("POLL_SECONDS", overrides), DEFAULT_POLL_SECONDS
            ),
            max_body_chars=_as_int(
                "MAX_BODY_CHARS", _lookup("MAX_BODY_CHARS", overrides), DEFAULT_MAX_BODY_CHARS
            ),
            dry_run=_as_bool(_lookup("DRY_RUN", overrides), True),
            company_name=(_lookup("COMPANY_NAME", overrides) or DEFAULT_COMPANY_NAME).strip(),
            assistant_name=(
                _lookup("ASSISTANT_NAME", overrides) or DEFAULT_ASSISTANT_NAME
            ).strip(),
            signature=(_lookup("SIGNATURE", overrides) or DEFAULT_SIGNATURE).strip(),
            drafted_category=(
                _lookup("DRAFTED_CATEGORY", overrides) or DEFAULT_DRAFTED_CATEGORY
            ).strip(),
            escalated_category=(
                _lookup("ESCALATED_CATEGORY", overrides) or DEFAULT_ESCALATED_CATEGORY
            ).strip(),
            log_level=(_lookup("LOG_LEVEL", overrides) or DEFAULT_LOG_LEVEL).strip().upper(),
            log_file=Path(log_file_raw.strip())
            if log_file_raw and log_file_raw.strip()
            else None,
        )
