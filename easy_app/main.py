"""
Polymarket Crypto 5-Min Bot — Free web dashboard.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import streamlit as st

st.set_option("client.toolbarMode", "viewer")

from easy_app.config import (
    ADVANCED_DEFAULTS,
    APP_VERSION,
    ENV_PATH,
    GENERATE_KEYS_URL,
    GITHUB_REPO_URL,
    PREMIUM_URL,
    PROJECT_ROOT,
    SUPPORT_EMAIL,
    TELEGRAM_CHANNEL_URL,
    TELEGRAM_URL,
    WHATSAPP_DISPLAY,
    WHATSAPP_URL,
    clamp_stake_usd,
    credentials_complete,
    ensure_env_file,
    load_env,
    log_file_meta,
    save_env,
    tail_file,
)
from easy_app.plans import (
    FREE_FEATURES,
    FREE_LIMITATIONS,
    PREMIUM_FEATURES,
    PREMIUM_WHY_BETTER,
    is_premium,
    plan_badge_class,
    plan_comparison_rows,
    plan_label,
    render_upgrade_contact_markdown,
)
from easy_app.process import get_status, start_bot, stop_bot
from easy_app.stats import (
    compute_stats,
    format_uptime,
    load_trades,
    trades_to_table,
)
from easy_app.theme import inject_premium_theme

st.set_page_config(
    page_title="Polymarket Crypto 5-Min Bot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_premium_theme()

PAGES = (
    "Dashboard",
    "Performance",
    "Control",
    "Setup",
    "Logs",
    "Plans",
    "Help",
)


def refresh() -> None:
    st.rerun()


def render_upgrade_cta(title: str = "Upgrade to Premium") -> None:
    st.markdown(
        f"""
        <div class="upgrade-box">
            <h3>{title}</h3>
            <p>Advanced predictor gates, Limit Signal / Limit scalp, Performance charts, advanced settings, and log downloads are Premium features.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.link_button("Get Premium", PREMIUM_URL, use_container_width=True, type="primary")
    st.markdown(render_upgrade_contact_markdown())
    c1, c2, c3 = st.columns(3)
    with c1:
        st.link_button("Telegram @yesolaf", TELEGRAM_URL, use_container_width=True)
    with c2:
        st.link_button(f"WhatsApp {WHATSAPP_DISPLAY}", WHATSAPP_URL, use_container_width=True)
    with c3:
        st.link_button(f"Email {SUPPORT_EMAIL}", f"mailto:{SUPPORT_EMAIL}", use_container_width=True)


def _active_assets_from_env(env: dict) -> list[str]:
    from core.updown_assets import SUPPORTED_ASSET_IDS

    raw = (env.get("TRADING_ASSETS") or "").strip()
    if raw:
        out: list[str] = []
        for part in raw.split(","):
            key = part.strip().lower()
            if key in SUPPORTED_ASSET_IDS and key not in out:
                out.append(key)
        if out:
            return out
    single = (env.get("TRADING_ASSET") or "btc").strip().lower()
    return [single] if single in SUPPORTED_ASSET_IDS else ["btc"]


def render_asset_pills(assets: list[str]) -> None:
    from core.updown_assets import ASSET_REGISTRY

    pills = " ".join(
        f'<span class="asset-pill">{ASSET_REGISTRY[a].label if a in ASSET_REGISTRY else a.upper()}</span>'
        for a in assets
    )
    st.markdown(pills, unsafe_allow_html=True)


def render_status_sidebar() -> str:
    status = get_status()
    badge = plan_badge_class()
    label = plan_label()
    env = load_env()
    assets = _active_assets_from_env(env)

    with st.sidebar:
        st.markdown(
            f'<span class="{badge}">{label} Plan</span>'
            f'<span class="version-chip">v{APP_VERSION}</span>',
            unsafe_allow_html=True,
        )
        st.title("Crypto 5-Min Bot")
        st.caption("Multi-asset UP/DOWN · no coding required")
        render_asset_pills(assets)
        st.divider()

        if status.get("running"):
            mode = status.get("mode_label", "Running")
            live = status.get("live")
            pill_class = "live" if live else "running"
            st.markdown(
                f'<span class="status-pill {pill_class}">● {mode}</span>',
                unsafe_allow_html=True,
            )
            st.caption(f"PID {status.get('pid')} · Uptime {format_uptime(status.get('started_at'))}")
        else:
            st.markdown(
                '<span class="status-pill stopped">● Stopped</span>',
                unsafe_allow_html=True,
            )

        if credentials_complete():
            st.success("API keys configured")
        else:
            st.warning("Complete setup first")

        st.divider()
        page = st.radio("Navigate", PAGES, label_visibility="collapsed")

        if st.button("↻ Refresh", use_container_width=True):
            refresh()

        st.caption(f"Updated {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")

    return page


