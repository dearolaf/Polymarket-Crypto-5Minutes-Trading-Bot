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

# Default stake when .env is missing or has an outdated huge value.
DEFAULT_STAKE_USD = "1.0"
APP_VERSION = "6.0.0"
GITHUB_REPO_URL = "https://github.com/dearolaf/Polymarket-Crypto-5Minutes-Trading-Bot"
GENERATE_KEYS_URL = "https://polymarkettool-272623624738.us-central1.run.app/"
PREMIUM_URL = "https://polymarkettool-272623624738.us-central1.run.app/premium"
TELEGRAM_URL = "https://t.me/yesolaf"
TELEGRAM_CHANNEL_URL = "https://t.me/Signal5MinOfficial"
WHATSAPP_URL = "https://wa.me/13192101283"
WHATSAPP_DISPLAY = "+1 319 210 1283"
SUPPORT_EMAIL = "xapple126@gmail.com"


def clamp_stake_usd(raw: str | float | None, *, lo: float = 1.0, hi: float = 100.0) -> float:
    try:
        val = float(raw if raw is not None and str(raw).strip() != "" else DEFAULT_STAKE_USD)
    except (TypeError, ValueError):
        val = float(DEFAULT_STAKE_USD)
    return max(lo, min(hi, val))


def log_file_meta(path: Path) -> str:
    if not path.exists():
        return "File not created yet"
    try:
        st = path.stat()
        size = st.st_size
        if size < 1024:
            size_s = f"{size} B"
        elif size < 1024 * 1024:
            size_s = f"{size / 1024:.1f} KB"
        else:
            size_s = f"{size / (1024 * 1024):.1f} MB"
        from datetime import datetime

        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return f"{size_s} · updated {mtime}"
    except OSError as exc:
        return f"Unavailable ({exc})"


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
