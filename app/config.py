"""Runtime configuration, read from environment variables (12-factor style)."""
import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/shortener.db"
    base_url: str | None = None      # public URL used when building short links
    visitor_salt: str = ""           # secret mixed into visitor hashes
    trust_proxy: bool = False        # only True behind a proxy you control
    rate_limit_per_min: int = 30     # link creations per IP per minute (0 = off)

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_path=env.get("DATABASE_PATH") or "data/shortener.db",
            base_url=(env.get("BASE_URL") or "").rstrip("/") or None,
            # No hardcoded secret: generate one per process if none is provided.
            visitor_salt=env.get("VISITOR_SALT") or secrets.token_hex(16),
            trust_proxy=env.get("TRUST_PROXY", "").lower() in {"1", "true", "yes"},
            rate_limit_per_min=int(env.get("RATE_LIMIT_PER_MIN") or 30),
        )
