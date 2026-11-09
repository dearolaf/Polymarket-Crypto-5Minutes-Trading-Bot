# Polymarket Crypto 5-Minute Trading Bot

> **v6.0.0 Free** — **UP/DOWN** bot for [Polymarket](https://polymarket.com) **5-minute crypto** markets — **BTC · ETH · SOL · XRP · DOGE · HYPE · BNB** (`{asset}-updown-5m-*`). Standard predictor, practice/live dashboard, multi-asset mode, safer $1 defaults.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![v6.0.0](https://img.shields.io/badge/release-v6.0.0-green.svg)](#whats-new-in-v600)
[![Free](https://img.shields.io/badge/edition-Free-cyan.svg)](#free-vs-premium)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Automated **UP/DOWN** bot for [Polymarket](https://polymarket.com) **5-minute crypto** markets. Each market uses the same pattern: **`{asset}-updown-5m-{timestamp}`** (e.g. `btc-updown-5m-…`, `eth-updown-5m-…`, `sol-updown-5m-…`).

**Supported assets:** **BTC · ETH · SOL · XRP · DOGE · HYPE · BNB** — run one coin or many at once. Coinbase spot, 5m candles, and CLOB book signals **per asset**. Set `TRADING_ASSET=eth` or `TRADING_ASSETS=btc,eth,sol` in `.env`, or pick markets in the dashboard (**Setup → Trading**). **Practice first**, then go live.

This is the **Free** public build (`BOT_PLAN=free`). Looking for Premium (advanced predictor, 15m · 1h bots, Limit Signal / Limit scalp, charts)? See **[Premium Version](https://polymarkettool-272623624738.us-central1.run.app/premium)**.

### What's new in v6.0.0

**Defaults & safety**
- Base stake **$1** and martingale max **$16** in `.env.example` (replaces the old $1000 / $16000 example)
- Stake input clamped in Setup so huge legacy `.env` values cannot break the form

**Dashboard**
- Crypto branding, `v6.0.0` chip, active market pills (BTC · ETH · SOL · …)
- **Basic Performance (free)** — win rate, UP/DOWN rates, streak, and result sequence (charts stay Premium)
- **Logs refresh** — file size / last-updated meta, clearer empty states, **runner.log** tab
- **Setup → Trading** — auto-redeem toggle alongside stake, markets, and predictor on/off

**Still Free vs Premium**
- Advanced predictor gates, Limit Signal / Limit scalp, Performance charts, log downloads, Firebase login, and neon console remain in the [Premium edition](https://polymarkettool-272623624738.us-central1.run.app/premium)

---

## Easy Mode (no coding)

| Step | Windows | Mac / Ubuntu |
|------|---------|--------------|
| Install | `Install.bat` | `./install.sh` |
| Dashboard | `Start.bat` | `./start.sh` |
| Run | **Setup** → keys & markets → **Control** → **Start practice** | same |

- **Practice** = simulation · **Test mode** = faster scoring · **Live** = real USDC
- Keys: **[Generate Keys](https://polymarkettool-272623624738.us-central1.run.app/)**
- Start with **$1** stake and one market; add assets in **Setup → Trading** when ready

### Dashboard pages

| Page | Free |
|------|------|
| **Dashboard** | Win rate, streak, pending, recent signals, active markets |
| **Performance** | Metrics + result sequence (charts are Premium) |
| **Control** | Start/stop practice, test mode, live |
| **Setup** | API keys · markets · stake · predictor · auto-redeem |
| **Logs** | View `log.txt` · `orders.log` · `runner.log` |
| **Plans** | Free vs Premium comparison |
| **Help** | Quick start & support links |

---

## Free vs Premium

| | **Free (this repo)** | **Premium edition** |
|--|----------------------|---------------------|
| **5-minute** up/down bot | ✓ | ✓ |
| **15-minute** up/down bot | — | ✓ |
| **1-hour** up/down bot | — | ✓ |
| **Predictor** | **Standard** — Coinbase spot + 5m candles + CLOB book; min-score filter (0.50); 5s poll | **Advanced** — settlement-focused scoring with extra quality gates (weak/chop/agreement, trend & candle align, CLOB mid bands); tunable min score & 3s poll |
| **Limit Signal** (manual GTC) | — | ✓ |
| **Limit scalp** (cheap-side auto) | — | ✓ |
| Dashboard, practice/live | ✓ | ✓ |
| Performance analytics | Basic metrics + result sequence | Full charts + history |
| Advanced settings (predictor, timing, martingale) | Stake, markets, auto-redeem | Full advanced tab |
| Log viewer (summary / orders / runner) | ✓ | ✓ |
| Log downloads | — | ✓ |
| Priority support | — | ✓ |

**Predictor — free vs Premium:** Both use the same data sources (spot, 5m bars, order book). The **free** build runs the **standard** lightweight predictor with straightforward defaults. **Premium** ships **advanced predictor logic** — more layers before a trade is allowed (skip weak/choppy setups, require agreement across signals, stricter alignment with trend and the live 5m bar, and finer tuning in the dashboard). Fewer trades, higher bar for entries.

**Get Premium:** **[Premium Version](https://polymarkettool-272623624738.us-central1.run.app/premium)** — advanced predictor, 5m + **15m** + **1h** bots, Limit Signal / Limit scalp, full dashboard, priority support.  
After purchase: use the **Premium edition** repo/build (`BOT_PLAN=premium` in `.env`).

---

## Terminal

```bash
git clone https://github.com/dearolaf/Polymarket-Crypto-5Minutes-Trading-Bot.git
cd Polymarket-Crypto-5Minutes-Trading-Bot
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
| `MARKET_BUY_USD` | `1.0` | Base stake (synced to martingale) |
| `MARTINGALE_MAX_STAKE_USD` | `16` | Martingale cap |
| `PREDICTOR_MIN_SCORE` | `0.50` | Skip trades below this \|score\| |
| `POLYMARKET_AUTO_REDEEM` | `1` | Auto-claim winning positions |
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
| Bug reports | [GitHub Issues](https://github.com/dearolaf/Polymarket-Crypto-5Minutes-Trading-Bot/issues) |

---

## Contact

Setup, Premium, or bot questions — reach out anytime.

| Channel | |
|---------|---|
| Telegram (updates) | [@Signal5MinOfficial](https://t.me/Signal5MinOfficial) |
| Telegram (support) | [@dearolaf](https://t.me/dearolaf) |
| Email | [xapple126@gmail.com](mailto:xapple126@gmail.com) |
