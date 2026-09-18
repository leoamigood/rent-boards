"""Configuration, loaded from the environment with a .env fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file. Existing vars win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Config:
    api_id: int | None
    api_hash: str | None
    phone: str | None
    chat: str
    db_path: Path
    session_path: Path
    gel_per_usd: float
    eur_per_usd: float

    def require_api(self) -> tuple[int, str]:
        if not self.api_id or not self.api_hash:
            raise SystemExit(
                "TG_API_ID / TG_API_HASH are not set.\n"
                "Create them at https://my.telegram.org -> API development tools,\n"
                "then copy .env.example to .env and fill them in."
            )
        return self.api_id, self.api_hash


def load(chat_override: str | None = None, db_override: str | None = None) -> Config:
    load_dotenv()
    api_id = os.environ.get("TG_API_ID", "").strip()
    cfg = Config(
        api_id=int(api_id) if api_id.isdigit() else None,
        api_hash=os.environ.get("TG_API_HASH", "").strip() or None,
        phone=os.environ.get("TG_PHONE", "").strip() or None,
        chat=normalise_chat(chat_override or os.environ.get("TG_CHAT", "batumiarendachat")),
        db_path=_path(db_override or os.environ.get("DB_PATH", "data/rentals.db")),
        session_path=_path(os.environ.get("SESSION_PATH", "data/tg.session")),
        gel_per_usd=float(os.environ.get("GEL_PER_USD", "2.70")),
        eur_per_usd=float(os.environ.get("EUR_PER_USD", "0.92")),
    )
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.session_path.parent.mkdir(parents=True, exist_ok=True)
    return cfg


def normalise_chat(value: str) -> str:
    """Accept t.me/foo, https://t.me/foo, @foo, foo or a numeric id."""
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    if value.startswith("t.me/"):
        value = value[len("t.me/"):]
    value = value.strip("/").lstrip("@")
    return value
