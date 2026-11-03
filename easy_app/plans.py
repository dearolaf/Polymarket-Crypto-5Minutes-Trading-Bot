"""Free and Premium plan definitions for the dashboard."""
from __future__ import annotations

from typing import Dict, List

from easy_app.config import SUPPORT_EMAIL, TELEGRAM_URL, WHATSAPP_DISPLAY, WHATSAPP_URL, load_env

PLAN_FREE = "free"
PLAN_PREMIUM = "premium"

FREE_FEATURES: List[str] = [
    "Web dashboard (Easy Mode)",
    "Practice & live trading on crypto 5-minute markets (BTC · ETH · SOL · XRP · DOGE · HYPE · BNB)",
    "Multi-asset mode — trade one coin or several at once",
    "Polymarket API key setup",
    "Basic trade settings (stake size, predictor on/off, auto-redeem)",
    "Standard predictor with min-score filter (0.50 default)",
    "Dashboard overview, basic Performance stats & recent signals",
    "Activity logs (summary, orders, runner) — view in browser",
    "Safe $1 default stake (synced martingale base)",
    "24/7 auto-restart bot runner",
    "Community support (GitHub Issues)",
]

PREMIUM_FEATURES: List[str] = [
    "Everything in the Free plan",
    "Enhanced predictor logic & tuned signal filters",
    "Multi-source ensemble (Coinbase price + 5m candles + CLOB order book)",
    "Custom predictor minimum score & faster poll interval",
    "Martingale recovery strategy (configurable base & max stake)",
    "Precision order timing (submit window & result evaluation delay)",
    "Auto-redeem winning positions with optimized timing",
    "Performance analytics dashboard with live win-rate tracking",
    "Cumulative win charts & UP/DOWN direction breakdown",
    "Win/loss streak tracking & recent result sequence",
    "Advanced strategy settings in the dashboard (no manual .env editing)",
    "Downloadable trade summary & detailed order logs",
    "Priority 1-on-1 support (Telegram, WhatsApp, email)",
    "Optimized Premium presets for higher signal quality",
    "Early access to predictor & strategy improvements",
]

PREMIUM_WHY_BETTER: List[Dict[str, str]] = [
    {
        "title": "Smarter predictor logic",
        "free": "Standard on/off predictor with fixed default thresholds.",
        "premium": "Enhanced ensemble scoring across Coinbase BTC price, 5-minute candles, and Polymarket CLOB order-book imbalance — with tunable minimum score and faster polling for sharper entries.",
    },
    {
        "title": "Better signal filtering",
        "free": "Default filters only — trades on every qualified signal.",
        "premium": "Fine-tune predictor min score, poll speed, and timing windows to skip weak setups and focus on higher-confidence BTC 5-minute signals.",
    },
    {
        "title": "Martingale recovery",
        "free": "Fixed stake per trade — no loss-recovery sizing.",
        "premium": "Configurable martingale base & max stake to recover after losses while staying capped (e.g. $1 → $2 → $4 up to your max).",
    },
    {
        "title": "Precision timing",
        "free": "Default submit & evaluation timing.",
        "premium": "Adjust seconds-before-boundary and post-round evaluation delay to align orders with your market and reduce late entries.",
    },
    {
        "title": "Auto-redeem optimization",
        "free": "Basic auto-redeem with defaults.",
        "premium": "Full control over auto-redeem and verbose logging so winning positions are claimed faster with less manual work.",
    },
    {
        "title": "Performance analytics",
        "free": "Basic Performance page — win rate, UP/DOWN rates, streak, and result sequence (no charts).",
        "premium": "Full Performance page — cumulative win charts, wins vs losses bars, and richer history to measure what is working.",
    },
    {
        "title": "Advanced dashboard controls",
        "free": "Keys + basic stake settings.",
        "premium": "Advanced tab for martingale, timing, predictor score, poll interval, and auto-redeem — no terminal or manual config editing.",
    },
    {
        "title": "Export & audit logs",
        "free": "View logs in the browser only.",
        "premium": "Download `log.txt` and `orders.log` for record-keeping, backtesting review, and sharing with support.",
    },
    {
        "title": "Priority support",
        "free": "GitHub Issues & community help.",
        "premium": "Direct help via Telegram @dearolaf, WhatsApp, and email — faster answers for setup, tuning, and live trading.",
    },
    {
        "title": "Ongoing improvements",
        "free": "Stable public release.",
        "premium": "Early access to predictor tuning updates, strategy presets, and Premium-only optimizations as they ship.",
    },
]

