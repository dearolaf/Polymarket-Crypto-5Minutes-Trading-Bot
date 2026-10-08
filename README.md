# Polymarket Crypto 5-Minute Trading Bot

> **v5.0.0** — Automated **UP/DOWN** bot for [Polymarket](https://polymarket.com) 5-minute crypto markets — **BTC, ETH, SOL, XRP, DOGE, HYPE, BNB** (`{asset}-updown-5m-*`).

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![v5.0.0](https://img.shields.io/badge/release-v5.0.0-green.svg)](https://github.com/dearolaf/Polymarket-BTC-5Minutes-Trading-Bot/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Uses Coinbase spot per asset, 5m candles, and Polymarket CLOB book signals. Set `TRADING_ASSET=eth` or `TRADING_ASSETS=btc,eth,sol` in `.env` (or pick markets in the dashboard). **Practice first**, then go live.

### What's new in v5.0.0
- **Crypto multi-asset 5m** — btc, eth, sol, xrp, doge, hype, bnb up/down slugs in one bot
- **Min-score filter** — weak predictor signals are skipped (not just logged)
- **Stake sync** — `MARKET_BUY_USD` drives martingale base stake
- **Premium edition** — separate build with enhanced predictor, analytics, and advanced dashboard; also supports **15-minute** and **1-hour** up/down trading bots (not included in this free repo)
- **Simulation fix** — practice mode logs to `log.txt` only (no random paper trades)
- **Test mode** in dashboard + improved auto-restart logging (`logs/runner.log`)

---

## Easy Mode (no coding)

| Step | Windows | Mac / Ubuntu |
|------|---------|--------------|
| Install | `Install.bat` | `./install.sh` |
| Dashboard | `Start.bat` | `./start.sh` |
| Run | **Setup** → keys → **Control** → **Start practice** | same |

- **Practice** = simulation · **Test mode** = faster scoring · **Live** = real USDC  
- Keys: **[Generate Keys](https://polymarkettool-272623624738.us-central1.run.app/)**

---

## Free vs Premium

| | **Free (this repo)** | **Premium** |
|--|----------------------|-------------|
| **5-minute** up/down bot | ✓ | ✓ |
| **15-minute** up/down bot | — | ✓ |
| **1-hour** up/down bot | — | ✓ |
| Dashboard, practice/live | ✓ | ✓ |
| Min-score signal filter | ✓ (0.50 default) | ✓ (tunable + extra filters) |
| Performance analytics | — | ✓ |
| Advanced settings | — | ✓ |
| Log downloads | — | ✓ |

**Get Premium:** **[Premium Version](https://polymarkettool-272623624738.us-central1.run.app/premium)** — 5m, **15m**, and **1h** crypto up/down bots, full dashboard, and priority support.  
**Support:** [@dearolaf](https://t.me/dearolaf) · [WhatsApp +1 319 210 1283](https://wa.me/13192101283) · [xapple126@gmail.com](mailto:xapple126@gmail.com)  
After purchase: use the Premium edition repo/build (`BOT_PLAN=premium` in `.env`).

---

## Terminal

```bash
git clone https://github.com/dearolaf/Polymarket-BTC-5Minutes-Trading-Bot.git
cd Polymarket-BTC-5Minutes-Trading-Bot
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt && cp .env.example .env
```

```bash
python 5m_bot_runner.py              # practice
python 5m_bot_runner.py --test-mode  # faster eval
python 5m_bot_runner.py --live       # real orders
pytest tests/                        # run release tests
```

---

## Key `.env` settings

| Variable | Default | Notes |
|----------|---------|-------|
| `TRADING_ASSET` / `TRADING_ASSETS` | `btc` | Single asset or comma-separated list |
| `MARKET_BUY_USD` | `10.0` | Base stake (synced to martingale) |
| `PREDICTOR_MIN_SCORE` | `0.50` | Skip trades below this \|score\| |
| `MARTINGALE_MAX_STAKE_USD` | `16` | Martingale cap |
| `BOT_PLAN` | `free` | Premium edition uses `premium` |

See `.env.example` for the full list.

**Logs:** `log.txt` · `logs/orders.log` · `logs/runner.log`

---

## Disclaimer

**Trading involves significant risk.** Educational use only. Test in simulation before live. Only trade what you can afford to lose.

---

## Community

- **Premium (5m + 15m + 1h):** [polymarkettool/premium](https://polymarkettool-272623624738.us-central1.run.app/premium)
- **Keys:** [Generate Keys](https://polymarkettool-272623624738.us-central1.run.app/)
- **Support:** [Telegram @dearolaf](https://t.me/dearolaf) · [WhatsApp +1 319 210 1283](https://wa.me/13192101283) · [xapple126@gmail.com](mailto:xapple126@gmail.com)
- **Issues:** [GitHub](https://github.com/dearolaf/Polymarket-BTC-5Minutes-Trading-Bot/issues)

If this project helps you, a small tip is always appreciated (never required) — thank you for your support.  
**USDT / USDC (ERC20, BEP20):** `0x60ef6388d63016a457e2bf880f34b4d4052d0ef5`
