"""Read and write .env settings for the easy UI."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

SETUP_KEYS = (
    "POLYMARKET_PK",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_PASSPHRASE",
    "POLYMARKET_FUNDER",
    "MARKET_BUY_USD",
    "USE_LIGHTWEIGHT_PREDICTOR",
    "TRADING_ASSET",
    "TRADING_ASSETS",
)

ADVANCED_KEYS = (
    "SUBMIT_SECONDS_BEFORE_BOUNDARY",
    "PREDICTOR_EVAL_POST_END_SEC",
    "MARTINGALE_BASE_USD",
    "MARTINGALE_MAX_STAKE_USD",
    "POLYMARKET_AUTO_REDEEM",
    "PREDICTOR_MIN_SCORE",
    "PREDICTOR_POLL_SECONDS",
    "BOT_VERBOSE",
)

ALL_MANAGED_KEYS: Tuple[str, ...] = SETUP_KEYS + ADVANCED_KEYS

ADVANCED_DEFAULTS: Dict[str, str] = {
    "SUBMIT_SECONDS_BEFORE_BOUNDARY": "60",
    "PREDICTOR_EVAL_POST_END_SEC": "90",
    "MARTINGALE_BASE_USD": "1",
    "MARTINGALE_MAX_STAKE_USD": "16",
    "POLYMARKET_AUTO_REDEEM": "1",
    "PREDICTOR_MIN_SCORE": "0.50",
    "PREDICTOR_POLL_SECONDS": "5",
    "BOT_VERBOSE": "0",
}

GENERATE_KEYS_URL = "https://polymarkettool-272623624738.us-central1.run.app/"
TELEGRAM_URL = "https://t.me/dearolaf"
WHATSAPP_URL = "https://wa.me/13192101283"
WHATSAPP_DISPLAY = "+1 319 210 1283"
SUPPORT_EMAIL = "xapple126@gmail.com"


def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE.exists():
        ENV_PATH.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        ENV_PATH.write_text(
            "\n".join(f"{k}=" for k in ALL_MANAGED_KEYS) + "\n",
            encoding="utf-8",
        )


def load_env() -> Dict[str, str]:
    ensure_env_file()
    values: Dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def save_env(updates: Dict[str, str]) -> None:
    ensure_env_file()
    current = load_env()
    current.update({k: (v or "").strip() for k, v in updates.items()})

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={current[key]}")
                seen.add(key)
                continue
        out.append(line)

    for key in ALL_MANAGED_KEYS:
        if key in updates and key not in seen:
            out.append(f"{key}={current.get(key, '')}")

    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def credentials_complete() -> bool:
    env = load_env()
    required = (
        "POLYMARKET_PK",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_PASSPHRASE",
    )
    return all(env.get(k) for k in required)


def tail_file(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return "(No log yet — start the bot to create this file.)"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(Could not read log: {exc})"
    if not lines:
        return "(Log file is empty.)"
    return "\n".join(lines[-max_lines:])