def render_hero(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="premium-hero">
            <h1>{title}</h1>
            <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dashboard() -> None:
    status = get_status()
    stats = compute_stats()
    env = load_env()
    assets = _active_assets_from_env(env)

    render_hero(
        "Trading Dashboard",
        "Live overview of bot status, win rate, and recent crypto 5-minute UP/DOWN signals.",
    )
    render_asset_pills(assets)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Win rate", f"{stats.win_rate}%")
    c2.metric("Wins", stats.wins)
    c3.metric("Losses", stats.losses)
    c4.metric("Pending", stats.pending)
    streak_label = "—"
    if stats.streak_type == "win":
        streak_label = f"{stats.current_streak}W"
    elif stats.streak_type == "loss":
        streak_label = f"{stats.current_streak}L"
    c5.metric("Streak", streak_label)

    r1, r2, r3 = st.columns(3)
    with r1:
        if status.get("running"):
            st.success(f"Bot running — {status.get('mode_label')}")
        else:
            st.info("Bot is stopped — open **Control** to start practice")
    with r2:
        base = clamp_stake_usd(env.get("MARTINGALE_BASE_USD") or env.get("MARKET_BUY_USD"))
        mg_max = env.get("MARTINGALE_MAX_STAKE_USD", "16")
        predictor = "On" if env.get("USE_LIGHTWEIGHT_PREDICTOR", "1") == "1" else "Off"
        redeem = "On" if env.get("POLYMARKET_AUTO_REDEEM", "1") == "1" else "Off"
        st.caption(
            f"Base stake **${base:g}** (max **${mg_max}**) · Predictor **{predictor}** · Auto-redeem **{redeem}**"
        )
    with r3:
        if status.get("running"):
            st.caption(f"Uptime: **{format_uptime(status.get('started_at'))}**")
        else:
            st.caption(f"Markets: **{', '.join(a.upper() for a in assets)}**")

    st.subheader("Recent signals")
    rows = trades_to_table(stats.trades, limit=20)
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet. Start practice mode to begin logging signals.")

    if stats.recent_results:
        wins = stats.recent_results.count("W")
        losses = stats.recent_results.count("L")
        st.caption(f"Last {len(stats.recent_results)} results: {wins} wins · {losses} losses")


def render_performance() -> None:
    stats = compute_stats()
    premium = is_premium()

    render_hero(
        "Performance Analytics",
        "Win rate and direction breakdown from log.txt."
        + (" Charts unlock on Premium." if not premium else " Charts, streaks, and full history."),
    )

    if stats.total_scored == 0:
        st.info("No scored trades yet. Run the bot in practice mode to build history.")
        if not premium:
            st.caption("Charts and downloadable history remain Premium — basic stats are free.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total scored", stats.total_scored)
    m2.metric("Win rate", f"{stats.win_rate}%")
    up_wr = round(100 * stats.up_wins / stats.up_orders, 1) if stats.up_orders else 0
    down_wr = round(100 * stats.down_wins / stats.down_orders, 1) if stats.down_orders else 0
    m3.metric("UP win rate", f"{up_wr}%", help=f"{stats.up_wins}/{stats.up_orders} UP orders won")
    m4.metric("DOWN win rate", f"{down_wr}%", help=f"{stats.down_wins}/{stats.down_orders} DOWN orders won")

    streak_label = "—"
    if stats.streak_type == "win":
        streak_label = f"{stats.current_streak}W"
    elif stats.streak_type == "loss":
        streak_label = f"{stats.current_streak}L"
    st.caption(f"Current streak: **{streak_label}** · Pending: **{stats.pending}**")

    st.subheader("Recent result sequence")
    if stats.recent_results:
        seq_html = " ".join(
            f'<span style="display:inline-block;padding:4px 8px;margin:2px;border-radius:6px;'
            f'background:{"rgba(52,211,153,0.2)" if r == "W" else "rgba(248,113,113,0.2)"};'
            f'color:{"#34d399" if r == "W" else "#f87171"};font-weight:600;">{r}</span>'
            for r in stats.recent_results[-30:]
        )
        st.markdown(seq_html, unsafe_allow_html=True)
    else:
        st.caption("No results to display.")

    if premium:
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Cumulative wins")
            if stats.chart_cumulative_wins:
                chart_df = pd.DataFrame(
                    {"Cumulative wins": stats.chart_cumulative_wins},
                    index=stats.chart_labels,
                )
                st.line_chart(chart_df)
            else:
                st.caption("Not enough data for chart.")
        with col_b:
            st.subheader("Wins vs losses")
            outcome_df = pd.DataFrame(
                {"Count": [stats.wins, stats.losses]},
                index=["Wins", "Losses"],
            )
            st.bar_chart(outcome_df)
    else:
        st.divider()
        st.caption("**Premium:** cumulative win chart, wins vs losses bar chart, and log downloads.")
        render_upgrade_cta("Unlock Performance charts")


def render_control() -> None:
    status = get_status()

    render_hero(
        "Bot Control",
        "Start practice mode first. Switch to live only when you are confident.",
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        if st.button("▶ Start practice", use_container_width=True, disabled=status.get("running")):
            if not credentials_complete():
                st.error("Go to **Setup** and save your Polymarket credentials first.")
            else:
                try:
                    start_bot(live=False)
                    st.success("Practice mode started.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))

    with c2:
        if st.button("▶ Test mode", use_container_width=True, disabled=status.get("running")):
            if not credentials_complete():
                st.error("Go to **Setup** and save your Polymarket credentials first.")
            else:
                try:
                    start_bot(live=False, test_mode=True)
                    st.success("Test mode started (faster scoring).")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))

    with c3:
        if st.button("▶ Start live", use_container_width=True, disabled=status.get("running")):
            if not credentials_complete():
                st.error("Go to **Setup** and save your Polymarket credentials first.")
            else:
                st.session_state["confirm_live"] = True

    with c4:
        if st.button("■ Stop bot", use_container_width=True, disabled=not status.get("running")):
            stop_bot()
            st.success("Bot stopped.")
            refresh()

    if st.session_state.get("confirm_live"):
        st.error("**Live trading uses real USDC.** Only continue if your wallet is funded and keys are correct.")
        y, n = st.columns(2)
        with y:
            if st.button("Yes — start live trading", type="primary"):
                try:
                    start_bot(live=True)
                    st.session_state.pop("confirm_live", None)
                    st.success("Live trading started.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))
        with n:
            if st.button("Cancel"):
                st.session_state.pop("confirm_live", None)
                refresh()

    st.divider()
    st.subheader("Session info")
    if status.get("running"):
        st.write(
            {
                "Mode": status.get("mode_label"),
                "Process ID": status.get("pid"),
                "Started": status.get("started_at", "—"),
                "Uptime": format_uptime(status.get("started_at")),
                "Restarts": status.get("restart_count", 0),
                "Last exit code": status.get("last_exit_code", "—"),
            }
        )
    elif status.get("last_exit_code") is not None:
        st.warning(f"Bot stopped (last exit code: {status.get('last_exit_code')}). Check `logs/runner.log`.")
    else:
        st.caption("No active session.")


def render_setup() -> None:
    render_hero(
        "Configuration",
        "API keys, trade size, and advanced strategy settings.",
    )

    ensure_env_file()
    env = load_env()

    tab_keys, tab_basic, tab_advanced = st.tabs(["API keys", "Trading", "Advanced"])

    with tab_keys:
        st.link_button("Generate Keys", GENERATE_KEYS_URL, use_container_width=False)
        st.info(
            "**Email / Google login:** use Polymarket **Reveal Private Key** "
            "([Magic export](https://reveal.magic.link/polymarket)). "
            "**MetaMask:** export your wallet private key from the wallet app."
        )

        with st.form("keys_form"):
            pk = st.text_input("Wallet private key", value=env.get("POLYMARKET_PK", ""), type="password")
            api_key = st.text_input("API key", value=env.get("POLYMARKET_API_KEY", ""), type="password")
            api_secret = st.text_input("API secret", value=env.get("POLYMARKET_API_SECRET", ""), type="password")
            passphrase = st.text_input("Passphrase", value=env.get("POLYMARKET_PASSPHRASE", ""), type="password")
            funder = st.text_input("Proxy wallet (optional)", value=env.get("POLYMARKET_FUNDER", ""))
            save_keys = st.form_submit_button("Save keys", type="primary", use_container_width=True)

        if save_keys:
            save_env(
                {
                    "POLYMARKET_PK": pk.strip(),
                    "POLYMARKET_API_KEY": api_key.strip(),
                    "POLYMARKET_API_SECRET": api_secret.strip(),
                    "POLYMARKET_PASSPHRASE": passphrase.strip(),
                    "POLYMARKET_FUNDER": funder.strip(),
                }
            )
            st.success(f"Keys saved to `{ENV_PATH.name}`.")
            refresh()

    with tab_basic:
        from core.updown_assets import ASSET_REGISTRY, SUPPORTED_ASSET_IDS

        def _assets_from_env(values: dict) -> list[str]:
            raw = (values.get("TRADING_ASSETS") or "").strip()
            if raw:
                out: list[str] = []
                for part in raw.split(","):
                    key = part.strip().lower()
                    if key in SUPPORTED_ASSET_IDS and key not in out:
                        out.append(key)
                if out:
                    return out
            single = (values.get("TRADING_ASSET") or "btc").strip().lower()
            return [single] if single in SUPPORTED_ASSET_IDS else ["btc"]

        asset_labels = {aid: ASSET_REGISTRY[aid].label for aid in SUPPORTED_ASSET_IDS}
        label_to_id = {v: k for k, v in asset_labels.items()}
        current_assets = _assets_from_env(env)
        default_labels = [asset_labels[a] for a in current_assets if a in asset_labels]

        with st.form("basic_form"):
            selected_labels = st.multiselect(
                "5-minute markets (up/down)",
                options=[asset_labels[a] for a in SUPPORTED_ASSET_IDS],
                default=default_labels,
                help="One asset = single-market mode. Multiple assets trade in parallel (shared schedule).",
            )
            trade_usd = st.number_input(
                "Base stake per trade (USD)",
                min_value=1.0,
                max_value=100.0,
                value=clamp_stake_usd(env.get("MARTINGALE_BASE_USD") or env.get("MARKET_BUY_USD")),
                step=1.0,
                help="Default $1. Synced to MARKET_BUY_USD and MARTINGALE_BASE_USD. Start small in practice.",
            )
            predictor_on = st.checkbox(
                "Use smart predictor (recommended)",
                value=(env.get("USE_LIGHTWEIGHT_PREDICTOR", "1") == "1"),
            )
            auto_redeem = st.checkbox(
                "Auto-redeem winning positions",
                value=(env.get("POLYMARKET_AUTO_REDEEM", "1") == "1"),
                help="Claim winning shares automatically after settlement.",
            )
            save_basic = st.form_submit_button("Save trading settings", type="primary", use_container_width=True)

        if save_basic:
            picked = [label_to_id[lbl] for lbl in selected_labels if lbl in label_to_id]
            if not picked:
                picked = ["btc"]
            stake = clamp_stake_usd(trade_usd)
            asset_updates: dict[str, str] = {
                "MARKET_BUY_USD": f"{stake:.2f}",
                "MARTINGALE_BASE_USD": f"{stake:.0f}",
                "USE_LIGHTWEIGHT_PREDICTOR": "1" if predictor_on else "0",
                "POLYMARKET_AUTO_REDEEM": "1" if auto_redeem else "0",
            }
            if len(picked) == 1:
                asset_updates["TRADING_ASSET"] = picked[0]
                asset_updates["TRADING_ASSETS"] = ""
            else:
                asset_updates["TRADING_ASSET"] = ""
                asset_updates["TRADING_ASSETS"] = ",".join(picked)
            save_env(asset_updates)
            st.success("Trading settings saved. Restart the bot to apply changes.")
            refresh()

    with tab_advanced:
        if not is_premium():
            st.info("Advanced strategy settings are available on the **Premium** plan.")
            render_upgrade_cta("Unlock Advanced Settings")
        else:
            st.caption("These map directly to `.env` variables used by the bot.")

            with st.form("advanced_form"):
                submit_before = st.number_input(
                    "Submit seconds before boundary",
                    min_value=10,
                    max_value=300,
                    value=int(env.get("SUBMIT_SECONDS_BEFORE_BOUNDARY") or ADVANCED_DEFAULTS["SUBMIT_SECONDS_BEFORE_BOUNDARY"]),
                    help="How early to place orders before each 5-minute window ends.",
                )
                eval_post = st.number_input(
                    "Result evaluation delay (sec)",
                    min_value=30,
                    max_value=180,
                    value=int(env.get("PREDICTOR_EVAL_POST_END_SEC") or ADVANCED_DEFAULTS["PREDICTOR_EVAL_POST_END_SEC"]),
                )
                mg_base = st.number_input(
                    "Martingale base (USD)",
                    min_value=1.0,
                    max_value=50.0,
                    value=float(env.get("MARTINGALE_BASE_USD") or ADVANCED_DEFAULTS["MARTINGALE_BASE_USD"]),
                    step=1.0,
                )
                mg_max = st.number_input(
                    "Martingale max stake (USD)",
                    min_value=1.0,
                    max_value=100.0,
                    value=float(env.get("MARTINGALE_MAX_STAKE_USD") or ADVANCED_DEFAULTS["MARTINGALE_MAX_STAKE_USD"]),
                    step=1.0,
                )
                min_score = st.slider(
                    "Predictor minimum score",
                    min_value=0.0,
                    max_value=5.0,
                    value=float(env.get("PREDICTOR_MIN_SCORE") or ADVANCED_DEFAULTS["PREDICTOR_MIN_SCORE"]),
                    step=0.05,
                )
                poll_sec = st.number_input(
                    "Predictor poll interval (sec)",
                    min_value=1,
                    max_value=30,
                    value=int(env.get("PREDICTOR_POLL_SECONDS") or ADVANCED_DEFAULTS["PREDICTOR_POLL_SECONDS"]),
                )
                auto_redeem = st.checkbox(
                    "Auto-redeem winning positions",
                    value=(env.get("POLYMARKET_AUTO_REDEEM", "1") == "1"),
                )
                verbose = st.checkbox(
                    "Verbose bot logging",
                    value=(env.get("BOT_VERBOSE", "0") == "1"),
                )
                save_advanced = st.form_submit_button("Save advanced settings", type="primary", use_container_width=True)

            if save_advanced:
                save_env(
                    {
                        "SUBMIT_SECONDS_BEFORE_BOUNDARY": str(int(submit_before)),
                        "PREDICTOR_EVAL_POST_END_SEC": str(int(eval_post)),
                        "MARTINGALE_BASE_USD": f"{mg_base:.0f}",
                        "MARTINGALE_MAX_STAKE_USD": f"{mg_max:.0f}",
                        "PREDICTOR_MIN_SCORE": f"{min_score:.2f}",
                        "PREDICTOR_POLL_SECONDS": str(int(poll_sec)),
                        "POLYMARKET_AUTO_REDEEM": "1" if auto_redeem else "0",
                        "BOT_VERBOSE": "1" if verbose else "0",
                    }
                )
                st.success("Advanced settings saved. Restart the bot to apply changes.")
                refresh()

    st.caption("Keys and settings stay on your machine in `.env` only — never share this file.")


def _render_log_panel(path: Path, *, max_lines: int = 150, download_name: str | None = None) -> None:
    meta = log_file_meta(path)
    st.markdown(f'<p class="log-meta">{path.name} · {meta}</p>', unsafe_allow_html=True)
    content = tail_file(path, max_lines=max_lines)
    emptyish = (
        content.startswith("(No log")
        or content.startswith("(Log file is empty")
        or content.startswith("(Could not read")
    )
    if emptyish:
        st.markdown(f'<div class="log-empty">{content}</div>', unsafe_allow_html=True)
    else:
        st.code(content, language=None)
    if is_premium() and path.exists() and download_name:
        st.download_button(
            f"Download {download_name}",
            path.read_text(encoding="utf-8", errors="replace"),
            file_name=download_name,
            mime="text/plain",
        )
    elif not is_premium():
        st.caption("Downloads are **Premium**. View logs here for free — see **Plans** to upgrade.")


def render_logs() -> None:
    render_hero("Activity Logs", "Trade summary, order detail, and runner auto-restart log.")

    log_path = PROJECT_ROOT / "log.txt"
    orders_path = PROJECT_ROOT / "logs" / "orders.log"
    runner_path = PROJECT_ROOT / "logs" / "runner.log"

    tab1, tab2, tab3 = st.tabs(
        ["Trade summary (log.txt)", "Order detail (orders.log)", "Runner (runner.log)"]
    )

    with tab1:
        _render_log_panel(log_path, max_lines=150, download_name="log.txt")
    with tab2:
        _render_log_panel(orders_path, max_lines=150, download_name="orders.log")
    with tab3:
        _render_log_panel(runner_path, max_lines=120, download_name="runner.log")


def render_plans() -> None:
    render_hero(
        "Free & Premium Plans",
        f"You are on the **{plan_label()}** plan. Premium unlocks better predictor logic, analytics, and advanced trading tools.",
    )

    if not is_premium():
        st.markdown(
            """
            **Why upgrade?** Premium is built for serious traders who want higher-quality BTC 5-minute
            signals, configurable recovery sizing, full performance tracking, and direct support —
            not just a basic bot runner.
            """
        )

    st.subheader("Why Premium is better")
    for item in PREMIUM_WHY_BETTER:
        with st.expander(item["title"], expanded=not is_premium() and item["title"] == "Smarter predictor logic"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Free**")
                st.caption(item["free"])
            with c2:
                st.markdown("**Premium**")
                st.success(item["premium"])

    st.subheader("Full plan comparison")
    st.dataframe(pd.DataFrame(plan_comparison_rows()), use_container_width=True, hide_index=True)

    col_free, col_premium = st.columns(2)

    with col_free:
        st.markdown("### Free")
        for item in FREE_FEATURES:
            st.markdown(f"- {item}")
        st.markdown("**Free limitations**")
        for item in FREE_LIMITATIONS:
            st.markdown(f"- {item}")
        if not is_premium():
            st.success("Your current plan")

    with col_premium:
        st.markdown("### Premium")
        for item in PREMIUM_FEATURES:
            st.markdown(f"- {item}")
        if is_premium():
            st.success("Your current plan")
        else:
            render_upgrade_cta("Get Premium — Better Predictor & Analytics")


def render_help() -> None:
    render_hero("Help & Support", "Quick start guide and community links.")

    st.markdown(
        f"""
        ### Quick start

        1. **Install once** — `Install.bat` (Windows) or `./install.sh` (Mac/Ubuntu)
        2. **Open dashboard** — `Start.bat` or `./start.sh`
        3. **Setup → Trading** — pick markets (BTC / ETH / SOL / …), keep stake at **$1**, enable predictor
        4. **Setup → API keys** — paste keys from **[Generate Keys]({GENERATE_KEYS_URL})** → Save
        5. **Control** → **Start practice** (simulation, no real money)
        6. **Performance** → basic win rate & result sequence (charts are Premium)
        7. When ready → **Start live** (real USDC on Polymarket)

        ### Plans

        - **Free (v{APP_VERSION})** — multi-asset 5m bot, standard predictor, basic Performance, log viewer, $1 default stake
        - **Premium** — advanced predictor gates, Limit Signal / Limit scalp, charts, advanced settings, log downloads, Firebase login, priority support

        **Get Premium:** [Premium Version]({PREMIUM_URL})

        Contact:

        - Telegram updates: [@Signal5MinOfficial]({TELEGRAM_CHANNEL_URL})
        - Telegram support: [@yesolaf]({TELEGRAM_URL})
        - WhatsApp: [{WHATSAPP_DISPLAY}]({WHATSAPP_URL})
        - Email: [{SUPPORT_EMAIL}](mailto:{SUPPORT_EMAIL})

        ### Free vs Premium pages

        | Page | Free | Premium |
        |------|------|---------|
        | **Dashboard** | Status, win rate, streak, signals | Same + richer console |
        | **Performance** | Metrics + result sequence | + charts |
        | **Setup → Advanced** | — | Martingale, timing, min score |
        | **Logs** | View summary / orders / runner | + downloads |

        ### Terminal (developers)

        ```bash
        python 5m_bot_runner.py           # practice
        python 5m_bot_runner.py --live    # live
        ```

        ### Support

        - [GitHub Issues]({GITHUB_REPO_URL}/issues)
        - Telegram: [@yesolaf]({TELEGRAM_URL})

        **Risk warning:** Trading involves loss. Always start in practice mode.
        """
    )


page = render_status_sidebar()

if page == "Dashboard":
    render_dashboard()
elif page == "Performance":
    render_performance()
elif page == "Control":
    render_control()
elif page == "Setup":
    render_setup()
elif page == "Logs":
    render_logs()
elif page == "Plans":
    render_plans()
else:
    render_help()
