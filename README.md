# Polymarket Crypto 5-Minute Trading Bot

> **v5.0.0 — big update:** trade **all supported Polymarket crypto 5-minute UP/DOWN markets** from one bot — not BTC-only anymore.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![v5.0.0](https://img.shields.io/badge/release-v5.0.0-green.svg)](https://github.com/dearolaf/Polymarket-BTC-5Minutes-Trading-Bot/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Automated **UP/DOWN** bot for [Polymarket](https://polymarket.com) **5-minute crypto** markets. Each market uses the same pattern: **`{asset}-updown-5m-{timestamp}`** (e.g. `btc-updown-5m-…`, `eth-updown-5m-…`, `sol-updown-5m-…`).

**Supported assets:** **BTC · ETH · SOL · XRP · DOGE · HYPE · BNB** — run one coin or many at once. Coinbase spot, 5m candles, and CLOB book signals **per asset**. Set `TRADING_ASSET=eth` or `TRADING_ASSETS=btc,eth,sol` in `.env`, or pick markets in the dashboard (**Setup → Trading**). **Practice first**, then go live.

### What's new in v5.0.0
- **All-crypto 5m trading** — one bot for every supported `{asset}-updown-5m-*` market (multi-asset mode)
- **Min-score filter** — weak predictor signals are skipped (not just logged)
- **Stake sync** — `MARKET_BUY_USD` drives martingale base stake
- **Premium edition** — separate build with **advanced predictor logic** (stricter multi-factor filters vs free), analytics, and advanced dashboard; also **15-minute** and **1-hour** up/down bots (not in this free repo)
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

| | **Free (this repo)** | **Premium edition** |
|--|----------------------|---------------------|
| **5-minute** up/down bot | ✓ | ✓ |
| **15-minute** up/down bot | — | ✓ |
| **1-hour** up/down bot | — | ✓ |
| **Predictor** | **Standard** — Coinbase spot + 5m candles + CLOB book ensemble; min-score filter (0.50 default); 5s poll | **Advanced** — settlement-focused scoring with extra quality gates (weak-signal & chop filters, multi-factor agreement, trend/candle alignment, CLOB mid bands); tunable min score & 3s poll; optional score-scaled stakes & stake modes |
| Dashboard, practice/live | ✓ | ✓ |
| Performance analytics | — | ✓ |
| Advanced settings (predictor, timing, martingale) | Basic setup only | ✓ |
| Log downloads | — | ✓ |

**Predictor — free vs Premium:** Both use the same data sources (spot, 5m bars, order book). The **free** build runs the **standard** lightweight predictor with straightforward defaults. **Premium** ships **advanced predictor logic** — more layers before a trade is allowed (skip weak/choppy setups, require agreement across signals, stricter alignment with trend and the live 5m bar, and finer tuning in the dashboard). Fewer trades, higher bar for entries.

**Get Premium:** **[Premium Version](https://polymarkettool-272623624738.us-central1.run.app/premium)** — advanced predictor, 5m + **15m** + **1h** bots, full dashboard, priority support.  
After purchase: use the **Premium edition** repo/build (`BOT_PLAN=premium` in `.env`).

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
| `MARKET_BUY_USD` | `1000` | Base stake (synced to martingale) |
| `PREDICTOR_MIN_SCORE` | `0.50` | Skip trades below this \|score\| |
| `MARTINGALE_MAX_STAKE_USD` | `16` | Martingale cap |
| `BOT_PLAN` | `free` | Premium edition uses `premium` |

See `.env.example` for the full list.

**Logs:** `log.txt` · `logs/orders.log` · `logs/runner.log`

---

## Disclaimer

**Trading involves significant risk.** Educational use only. Test in simulation before live. Only trade what you can afford to lose.

---

## Links

| | |
|---|---|
| Telegram channel | [@Signal5MinOfficial](https://t.me/Signal5MinOfficial) — setup, updates & tips |
| Premium (5m · 15m · 1h) | [polymarkettool/premium](https://polymarkettool-272623624738.us-central1.run.app/premium) |
| API keys | [Generate Keys](https://polymarkettool-272623624738.us-central1.run.app/) |
| Bug reports | [GitHub Issues](https://github.com/dearolaf/Polymarket-BTC-5Minutes-Trading-Bot/issues) |

---

## Contact

Setup, Premium, or bot questions — reach out anytime.

| Channel | |
|---------|---|
| Telegram (updates) | [@Signal5MinOfficial](https://t.me/Signal5MinOfficial) |
| Telegram (support) | [@dearolaf](https://t.me/dearolaf) |
| WhatsApp | [+1 319 210 1283](https://wa.me/13192101283) |
| Email | [xapple126@gmail.com](mailto:xapple126@gmail.com) |

---

**Support the project** — If this repo helps you, a small tip is always appreciated (never required). Thank you.

`0x60ef6388d63016a457e2bf880f34b4d4052d0ef5` · USDT / USDC · ERC20 · BEP20