FREE_LIMITATIONS: List[str] = [
    "Fixed predictor score threshold (0.50) — no dashboard tuning",
    "No trend/candle alignment filters (Premium defaults)",
    "No Performance charts or advanced settings tab",
    "Cannot download trade or order logs",
    "No Limit Signal / Limit scalp / Firebase login (Premium)",
    "No priority direct support",
]


def get_plan() -> str:
    env = load_env()
    raw = (env.get("BOT_PLAN") or PLAN_FREE).strip().lower()
    return PLAN_PREMIUM if raw == PLAN_PREMIUM else PLAN_FREE


def is_premium() -> bool:
    return get_plan() == PLAN_PREMIUM


def plan_label() -> str:
    return "Premium" if is_premium() else "Free"


def plan_badge_class() -> str:
    return "premium-badge" if is_premium() else "free-badge"


def render_upgrade_contact_markdown() -> str:
    return f"""
To unlock **Premium**, contact us with any of the options below:

| Channel | Contact |
|---------|---------|
| **Telegram** | [@dearolaf]({TELEGRAM_URL}) |
| **WhatsApp** | [{WHATSAPP_DISPLAY}]({WHATSAPP_URL}) |
| **Email** | [{SUPPORT_EMAIL}](mailto:{SUPPORT_EMAIL}) |

After purchase, you will receive Premium activation instructions for your setup.
"""


def plan_comparison_rows() -> List[Dict[str, str]]:
    return [
        {"Feature": "Web dashboard & Easy Mode", "Free": "✓", "Premium": "✓"},
        {"Feature": "Practice & live BTC 5m trading", "Free": "✓", "Premium": "✓"},
        {"Feature": "API key setup", "Free": "✓", "Premium": "✓"},
        {"Feature": "Basic stake & predictor on/off", "Free": "✓", "Premium": "✓"},
        {"Feature": "Auto-restart 24/7 runner", "Free": "✓", "Premium": "✓"},
        {"Feature": "Predictor logic", "Free": "Standard (defaults)", "Premium": "Enhanced ensemble + tuning"},
        {"Feature": "Coinbase + 5m candle + CLOB book signals", "Free": "Default blend", "Premium": "Optimized Premium presets"},
        {"Feature": "Predictor min score & poll interval", "Free": "Fixed", "Premium": "Fully adjustable"},
        {"Feature": "Signal filtering & skip weak setups", "Free": "—", "Premium": "✓"},
        {"Feature": "Martingale recovery sizing", "Free": "—", "Premium": "✓"},
        {"Feature": "Custom submit & eval timing", "Free": "—", "Premium": "✓"},
        {"Feature": "Auto-redeem control", "Free": "Default", "Premium": "Full control"},
        {"Feature": "Dashboard win rate & streaks", "Free": "Basic", "Premium": "Full analytics"},
        {"Feature": "Performance charts & UP/DOWN stats", "Free": "—", "Premium": "✓"},
        {"Feature": "Advanced settings in dashboard", "Free": "—", "Premium": "✓"},
        {"Feature": "Download trade & order logs", "Free": "—", "Premium": "✓"},
        {"Feature": "Priority 1-on-1 support", "Free": "—", "Premium": "✓"},
        {"Feature": "Early access to predictor updates", "Free": "—", "Premium": "✓"},
    ]
