"""Runtime configuration, read from environment variables (never from files)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .instagram import API_URLS


class ConfigError(RuntimeError):
    """Required configuration is missing."""


def _require(env: Mapping[str, str], names: tuple[str, ...]) -> dict[str, str]:
    values = {name: env.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"missing environment variable(s): {', '.join(missing)}")
    return values


@dataclass(frozen=True)
class InstagramSettings:
    user_id: str
    access_token: str
    api: str = "facebook"  # "facebook" (Page-linked) or "instagram" (Instagram Login)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> InstagramSettings:
        env = os.environ if environ is None else environ
        values = _require(env, ("IG_USER_ID", "IG_ACCESS_TOKEN"))
        # Without IG_API, tell the two token kinds apart: Instagram Login tokens start with
        # "IG", Facebook ones with "EAA".
        guessed = "instagram" if values["IG_ACCESS_TOKEN"].startswith("IG") else "facebook"
        api = env.get("IG_API", "").strip().lower() or guessed
        if api not in API_URLS:
            raise ConfigError(f"IG_API must be one of {sorted(API_URLS)}, got {api!r}")
        return cls(values["IG_USER_ID"], values["IG_ACCESS_TOKEN"], api)


@dataclass(frozen=True)
class R2Settings:
    """Cloudflare R2 bucket (S3-compatible) that hosts the arts Meta downloads."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_url: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> R2Settings:
        env = os.environ if environ is None else environ
        values = _require(
            env,
            (
                "R2_ACCOUNT_ID",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "R2_BUCKET",
                "R2_PUBLIC_URL",
            ),
        )
        return cls(
            account_id=values["R2_ACCOUNT_ID"],
            access_key_id=values["R2_ACCESS_KEY_ID"],
            secret_access_key=values["R2_SECRET_ACCESS_KEY"],
            bucket=values["R2_BUCKET"],
            public_url=values["R2_PUBLIC_URL"].rstrip("/"),
        )
