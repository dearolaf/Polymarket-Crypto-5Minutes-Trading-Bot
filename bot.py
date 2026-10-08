import asyncio
import os
import sys
import threading
from pathlib import Path

# --verbose is applied before load_dotenv(); use BOT_VERBOSE in .env after dotenv loads.
if "--verbose" in sys.argv:
    os.environ["BOT_VERBOSE"] = "1"
from datetime import datetime, timezone, timedelta
import math
from decimal import Decimal
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Dict
import concurrent.futures
import signal
import random
from collections import deque

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


try:
    from patch_gamma_markets import apply_gamma_markets_patch, verify_patch
    patch_applied = apply_gamma_markets_patch()
    if patch_applied:
        verify_patch()
    else:
        print("ERROR: Failed to apply gamma_market patch")
        sys.exit(1)
except ImportError as e:
    print(f"ERROR: Could not import patch module: {e}")
    print("Make sure patch_gamma_markets.py is in the same directory")
    sys.exit(1)

# Now import Nautilus
from nautilus_trader.config import (
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.data import QuoteTick

from dotenv import load_dotenv
from loguru import logger
import redis


def _bot_verbose() -> bool:
    return os.getenv("BOT_VERBOSE", "").lower() in ("1", "true", "yes")


def configure_loguru_for_bot() -> None:
    """
    - ``ORDER_LOG_FILE`` (default ``logs/orders.log``): receives only lines logged with
      ``order_logger = logger.bind(order_only=True)`` (predictor trade/result + orders).
    - ``ORDER_LOG_ONLY`` (default ``1``): stderr is WARNING+ only so routine bot INFO does not spam console.
      Set ``ORDER_LOG_ONLY=0`` to restore previous "quiet" stderr (bot INFO from __main__ + patches).
    - ``ORDER_LOG_CONSOLE`` (default ``1``): when ``ORDER_LOG_ONLY=1``, also mirror ``order_logger`` lines
      to stderr (set ``0`` to send order detail to the file only).
    - ``BOT_VERBOSE=1`` or ``--verbose``: full stderr; order file still gets ``order_logger`` lines.
    """
    order_path = os.getenv("ORDER_LOG_FILE", "logs/orders.log").strip()

    def _order_sink_filter(record):
        return record["extra"].get("order_only") is True

    if _bot_verbose():
        if order_path:
            Path(order_path).parent.mkdir(parents=True, exist_ok=True)
            logger.add(
                order_path,
                filter=_order_sink_filter,
                level="DEBUG",
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}\n",
                rotation="20 MB",
                retention="14 days",
                enqueue=True,
            )
        return

    logger.remove()

    if order_path:
        Path(order_path).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            order_path,
            filter=_order_sink_filter,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}\n",
            rotation="20 MB",
            retention="14 days",
            enqueue=True,
        )

    info_modules = frozenset(
        {
            "__main__",
            "patch_market_orders",
            "patch_polymarket_user_ws",
            "patch_gamma_markets",
            "patch_polymarket_ws_subscribe",
            "patch_polymarket_market_ws_malformed",
            "patch_polymarket_trade_report_noise",
            "5m_bot_runner",
            "execution.polymarket_auto_redeem",
        }
    )

    def _important_only(record):
        level = record["level"].no
        if level >= 30:
            return True
        if level < 20:
            return False
        name = record["name"]
        if name in info_modules:
            return True
        return False

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan> - <level>{message}</level>\n"
    )
    order_log_only = os.getenv("ORDER_LOG_ONLY", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if order_log_only:
        logger.add(sys.stderr, level="WARNING", format=fmt)
        logger.warning(
            f"ORDER_LOG_ONLY=1: stderr WARNING+ only; predictor/order detail → {order_path or 'disabled'}"
        )
        if os.getenv("ORDER_LOG_CONSOLE", "1").strip().lower() not in ("0", "false", "no", "off"):
            logger.add(
                sys.stderr,
                filter=_order_sink_filter,
                level="DEBUG",
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}\n",
            )
    else:
        logger.add(sys.stderr, level="DEBUG", filter=_important_only, format=fmt)
        logger.info(
            "Quiet stderr: bot INFO + WARNING/ERROR elsewhere; predictor/order detail in "
            f"{order_path or 'ORDER_LOG_FILE unset'}. Full noise: BOT_VERBOSE=1."
        )


# Import our phases
from core.strategy_brain.signal_processors.spike_detector import SpikeDetectionProcessor
from core.strategy_brain.signal_processors.sentiment_processor import SentimentProcessor
from core.strategy_brain.signal_processors.divergence_processor import PriceDivergenceProcessor
from core.strategy_brain.signal_processors.orderbook_processor import OrderBookImbalanceProcessor
from core.strategy_brain.signal_processors.tick_velocity_processor import TickVelocityProcessor
from core.strategy_brain.signal_processors.deribit_pcr_processor import DeribitPCRProcessor
from core.strategy_brain.fusion_engine.signal_fusion import get_fusion_engine
from execution.risk_engine import get_risk_engine
from monitoring.performance_tracker import get_performance_tracker
from monitoring.grafana_exporter import get_grafana_exporter
from feedback.learning_engine import get_learning_engine
from core.updown_assets import (
    ASSET_REGISTRY,
    asset_from_slug,
    build_slug_list,
    coinbase_product_for_asset,
    is_tracked_updown_slug,
    make_trade_key,
    parse_active_assets,
    slug_prefix_for_asset,
    trade_key_parts,
)

load_dotenv()
configure_loguru_for_bot()

ACTIVE_ASSETS = parse_active_assets()
PRIMARY_ASSET = ACTIVE_ASSETS[0]
MULTI_ASSET_MODE = len(ACTIVE_ASSETS) > 1
# Predictor + placement + fills: use order_logger → ORDER_LOG_FILE; with ORDER_LOG_ONLY=1 also mirrors to stderr if ORDER_LOG_CONSOLE=1.
order_logger = logger.bind(order_only=True)

_active_trading_node: Any = None
_graceful_shutdown_requested: bool = False
_last_graceful_stop_reason: Optional[str] = None


def set_active_trading_node_for_shutdown(node: Any) -> None:
    global _active_trading_node
    _active_trading_node = node


def request_graceful_trading_node_stop(reason: str = "shutdown") -> None:
    """
    Stop the Nautilus TradingNode from a non-kernel thread (e.g. timer executor).
    Avoids SIGTERM races with in-flight market orders and executor futures.
    """
    global _graceful_shutdown_requested, _last_graceful_stop_reason
    _last_graceful_stop_reason = reason
    _graceful_shutdown_requested = True
    time.sleep(0.35)

    node = _active_trading_node
    loop = None
    if node is not None and getattr(node, "kernel", None) is not None:
        loop = getattr(node.kernel, "loop", None)
    if loop is None or loop.is_closed():
        logger.warning(
            f"No usable kernel loop for graceful stop ({reason}); sending SIGTERM"
        )
        os.kill(os.getpid(), signal.SIGTERM)
        return

    timeout = float(os.getenv("GRACEFUL_STOP_TIMEOUT_SEC", "180"))
    try:
        fut = asyncio.run_coroutine_threadsafe(node.stop_async(), loop)
        fut.result(timeout=timeout)
        logger.info(f"TradingNode stopped gracefully ({reason})")
    except concurrent.futures.TimeoutError:
        logger.error(f"Graceful stop timed out after {timeout}s ({reason}); sending SIGTERM")
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as e:
        logger.exception(f"Graceful stop failed ({reason}): {e}; sending SIGTERM")
        os.kill(os.getpid(), signal.SIGTERM)


def _dispose_trading_node_with_timeout(
    node: Any,
    timeout_sec: float,
    context: str = "",
) -> bool:
    """
    Nautilus ``TradingNode.dispose()`` can block without bound on
    ``ThreadPoolExecutor.shutdown(wait=True)`` when IOC/partial fills and WS
    teardown leave work in flight. Auto-restart must not hang the outer loop.
    Returns True if dispose finished (possibly with an exception logged), False on timeout.
    """
    if node is None:
        return True
    err_holder: list[Exception] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            node.dispose()
        except Exception as e:
            err_holder.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_worker, name="nautilus-node-dispose", daemon=True)
    t.start()
    if not done.wait(timeout=timeout_sec):
        logger.error(
            f"node.dispose() timed out after {timeout_sec}s ({context}). "
            f"Continuing outer restart loop — set NODE_DISPOSE_TIMEOUT_SEC or "
            f"BOT_PERIODIC_RESTART_MINUTES=0 if this persists; old dispose may still run in background."
        )
        return False
    if err_holder:
        logger.warning(f"node.dispose() raised ({context}): {err_holder[0]}")
    return True


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


from patch_market_orders import apply_market_order_patch
from patch_polymarket_user_ws import apply_polymarket_user_ws_patch
from patch_polymarket_ws_subscribe import apply_polymarket_ws_subscribe_patch
from patch_polymarket_market_ws_malformed import apply_polymarket_market_ws_malformed_patch
from patch_polymarket_trade_report_noise import apply_polymarket_trade_report_noise_patch

try:
    from execution.polymarket_auto_redeem import redeem_once, start_auto_redeem_thread
    _redeem_once_available = True
except ImportError as _auto_redeem_import_err:
    try:
        from polymarket_auto_redeem import redeem_once, start_auto_redeem_thread

        _redeem_once_available = True
    except ImportError:
        _redeem_once_available = False

        def redeem_once() -> bool:
            return False

        def start_auto_redeem_thread(stop: threading.Event) -> Optional[threading.Thread]:
            return None

        _redeem_msg = (
            "polymarket_auto_redeem not available: {}. "
            "Install `py-builder-relayer-client` + `httpx`, set Builder API creds."
        )
        if _env_truthy("POLYMARKET_AUTO_REDEEM"):
            logger.warning(_redeem_msg, _auto_redeem_import_err)
        else:
            logger.debug(_redeem_msg, _auto_redeem_import_err)

patch_applied = apply_market_order_patch()
if patch_applied:
    logger.info("Market order patch applied successfully")
else:
    logger.warning("Market order patch failed - orders may be rejected")

if apply_polymarket_user_ws_patch():
    logger.info("Polymarket user websocket patch applied successfully")
else:
    logger.warning("Polymarket user websocket patch not applied — auto_redeem WS may log errors")

if apply_polymarket_ws_subscribe_patch():
    logger.info("Polymarket WS subscribe reconnect patch applied successfully")
else:
    logger.warning(
        "Polymarket WS subscribe patch not applied — submit_order may fail with "
        "'connection not active' after idle disconnect"
    )

if apply_polymarket_market_ws_malformed_patch():
    logger.info("Polymarket market WS malformed-frame patch applied successfully")
else:
    logger.warning(
        "Polymarket market WS malformed patch not applied — binary/non-JSON frames "
        "may break quote parsing after reconnect (DecodeError / stall)"
    )

if apply_polymarket_trade_report_noise_patch():
    logger.info("Polymarket trade-report noise patch applied successfully")
else:
    logger.warning(
        "Polymarket trade-report noise patch not applied — startup may spam "
        "'instrument … not found' for wallet history outside loaded slug list"
    )


# =============================================================================
# CONSTANTS
# =============================================================================
QUOTE_STABILITY_REQUIRED = 3      # Need only 3 valid ticks to be stable (faster startup)
QUOTE_MIN_SPREAD = 0.001          # Both bid AND ask must be at least this
BTC_UPDOWN_SLUG_PREFIX = slug_prefix_for_asset(PRIMARY_ASSET)
COINBASE_PRODUCT_ID = (
    os.getenv("COINBASE_PRODUCT_ID") or coinbase_product_for_asset(PRIMARY_ASSET)
).strip()
try:
    MARKET_INTERVAL_SECONDS = max(
        60,
        min(
            7200,
            int(
                os.getenv("BTC_MARKET_INTERVAL_SECONDS")
                or os.getenv("SOL_MARKET_INTERVAL_SECONDS", "300")
            ),
        ),
    )
except ValueError:
    MARKET_INTERVAL_SECONDS = 300  # 5-minute Polymarket BTC up/down markets


def _is_btc_updown_slug(slug: str) -> bool:
    """
    True for configured 5m up/down slug families (see ``TRADING_ASSET(S)``).

    Do **not** use ``'5m' in slug`` — that falsely matches other interval families.
    """
    if is_tracked_updown_slug(slug, ACTIVE_ASSETS):
        return True
    s = (slug or "").strip().lower()
    pfx = (BTC_UPDOWN_SLUG_PREFIX or "").strip().lower()
    if not s or not pfx:
        return False
    return s.startswith(f"{pfx}-")

# Dedupe: one ``submit`` line and one ``result`` line per slug per summary log path.
_order_summary_lock = threading.Lock()
_order_summary_logged: set[tuple[str, int, str]] = set()


def append_predictor_order_summary_line(
    trade_key: tuple,
    order_side: str,
    stake_usd: Decimal,
    result_direction: str,
    *,
    phase: str = "result",
    model_signal: Optional[str] = None,
    round_idx: Optional[int] = None,
) -> None:
    """
    Append one compact line to the predictor summary log (``log.txt`` by default).

    ``order_side`` is the direction **actually submitted** on the CLOB (UP = YES token,
    DOWN = NO token), not the raw model signal.

    ``phase``:

    - ``submit`` — written once when an order (live or paper) is successfully placed for this slug.
      ``result_direction`` should be ``pending`` (shown in the ``Result :`` column).
    - ``result`` (default) — written once when that slug is scored after the interval ends.

    At most one line per ``(path, trade_key[0], phase)``; duplicates are ignored.

    ``ORDER_SUMMARY_LOG_FILE``: unset → ``<project>/log.txt``; relative paths are under the
    project root; set to empty in the environment to disable.

    Examples::

        slug_start_ts=1747143300 | phase=submit | Time: 2026-05-12 10:55~2026-05-12 11:00 UTC | Order : DOWN | $1 | Result : pending
        slug_start_ts=1747143300 | phase=result | Time: 2026-05-12 10:55~2026-05-12 11:00 UTC | Order : DOWN | $1 | Result : UP

    ``slug_start_ts`` is the Polymarket 5m interval start (Unix, UTC); keep it first so ``sort`` restores chronological order.
    """
    ex = os.getenv("ORDER_SUMMARY_LOG_FILE")
    if ex is not None and ex.strip() == "":
        return
    raw = ex.strip() if ex is not None else str(project_root / "log.txt")
    path = raw if os.path.isabs(raw) else str(project_root / raw)
    try:
        asset_id, market_ts = trade_key_parts(trade_key, PRIMARY_ASSET)
    except (TypeError, ValueError, IndexError):
        return
    start = datetime.fromtimestamp(market_ts, tz=timezone.utc)
    end = start + timedelta(seconds=MARKET_INTERVAL_SECONDS)
    time_s = (
        f"{start.strftime('%Y-%m-%d %H:%M')}~{end.strftime('%Y-%m-%d %H:%M')} UTC"
    )
    os_ = (order_side or "?").strip().upper()
    if os_ not in ("UP", "DOWN"):
        os_ = (order_side or "?").strip()
    ph = (phase or "result").strip().lower()
    if ph not in ("submit", "result"):
        ph = "result"
    if ph == "submit":
        rd = (result_direction or "pending").strip()
    else:
        rd = (result_direction or "?").strip().upper()
    sf = float(stake_usd)
    if abs(sf - round(sf)) < 1e-9:
        stake_str = f"${int(round(sf))}"
    else:
        stake_str = f"${sf:.2f}".rstrip("0").rstrip(".")
    ms = (model_signal or "").strip().upper()
    model_part = f" | Model : {ms}" if ms in ("UP", "DOWN") else ""
    round_part = f" | round={int(round_idx)}" if round_idx is not None else ""
    asset_part = f"asset={asset_id} | " if MULTI_ASSET_MODE else ""
    line = (
        f"{asset_part}slug_start_ts={market_ts} | phase={ph} | Time: {time_s}{round_part}{model_part} | "
        f"Order : {os_} | {stake_str} | Result : {rd}\n"
    )
    fp = Path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    key = (path, asset_id if MULTI_ASSET_MODE else "", market_ts, ph)
    with _order_summary_lock:
        if key in _order_summary_logged:
            return
        _order_summary_logged.add(key)
        if len(_order_summary_logged) > 4096:
            cutoff = int(time.time()) - 8 * 86400
            for _k in list(_order_summary_logged):
                if len(_k) >= 3 and _k[2] < cutoff:
                    _order_summary_logged.discard(_k)
        with open(fp, "a", encoding="utf-8") as fh:
            fh.write(line)
    print(line.rstrip(), flush=True)


# Gamma /markets uses GET with repeated slug= params; ~24h of 5m slugs → HTTP 414 URI Too Large.
# Default lookahead is conservative; raise BTC_SLUG_HOURS_AHEAD only if you need more (watch 414).
BTC_SLUG_HOURS_AHEAD_DEFAULT = 6.0
BTC_SLUG_COUNT_CAP_DEFAULT = 96   # ~8h of 5m slots + prior interval; keeps URL under typical nginx limits

BOT_VERSION = "5.0.0"

# ---------------------------------------------------------------------------
# Lightweight predictor mode (Coinbase-based), for next 5m direction.
# ---------------------------------------------------------------------------
BOT_PLAN = os.getenv("BOT_PLAN", "free").strip().lower()
IS_PREMIUM_PLAN = BOT_PLAN == "premium"

USE_LIGHTWEIGHT_PREDICTOR = os.getenv("USE_LIGHTWEIGHT_PREDICTOR", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


try:
    _default_min_score = "0.55" if IS_PREMIUM_PLAN and os.getenv("PREDICTOR_MIN_SCORE") is None else "0.50"
    PREDICTOR_MIN_SCORE = max(
        0.0,
        min(5.0, float(os.getenv("PREDICTOR_MIN_SCORE", _default_min_score))),
    )
except ValueError:
    PREDICTOR_MIN_SCORE = 0.55 if IS_PREMIUM_PLAN else 0.50
try:
    PREDICTOR_WINDOW_SAMPLES = max(10, min(120, int(os.getenv("PREDICTOR_WINDOW_SAMPLES", "60"))))
except ValueError:
    PREDICTOR_WINDOW_SAMPLES = 60
PREDICTOR_REQUIRE_TREND_ALIGN = _env_bool(
    "PREDICTOR_REQUIRE_TREND_ALIGN", IS_PREMIUM_PLAN
)
PREDICTOR_USE_5M_CANDLE = os.getenv("PREDICTOR_USE_5M_CANDLE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
try:
    PREDICTOR_REVERSAL_WEIGHT = max(
        0.0, min(3.0, float(os.getenv("PREDICTOR_REVERSAL_WEIGHT", "0")))
    )
except ValueError:
    PREDICTOR_REVERSAL_WEIGHT = 0.0
PREDICTOR_USE_CLOB_BOOK = os.getenv("PREDICTOR_USE_CLOB_BOOK", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
PREDICTOR_REQUIRE_CANDLE_ALIGN = _env_bool(
    "PREDICTOR_REQUIRE_CANDLE_ALIGN", IS_PREMIUM_PLAN
)
PREDICTOR_REQUIRE_VOLUME_CONFIRM = _env_bool("PREDICTOR_REQUIRE_VOLUME_CONFIRM", False)
try:
    PREDICTOR_MIN_BAR_MOVE = max(
        0.0, min(0.01, float(os.getenv("PREDICTOR_MIN_BAR_MOVE", "0.00015")))
    )
except ValueError:
    PREDICTOR_MIN_BAR_MOVE = 0.00015
PREDICTOR_CONTRARIAN = os.getenv("PREDICTOR_CONTRARIAN", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
try:
    _default_poll = "3" if IS_PREMIUM_PLAN and os.getenv("PREDICTOR_POLL_SECONDS") is None else "5"
    PREDICTOR_POLL_SECONDS = max(1, min(30, int(os.getenv("PREDICTOR_POLL_SECONDS", _default_poll))))
except ValueError:
    PREDICTOR_POLL_SECONDS = 3 if IS_PREMIUM_PLAN else 5
# Predictor submit timing (UTC slug = 5m):
# - ``SUBMIT_SECONDS_BEFORE_BOUNDARY`` (default 60): only in the last N seconds *before the next
#   slug opens*, submit for that next market (``order_market`` path). This is "1 min before next round".
# - ``SUBMIT_SECONDS_AFTER_BOUNDARY``: seconds after *current* slug open for a second path; default
#   is disabled (MARKET_INTERVAL+1) when using the default before-window, so we do NOT submit every
#   second after 12s into the round (that caused "every round" spam). Set BEFORE=0 and AFTER=12
#   in .env to restore old "submit shortly after current slug opens" behavior.
_env_sb = os.getenv("SUBMIT_SECONDS_BEFORE_BOUNDARY")
if _env_sb is not None and str(_env_sb).strip() != "":
    SUBMIT_SECONDS_BEFORE_BOUNDARY = max(0, min(300, int(float(_env_sb))))
else:
    SUBMIT_SECONDS_BEFORE_BOUNDARY = 60

_env_sa = os.getenv("SUBMIT_SECONDS_AFTER_BOUNDARY")
if _env_sa is not None and str(_env_sa).strip() != "":
    SUBMIT_SECONDS_AFTER_BOUNDARY = max(0, min(MARKET_INTERVAL_SECONDS + 120, int(float(_env_sa))))
else:
    SUBMIT_SECONDS_AFTER_BOUNDARY = (
        MARKET_INTERVAL_SECONDS + 1 if SUBMIT_SECONDS_BEFORE_BOUNDARY > 0 else 12
    )
# Minimum seconds between any two predictor-driven $1 CLOB submits (blocks post-open + early-next
# both firing within the same 5m wall). Default = one slug interval.
PREDICTOR_MIN_SUBMIT_GAP_SEC = max(
    1.0,
    min(3600.0, float(os.getenv("PREDICTOR_MIN_SUBMIT_GAP_SEC", str(float(MARKET_INTERVAL_SECONDS))))),
)
# After a bet is scored for slug start ``S``, the next submit targets slug start
# ``>= S + (1 + N) * MARKET_INTERVAL_SECONDS`` (skip ``N`` full 5m bars after ``S``). Default ``N=1``
# skips the single bar immediately after ``S`` so we only re-arm in the last-60s window before a
# later slug (not mid-bar on the consecutive market).
_pred_skip_env = os.getenv("PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE", "1").strip()
try:
    PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE = max(0, min(24, int(float(_pred_skip_env))))
except ValueError:
    PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE = 1
# Submit for the *next* 5m slug once the current slug is this many seconds old (default 60).
_env_pas = os.getenv("PREDICTOR_SUBMIT_AFTER_ROUND_START_SEC", "60").strip()
try:
    PREDICTOR_SUBMIT_AFTER_ROUND_START_SEC = max(
        0, min(240, int(float(_env_pas)))
    )
except ValueError:
    PREDICTOR_SUBMIT_AFTER_ROUND_START_SEC = 60
_env_paw = os.getenv("PREDICTOR_SUBMIT_AFTER_ROUND_WINDOW_SEC", "50").strip()
try:
    PREDICTOR_SUBMIT_AFTER_ROUND_WINDOW_SEC = max(
        5, min(240, int(float(_env_paw)))
    )
except ValueError:
    PREDICTOR_SUBMIT_AFTER_ROUND_WINDOW_SEC = 50
STAKE_BASE_USD = Decimal(os.getenv("STAKE_BASE_USD", "1.0"))
STAKE_MAX_USD = Decimal(os.getenv("STAKE_MAX_USD", "1.0"))
# Martingale (predictor): base stake after win; doubles on loss up to max cap.
_mg_base_raw = os.getenv("MARTINGALE_BASE_USD", "").strip()
_mb_raw = os.getenv("MARKET_BUY_USD", "").strip()
if _mg_base_raw:
    MARTINGALE_BASE_USD = Decimal(_mg_base_raw)
elif _mb_raw:
    MARTINGALE_BASE_USD = Decimal(_mb_raw)
else:
    MARTINGALE_BASE_USD = Decimal("1")
MARTINGALE_MAX_STAKE_USD = Decimal(os.getenv("MARTINGALE_MAX_STAKE_USD", "16"))
# ``polymarket`` (default): poll Gamma API until closed with winner (accurate, waits up to 5 min).
# ``candle``: Coinbase 5m OHLC (fast but may disagree with Chainlink oracle on close calls).
# ``submit_spot`` / ``legacy``: spot at eval minus spot at order submit (often wrong vs settlement).
_pr_mode = os.getenv("PREDICTOR_RESOLUTION_MODE", "polymarket").strip().lower()
PREDICTOR_RESOLUTION_MODE = (
    _pr_mode if _pr_mode in ("polymarket", "candle", "submit_spot", "submit_vs_spot", "legacy") else "polymarket"
)
# Do not score predictor bet until slug wall end + this many seconds (oracle / candle lag). 0 = end only.
PREDICTOR_EVAL_POST_END_SEC = max(
    0, min(180, int(os.getenv("PREDICTOR_EVAL_POST_END_SEC", "90")))
)


@dataclass
class PaperTrade:
    """Track paper/simulation trades"""
    timestamp: datetime
    direction: str
    size_usd: float
    price: float
    signal_score: float
    signal_confidence: float
    outcome: str = "PENDING"

    def to_dict(self):
        return {
            'timestamp': self.timestamp.isoformat(),
            'direction': self.direction,
            'size_usd': self.size_usd,
            'price': self.price,
            'signal_score': self.signal_score,
            'signal_confidence': self.signal_confidence,
            'outcome': self.outcome,
        }


class LightweightPredictor:
    """
    Coinbase tick momentum + order flow, optionally blended with 5m candle context
    (closer to how Polymarket 5m UP/DOWN settles: open vs close of the window).
    """

    def __init__(self, window: int = PREDICTOR_WINDOW_SAMPLES):
        self.prices = deque(maxlen=window)
        self.buy_volume = deque(maxlen=window)
        self.sell_volume = deque(maxlen=window)

    def update(self, price: float, buy_vol: float, sell_vol: float):
        self.prices.append(float(price))
        self.buy_volume.append(float(buy_vol))
        self.sell_volume.append(float(sell_vol))

    @staticmethod
    def _signed_return(open_px: float, close_px: float) -> float:
        if open_px <= 0:
            return 0.0
        return (close_px - open_px) / open_px

    @staticmethod
    def _candle_vote(return_frac: float) -> int:
        """+1 UP bar, -1 DOWN bar, 0 flat/chop."""
        if return_frac > PREDICTOR_MIN_BAR_MOVE:
            return 1
        if return_frac < -PREDICTOR_MIN_BAR_MOVE:
            return -1
        return 0

    def predict(
        self,
        candle_context: Optional[dict] = None,
        clob_imbalance: Optional[float] = None,
    ):
        """
        Always returns UP or DOWN. Combines tick momentum, 5m candles, flow, and CLOB book
        in a weighted ensemble (no skip / FLAT).
        """
        prices = list(self.prices)
        if len(prices) < 2:
            return "UP", 0.0

        current = prices[-1]
        n = len(prices)
        i3 = max(0, n - 3)
        i7 = max(0, n - 7)
        i15 = max(0, n - 15)

        short_momentum = current - prices[i3]
        medium_momentum = current - prices[i7]
        long_momentum = current - prices[i15]

        highest = max(prices)
        lowest = min(prices)
        volatility = max(highest - lowest, 0.000001)

        short_norm = short_momentum / volatility
        medium_norm = medium_momentum / volatility
        long_norm = long_momentum / volatility

        # Tick component: always blended (no hard alignment skip).
        tick_score = long_norm * 4.0 + medium_norm * 3.0 + short_norm * 2.0
        if n >= 15:
            up_moves = sum(1 for i in range(1, n) if prices[i] > prices[i - 1])
            trend_bias = (2 * up_moves - (n - 1)) / max(n - 1, 1)
            tick_score += trend_bias * 1.5
            acceleration = short_momentum - medium_momentum
            tick_score += (0.75 if acceleration > 0 else -0.75)
            if PREDICTOR_REVERSAL_WEIGHT > 0:
                recent_move = current - prices[-2]
                if recent_move > volatility * 0.25:
                    tick_score -= PREDICTOR_REVERSAL_WEIGHT
                elif recent_move < -volatility * 0.25:
                    tick_score += PREDICTOR_REVERSAL_WEIGHT

        buy_pressure = sum(self.buy_volume)
        sell_pressure = sum(self.sell_volume)
        total_volume = max(buy_pressure + sell_pressure, 1.0)
        pressure_ratio = (buy_pressure - sell_pressure) / total_volume
        flow_score = pressure_ratio * 2.5

        candle_score = 0.0
        if candle_context:
            cur_ret = float(candle_context.get("cur_5m_return") or 0.0)
            prev_ret = float(
                candle_context.get("prev_5m_return")
                or candle_context.get("bar_m1_return")
                or 0.0
            )
            m2_ret = float(candle_context.get("bar_m2_return") or 0.0)
            # Leading bar + last closed bar matter most for 5m settlement.
            candle_score += max(-3.0, min(3.0, cur_ret * 500.0)) * 2.8
            candle_score += max(-2.0, min(2.0, prev_ret * 500.0)) * 1.5
            candle_score += max(-2.0, min(2.0, m2_ret * 500.0)) * 0.8
            votes = 0
            for key in ("bar_m2_return", "bar_m1_return", "prev_5m_return", "cur_5m_return"):
                ret = candle_context.get(key)
                if ret is not None:
                    votes += self._candle_vote(float(ret))
            candle_score += votes * 0.6

        clob_score = 0.0
        if clob_imbalance is not None:
            clob_score = max(-1.0, min(1.0, float(clob_imbalance))) * 3.0

        # Ensemble: emphasize 5m structure, then ticks, then flow/book.
        score = (
            tick_score * 0.30
            + candle_score * 0.45
            + flow_score * 0.15
            + clob_score * 0.10
        )
        if score == 0.0:
            score = current - prices[0]

        score = round(score, 4)
        signal = "UP" if score >= 0 else "DOWN"
        return signal, score

def init_redis():
    """Initialize Redis connection for simulation mode control."""
    try:
        redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 2)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        redis_client.ping()
        logger.info("Redis connection established")
        return redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        logger.warning("Simulation mode will be static (from .env)")
        return None


class IntegratedBTCStrategy(Strategy):
    """
    Integrated BTC Strategy - FIXED VERSION
    - Subscribes immediately at startup
    - Forces stability for first trade
    - Correct timing for market switching
    """

    def __init__(self, redis_client=None, enable_grafana=True, test_mode=False, simulation=False):
        super().__init__()

        self.bot_start_time = datetime.now(timezone.utc)
        # Periodic process refresh: rebuild Gamma slug filter window. Default OFF for 24/7 runs.
        _rpm = os.getenv("BOT_PERIODIC_RESTART_MINUTES", "0").strip().lower()
        if _rpm in ("", "0", "off", "false", "never", "disable", "no"):
            self.restart_after_minutes = float("inf")
            logger.info(
                "BOT_PERIODIC_RESTART_MINUTES off (default) — no timer shutdown; "
                "set e.g. 90 to refresh Gamma slug window periodically"
            )
        else:
            try:
                self.restart_after_minutes = max(1.0, float(_rpm))
                logger.warning(
                    f"BOT_PERIODIC_RESTART_MINUTES={self.restart_after_minutes:g} — "
                    f"TradingNode will stop/rebuild on that interval"
                )
            except ValueError:
                self.restart_after_minutes = float("inf")
                logger.warning(
                    f"Invalid BOT_PERIODIC_RESTART_MINUTES={_rpm!r}; treating as off (24/7)"
                )

        # Nautilus
        self.instrument_id = None
        self.redis_client = redis_client
        self.current_simulation_mode = bool(simulation)

        # Store ALL BTC instruments
        self.all_btc_instruments: List[Dict] = []
        self.current_instrument_index: int = -1
        self.next_switch_time: Optional[datetime] = None

        # Quote-stability tracking
        self._stable_tick_count = 0
        self._market_stable = False
        self._last_instrument_switch = None
        
        # =========================================================================
        # FIX 1: Force first trade by setting last_trade_time to -1
        # =========================================================================
        self.last_trade_time = -1  # Force first trade immediately!
        # Predictor: at most one invocation per (market_start_ts, sub_interval) — no double submits.
        self._predictor_attempted_keys: set[tuple] = set()
        self._predictor_submit_lock = threading.Lock()
        self._predictor_arm_lock = threading.Lock()
        self._predictor_armed_keys: set[tuple] = set()
        self._predictor_last_submit_ts: float = 0.0
        self._waiting_for_market_open = False  # True when waiting for a future market to open
        self._last_bid_ask = None  # (bid_decimal, ask_decimal) from last tick, for liquidity checks

        # Tick buffer: rolling 90s of ticks for TickVelocityProcessor
        self._tick_buffer: deque = deque(maxlen=500)  # ~500 ticks = well over 90s

        # YES token id for the current market (set in _load_all_btc_instruments)
        self._yes_token_id: Optional[str] = None

        # Phase 4: Signal Processors
        self.spike_detector = SpikeDetectionProcessor(
            spike_threshold=0.05,       # FIXED: was 0.15 (too high for probabilities)
            lookback_periods=20,
        )
        self.sentiment_processor = SentimentProcessor(
            extreme_fear_threshold=25,
            extreme_greed_threshold=75,
        )
        self.divergence_processor = PriceDivergenceProcessor(
            divergence_threshold=0.05,
        )
        self.orderbook_processor = OrderBookImbalanceProcessor(
            imbalance_threshold=0.30,   # 30% skew to signal
            min_book_volume=50.0,       # ignore illiquid books
        )
        self.tick_velocity_processor = TickVelocityProcessor(
            velocity_threshold_60s=0.015,  # 1.5% move in 60s
            velocity_threshold_30s=0.010,  # 1.0% move in 30s
        )
        self.deribit_pcr_processor = DeribitPCRProcessor(
            bullish_pcr_threshold=1.20,
            bearish_pcr_threshold=0.70,
            max_days_to_expiry=2,
            cache_seconds=300,          # refresh every 5 min
        )

        # Phase 4: Signal Fusion — update weights for 6 processors
        self.fusion_engine = get_fusion_engine()
        # Rebalanced weights (must sum ≤ 1.0; higher = more influence)
        self.fusion_engine.set_weight("OrderBookImbalance", 0.30)  # best real-time signal
        self.fusion_engine.set_weight("TickVelocity",       0.25)  # fast poly momentum
        self.fusion_engine.set_weight("PriceDivergence",    0.18)  # spot momentum
        self.fusion_engine.set_weight("SpikeDetection",     0.12)  # mean reversion
        self.fusion_engine.set_weight("DeribitPCR",         0.10)  # institutional sentiment
        self.fusion_engine.set_weight("SentimentAnalysis",  0.05)  # daily F&G (weak)

        # Phase 5: Risk Management
        self.risk_engine = get_risk_engine()

        # Phase 6: Performance Tracking
        self.performance_tracker = get_performance_tracker()

        # Phase 7: Learning Engine
        self.learning_engine = get_learning_engine()

        # Phase 6: Grafana (optional)
        if enable_grafana:
            self.grafana_exporter = get_grafana_exporter()
        else:
            self.grafana_exporter = None

        # Price history
        self.price_history = []
        self.max_history = 100

        # Paper trading tracker
        self.paper_trades: List[PaperTrade] = []

        self.test_mode = test_mode

        # Lightweight predictor state (Coinbase-based)
        # Do not silently disable based on interval; we want clear logs.
        self._predictor_enabled = USE_LIGHTWEIGHT_PREDICTOR
        self._active_assets = list(ACTIVE_ASSETS)
        self._multi_asset = MULTI_ASSET_MODE
        self._primary_asset = PRIMARY_ASSET
        self._predictors = {a: LightweightPredictor() for a in self._active_assets}
        self._predictor = self._predictors[self._primary_asset]
        self._latest_spot_by_asset: Dict[str, Optional[Decimal]] = {
            a: None for a in self._active_assets
        }
        self._latest_spot_price: Optional[Decimal] = None
        self._latest_buy_vol_by_asset: Dict[str, float] = {a: 0.0 for a in self._active_assets}
        self._latest_sell_vol_by_asset: Dict[str, float] = {a: 0.0 for a in self._active_assets}
        self._latest_buy_vol: float = 0.0
        self._latest_sell_vol: float = 0.0
        self._predictor_last_trade_poll_ts_by_asset: Dict[str, Optional[float]] = {}
        self._stake_usd_by_asset: Dict[str, Decimal] = {
            a: MARTINGALE_BASE_USD for a in self._active_assets
        }
        self._stake_usd: Decimal = MARTINGALE_BASE_USD
        self._predictor_last_bet_by_asset: Dict[str, Dict[str, Any]] = {}
        self._predictor_next_target_round_by_asset: Dict[str, int] = {}
        self._predictor_earliest_next_order_slug_ts_by_asset: Dict[str, int] = {}
        self._martingale_max_loss_stopped: bool = False
        # Martingale: win -> base stake; loss -> double (cap MARTINGALE_MAX_STAKE_USD); lose at cap -> reset to base.
        # Submit cadence: on successful submit for slug ``S``, floor next slug start to
        # ``S + (1+N)*300`` (N=PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE, default 1) so we never arm
        # the consecutive bar; eval still runs after ``S`` ends; next submit in last 60s before an
        # allowed slug opens.
        self._predictor_last_bet: Optional[Dict[str, Any]] = None
        self._predictor_earliest_next_order_slug_ts: int = 0  # min slug start for next order
        self._predictor_eval_in_progress: bool = False
        # Round 0 = active 5m slug at bot start; bets only on odd rounds 1, 3, 5, 7, …
        self._predictor_anchor_slug_ts: int = 0
        self._predictor_next_target_round: int = 1

        if test_mode:
            if not self._predictor_enabled:
                self._predictor_enabled = True
                logger.info(
                    "TEST MODE: lightweight predictor enabled (same submit/eval/log.txt path as --live)"
                )
            logger.info("=" * 80)
            logger.info(
                "  TEST MODE ACTIVE — predictor pipeline + log.txt (simulation/paper only, no CLOB)"
            )
            logger.info("=" * 80)

        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY INITIALIZED - FIXED VERSION")
        logger.info("  Phase 4: Signal processors ready")
        logger.info("  Phase 5: Risk engine ready")
        logger.info("  Phase 6: Performance tracking ready")
        logger.info("  Phase 7: Learning engine ready")
        logger.info("  $1 per trade maximum")
        logger.info("=" * 80)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _spot_for_asset(self, asset_id: str) -> Optional[Decimal]:
        if self._multi_asset:
            return self._latest_spot_by_asset.get(asset_id)
        if asset_id == self._primary_asset:
            return self._latest_spot_price
        return None

    def _trade_key_slug_ts(self, trade_key: tuple) -> int:
        _, slug_ts = trade_key_parts(trade_key, self._primary_asset)
        return slug_ts

    def _predictor_next_target_round_for(self, asset_id: str) -> int:
        if self._multi_asset:
            return int(self._predictor_next_target_round_by_asset.get(asset_id, 1))
        return self._predictor_next_target_round

    def _predictor_set_next_target_round_for(self, asset_id: str, rnd: int) -> None:
        if self._multi_asset:
            self._predictor_next_target_round_by_asset[asset_id] = int(rnd)
        else:
            self._predictor_next_target_round = int(rnd)

    def _predictor_earliest_slug_for(self, asset_id: str) -> int:
        if self._multi_asset:
            return int(self._predictor_earliest_next_order_slug_ts_by_asset.get(asset_id, 0))
        return int(self._predictor_earliest_next_order_slug_ts or 0)

    def _predictor_set_earliest_slug_for(self, asset_id: str, slug_ts: int) -> None:
        if self._multi_asset:
            self._predictor_earliest_next_order_slug_ts_by_asset[asset_id] = int(slug_ts)
        else:
            self._predictor_earliest_next_order_slug_ts = int(slug_ts)

    def _predictor_pending_bet(self, asset_id: str) -> Optional[Dict[str, Any]]:
        if self._multi_asset:
            return self._predictor_last_bet_by_asset.get(asset_id)
        if asset_id == self._primary_asset:
            return self._predictor_last_bet
        return None

    def _predictor_store_pending_bet(self, asset_id: str, bet: Optional[Dict[str, Any]]) -> None:
        if self._multi_asset:
            if bet is None:
                self._predictor_last_bet_by_asset.pop(asset_id, None)
            else:
                self._predictor_last_bet_by_asset[asset_id] = bet
        elif asset_id == self._primary_asset:
            self._predictor_last_bet = bet

    def _predictor_has_any_pending_bet(self) -> bool:
        if self._multi_asset:
            return bool(self._predictor_last_bet_by_asset)
        return self._predictor_last_bet is not None

    def _slug_for_asset_ts(self, asset_id: str, slug_start_ts: int) -> str:
        return f"{slug_prefix_for_asset(asset_id)}-{slug_start_ts}"

    def _predictor_eval_post_end_sec(self) -> int:
        """Seconds after slug wall end before scoring (shorter in --test-mode)."""
        if self.test_mode:
            try:
                return max(
                    0,
                    min(180, int(os.getenv("TEST_PREDICTOR_EVAL_POST_END_SEC", "5"))),
                )
            except ValueError:
                return 5
        return PREDICTOR_EVAL_POST_END_SEC

    def _predictor_slugs_to_skip_after_submit(self) -> int:
        """Full 5m bars to skip after each scored bet before re-arming."""
        return max(0, min(24, PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE))

    def _predictor_passes_quality_gates(
        self,
        signal: str,
        score: float,
        candle_ctx: Optional[dict],
        asset_id: Optional[str] = None,
    ) -> bool:
        """Return False when signal should be skipped (weak or misaligned)."""
        aid = asset_id or self._primary_asset
        predictor = self._predictors.get(aid, self._predictor)
        if abs(score) < PREDICTOR_MIN_SCORE:
            order_logger.info(
                f"Predictor ({aid}): SKIP weak signal (|score|={abs(score):.4f} < min {PREDICTOR_MIN_SCORE})"
            )
            return False

        if PREDICTOR_REQUIRE_TREND_ALIGN:
            prices = list(predictor.prices)
            if len(prices) >= 7:
                trend = prices[-1] - prices[max(0, len(prices) - 7)]
                if (signal == "UP" and trend < 0) or (signal == "DOWN" and trend > 0):
                    order_logger.info(
                        f"Predictor ({aid}): SKIP trend misalignment (signal={signal}, trend={trend:+.6f})"
                    )
                    return False

        if PREDICTOR_REQUIRE_CANDLE_ALIGN and candle_ctx:
            cur_ret = float(candle_ctx.get("cur_5m_return") or 0.0)
            if (signal == "UP" and cur_ret < 0) or (signal == "DOWN" and cur_ret > 0):
                order_logger.info(
                    f"Predictor ({aid}): SKIP candle misalignment (signal={signal}, cur_5m={cur_ret:+.4%})"
                )
                return False

        if PREDICTOR_REQUIRE_VOLUME_CONFIRM:
            buy_v = self._latest_buy_vol_by_asset.get(aid, self._latest_buy_vol)
            sell_v = self._latest_sell_vol_by_asset.get(aid, self._latest_sell_vol)
            total_vol = buy_v + sell_v
            if total_vol <= 0:
                order_logger.info(f"Predictor ({aid}): SKIP — no volume confirmation")
                return False

        return True

    def _predictor_slug_for_round(self, round_idx: int) -> int:
        return self._predictor_anchor_slug_ts + int(round_idx) * MARKET_INTERVAL_SECONDS

    def _predictor_round_index_for_slug(self, slug_start_ts: int) -> int:
        return (int(slug_start_ts) - self._predictor_anchor_slug_ts) // MARKET_INTERVAL_SECONDS

    def _predictor_init_round_schedule(self, now_ts: Optional[float] = None) -> None:
        """Round 0 = current 5m bar; first order targets odd round 1, then 3, 5, 7, …"""
        if now_ts is None:
            now_ts = time.time()
        iv = MARKET_INTERVAL_SECONDS
        self._predictor_anchor_slug_ts = (int(now_ts) // iv) * iv
        self._predictor_next_target_round = 1
        self._predictor_earliest_next_order_slug_ts = self._predictor_slug_for_round(1)
        for aid in self._active_assets:
            self._predictor_set_next_target_round_for(aid, 1)
            self._predictor_set_earliest_slug_for(aid, self._predictor_slug_for_round(1))
        self._predictor_advance_past_missed_targets(now_ts)
        assets_label = ",".join(self._active_assets)
        order_logger.info(
            f"Predictor odd-round schedule [{assets_label}]: round-0 slug={self._predictor_anchor_slug_ts} "
            f"(current bar); next bet round {self._predictor_next_target_round} "
            f"slug={self._predictor_slug_for_round(self._predictor_next_target_round)}; "
            f"submit in last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before that slug opens; "
            f"martingale ${float(MARTINGALE_BASE_USD):.0f}→…→${float(MARTINGALE_MAX_STAKE_USD):.0f}"
        )

    def _predictor_advance_past_missed_targets(self, now_ts: float) -> None:
        """If we missed the pre-open window for the target odd round, jump to the next odd."""
        if self._predictor_anchor_slug_ts <= 0:
            return
        for aid in self._active_assets:
            rnd = self._predictor_next_target_round_for(aid)
            if rnd % 2 == 0:
                rnd += 1
                self._predictor_set_next_target_round_for(aid, rnd)
            while True:
                slug = self._predictor_slug_for_round(rnd)
                if now_ts < slug:
                    break
                tk = make_trade_key(aid, slug, self._multi_asset)
                if tk in self._predictor_attempted_keys:
                    break
                order_logger.info(
                    f"Predictor ({aid}): missed pre-open for round {rnd} "
                    f"(slug {slug}) — advancing to round {rnd + 2}"
                )
                rnd += 2
            self._predictor_set_next_target_round_for(aid, rnd)
            self._predictor_set_earliest_slug_for(aid, self._predictor_slug_for_round(rnd))
        self._predictor_next_target_round = self._predictor_next_target_round_for(
            self._primary_asset
        )
        self._predictor_earliest_next_order_slug_ts = self._predictor_earliest_slug_for(
            self._primary_asset
        )

    def _predictor_pending_earliest_eval_ts(self) -> Optional[float]:
        earliest: Optional[float] = None
        candidates: List[Dict[str, Any]] = []
        if self._multi_asset:
            candidates = list(self._predictor_last_bet_by_asset.values())
        elif self._predictor_last_bet:
            candidates = [self._predictor_last_bet]
        for pred in candidates:
            tk = pred.get("trade_key")
            if not isinstance(tk, tuple) or len(tk) < 1:
                continue
            try:
                interval_start_ts = self._trade_key_slug_ts(tk)
            except (TypeError, ValueError):
                continue
            if interval_start_ts <= 0:
                continue
            ts = float(
                interval_start_ts
                + MARKET_INTERVAL_SECONDS
                + self._predictor_eval_post_end_sec()
            )
            if earliest is None or ts < earliest:
                earliest = ts
        return earliest

    def _schedule_predictor_eval_if_ready(self) -> None:
        """Score the last bet in a background executor (never blocks the submit window)."""
        if not self._predictor_has_any_pending_bet() or self._predictor_eval_in_progress:
            return
        earliest = self._predictor_pending_earliest_eval_ts()
        if earliest is None or time.time() < earliest:
            return
        self._predictor_eval_in_progress = True
        threading.Thread(
            target=self._evaluate_predictor_pending_sync,
            daemon=True,
            name="predictor-eval",
        ).start()

    def _evaluate_predictor_pending_sync(self) -> None:
        with self._predictor_submit_lock:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._evaluate_last_submitted_bet_for_martingale())
            finally:
                loop.close()
                self._predictor_eval_in_progress = False

    def _schedule_redeem_on_win(self) -> None:
        """Claim resolved winning positions via Polymarket relayer (live only)."""
        if not _redeem_once_available:
            order_logger.warning(
                "auto-redeem: skipped (install py-builder-relayer-client + Builder API creds)"
            )
            return

        def _run() -> None:
            try:
                delay = float(os.getenv("POLYMARKET_REDEEM_ON_WIN_DELAY_SEC", "45"))
            except ValueError:
                delay = 45.0
            delay = max(0.0, min(600.0, delay))
            if delay > 0:
                order_logger.info(
                    f"auto-redeem: win — waiting {delay:.0f}s for redeemable positions"
                )
                time.sleep(delay)
            try:
                ok = redeem_once()
            except Exception as e:
                order_logger.error(f"auto-redeem: win-triggered redeem failed: {e}")
                return
            if ok:
                order_logger.info("auto-redeem: win-triggered redemption batch submitted")
            else:
                order_logger.info(
                    "auto-redeem: win-triggered redeem found nothing yet "
                    "(background loop may retry)"
                )

        threading.Thread(
            target=_run,
            daemon=True,
            name="predictor-redeem-on-win",
        ).start()

    def _predictor_arm_submit_for_next_slug(
        self,
        now_ts: float,
        trade_key_next: tuple,
        order_market: dict,
        *,
        round_idx: Optional[int] = None,
        asset_id: Optional[str] = None,
    ) -> bool:
        """Queue one submit for ``trade_key_next`` if gates pass. Returns True if armed."""
        aid = asset_id or trade_key_parts(trade_key_next, self._primary_asset)[0]
        spot = self._spot_for_asset(aid)
        if spot is None:
            return False
        slug_ts = self._trade_key_slug_ts(trade_key_next)
        with self._predictor_arm_lock:
            if trade_key_next == self.last_trade_time:
                return False
            if trade_key_next in self._predictor_attempted_keys:
                return False
            if trade_key_next in self._predictor_armed_keys:
                return False
            floor_ts = self._predictor_earliest_slug_for(aid)
            if floor_ts and slug_ts > 0 and slug_ts < floor_ts:
                return False
            pending = self._predictor_pending_bet(aid)
            if pending and pending.get("trade_key") == trade_key_next:
                return False
            self._predictor_armed_keys.add(trade_key_next)
        om = dict(order_market)
        if round_idx is None and self._predictor_anchor_slug_ts > 0:
            round_idx = self._predictor_round_index_for_slug(slug_ts)
        secs_until = float(slug_ts) - now_ts
        order_logger.info(
            f"Predictor ({aid}): arming round {round_idx} slug {slug_ts} "
            f"({secs_until:.0f}s until open, stake=${float(self._predictor_clamped_stake(aid)):.2f}, "
            f"spot=${float(spot):,.2f})"
        )
        threading.Thread(
            target=self._make_predictor_trade_sync,
            args=(trade_key_next, om, round_idx),
            daemon=True,
            name=f"predictor-submit-r{round_idx}-{slug_ts}",
        ).start()
        return True

    def _predictor_wall_clock_slots(self, now_ts: float) -> tuple[int, int, float]:
        """(current_slug_start, next_slug_start, seconds_into_current_slug)."""
        iv = MARKET_INTERVAL_SECONDS
        cur = (int(now_ts) // iv) * iv
        return cur, cur + iv, now_ts - float(cur)

    def _predictor_market_for_slug_ts(
        self, slug_start_ts: int, asset_id: Optional[str] = None
    ) -> Optional[dict]:
        aid = asset_id or self._primary_asset
        slug = self._slug_for_asset_ts(aid, slug_start_ts)
        for m in self.all_btc_instruments:
            if m.get("slug") == slug:
                return m
        for m in self.all_btc_instruments:
            if m.get("market_timestamp") == slug_start_ts and asset_from_slug(
                m.get("slug", "")
            ) == aid:
                return m
        return None

    def _predictor_try_scheduled_submits(self, now_ts: float) -> None:
        """
        Odd rounds only (1, 3, 5, 7, …): submit in the last ``SUBMIT_SECONDS_BEFORE_BOUNDARY``
        seconds before that round's slug opens. Round 0 is the current bar at bot start.
        """
        if not self._predictor_enabled or self._martingale_max_loss_stopped:
            return
        if self._predictor_anchor_slug_ts <= 0:
            self._predictor_init_round_schedule(now_ts)

        self._predictor_advance_past_missed_targets(now_ts)

        for aid in self._active_assets:
            if self._spot_for_asset(aid) is None:
                continue
            rnd = self._predictor_next_target_round_for(aid)
            if rnd % 2 == 0:
                rnd += 1
                self._predictor_set_next_target_round_for(aid, rnd)

            target_slug_ts = self._predictor_slug_for_round(rnd)
            secs_until = float(target_slug_ts) - now_ts
            if not (
                SUBMIT_SECONDS_BEFORE_BOUNDARY > 0
                and 0 < secs_until <= float(SUBMIT_SECONDS_BEFORE_BOUNDARY)
            ):
                continue

            floor_ts = self._predictor_earliest_slug_for(aid)
            if floor_ts and target_slug_ts < floor_ts:
                continue

            trade_key = make_trade_key(aid, target_slug_ts, self._multi_asset)
            order_market = self._predictor_market_for_slug_ts(target_slug_ts, aid)
            if order_market is None:
                order_market = {
                    "slug": self._slug_for_asset_ts(aid, target_slug_ts),
                    "market_timestamp": target_slug_ts,
                    "yes_instrument_id": self._yes_instrument_id,
                    "yes_token_id": self._yes_token_id,
                    "asset_id": aid,
                }

            self._predictor_arm_submit_for_next_slug(
                now_ts,
                trade_key,
                order_market,
                round_idx=rnd,
                asset_id=aid,
            )

    def _seconds_to_next_interval_boundary(self) -> float:
        """Return seconds until the next 5-minute UTC boundary (slug alignment)."""
        now_ts = datetime.now(timezone.utc).timestamp()
        next_boundary = (math.floor(now_ts / MARKET_INTERVAL_SECONDS) + 1) * MARKET_INTERVAL_SECONDS
        return next_boundary - now_ts

    def _is_quote_valid(self, bid, ask) -> bool:
        """Return True only when BOTH bid and ask are present and make sense."""
        if bid is None or ask is None:
            return False
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return False
        if b < QUOTE_MIN_SPREAD or a < QUOTE_MIN_SPREAD:
            return False
        if b > 0.999 or a > 0.999:
            return False
        return True

    def _reset_stability(self, reason: str = ""):
        """Mark the market as unstable and reset the counter."""
        if self._market_stable:
            logger.warning(f"Market stability RESET{' – ' + reason if reason else ''}")
        self._market_stable = False
        self._stable_tick_count = 0

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    async def check_simulation_mode(self) -> bool:
        """Check Redis for current simulation mode."""
        if not self.redis_client:
            return self.current_simulation_mode
        try:
            sim_mode = (
                self.redis_client.get('btc_trading:simulation_mode')
                or self.redis_client.get('sol_trading:simulation_mode')
            )
            if sim_mode is not None:
                redis_simulation = sim_mode == '1'
                if redis_simulation != self.current_simulation_mode:
                    self.current_simulation_mode = redis_simulation
                    mode_text = "SIMULATION" if redis_simulation else "LIVE TRADING"
                    logger.warning(f"Trading mode changed to: {mode_text}")
                    if not redis_simulation:
                        logger.warning("LIVE TRADING ACTIVE - Real money at risk!")
                return redis_simulation
        except Exception as e:
            logger.warning(f"Failed to check Redis simulation mode: {e}")
        return self.current_simulation_mode

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self):
        """Called when strategy starts - LOAD ALL MARKETS AND SUBSCRIBE IMMEDIATELY"""
        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY STARTED - FIXED VERSION")
        logger.info("=" * 80)
        assets_cfg = ",".join(self._active_assets)
        logger.info(
            f"Config: interval={MARKET_INTERVAL_SECONDS}s assets=[{assets_cfg}] "
            f"primary_slug_prefix='{BTC_UPDOWN_SLUG_PREFIX}' "
            f"| predictor={'ON' if self._predictor_enabled else 'OFF'} "
            f"(USE_LIGHTWEIGHT_PREDICTOR={os.getenv('USE_LIGHTWEIGHT_PREDICTOR','')!r}) "
            f"| submit_before_next={SUBMIT_SECONDS_BEFORE_BOUNDARY}s"
            + (
                f" submit_after_open={SUBMIT_SECONDS_AFTER_BOUNDARY}s"
                if SUBMIT_SECONDS_AFTER_BOUNDARY <= MARKET_INTERVAL_SECONDS
                else " submit_after_open=OFF"
            )
        )

        # =========================================================================
        # FIX 2: Load ALL BTC instruments at startup
        # =========================================================================
        self._load_all_btc_instruments()

        # =========================================================================
        # FIX 3: Force subscribe to current market IMMEDIATELY
        # =========================================================================
        if self.instrument_id:
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"✓ SUBSCRIBED to market: {self.instrument_id}")
            
            # Try to get current price from cache
            try:
                quote = self.cache.quote_tick(self.instrument_id)
                if quote and quote.bid_price and quote.ask_price:
                    current_price = (quote.bid_price + quote.ask_price) / 2
                    self.price_history.append(current_price)
                    logger.info(f"✓ Initial price: ${float(current_price):.4f}")
            except Exception as e:
                logger.debug(f"No initial price yet: {e}")

        # Generate synthetic history if needed
        if len(self.price_history) < 20:
            self._generate_synthetic_history(target_count=20, existing_count=len(self.price_history))

        # =========================================================================
        # FIX 4: Start the timer loop (but don't rely on it for trading)
        # =========================================================================
        self.run_in_executor(self._start_timer_loop)

        if self._predictor_enabled:
            self._predictor_init_round_schedule()
            self.run_in_executor(self._start_predictor_loop)

        if self.grafana_exporter:
            import threading
            threading.Thread(target=self._start_grafana_sync, daemon=True).start()

        logger.info("=" * 80)
        if self.test_mode and self._predictor_enabled:
            _sum = os.getenv("ORDER_SUMMARY_LOG_FILE")
            _log_path = (
                "disabled"
                if _sum is not None and _sum.strip() == ""
                else (_sum.strip() if _sum else str(project_root / "log.txt"))
            )
            logger.info(
                f"Trade timing: TEST predictor — same as live (simulation); "
                f"odd rounds 1,3,5,7… only; submit last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before "
                f"each target slug opens; "
                f"eval +{self._predictor_eval_post_end_sec()}s after bet slug ends; "
                f"skip {self._predictor_slugs_to_skip_after_submit()} full 5m bar(s) after each submit; "
                f"summary log: {_log_path}"
            )
        elif self.test_mode:
            logger.info(
                "Trade timing: TEST — early quote-tick window (predictor off); "
                "simulation only."
            )
        else:
            if self._predictor_enabled:
                _post_off = SUBMIT_SECONDS_AFTER_BOUNDARY > MARKET_INTERVAL_SECONDS
                logger.info(
                    f"Trade timing: LIVE predictor — place order in the last "
                    f"{SUBMIT_SECONDS_BEFORE_BOUNDARY}s before the *next* 5m slug opens "
                    f"(default 1 minute)."
                    + (
                        " Second path (seconds after current slug open) is disabled — "
                        "set SUBMIT_SECONDS_AFTER_BOUNDARY≤300 in .env to re-enable."
                        if _post_off
                        else f" Also allowed ≥{SUBMIT_SECONDS_AFTER_BOUNDARY}s after current slug open."
                    )
                )
            else:
                logger.info(
                    "Trade timing: LIVE logic — at most one attempt per 5m market, "
                    "only in the last 60s before that market ends (UTC clock from slug start)."
                )
            logger.info(
                "Real orders: run with `python bot.py --live` (default is simulation / paper only)."
            )
        logger.info(
            "Trend gate: mid must be >0.60 (buy YES) or <0.40 (buy NO); 0.40–0.60 skips."
        )
        if self._predictor_enabled:
            pre_txt = ""
            if SUBMIT_SECONDS_BEFORE_BOUNDARY:
                pre_txt = (
                    f" (also: last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before *this* slug opens "
                    f"when elapsed<0)"
                )
            _post_txt = (
                "post-open submit: OFF"
                if SUBMIT_SECONDS_AFTER_BOUNDARY > MARKET_INTERVAL_SECONDS
                else f"post-open submit: ≥{SUBMIT_SECONDS_AFTER_BOUNDARY}s into current slug"
            )
            logger.info(
                f"Lightweight predictor: ON (Coinbase poll={PREDICTOR_POLL_SECONDS}s; "
                f"default submit window: last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before next slug opens"
                f"{pre_txt}; {_post_txt}; "
                f"min_gap={PREDICTOR_MIN_SUBMIT_GAP_SEC:.0f}s between submits; "
                f"Martingale: ${float(MARTINGALE_BASE_USD):.0f} on win; "
                f"on loss double (cap ${float(MARTINGALE_MAX_STAKE_USD):.0f}); "
                f"lose at cap → reset to ${float(MARTINGALE_BASE_USD):.0f}; "
                f"submit → floor next slug (skip {self._predictor_slugs_to_skip_after_submit()} full 5m bar(s) after each submit) → eval → "
                f"next submit in last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before an allowed slug); "
                f"always bets UP/DOWN (ensemble, no skip); "
                f"window={PREDICTOR_WINDOW_SAMPLES} samples; "
                f"5m_candle={'on' if PREDICTOR_USE_5M_CANDLE else 'off'}; "
                f"clob_book={'on' if PREDICTOR_USE_CLOB_BOOK else 'off'}; "
                f"{'contrarian' if PREDICTOR_CONTRARIAN else 'follow-model'} orders"
            )
        logger.info(f"Price history: {len(self.price_history)} points")
        if len(self.price_history) >= 20:
            logger.info("✓ Enough history for decision path")
        else:
            logger.warning(f"⚠ Need more history ({len(self.price_history)}/20)")
        logger.info("=" * 80)

    def _generate_synthetic_history(self, target_count: int = 20, existing_count: int = 0):
        """Generate synthetic price history for testing"""
        if self.price_history:
            base_price = self.price_history[-1]
        else:
            base_price = Decimal("0.5")
        needed = target_count - existing_count
        if needed <= 0:
            return
        for _ in range(needed):
            change = Decimal(str(random.uniform(-0.03, 0.03)))
            new_price = base_price * (Decimal("1.0") + change)
            new_price = max(Decimal("0.01"), min(Decimal("0.99"), new_price))
            self.price_history.append(new_price)
            base_price = new_price

    # ------------------------------------------------------------------
    # Load all BTC instruments at once
    # ------------------------------------------------------------------

    def _load_all_btc_instruments(self):
        """Load ALL BTC instruments from cache and sort by start time"""
        instruments = self.cache.instruments()
        logger.info(f"Loading ALL BTC instruments from {len(instruments)} total...")
        
        now = datetime.now(timezone.utc)
        current_timestamp = int(now.timestamp())
        
        btc_instruments = []
        
        for instrument in instruments:
            try:
                if hasattr(instrument, 'info') and instrument.info:
                    question = instrument.info.get('question', '').lower()
                    slug = instrument.info.get('market_slug', '').lower()
                    
                    if _is_btc_updown_slug(slug):
                        try:
                            timestamp_part = slug.split('-')[-1]
                            market_timestamp = int(timestamp_part)
                            
                            # The slug timestamp IS the market start time (Unix, no offset).
                            # end_date_iso is a DATE-only string (e.g. "2026-02-20"), NOT a datetime,
                            # so parsing it gives midnight UTC which is wrong for intraday markets.
                            # Always derive end_timestamp from the slug: start + interval.
                            real_start_ts = market_timestamp
                            end_timestamp = market_timestamp + MARKET_INTERVAL_SECONDS
                            time_diff = real_start_ts - current_timestamp
                            
                            # Only include markets that haven't ended yet
                            if end_timestamp > current_timestamp:
                                # Extract YES token ID for CLOB order book API.
                                # Nautilus instrument ID format:
                                #   {condition_id}-{token_id}.POLYMARKET
                                # The CLOB /book endpoint only accepts the token_id
                                # (the part after the dash, before .POLYMARKET).
                                raw_id = str(instrument.id)
                                # Strip .POLYMARKET suffix first
                                without_suffix = raw_id.split('.')[0] if '.' in raw_id else raw_id
                                # Then take the token_id after the condition_id dash
                                yes_token_id = without_suffix.split('-')[-1] if '-' in without_suffix else without_suffix

                                btc_instruments.append({
                                    'instrument': instrument,
                                    'slug': slug,
                                    'asset_id': asset_from_slug(slug) or self._primary_asset,
                                    'start_time': datetime.fromtimestamp(real_start_ts, tz=timezone.utc),
                                    'end_time': datetime.fromtimestamp(end_timestamp, tz=timezone.utc),
                                    'market_timestamp': market_timestamp,
                                    'end_timestamp': end_timestamp,
                                    'time_diff_minutes': time_diff / 60,
                                    'yes_token_id': yes_token_id,
                                })
                        except (ValueError, IndexError):
                            continue
            except Exception:
                continue
        
        # Pair YES and NO tokens by slug.
        # Each Polymarket market has two tokens loaded as separate Nautilus instruments.
        # The first instrument found for a slug is stored as the primary (YES/UP).
        # The second instrument found for the same slug is the NO/DOWN token.
        seen_slugs = {}
        deduped = []
        for inst in btc_instruments:
            slug = inst['slug']
            if slug not in seen_slugs:
                # First token seen = YES (UP)
                inst['yes_instrument_id'] = inst['instrument'].id
                inst['no_instrument_id'] = None  # will be filled when second token found
                seen_slugs[slug] = inst
                deduped.append(inst)
            else:
                # Second token seen = NO (DOWN) — store it on the existing entry
                seen_slugs[slug]['no_instrument_id'] = inst['instrument'].id
        btc_instruments = deduped
        
        # Sort by start time (absolute timestamp, not time-of-day)
        btc_instruments.sort(key=lambda x: x['market_timestamp'])
        
        logger.info("=" * 80)
        logger.info(f"FOUND {len(btc_instruments)} 5-MIN UP/DOWN MARKETS:")
        for i, inst in enumerate(btc_instruments):
            # A market is ACTIVE if it has started AND not yet ended
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            status = "ACTIVE" if is_active else "FUTURE" if inst['time_diff_minutes'] > 0 else "PAST"
            logger.info(f"  [{i}] {inst['slug']}: {status} (starts at {inst['start_time'].strftime('%H:%M:%S')}, ends at {inst['end_time'].strftime('%H:%M:%S')})")
        logger.info("=" * 80)
        
        self.all_btc_instruments = btc_instruments
        
        # Find current market and SUBSCRIBE IMMEDIATELY
        # FIXED: A market is current if it has STARTED and not yet ENDED (use end_time, not a hardcoded window)
        for i, inst in enumerate(btc_instruments):
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            if is_active:
                self.current_instrument_index = i
                self.instrument_id = inst['instrument'].id
                self.next_switch_time = inst['end_time']
                self._yes_token_id = inst.get('yes_token_id')
                self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
                self._no_instrument_id = inst.get('no_instrument_id')
                logger.info(f"✓ CURRENT MARKET: {inst['slug']} (index {i})")
                logger.info(f"  Next switch at: {self.next_switch_time.strftime('%H:%M:%S')}")
                logger.info(f"  YES token: {self._yes_token_id[:16]}…" if self._yes_token_id else "  YES token: unknown")
                
                # =========================================================================
                # CRITICAL FIX: Subscribe immediately!
                # =========================================================================
                self.subscribe_quote_ticks(self.instrument_id)
                logger.info(f"  ✓ SUBSCRIBED to current market")
                break
        
        if self.current_instrument_index == -1 and btc_instruments:
            # No currently-active market — find the NEAREST upcoming one
            # (smallest positive time_diff_minutes = starts soonest)
            future_markets = [inst for inst in btc_instruments if inst['time_diff_minutes'] > 0]
            if future_markets:
                nearest = min(future_markets, key=lambda x: x['time_diff_minutes'])
                nearest_idx = btc_instruments.index(nearest)
            else:
                # All markets are in the past — use the last one
                nearest = btc_instruments[-1]
                nearest_idx = len(btc_instruments) - 1

            self.current_instrument_index = nearest_idx
            inst = nearest
            self.instrument_id = inst['instrument'].id
            self._yes_token_id = inst.get('yes_token_id')
            self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
            self._no_instrument_id = inst.get('no_instrument_id')
            self.next_switch_time = inst['start_time']  # switch_time = when it OPENS
            logger.info(f"⚠ NO CURRENT MARKET - WAITING FOR NEAREST FUTURE: {inst['slug']}")
            logger.info(f"  Starts in {inst['time_diff_minutes']:.1f} min at {self.next_switch_time.strftime('%H:%M:%S')} UTC")

            # Subscribe so we get ticks when it opens
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"  ✓ SUBSCRIBED to future market")
            # Block trading until the market actually opens (timer loop sets _market_open flag)
            self._waiting_for_market_open = True
            
    def _switch_to_next_market(self):
        """Switch to the next market in the pre-loaded list"""
        if not self.all_btc_instruments:
            logger.error("No instruments loaded!")
            return False
        
        next_index = self.current_instrument_index + 1
        if next_index >= len(self.all_btc_instruments):
            last_slug = self.all_btc_instruments[-1].get('slug', '?') if self.all_btc_instruments else '?'
            logger.warning(
                f"No more markets available — last slug was {last_slug}. "
                f"Requesting graceful restart to reload fresh Gamma slug list "
                f"(BTC_SLUG_HOURS_AHEAD={os.getenv('BTC_SLUG_HOURS_AHEAD') or os.getenv('SOL_SLUG_HOURS_AHEAD', str(BTC_SLUG_HOURS_AHEAD_DEFAULT))})"
            )
            request_graceful_trading_node_stop("markets-exhausted")
            return False
        
        next_market = self.all_btc_instruments[next_index]
        now = datetime.now(timezone.utc)
        
        # Check if next market is ready
        if now < next_market['start_time']:
            logger.info(f"Waiting for next market at {next_market['start_time'].strftime('%H:%M:%S')}")
            return False
        
        # Switch to next market
        self.current_instrument_index = next_index
        self.instrument_id = next_market['instrument'].id
        self.next_switch_time = next_market['end_time']
        self._yes_token_id = next_market.get('yes_token_id')
        self._yes_instrument_id = next_market.get('yes_instrument_id', next_market['instrument'].id)
        self._no_instrument_id = next_market.get('no_instrument_id')
        
        logger.info("=" * 80)
        logger.info(f"SWITCHING TO NEXT MARKET: {next_market['slug']}")
        logger.info(f"  Current time: {now.strftime('%H:%M:%S')}")
        logger.info(f"  Market ends at: {self.next_switch_time.strftime('%H:%M:%S')}")
        logger.info("=" * 80)
        
        # =========================================================================
        # FIX 5: Force stability for new market and reset trade timer correctly
        # =========================================================================
        self._stable_tick_count = QUOTE_STABILITY_REQUIRED  # Force stable immediately
        self._market_stable = True
        self._waiting_for_market_open = False  # Market is now active
        
        # Non-predictor late-window uses last_trade_time=-1 to arm the next tick; predictor dedup
        # must not be wiped here or we re-open a submit every market switch.
        if not self._predictor_enabled:
            self.last_trade_time = -1
            logger.info("  Trade timer reset — will trade on next tick")
        else:
            logger.info("  Predictor: last_trade_time left unchanged across market switch")
        
        self.subscribe_quote_ticks(self.instrument_id)
        return True

    # ------------------------------------------------------------------
    # Timer loop - SIMPLIFIED
    # ------------------------------------------------------------------

    def _start_timer_loop(self):
        """Start timer loop in executor"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._timer_loop())
        finally:
            loop.close()

    # ------------------------------------------------------------------
    # Predictor loop (Coinbase trades)
    # ------------------------------------------------------------------
    def _start_predictor_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._predictor_loop())
        finally:
            loop.close()

    async def _predictor_loop(self):
        """
        Poll Coinbase for current price + recent trades and feed the lightweight predictor.
        """
        from data_sources.coinbase.adapter import CoinbaseDataSource

        clients: Dict[str, Any] = {}
        for aid in self._active_assets:
            src = CoinbaseDataSource(product_id=coinbase_product_for_asset(aid))
            if await src.connect():
                clients[aid] = src
            else:
                order_logger.warning(f"Predictor: cannot connect Coinbase for {aid}")

        if not clients:
            logger.warning("Lightweight predictor disabled: cannot connect to Coinbase")
            self._predictor_enabled = False
            return

        try:
            while True:
                if _graceful_shutdown_requested:
                    await asyncio.sleep(0.5)
                    continue
                for aid, coinbase in clients.items():
                    try:
                        price = await coinbase.get_current_price()
                        trades = await coinbase.get_recent_trades(limit=80)

                        last_poll = self._predictor_last_trade_poll_ts_by_asset.get(aid)
                        delta_buy = 0.0
                        delta_sell = 0.0
                        newest_ts = last_poll
                        for t in trades:
                            ts = t.get("timestamp")
                            if ts is None:
                                continue
                            if last_poll is not None and ts <= last_poll:
                                continue
                            sz = float(t.get("size") or 0.0)
                            side = str(t.get("side") or "").lower()
                            if side == "buy":
                                delta_buy += sz
                            elif side == "sell":
                                delta_sell += sz
                            if newest_ts is None or ts > newest_ts:
                                newest_ts = ts

                        if price is not None:
                            spot_dec = Decimal(str(price))
                            self._latest_spot_by_asset[aid] = spot_dec
                            self._latest_buy_vol_by_asset[aid] = delta_buy
                            self._latest_sell_vol_by_asset[aid] = delta_sell
                            self._predictors[aid].update(float(price), delta_buy, delta_sell)
                            if newest_ts is not None:
                                self._predictor_last_trade_poll_ts_by_asset[aid] = newest_ts
                    except Exception as e:
                        logger.debug(f"Predictor loop error ({aid}, non-fatal): {e}")

                primary = self._primary_asset
                self._latest_spot_price = self._latest_spot_by_asset.get(primary)
                self._latest_buy_vol = self._latest_buy_vol_by_asset.get(primary, 0.0)
                self._latest_sell_vol = self._latest_sell_vol_by_asset.get(primary, 0.0)

                await asyncio.sleep(PREDICTOR_POLL_SECONDS)
        finally:
            for coinbase in clients.values():
                await coinbase.disconnect()

    async def _timer_loop(self):
        """
        Timer loop: checks every 10 seconds if it's time to switch markets.
        Also handles the case where we're waiting for a future market to open.
        """
        logger.info("Timer loop started")
        _last_heartbeat = 0.0
        while True:
            # --- auto-restart check ---
            uptime_minutes = (datetime.now(timezone.utc) - self.bot_start_time).total_seconds() / 60
            if uptime_minutes >= self.restart_after_minutes:
                logger.warning("AUTO-RESTART TIME - Loading fresh filters")
                request_graceful_trading_node_stop("auto-restart")
                return

            now = datetime.now(timezone.utc)
            if now.timestamp() - _last_heartbeat >= 30:
                _last_heartbeat = now.timestamp()
                try:
                    if (
                        self.current_instrument_index >= 0
                        and self.current_instrument_index < len(self.all_btc_instruments)
                    ):
                        cm = self.all_btc_instruments[self.current_instrument_index]
                        ms = cm["market_timestamp"]
                        elapsed = now.timestamp() - ms
                        if elapsed >= 0:
                            sub_i = int(elapsed // MARKET_INTERVAL_SECONDS)
                            sec_into = elapsed % MARKET_INTERVAL_SECONDS
                            logger.info(
                                f"[HB] market={cm['slug']} sub={sub_i} t+{sec_into:.1f}s "
                                f"predictor={'ON' if self._predictor_enabled else 'OFF'}"
                            )
                except Exception:
                    pass

            if self.next_switch_time and now >= self.next_switch_time:
                if self._waiting_for_market_open:
                    # The future market we were waiting for has now opened
                    # Treat it like a market switch so trade timer resets
                    logger.info("=" * 80)
                    logger.info(f"⏰ WAITING MARKET NOW OPEN: {now.strftime('%H:%M:%S')} UTC")
                    logger.info("=" * 80)
                    # Update next_switch_time to the market's END time
                    if (self.current_instrument_index >= 0 and
                            self.current_instrument_index < len(self.all_btc_instruments)):
                        current_market = self.all_btc_instruments[self.current_instrument_index]
                        self.next_switch_time = current_market['end_time']
                        logger.info(f"  Market ends at {self.next_switch_time.strftime('%H:%M:%S')} UTC")
                    self._waiting_for_market_open = False
                    self._market_stable = True
                    self._stable_tick_count = QUOTE_STABILITY_REQUIRED
                    if not self._predictor_enabled:
                        self.last_trade_time = -1  # Arm non-predictor late-window only
                    logger.info("  ✓ MARKET OPEN — ready to trade on next tick")
                else:
                    # Normal market switch
                    self._switch_to_next_market()

            # Predictor: eval pending bets + wall-clock submit windows (UTC 5m).
            try:
                if self._predictor_enabled:
                    self._schedule_predictor_eval_if_ready()
                    self._predictor_try_scheduled_submits(now.timestamp())
            except Exception as e:
                logger.warning(f"Predictor timer submit error: {e}")

            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # Quote tick handler - SIMPLIFIED
    # ------------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick):
        """Handle quote tick — trade in the configured window each 5m market."""
        try:
            # Only process ticks from current instrument
            if self.instrument_id is None or tick.instrument_id != self.instrument_id:
                return

            now = datetime.now(timezone.utc)
            bid = tick.bid_price
            ask = tick.ask_price

            if bid is None or ask is None:
                return
                
            try:
                bid_decimal = bid.as_decimal()
                ask_decimal = ask.as_decimal()
            except:
                return

            # Always store price history
            mid_price = (bid_decimal + ask_decimal) / 2
            self.price_history.append(mid_price)
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)
            
            # Store latest bid/ask for liquidity check before order placement
            self._last_bid_ask = (bid_decimal, ask_decimal)

            # Tick buffer for TickVelocityProcessor (rolling 90s window)
            self._tick_buffer.append({'ts': now, 'price': mid_price})

            # Stability gate
            if not self._market_stable:
                self._stable_tick_count += 1
                if self._stable_tick_count >= QUOTE_STABILITY_REQUIRED:
                    self._market_stable = True
                    logger.info(f"✓ Market STABLE ({self._stable_tick_count} ticks)")
                else:
                    return

            # =========================================================================
            # FIXED TRADING LOGIC:
            #
            # We trade at most once per 5-min market (slug interval = MARKET_INTERVAL_SECONDS).
            # Trade key = (market_start_timestamp, sub_interval) where sub_interval counts
            # full intervals since open (usually 0 for a single 5m slug window).
            #
            # If _waiting_for_market_open is True (started before market opens),
            # we block trading until the timer loop opens the market.
            # =========================================================================

            # Block trading if waiting for a future market to open
            if self._waiting_for_market_open:
                return

            # Get current market info
            if (self.current_instrument_index < 0 or
                    self.current_instrument_index >= len(self.all_btc_instruments)):
                return

            current_market = self.all_btc_instruments[self.current_instrument_index]
            market_start_ts = current_market['market_timestamp']  # Slug timestamp = market start (Unix)

            # How many 5-min intervals have elapsed since this market opened?
            elapsed_secs = now.timestamp() - market_start_ts
            if elapsed_secs < 0:
                # Market hasn't started yet — block
                return

            sub_interval = int(elapsed_secs // MARKET_INTERVAL_SECONDS)

            # Unique trade key: (market_start_timestamp, sub_interval)
            trade_key = (market_start_ts, sub_interval)

            # =========================================================================
            # TRADE WINDOW (5m markets):
            #   LIVE: last 60s of the 300s window (240–300s) before slug end.
            #   TEST: early window 15–180s for faster paper runs.
            #
            # TREND FILTER (in _make_trading_decision):
            #   Price > 0.60 → buy YES; < 0.40 → buy NO; 0.40–0.60 → skip.
            # =========================================================================
            seconds_into_sub_interval = elapsed_secs % MARKET_INTERVAL_SECONDS
            if self._predictor_enabled:
                # Predictor submissions use the timer loop (live and --test-mode).
                return
            if self.test_mode:
                TRADE_WINDOW_START = 15
                TRADE_WINDOW_END = 180
            else:
                TRADE_WINDOW_START = 240   # last minute of 5m market
                TRADE_WINDOW_END = 300

            if TRADE_WINDOW_START <= seconds_into_sub_interval < TRADE_WINDOW_END and trade_key != self.last_trade_time:
                self.last_trade_time = trade_key

                logger.info("=" * 80)
                logger.info(f" LATE-WINDOW TRADE: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info(f"   Market: {current_market['slug']}")
                logger.info(f"   Sub-interval #{sub_interval} ({seconds_into_sub_interval:.1f}s in = {seconds_into_sub_interval/60:.1f} min)")
                logger.info(f"   Price: ${float(mid_price):,.4f} | Bid: ${float(bid_decimal):,.4f} | Ask: ${float(ask_decimal):,.4f}")
                logger.info(f"   Trend strength: {'STRONG ✓' if float(mid_price) > 0.60 or float(mid_price) < 0.40 else 'WEAK — may skip'}")
                logger.info(f"   Price history: {len(self.price_history)} points")
                logger.info("=" * 80)

                # Live + predictor: orders come only from the timer loop (never quote ticks).
                self.run_in_executor(lambda: self._make_trading_decision_sync(float(mid_price)))

        except Exception as e:
            logger.error(f"Error processing quote tick: {e}")

    def _make_predictor_trade_sync(self, trade_key, order_market=None, round_idx: Optional[int] = None):
        # Hold the lock for the entire submit coroutine (timer thread must not use run_in_executor).
        try:
            with self._predictor_submit_lock:
                now_ts = time.time()
                gap = PREDICTOR_MIN_SUBMIT_GAP_SEC
                if self._predictor_last_submit_ts > 0 and (now_ts - self._predictor_last_submit_ts) < gap:
                    logger.info(
                        f"Predictor: skip — min {gap:.0f}s between submits "
                        f"({now_ts - self._predictor_last_submit_ts:.1f}s since last)"
                    )
                    return
                cutoff = int(time.time()) - 4 * MARKET_INTERVAL_SECONDS
                self._predictor_attempted_keys = {
                    k
                    for k in self._predictor_attempted_keys
                    if self._trade_key_slug_ts(k) >= cutoff
                }
                if trade_key in self._predictor_attempted_keys:
                    logger.info(f"Predictor: skip — already handled round {trade_key!r}")
                    return
                aid, slug_ts = trade_key_parts(trade_key, self._primary_asset)
                floor_ts = self._predictor_earliest_slug_for(aid)
                if floor_ts and slug_ts > 0 and slug_ts < floor_ts:
                    order_logger.info(
                        f"Predictor: skip — order slug start {slug_ts} < next allowed {floor_ts} "
                        f"(skip {self._predictor_slugs_to_skip_after_submit()} full 5m bar(s) after last "
                        f"submit; next arm at +{PREDICTOR_SUBMIT_AFTER_ROUND_START_SEC}s into a later slug)"
                    )
                    return
                pending = self._predictor_pending_bet(aid)
                if pending and pending.get("trade_key") == trade_key:
                    order_logger.info(
                        f"Predictor: skip — already have open bet for slug {trade_key!r}"
                    )
                    return

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        self._make_predictor_trade(trade_key, order_market, round_idx=round_idx)
                    )
                finally:
                    loop.close()
        finally:
            with self._predictor_arm_lock:
                self._predictor_armed_keys.discard(trade_key)

    def _predictor_clamped_stake(self, asset_id: Optional[str] = None) -> Decimal:
        """Dollar size for the next predictor order: stake clamped to [base, max]."""
        aid = asset_id or self._primary_asset
        if self._multi_asset:
            stake = self._stake_usd_by_asset.get(aid, MARTINGALE_BASE_USD)
        else:
            stake = self._stake_usd
        return max(MARTINGALE_BASE_USD, min(stake, MARTINGALE_MAX_STAKE_USD))

    def _predictor_apply_martingale_from_result(
        self, won: bool, stake_just_risked: Decimal, asset_id: Optional[str] = None
    ) -> bool:
        """
        Update stake after a scored bet.

        - **Win** → ``MARTINGALE_BASE_USD``.
        - **Loss** → double ``stake_just_risked``, capped at ``MARTINGALE_MAX_STAKE_USD``.
        - **Loss at cap** → reset to ``MARTINGALE_BASE_USD`` (restart ladder).
        """
        aid = asset_id or self._primary_asset

        def _set_stake(val: Decimal) -> None:
            if self._multi_asset:
                self._stake_usd_by_asset[aid] = val
            else:
                self._stake_usd = val

        if won:
            _set_stake(MARTINGALE_BASE_USD)
            return False
        if stake_just_risked >= MARTINGALE_MAX_STAKE_USD:
            _set_stake(MARTINGALE_BASE_USD)
            return False
        _set_stake(min(stake_just_risked * Decimal(2), MARTINGALE_MAX_STAKE_USD))
        return False

    def _predictor_seed_last_trade_after_eval(
        self, asset_id: str, scored_slug_start_ts: int
    ) -> None:
        """
        After scoring (or clearing) the bet for slug ``scored_slug_start_ts``, set
        ``last_trade_time`` so the timer does not double-fire until the next allowed pre-open
        window. The earliest slug for the *next* order is advanced on successful **submit** (see
        ``_make_predictor_trade``), not here, so we never target the consecutive bar even if eval
        is still pending.
        """
        if scored_slug_start_ts > 0:
            self.last_trade_time = make_trade_key(
                asset_id, scored_slug_start_ts, self._multi_asset
            )
        else:
            self.last_trade_time = -1

    async def _predictor_yes_token_for_slug(
        self, slug_start_ts: int, asset_id: Optional[str] = None
    ) -> Optional[str]:
        """Verified UP (Yes) CLOB token from Gamma for a slug."""
        import httpx
        import json as _json

        aid = asset_id or self._primary_asset
        slug = self._slug_for_asset_ts(aid, slug_start_ts)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": slug},
                )
                resp.raise_for_status()
                data = resp.json()
            if not data:
                return None
            raw = data[0].get("clobTokenIds")
            if not raw:
                return None
            tokens = _json.loads(raw) if isinstance(raw, str) else raw
            return str(tokens[0]) if tokens else None
        except Exception as e:
            order_logger.debug(f"Predictor token lookup failed for {slug}: {e}")
            return None

    async def _predictor_fetch_clob_imbalance(self, yes_token_id: str) -> Optional[float]:
        """
        Polymarket YES order book skew: (bid_usd - ask_usd) / total.
        Positive = more buyers of UP (bullish on the round).
        """
        import httpx

        if not yes_token_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://clob.polymarket.com/book",
                    params={"token_id": yes_token_id},
                )
                if resp.status_code != 200:
                    return None
                book = resp.json()
            bid_vol = 0.0
            ask_vol = 0.0
            for level in book.get("bids", [])[:10]:
                try:
                    bid_vol += float(level["price"]) * float(level["size"])
                except (KeyError, TypeError, ValueError):
                    continue
            for level in book.get("asks", [])[:10]:
                try:
                    ask_vol += float(level["price"]) * float(level["size"])
                except (KeyError, TypeError, ValueError):
                    continue
            total = bid_vol + ask_vol
            if total < 50.0:
                return None
            return (bid_vol - ask_vol) / total
        except Exception as e:
            order_logger.debug(f"Predictor CLOB book fetch failed: {e}")
            return None

    async def _predictor_fetch_candle_context(
        self, target_slug_ts: int, asset_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Three 5m bars before the target slug opens:
        - bar_m2, bar_m1: last two *closed* 5m candles
        - cur_5m_return: leading bar (ends when target opens) — spot vs its open
        """
        if not PREDICTOR_USE_5M_CANDLE or target_slug_ts <= 0:
            return None
        aid = asset_id or self._primary_asset
        spot = self._spot_for_asset(aid)
        if spot is None:
            return None

        cur_bar = int(target_slug_ts) - MARKET_INTERVAL_SECONDS
        bar_m1 = cur_bar - MARKET_INTERVAL_SECONDS
        bar_m2 = bar_m1 - MARKET_INTERVAL_SECONDS
        from data_sources.coinbase.adapter import CoinbaseDataSource

        src = CoinbaseDataSource(product_id=coinbase_product_for_asset(aid))
        ctx: dict = {}
        try:
            if not await src.connect():
                return None
            m2_oc = await src.fetch_candle_open_close_for_bucket(
                bar_m2, MARKET_INTERVAL_SECONDS
            )
            m1_oc = await src.fetch_candle_open_close_for_bucket(
                bar_m1, MARKET_INTERVAL_SECONDS
            )
            cur_oc = await src.fetch_candle_open_close_for_bucket(
                cur_bar, MARKET_INTERVAL_SECONDS
            )
            if m2_oc:
                ctx["bar_m2_return"] = LightweightPredictor._signed_return(
                    float(m2_oc[0]), float(m2_oc[1])
                )
            if m1_oc:
                ctx["bar_m1_return"] = LightweightPredictor._signed_return(
                    float(m1_oc[0]), float(m1_oc[1])
                )
                ctx["prev_5m_return"] = ctx["bar_m1_return"]
            if cur_oc:
                ctx["cur_5m_return"] = LightweightPredictor._signed_return(
                    float(cur_oc[0]), float(spot)
                )
        except Exception as e:
            order_logger.debug(f"Predictor candle context failed: {e}")
            return None
        finally:
            await src.disconnect()
        return ctx or None

    async def _make_predictor_trade(
        self, trade_key, order_market=None, round_idx: Optional[int] = None
    ):
        """
        Martingale on odd rounds only (1, 3, 5, 7, …). Round 0 = anchor bar at bot start.

        Submit in the last ``SUBMIT_SECONDS_BEFORE_BOUNDARY`` seconds before the target slug opens.
        Win → ``$1`` on the next odd round; loss → double on the next odd round (+2); loss at
        ``MARTINGALE_MAX_STAKE_USD`` → stop bot.
        """
        try:
            if self._martingale_max_loss_stopped:
                return

            is_simulation = await self.check_simulation_mode()
            asset_id, slug_ts = trade_key_parts(trade_key or (), self._primary_asset)
            if order_market and order_market.get("asset_id"):
                asset_id = str(order_market["asset_id"]).lower()
            predictor = self._predictors.get(asset_id, self._predictor)
            logger.info(
                f"Predictor ({asset_id}) mode: {'SIMULATION' if is_simulation else 'LIVE'}"
            )

            spot = self._spot_for_asset(asset_id)
            if spot is None:
                logger.warning(
                    f"Predictor ({asset_id}): no Coinbase spot yet — "
                    "this round will not retry (one shot per round)"
                )
                return

            candle_ctx = None
            clob_imb = None
            yes_token = None
            if order_market:
                yes_token = order_market.get("yes_token_id")
            if slug_ts > 0:
                candle_ctx = await self._predictor_fetch_candle_context(slug_ts, asset_id)
                if candle_ctx:
                    order_logger.info(
                        f"Predictor ({asset_id}) 5m: m2={candle_ctx.get('bar_m2_return', 0):.4%} "
                        f"m1={candle_ctx.get('bar_m1_return', 0):.4%} "
                        f"lead={candle_ctx.get('cur_5m_return', 0):.4%}"
                    )
                if PREDICTOR_USE_CLOB_BOOK:
                    if not yes_token:
                        yes_token = await self._predictor_yes_token_for_slug(slug_ts, asset_id)
                    if yes_token:
                        clob_imb = await self._predictor_fetch_clob_imbalance(yes_token)
                        if clob_imb is not None:
                            order_logger.info(
                                f"Predictor ({asset_id}) CLOB book imbalance (UP token): {clob_imb:+.3f}"
                            )

            signal, score = predictor.predict(candle_ctx, clob_imb)
            if signal not in ("UP", "DOWN"):
                prices = list(predictor.prices)
                if len(prices) >= 2:
                    move = prices[-1] - prices[-2]
                    signal = "UP" if move >= 0 else "DOWN"
                    score = round(move, 4)
                else:
                    signal = "UP"
                    score = 0.0
            if not self._predictor_passes_quality_gates(
                signal, score, candle_ctx, asset_id=asset_id
            ):
                return

            conf = "strong" if abs(score) >= PREDICTOR_MIN_SCORE else "weak"
            order_logger.info(
                f"Predictor ({asset_id}): {signal} (score={score}, confidence={conf})"
            )

            if PREDICTOR_CONTRARIAN:
                bet_outcome = "DOWN" if signal == "UP" else "UP"
                mode_note = "contrarian (fade model)"
            else:
                bet_outcome = signal
                mode_note = "follow model"
            direction = "long" if bet_outcome == "UP" else "short"
            stake = self._predictor_clamped_stake(asset_id)
            buy_v = self._latest_buy_vol_by_asset.get(asset_id, self._latest_buy_vol)
            sell_v = self._latest_sell_vol_by_asset.get(asset_id, self._latest_sell_vol)

            order_logger.info("=" * 80)
            order_logger.info(f"PREDICTOR TRADE ({asset_id.upper()})")
            order_logger.info(
                f"  Model: {signal} | Score: {score} | {mode_note} → Order: {bet_outcome}"
            )
            order_logger.info(
                f"  Spot: ${float(spot):,.2f} | BuyVol={buy_v:.4f} SellVol={sell_v:.4f}"
            )
            order_logger.info(f"  Stake: ${float(stake):.2f}")
            if round_idx is not None:
                order_logger.info(f"  Target round: {round_idx} (odd ladder 1,3,5,7,…)")
            order_logger.info("=" * 80)

            if order_market:
                order_logger.info(
                    f"  Early/next market: {order_market.get('slug', '')!r} "
                    f"(YES={order_market.get('yes_instrument_id')})"
                )

            placed_ok = False
            if is_simulation:
                logger.warning(
                    "Predictor: SIMULATION — logging to log.txt only (no CLOB submit). "
                    "Use `--live` for real orders."
                )
                placed_ok = True
            else:
                os.environ["MARKET_BUY_USD"] = f"{float(stake):.2f}"
                fused = self._synthetic_fused_for_trend(
                    Decimal("0.61") if direction == "long" else Decimal("0.39")
                )
                placed_ok = await self._place_real_order(
                    fused, stake, Decimal("0.50"), direction, order_market=order_market
                )

            if not placed_ok:
                return

            self.last_trade_time = trade_key
            self._predictor_attempted_keys.add(trade_key)
            self._predictor_last_submit_ts = time.time()

            _sub_slug = slug_ts
            if round_idx is None and _sub_slug > 0 and self._predictor_anchor_slug_ts > 0:
                round_idx = self._predictor_round_index_for_slug(_sub_slug)

            if round_idx is not None and round_idx % 2 == 1:
                next_rnd = int(round_idx) + 2
                self._predictor_set_next_target_round_for(asset_id, next_rnd)
                self._predictor_set_earliest_slug_for(
                    asset_id, self._predictor_slug_for_round(next_rnd)
                )
                if asset_id == self._primary_asset:
                    self._predictor_next_target_round = next_rnd
                    self._predictor_earliest_next_order_slug_ts = (
                        self._predictor_slug_for_round(next_rnd)
                    )
                order_logger.info(
                    f"Predictor ({asset_id}): next target round {next_rnd} "
                    f"(slug {self._predictor_slug_for_round(next_rnd)}) after round {round_idx} submit"
                )
            elif _sub_slug > 0:
                self._predictor_set_earliest_slug_for(
                    asset_id, _sub_slug + 2 * MARKET_INTERVAL_SECONDS
                )
                if asset_id == self._primary_asset:
                    self._predictor_earliest_next_order_slug_ts = (
                        _sub_slug + 2 * MARKET_INTERVAL_SECONDS
                    )

            append_predictor_order_summary_line(
                trade_key,
                bet_outcome,
                stake,
                "pending",
                phase="submit",
                model_signal=signal,
                round_idx=round_idx,
            )

            self._predictor_store_pending_bet(
                asset_id,
                {
                    "signal": signal,
                    "bet_outcome": bet_outcome,
                    "spot_entry": spot,
                    "stake": stake,
                    "trade_key": trade_key,
                    "round_idx": round_idx,
                    "ts": datetime.now(timezone.utc),
                    "yes_token_id": yes_token or self._yes_token_id,
                    "asset_id": asset_id,
                },
            )
        except Exception as e:
            logger.error(f"Predictor trade failed: {e}")
            import traceback

            traceback.print_exc()

    async def _fetch_polymarket_resolution(
        self,
        slug_start_ts: int,
        asset_id: Optional[str] = None,
        timeout_sec: float = 120.0,
        poll_sec: float = 5.0,
    ) -> Optional[str]:
        """
        Determine resolution using CLOB /last-trade-price with verified token mapping.

        Strategy:
        1. Query Gamma /markets to get the verified UP token (clobTokenIds[0] = Up).
        2. Check CLOB /last-trade-price for the verified UP token.
        3. Price >= 0.95 means UP won; price <= 0.05 means DOWN won.
        4. If Gamma unavailable, use Gamma outcomePrices or CLOB with stored token.
        """
        import httpx
        import json as _json

        aid = asset_id or self._primary_asset
        slug = self._slug_for_asset_ts(aid, slug_start_ts)
        start = time.time()
        attempt = 0
        verified_up_token: Optional[str] = None

        while (time.time() - start) < timeout_sec:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Step 1: Get verified UP token from Gamma
                    gamma_resp = await client.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"slug": slug},
                    )
                    if gamma_resp.status_code == 200:
                        data = gamma_resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            market = data[0]
                            raw_tokens = market.get("clobTokenIds")
                            raw_prices = market.get("outcomePrices")

                            # Get verified UP token (outcomes[0] = "Up" = clobTokenIds[0])
                            if raw_tokens:
                                tokens = _json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
                                if len(tokens) >= 2:
                                    verified_up_token = tokens[0]

                            # Check Gamma outcomePrices directly
                            if raw_prices:
                                prices = _json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                                if len(prices) >= 2:
                                    up_f = float(prices[0])
                                    down_f = float(prices[1])
                                    if up_f >= 0.95 or down_f >= 0.95:
                                        result = "UP" if up_f > down_f else "DOWN"
                                        order_logger.info(
                                            f"Polymarket resolution: {slug} -> {result} "
                                            f"(Gamma prices=[{prices[0]},{prices[1]}], "
                                            f"attempt {attempt}, {time.time() - start:.0f}s)"
                                        )
                                        return result

                    # Step 2: Check CLOB /last-trade-price with verified UP token
                    up_token = verified_up_token
                    if up_token:
                        resp = await client.get(
                            "https://clob.polymarket.com/last-trade-price",
                            params={"token_id": up_token},
                        )
                        if resp.status_code == 200:
                            price_data = resp.json()
                            price = float(price_data.get("price", 0.5))
                            if price >= 0.95:
                                order_logger.info(
                                    f"Polymarket resolution: {slug} -> UP "
                                    f"(CLOB last_trade={price:.3f}, "
                                    f"attempt {attempt}, {time.time() - start:.0f}s)"
                                )
                                return "UP"
                            elif price <= 0.05:
                                order_logger.info(
                                    f"Polymarket resolution: {slug} -> DOWN "
                                    f"(CLOB last_trade={price:.3f}, "
                                    f"attempt {attempt}, {time.time() - start:.0f}s)"
                                )
                                return "DOWN"
                            else:
                                if attempt == 1 or attempt % 4 == 0:
                                    order_logger.info(
                                        f"Polymarket resolution: {slug} not resolved yet "
                                        f"(CLOB last_trade={price:.3f}, attempt {attempt}, "
                                        f"{time.time() - start:.0f}s)"
                                    )
                    elif attempt == 1 or attempt % 4 == 0:
                        order_logger.info(
                            f"Polymarket resolution: {slug} waiting for data "
                            f"(attempt {attempt}, {time.time() - start:.0f}s)"
                        )
            except Exception as e:
                order_logger.debug(
                    f"Polymarket resolution: fetch error attempt {attempt}: {e}"
                )
            await asyncio.sleep(poll_sec)

        order_logger.warning(
            f"Polymarket resolution: {slug} not resolved after {timeout_sec:.0f}s — "
            f"falling back to Coinbase candle"
        )
        return None

    async def _evaluate_last_submitted_bet_for_martingale(self) -> None:
        """
        Score the last predictor bet after the slug interval has ended (wall clock) plus a short
        post-end buffer so candles / settlement are available.
        """
        now_wall = time.time()
        asset_id: Optional[str] = None
        pred: Optional[Dict[str, Any]] = None
        for aid in self._active_assets:
            p = self._predictor_pending_bet(aid)
            if not p:
                continue
            tk0 = p.get("trade_key")
            if not isinstance(tk0, tuple):
                continue
            slug_ts = self._trade_key_slug_ts(tk0)
            if slug_ts <= 0:
                continue
            earliest = slug_ts + MARKET_INTERVAL_SECONDS + self._predictor_eval_post_end_sec()
            if now_wall < earliest:
                continue
            if pred is None:
                asset_id = aid
                pred = p
            elif slug_ts < self._trade_key_slug_ts(pred.get("trade_key") or ()):
                asset_id = aid
                pred = p

        if not pred or not asset_id:
            logger.debug("Predictor martingale eval: no pending bet ready")
            return

        tk = pred.get("trade_key")
        if not isinstance(tk, tuple) or len(tk) < 1:
            tk = make_trade_key(asset_id, 0, self._multi_asset)
        interval_start_ts = self._trade_key_slug_ts(tk)

        now_spot = self._spot_for_asset(asset_id)
        mode = PREDICTOR_RESOLUTION_MODE
        if mode in ("submit_vs_spot", "legacy"):
            mode = "submit_spot"

        if mode == "submit_spot" and now_spot is None:
            logger.warning(
                f"Predictor ({asset_id}) martingale eval: no Coinbase spot — "
                "clearing pending bet without stake update"
            )
            self._predictor_store_pending_bet(asset_id, None)
            self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
            return

        entry = pred.get("spot_entry")
        wanted = pred.get("bet_outcome") or pred.get("signal")

        realized: str
        resolution_note: str
        open_px: Optional[Decimal] = None
        close_px: Optional[Decimal] = None

        if mode == "polymarket" and interval_start_ts > 0:
            poly_result = await self._fetch_polymarket_resolution(
                interval_start_ts, asset_id=asset_id
            )
            if poly_result is not None:
                realized = poly_result
                resolution_note = (
                    f"Polymarket website: slug {self._slug_for_asset_ts(asset_id, interval_start_ts)} "
                    f"resolved as {realized}"
                )
            else:
                order_logger.warning(
                    "Polymarket resolution unavailable — falling back to Coinbase candle"
                )
                mode = "candle"

        if mode == "candle" and interval_start_ts > 0:  # noqa: SIM102
            from data_sources.coinbase.adapter import CoinbaseDataSource

            src = CoinbaseDataSource(product_id=coinbase_product_for_asset(asset_id))
            oc = None
            try:
                if await src.connect():
                    oc = await src.fetch_candle_open_close_for_bucket(
                        interval_start_ts,
                        MARKET_INTERVAL_SECONDS,
                    )
            finally:
                await src.disconnect()
            if oc is not None:
                open_px, close_px = oc
                diff = close_px - open_px
                tol = max(Decimal("0.01"), abs(open_px) * Decimal("1e-10"))
                if diff > tol:
                    realized = "UP"
                elif diff < -tol:
                    realized = "DOWN"
                else:
                    realized = "FLAT"
                resolution_note = (
                    f"Coinbase 5m candle ({asset_id}) for slug interval (UTC bucket {interval_start_ts}): "
                    f"open ${float(open_px):,.2f} → close ${float(close_px):,.2f} "
                    f"(Polymarket resolves from oracle/Chainlink; may differ slightly)"
                )
            else:
                resolution_note = (
                    "Coinbase 5m candle unavailable — falling back to submit vs now spot (legacy)"
                )
                if entry is None:
                    logger.warning(
                        f"Predictor ({asset_id}) martingale eval: no candle and no spot_entry — clearing"
                    )
                    self._predictor_store_pending_bet(asset_id, None)
                    self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
                    return
                if now_spot is None:
                    logger.warning(
                        f"Predictor ({asset_id}) martingale eval: no candle and no current spot — clearing"
                    )
                    self._predictor_store_pending_bet(asset_id, None)
                    self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
                    return
                move = now_spot - entry
                realized = "UP" if move > 0 else "DOWN" if move < 0 else "FLAT"
        elif mode == "submit_spot":
            if entry is None:
                self._predictor_store_pending_bet(asset_id, None)
                self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
                return
            if now_spot is None:
                logger.warning(
                    f"Predictor ({asset_id}) martingale eval: no Coinbase spot — clearing pending bet"
                )
                self._predictor_store_pending_bet(asset_id, None)
                self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
                return
            move = now_spot - entry
            realized = "UP" if move > 0 else "DOWN" if move < 0 else "FLAT"
            resolution_note = "Legacy: spot at eval minus spot at submit (not Polymarket settlement)"

        won = realized == wanted
        prev_stake = Decimal(str(pred.get("stake", MARTINGALE_BASE_USD)))
        model_sig = pred.get("signal")
        bet_round = pred.get("round_idx")
        next_stake = self._predictor_clamped_stake(asset_id)

        # Compact log line: always the CLOB side we bought (YES=UP, NO=DOWN), not the raw model.
        order_side_for_log = str(pred.get("bet_outcome") or "?")
        if realized == "FLAT":
            append_predictor_order_summary_line(
                tk,
                order_side_for_log,
                prev_stake,
                realized,
                model_signal=str(model_sig) if model_sig else None,
                round_idx=bet_round,
            )
            order_logger.info(
                f"Predictor ({asset_id}) martingale eval: FLAT interval — stake unchanged "
                f"(still ${float(next_stake):.2f})"
            )
            self._predictor_store_pending_bet(asset_id, None)
            self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)
            return

        self._predictor_apply_martingale_from_result(won, prev_stake, asset_id=asset_id)
        next_stake = self._predictor_clamped_stake(asset_id)
        if not won and prev_stake >= MARTINGALE_MAX_STAKE_USD:
            order_logger.warning(
                f"MARTINGALE: LOSS at max stake ${float(prev_stake):.2f} "
                f"(cap ${float(MARTINGALE_MAX_STAKE_USD):.2f}) — resetting to "
                f"${float(MARTINGALE_BASE_USD):.2f} and continuing"
            )

        append_predictor_order_summary_line(
            tk,
            order_side_for_log,
            prev_stake,
            realized,
            model_signal=str(model_sig) if model_sig else None,
            round_idx=bet_round,
        )

        order_logger.info("=" * 80)
        order_logger.info("PREDICTOR RESULT (post-submit skip cycle)")
        order_logger.info(f"  Bet trade_key: {tk!r}")
        order_logger.info(f"  {resolution_note}")
        if model_sig is not None:
            order_logger.info(
                f"  Order submitted: {wanted} (UP=YES, DOWN=NO) | Interval result: {realized} | "
                f"Model signal (contrarian input): {model_sig}"
            )
        else:
            order_logger.info(
                f"  Order submitted: {wanted} (UP=YES, DOWN=NO) | Interval result: {realized}"
            )
        if open_px is not None and close_px is not None:
            order_logger.info(
                f"  Interval candle: open ${float(open_px):,.2f} → close ${float(close_px):,.2f}"
            )
        if entry is not None and now_spot is not None:
            order_logger.info(
                f"  Spot at order submit: ${float(entry):,.2f}  Spot now: ${float(now_spot):,.2f}  "
                f"Delta (FYI, not settlement): {float(now_spot - entry):+.2f}"
            )
        elif entry is not None:
            order_logger.info(f"  Spot at order submit: ${float(entry):,.2f}")
        order_logger.info(
            f"  Outcome: {'WIN' if won else 'LOSS'} | Stake was: ${float(prev_stake):.2f} | "
            f"Next submit stake: ${float(next_stake):.2f}"
        )
        order_logger.info("=" * 80)

        if won:
            try:
                is_simulation = await self.check_simulation_mode()
            except Exception:
                is_simulation = self.current_simulation_mode
            if not is_simulation:
                order_logger.info("Predictor WIN — scheduling auto-redeem / claim")
                self._schedule_redeem_on_win()
            else:
                order_logger.debug("Predictor WIN (simulation) — skip auto-redeem")

        self._predictor_store_pending_bet(asset_id, None)
        self._predictor_seed_last_trade_after_eval(asset_id, interval_start_ts)

        # Immediately submit the next round order now that the result is confirmed.
        self._predictor_submit_next_round_immediately(asset_id)

    def _predictor_submit_next_round_immediately(
        self, asset_id: Optional[str] = None
    ) -> None:
        """After eval confirms a result, immediately arm an order for the next available slug."""
        aids = [asset_id] if asset_id else list(self._active_assets)
        now_ts = time.time()
        iv = MARKET_INTERVAL_SECONDS
        next_slug_ts = ((int(now_ts) // iv) + 1) * iv

        for aid in aids:
            if self._spot_for_asset(aid) is None:
                continue
            rnd = self._predictor_next_target_round_for(aid)
            if rnd % 2 == 0:
                rnd += 1
                self._predictor_set_next_target_round_for(aid, rnd)

            target_slug_ts = self._predictor_slug_for_round(rnd)
            if target_slug_ts < next_slug_ts:
                target_slug_ts = next_slug_ts
                if self._predictor_anchor_slug_ts > 0:
                    rnd = self._predictor_round_index_for_slug(target_slug_ts)
                    if rnd % 2 == 0:
                        rnd += 1
                        target_slug_ts = self._predictor_slug_for_round(rnd)
                    self._predictor_set_next_target_round_for(aid, rnd)

            self._predictor_set_earliest_slug_for(aid, target_slug_ts)
            if aid == self._primary_asset:
                self._predictor_next_target_round = rnd
                self._predictor_earliest_next_order_slug_ts = target_slug_ts

            trade_key = make_trade_key(aid, target_slug_ts, self._multi_asset)
            order_market = self._predictor_market_for_slug_ts(target_slug_ts, aid)
            if order_market is None:
                order_market = {
                    "slug": self._slug_for_asset_ts(aid, target_slug_ts),
                    "market_timestamp": target_slug_ts,
                    "yes_instrument_id": self._yes_instrument_id,
                    "yes_token_id": self._yes_token_id,
                    "asset_id": aid,
                }

            order_logger.info(
                f"Predictor ({aid}): result confirmed — immediately submitting next round {rnd} "
                f"(slug {target_slug_ts}, stake=${float(self._predictor_clamped_stake(aid)):.2f})"
            )
            self._predictor_arm_submit_for_next_slug(
                now_ts,
                trade_key,
                order_market,
                round_idx=rnd,
                asset_id=aid,
            )

    # ------------------------------------------------------------------
    # Trading decision (unchanged)
    # ------------------------------------------------------------------

    def _make_trading_decision_sync(self, current_price):
        """Synchronous wrapper for trading decision (called from executor)."""
        from decimal import Decimal
        price_decimal = Decimal(str(current_price))
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._make_trading_decision(price_decimal))
        finally:
            loop.close()
            
    async def _fetch_market_context(self, current_price: Decimal) -> dict:
        """
        Fetch REAL external data to populate signal processor metadata.

        Returns a dict with:
          - sentiment_score (float 0-100): live Fear & Greed index, or None
          - spot_price (float): live BTC-USD from Coinbase, or None
          - deviation (float): polymarket price vs SMA-20 (always computed)
          - momentum (float): 5-period rate of change (always computed)
          - volatility (float): price std-dev over last 20 ticks (always computed)
        """
        current_price_float = float(current_price)

        # --- Always-available stats from local price_history ---
        recent_prices = [float(p) for p in self.price_history[-20:]]
        sma_20 = sum(recent_prices) / len(recent_prices)
        deviation = (current_price_float - sma_20) / sma_20
        momentum = (
            (current_price_float - float(self.price_history[-5])) / float(self.price_history[-5])
            if len(self.price_history) >= 5 else 0.0
        )
        variance = sum((p - sma_20) ** 2 for p in recent_prices) / len(recent_prices)
        volatility = math.sqrt(variance)

        metadata = {
            "deviation": deviation,
            "momentum": momentum,
            "volatility": volatility,
            # Tick buffer for TickVelocityProcessor
            "tick_buffer": list(self._tick_buffer),
            # YES token id for OrderBookImbalanceProcessor
            "yes_token_id": self._yes_token_id,
        }

        # --- Real sentiment: Fear & Greed Index via NewsSocialDataSource ---
        try:
            from data_sources.news_social.adapter import NewsSocialDataSource
            news_source = NewsSocialDataSource()
            await news_source.connect()
            fg = await news_source.get_fear_greed_index()
            await news_source.disconnect()
            if fg and "value" in fg:
                metadata["sentiment_score"] = float(fg["value"])
                metadata["sentiment_classification"] = fg.get("classification", "")
                logger.info(
                    f"Fear & Greed: {metadata['sentiment_score']:.0f} "
                    f"({metadata['sentiment_classification']})"
                )
            else:
                logger.warning("Fear & Greed fetch returned no data — sentiment processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed index: {e} — sentiment processor skipped")

        # --- Real spot price: Coinbase BTC-USD REST API ---
        try:
            from data_sources.coinbase.adapter import CoinbaseDataSource
            coinbase = CoinbaseDataSource(product_id=COINBASE_PRODUCT_ID)
            await coinbase.connect()
            spot = await coinbase.get_current_price()
            await coinbase.disconnect()
            if spot:
                metadata["spot_price"] = float(spot)
                logger.info(f"Coinbase spot price: ${float(spot):,.2f}")
            else:
                logger.warning("Coinbase price fetch returned None — divergence processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Coinbase spot price: {e} — divergence processor skipped")

        logger.info(
            f"Market context — deviation={deviation:.2%}, "
            f"momentum={momentum:.2%}, volatility={volatility:.4f}, "
            f"sentiment={'%.0f' % metadata['sentiment_score'] if 'sentiment_score' in metadata else 'N/A'}, "
            f"spot=${'%.2f' % metadata['spot_price'] if 'spot_price' in metadata else 'N/A'}"
        )
        return metadata

    def _synthetic_fused_for_trend(self, current_price: Decimal) -> "FusedSignal":
        """Record-keeping only when we trade on Polymarket mid (no processor consensus)."""
        from core.strategy_brain.fusion_engine.signal_fusion import FusedSignal
        from core.strategy_brain.signal_processors.base_processor import SignalDirection

        pf = float(current_price)
        if pf > 0.60:
            direction = SignalDirection.BULLISH
            confidence = pf
        elif pf < 0.40:
            direction = SignalDirection.BEARISH
            confidence = 1.0 - pf
        else:
            raise ValueError("synthetic fuse only when mid is outside 0.40–0.60")

        return FusedSignal(
            timestamp=datetime.now(timezone.utc),
            direction=direction,
            confidence=confidence,
            score=max(40.0, confidence * 100.0),
            signals=[],
            weights={},
            metadata={"trend_only_synthetic": True},
        )

    async def _make_trading_decision(self, current_price: Decimal):
        """
        Make trading decision using our 7-phase system.

        Position size is always $1.00 — no variable sizing, no risk-engine
        calculation needed. The risk engine is still used to check that we
        don't already have too many open positions.
        """
        # --- Mode check ---
        is_simulation = await self.check_simulation_mode()
        logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")

        # --- Minimum history guard ---
        if len(self.price_history) < 20:
            logger.warning(f"Not enough price history ({len(self.price_history)}/20)")
            return

        logger.info(f"Current price: ${float(current_price):,.4f}")

        # --- Phase 4a: Build real metadata for processors ---
        metadata = await self._fetch_market_context(current_price)

        TREND_UP_THRESHOLD = 0.60
        TREND_DOWN_THRESHOLD = 0.40
        price_float = float(current_price)
        trend_clear = (
            price_float > TREND_UP_THRESHOLD or price_float < TREND_DOWN_THRESHOLD
        )

        # --- Phase 4b–4c: Signals + fusion (optional if mid is clearly skewed) ---
        signals = self._process_signals(current_price, metadata)
        fused = (
            self.fusion_engine.fuse_signals(signals, min_signals=1, min_score=40.0)
            if signals
            else None
        )

        if fused is None and trend_clear:
            logger.info(
                "No fused signal from processors — continuing on Polymarket mid only "
                "(trend path; UI can show >60% Up while processors stay quiet)"
            )
            fused = self._synthetic_fused_for_trend(current_price)
        elif fused is None:
            logger.info(
                "No trade: need either a fused processor signal or mid outside 0.40–0.60 "
                "(mid is neutral and fusion did not clear)"
            )
            return

        if signals:
            logger.info(f"Generated {len(signals)} signal(s):")
            for sig in signals:
                logger.info(
                    f"  [{sig.source}] {sig.direction.value}: "
                    f"score={sig.score:.1f}, confidence={sig.confidence:.2%}"
                )

        if fused.metadata.get("trend_only_synthetic"):
            logger.info(
                f"TREND-ONLY record: {fused.direction.value} "
                f"(score={fused.score:.1f}, conf={fused.confidence:.2%})"
            )
        else:
            logger.info(
                f"FUSED SIGNAL: {fused.direction.value} "
                f"(score={fused.score:.1f}, confidence={fused.confidence:.2%})"
            )

        # --- Phase 5: Position size is always exactly $1.00 ---
        POSITION_SIZE_USD = Decimal("1.00")

        # =========================================================================
        # TREND FILTER — direction follows Polymarket YES mid (processors optional above)
        #   price > 0.60 → buy YES;  price < 0.40 → buy NO;  else skip
        # =========================================================================

        if price_float > TREND_UP_THRESHOLD:
            direction = "long"
            trend_confidence = price_float  # e.g. 0.72 = 72% confident UP
            logger.info(
                f" TREND: UP ({price_float:.2%} YES probability) → buying YES"
            )
        elif price_float < TREND_DOWN_THRESHOLD:
            direction = "short"
            trend_confidence = 1.0 - price_float  # e.g. 0.31 price = 69% confident DOWN
            logger.info(
                f" TREND: DOWN ({price_float:.2%} YES probability = {1-price_float:.2%} NO) → buying NO"
            )
        else:
            logger.info(
                f"⏭ TREND: NEUTRAL ({price_float:.2%}) — price too close to 0.50, SKIPPING trade "
                f"(coin flip territory: {TREND_DOWN_THRESHOLD:.0%}–{TREND_UP_THRESHOLD:.0%})"
            )
            return

        # Risk engine: only check position-count / exposure limits (no sizing math)
        is_valid, error = self.risk_engine.validate_new_position(
            size=POSITION_SIZE_USD,
            direction=direction,
            current_price=current_price,
        )
        if not is_valid:
            logger.warning(f"Risk engine blocked trade: {error}")
            return

        logger.info(f"Position size: $1.00 (fixed) | Direction: {direction.upper()}")

        # --- Liquidity guard: don't place if market has no real depth ---
        # The current bid/ask come from the last processed quote tick.
        # If ask <= 0.02 or bid <= 0.02, the orderbook is essentially empty
        # and a FAK (IOC market) order will be rejected immediately.
        last_tick = getattr(self, '_last_bid_ask', None)
        if last_tick:
            last_bid, last_ask = last_tick
            MIN_LIQUIDITY = Decimal("0.02")
            if direction == "long" and last_ask <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for BUY: ask=${float(last_ask):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return
            if direction == "short" and last_bid <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for SELL: bid=${float(last_bid):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return

        # --- Phase 5 / 6: Execute ---
        if is_simulation:
            await self._record_paper_trade(fused, POSITION_SIZE_USD, current_price, direction)
        else:
            await self._place_real_order(fused, POSITION_SIZE_USD, current_price, direction)
            
    async def _record_paper_trade(self, signal, position_size, current_price, direction):
        exit_delta = timedelta(minutes=1) if self.test_mode else timedelta(minutes=5)
        exit_time = datetime.now(timezone.utc) + exit_delta

        if "BULLISH" in str(signal.direction):
            movement = random.uniform(-0.02, 0.08)
        else:
            movement = random.uniform(-0.08, 0.02)

        exit_price = current_price * (Decimal("1.0") + Decimal(str(movement)))
        exit_price = max(Decimal("0.01"), min(Decimal("0.99"), exit_price))

        if direction == "long":
            pnl = position_size * (exit_price - current_price) / current_price
        else:
            pnl = position_size * (current_price - exit_price) / current_price

        outcome = "WIN" if pnl > 0 else "LOSS"
        paper_trade = PaperTrade(
            timestamp=datetime.now(timezone.utc),
            direction=direction.upper(),
            size_usd=float(position_size),
            price=float(current_price),
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            outcome=outcome,
        )
        self.paper_trades.append(paper_trade)

        self.performance_tracker.record_trade(
            trade_id=f"paper_{int(datetime.now().timestamp())}",
            direction=direction,
            entry_price=current_price,
            exit_price=exit_price,
            size=position_size,
            entry_time=datetime.now(timezone.utc),
            exit_time=exit_time,
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            metadata={
                "simulated": True,
                "num_signals": signal.num_signals if hasattr(signal, 'num_signals') else 1,
                "fusion_score": signal.score,
            }
        )

        if hasattr(self, 'grafana_exporter') and self.grafana_exporter:
            self.grafana_exporter.increment_trade_counter(won=(pnl > 0))
            self.grafana_exporter.record_trade_duration(exit_delta.total_seconds())

        order_logger.info("=" * 80)
        order_logger.info("[SIMULATION] PAPER TRADE RECORDED")
        order_logger.info(f"  Direction: {direction.upper()}")
        order_logger.info(f"  Size: ${float(position_size):.2f}")
        order_logger.info(f"  Entry Price: ${float(current_price):,.4f}")
        order_logger.info(f"  Simulated Exit: ${float(exit_price):,.4f}")
        order_logger.info(f"  Simulated P&L: ${float(pnl):+.2f} ({movement*100:+.2f}%)")
        order_logger.info(f"  Outcome: {outcome}")
        order_logger.info(f"  Total Paper Trades: {len(self.paper_trades)}")
        order_logger.info("=" * 80)

        self._save_paper_trades()

    def _save_paper_trades(self):
        import json
        try:
            trades_data = [t.to_dict() for t in self.paper_trades]
            with open('paper_trades.json', 'w') as f:
                json.dump(trades_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper trades: {e}")

    # ------------------------------------------------------------------
    # Real order (unchanged)
    # ------------------------------------------------------------------

    async def _place_real_order(
        self, signal, position_size, current_price, direction, order_market=None
    ) -> bool:
        if not self.instrument_id:
            logger.error("No instrument available")
            return False

        try:
            # instrument is fetched below after determining YES vs NO token

            order_logger.info("=" * 80)
            order_logger.info("LIVE MODE - PLACING REAL ORDER!")
            order_logger.info("=" * 80)

            if order_market:
                order_logger.info(
                    f"  Target market (early/next slug): {order_market.get('slug', '')!r}"
                )

            # On Polymarket, both UP and DOWN are BUY orders.
            # Bullish = buy YES token (self._yes_instrument_id)
            # Bearish = buy NO token  (self._no_instrument_id)
            # There is NO sell — you always buy whichever side you want.
            side = OrderSide.BUY

            if direction == "long":
                if order_market and order_market.get("yes_instrument_id"):
                    trade_instrument_id = order_market["yes_instrument_id"]
                else:
                    trade_instrument_id = getattr(self, "_yes_instrument_id", self.instrument_id)
                trade_label = "YES (UP)"
            else:
                if order_market and order_market.get("no_instrument_id"):
                    no_id = order_market["no_instrument_id"]
                else:
                    no_id = getattr(self, "_no_instrument_id", None)
                if no_id is None:
                    logger.warning(
                        "NO token instrument not found for this market — "
                        "cannot bet DOWN. Skipping trade."
                    )
                    return False
                trade_instrument_id = no_id
                trade_label = "NO (DOWN)"

            instrument = self.cache.instrument(trade_instrument_id)
            if not instrument:
                logger.error(f"Instrument not in cache: {trade_instrument_id}")
                return False

            order_logger.info(f"Buying {trade_label} token: {trade_instrument_id}")

            trade_price = float(current_price)
            max_usd_amount = float(position_size)
            # Make sure the patched market-order spend matches the requested size.
            os.environ["MARKET_BUY_USD"] = f"{max_usd_amount:.2f}"

            precision = instrument.size_precision

            # Always BUY — the market-order patch converts this to a USD amount.
            # Pass dummy qty=5 (minimum) so Nautilus risk engine doesn't deny it.
            min_qty_val = float(getattr(instrument, 'min_quantity', None) or 5.0)
            token_qty = max(min_qty_val, 5.0)
            token_qty = round(token_qty, precision)
            order_logger.info(
                f"BUY {trade_label}: dummy qty={token_qty:.6f} "
                f"(patch converts to ${max_usd_amount:.2f} USD)"
            )

            qty = Quantity(token_qty, precision=precision)
            timestamp_ms = int(time.time() * 1000)
            unique_id = f"BTC-5MIN-${max_usd_amount:.0f}-{timestamp_ms}"

            order = self.order_factory.market(
                instrument_id=trade_instrument_id,
                order_side=side,
                quantity=qty,
                client_order_id=ClientOrderId(unique_id),
                quote_quantity=False,
                time_in_force=TimeInForce.IOC,
            )

            self.submit_order(order)

            order_logger.info(f"REAL ORDER SUBMITTED!")
            order_logger.info(f"  Order ID: {unique_id}")
            order_logger.info(f"  Direction: {trade_label}")
            order_logger.info(f"  Side: BUY")
            order_logger.info(f"  Token Quantity: {token_qty:.6f}")
            order_logger.info(f"  Estimated Cost: ~${max_usd_amount:.2f}")
            order_logger.info(f"  Price: ${trade_price:.4f}")
            order_logger.info("=" * 80)

            self._track_order_event("placed")
            return True

        except Exception as e:
            logger.error(f"Error placing real order: {e}")
            order_logger.exception(f"Error placing real order: {e}")
            self._track_order_event("rejected")
            return False

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _process_signals(self, current_price, metadata=None):
        signals = []
        if metadata is None:
            metadata = {}

        processed_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, float):
                processed_metadata[key] = Decimal(str(value))
            else:
                processed_metadata[key] = value

        spike_signal = self.spike_detector.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if spike_signal:
            signals.append(spike_signal)

        if 'sentiment_score' in processed_metadata:
            sentiment_signal = self.sentiment_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if sentiment_signal:
                signals.append(sentiment_signal)

        if 'spot_price' in processed_metadata:
            divergence_signal = self.divergence_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if divergence_signal:
                signals.append(divergence_signal)

        # --- Order Book Imbalance (real-time Polymarket CLOB depth) ---
        if processed_metadata.get('yes_token_id'):
            ob_signal = self.orderbook_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if ob_signal:
                signals.append(ob_signal)

        # --- Tick Velocity (last 60s of Polymarket probability movement) ---
        if processed_metadata.get('tick_buffer'):
            tv_signal = self.tick_velocity_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if tv_signal:
                signals.append(tv_signal)

        # --- Deribit Put/Call Ratio (institutional options sentiment) ---
        pcr_signal = self.deribit_pcr_processor.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if pcr_signal:
            signals.append(pcr_signal)

        return signals

    # ------------------------------------------------------------------
    # Order events
    # ------------------------------------------------------------------

    def _track_order_event(self, event_type: str) -> None:
        """
        Safely track an order event on the performance tracker.

        PerformanceTracker does not expose `increment_order_counter`, so we
        use whichever method is actually available, or fall back to a no-op.
        Supported event_type values: "placed", "filled", "rejected".
        """
        try:
            pt = self.performance_tracker
            # Try the method that actually exists first
            if hasattr(pt, 'record_order_event'):
                pt.record_order_event(event_type)
            elif hasattr(pt, 'increment_counter'):
                pt.increment_counter(event_type)
            elif hasattr(pt, 'increment_order_counter'):
                pt.increment_order_counter(event_type)
            else:
                # No suitable method found – log and carry on
                logger.debug(
                    f"PerformanceTracker has no order-counter method; "
                    f"ignoring event '{event_type}'"
                )
        except Exception as e:
            logger.warning(f"Failed to track order event '{event_type}': {e}")

    def on_order_filled(self, event):
        order_logger.info("=" * 80)
        order_logger.info(f"ORDER FILLED!")
        order_logger.info(f"  Order: {event.client_order_id}")
        order_logger.info(f"  Fill Price: ${float(event.last_px):.4f}")
        order_logger.info(f"  Quantity: {float(event.last_qty):.6f}")
        order_logger.info("=" * 80)
        self._track_order_event("filled")

    def on_order_denied(self, event):
        order_logger.error("=" * 80)
        order_logger.error(f"ORDER DENIED!")
        order_logger.error(f"  Order: {event.client_order_id}")
        order_logger.error(f"  Reason: {event.reason}")
        order_logger.error("=" * 80)
        self._track_order_event("rejected")

    def on_order_rejected(self, event):
        """Handle order rejection — non-predictor paths may retry; predictor is one order per round."""
        reason = str(getattr(event, 'reason', ''))
        reason_lower = reason.lower()
        if self._predictor_enabled:
            order_logger.warning(
                f"Order rejected (predictor — no second submit this round): {reason}"
            )
            return
        transient = (
            "no orders found" in reason_lower
            or "fak" in reason_lower
            or "no match" in reason_lower
            or "request exception" in reason_lower
            or "timed out" in reason_lower
            or "timeout" in reason_lower
        )
        if transient:
            order_logger.warning(
                "⚠ Order submit failed (liquidity or transient network) — "
                "resetting trade slot to retry next tick\n"
                f"  Reason: {reason}"
            )
            self.last_trade_time = -1
        else:
            order_logger.warning(f"Order rejected: {reason}")

    # ------------------------------------------------------------------
    # Grafana / stop
    # ------------------------------------------------------------------

    def _start_grafana_sync(self):
        try:
            self.grafana_exporter.start_sync()
            if getattr(self.grafana_exporter, "_is_running", False):
                logger.info(f"Grafana metrics on port {self.grafana_exporter.port}")
            else:
                logger.warning("Grafana metrics server did not start (see error above)")
        except Exception as e:
            logger.error(f"Failed to start Grafana: {e}")

    def on_stop(self):
        logger.info("Integrated BTC strategy stopped")
        logger.info(f"Total paper trades recorded: {len(self.paper_trades)}")
        if self.grafana_exporter:
            try:
                self.grafana_exporter.stop_sync()
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_integrated_bot(simulation: bool = False, enable_grafana: bool = True, test_mode: bool = False):
    """Run the integrated BTC 5-min trading bot (Gamma slug list capped to avoid 414 URI Too Large)."""
    global _graceful_shutdown_requested, _last_graceful_stop_reason

    print("=" * 80)
    assets_label = ",".join(ACTIVE_ASSETS).upper()
    print(f"INTEGRATED POLYMARKET 5-MIN UP/DOWN BOT v{BOT_VERSION} [{assets_label}]")
    print("Nautilus + Predictor + Redis Control")
    print("=" * 80)

    redis_client = init_redis()

    # ------------------------------------------------------------------
    # Preflight: fail fast if env/network breaks engines connect
    # ------------------------------------------------------------------
    def _preflight() -> None:
        missing = [
            k
            for k in (
                "POLYMARKET_PK",
                "POLYMARKET_API_KEY",
                "POLYMARKET_API_SECRET",
                "POLYMARKET_PASSPHRASE",
            )
            if not os.getenv(k)
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

        # Quick network checks (helps diagnose Ubuntu boxes with blocked egress/DNS).
        # Gamma must return 200. CLOB: only verify TLS/DNS/transport — many paths 404.
        try:
            import httpx

            with httpx.Client(timeout=10.0) as client:
                client.get(
                    "https://gamma-api.polymarket.com/markets", params={"limit": 1}
                ).raise_for_status()
                # Any HTTP response means host is reachable; do not use /health (often 404).
                client.get("https://clob.polymarket.com/", follow_redirects=True)
        except httpx.RequestError as e:
            raise RuntimeError(f"Network preflight failed (Gamma/CLOB transport): {e}")
        except httpx.HTTPStatusError as e:
            # Only Gamma uses raise_for_status() above
            raise RuntimeError(f"Network preflight failed (Gamma HTTP error): {e}")

    try:
        _preflight()
    except Exception as e:
        logger.error(f"Preflight failed: {e}")
        raise

    if redis_client:
        try:
            # ALWAYS overwrite Redis with the current session mode.
            # This prevents a stale value from a previous --live run
            # silently overriding --test-mode or --simulation runs.
            mode_value = '1' if simulation else '0'
            redis_client.set('btc_trading:simulation_mode', mode_value)
            mode_label = 'SIMULATION' if simulation else 'LIVE'
            logger.info(f"Redis simulation_mode forced to: {mode_label} ({mode_value})")
        except Exception as e:
            logger.warning(f"Could not set Redis simulation mode: {e}")

    print(f"\nConfiguration:")
    print(f"  Initial Mode: {'SIMULATION' if simulation else 'LIVE TRADING'}")
    print(f"  Redis Control: {'Enabled' if redis_client else 'Disabled'}")
    print(f"  Grafana: {'Enabled' if enable_grafana else 'Disabled'}")
    print(f"  Base stake (martingale): ${MARTINGALE_BASE_USD} (max ${MARTINGALE_MAX_STAKE_USD})")
    print(f"  Plan: {BOT_PLAN} | Min score filter: {PREDICTOR_MIN_SCORE}")
    print(
        f"  Predictor Martingale: ${float(MARTINGALE_BASE_USD):.0f} on win; "
        f"on each loss double the last stake until ${float(MARTINGALE_MAX_STAKE_USD):.0f} "
        f"(e.g. $1→$2→$4→$8→$16); win resets to ${float(MARTINGALE_BASE_USD):.0f}; "
        f"loss at ${float(MARTINGALE_MAX_STAKE_USD):.0f} resets to ${float(MARTINGALE_BASE_USD):.0f} (restart ladder). "
        f"After each successful submit for slug S, next order targets slug start >= "
        f"S+(1+N)*300s (N={PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE}, default 1 skips the consecutive bar); "
        f"then next order only in the last {SUBMIT_SECONDS_BEFORE_BOUNDARY}s before that slug opens "
        f"(PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE; 0 = allow consecutive slug)."
    )
    if test_mode:
        try:
            _test_eval = max(
                0,
                min(180, int(os.getenv("TEST_PREDICTOR_EVAL_POST_END_SEC", "5"))),
            )
        except ValueError:
            _test_eval = 5
        try:
            _test_skip = max(
                0,
                min(
                    24,
                    int(
                        float(
                            os.getenv("TEST_PREDICTOR_FULL_SLUGS_TO_SKIP_AFTER_SCORE", "0")
                        )
                    ),
                ),
            )
        except ValueError:
            _test_skip = 0
        print(
            f"  --test-mode: simulation + predictor log.txt (eval +{_test_eval}s after slug end; "
            f"skip {_test_skip} bar(s) after submit; override via TEST_PREDICTOR_* env)"
        )
    _olf = os.getenv("ORDER_LOG_FILE", "logs/orders.log").strip()
    _olo = os.getenv("ORDER_LOG_ONLY", "1").strip().lower() in ("1", "true", "yes", "on")
    if _olf:
        print(
            f"  Order / predictor detail log: {_olf} (ORDER_LOG_ONLY={'1' if _olo else '0'}; "
            f"ORDER_LOG_CONSOLE mirrors order lines to stderr when ORDER_LOG_ONLY=1, default on)"
        )
    else:
        print("  Order / predictor detail log: disabled (ORDER_LOG_FILE empty)")
    _sum_ex = os.getenv("ORDER_SUMMARY_LOG_FILE")
    if _sum_ex is not None and _sum_ex.strip() == "":
        print("  Compact one-line order summary: disabled (ORDER_SUMMARY_LOG_FILE is empty)")
    else:
        _sum_raw = _sum_ex.strip() if _sum_ex is not None else str(project_root / "log.txt")
        _sum_abs = _sum_raw if os.path.isabs(_sum_raw) else str(project_root / _sum_raw)
        print(
            f"  Compact one-line order summary: {_sum_abs} "
            f"(phase=submit when an order is placed, phase=result when the slug is scored; "
            f"slug_start_ts= prefix for sort)"
        )
    print(f"  Quote stability gate: {QUOTE_STABILITY_REQUIRED} valid ticks")
    _rpm_show = os.getenv("BOT_PERIODIC_RESTART_MINUTES", "0").strip()
    if _rpm_show.lower() in ("", "0", "off", "false", "never", "disable", "no"):
        print("  Periodic node restart: OFF (default) — run 24/7 until you stop the process")
    else:
        print(f"  Periodic node restart: every {_rpm_show} minutes (Gamma slug refresh)")
    # Nautilus risk engine needs a quote/trade tick to size MARKET orders; NO token for next
    # slug often has none yet → WARN + skipped checks. Bypass in live unless strict.
    _nautilus_risk_bypass = simulation or not _env_truthy("NAUTILUS_RISK_STRICT")
    print(
        f"  Nautilus risk engine: {'BYPASS (default live)' if _nautilus_risk_bypass and not simulation else 'BYPASS (simulation)' if simulation else 'STRICT'} — "
        f"NAUTILUS_RISK_STRICT={'on' if _env_truthy('NAUTILUS_RISK_STRICT') else 'off'}"
    )
    # Startup exec reconciliation: Polymarket mass/position reports often lack avg_px; Nautilus
    # then emits synthetic MARKET reconciliation orders and reconcile_execution_state can fail,
    # so the kernel never reaches trader.start(). Off by default; enable with NAUTILUS_EXEC_RECONCILIATION=1.
    _exec_reconcile = _env_truthy("NAUTILUS_EXEC_RECONCILIATION")
    _exec_gen_missing = _env_truthy("NAUTILUS_EXEC_GENERATE_MISSING_ORDERS")
    print(
        f"  Nautilus exec reconciliation: {'ON' if _exec_reconcile else 'OFF (default)'} "
        f"(set NAUTILUS_EXEC_RECONCILIATION=1 to reconcile wallet vs cache at startup); "
        f"synthetic recon orders: {'on' if _exec_gen_missing else 'off (default)'} "
        f"(NAUTILUS_EXEC_GENERATE_MISSING_ORDERS=1 only if reconciliation is on)"
    )
    print()

    _auto_redeem_stop: Optional[threading.Event] = None
    _auto_redeem_thread: Optional[threading.Thread] = None
    _redeem_env = os.getenv("POLYMARKET_AUTO_REDEEM", "").strip().lower()
    _redeem_off = _redeem_env in ("0", "false", "no", "off")
    _redeem_on = _redeem_env in ("1", "true", "yes", "on")
    # Live: background redeem loop on by default; win also triggers redeem_once().
    if (not simulation) and not _redeem_off and (_redeem_on or _redeem_env == ""):
        _auto_redeem_stop = threading.Event()
        _auto_redeem_thread = start_auto_redeem_thread(_auto_redeem_stop)
        if _auto_redeem_thread is not None:
            logger.info(
                "POLYMARKET_AUTO_REDEEM enabled: background relayer redemption "
                "(Builder API creds + POLYMARKET_PK required; see execution/polymarket_auto_redeem.py)"
            )
        else:
            logger.warning(
                "POLYMARKET_AUTO_REDEEM is set but the redeem module did not start "
                "(see WARNING above: add execution/polymarket_auto_redeem.py)."
            )

    nautilus_log = os.getenv(
        "NAUTILUS_LOG_LEVEL",
        "INFO" if _bot_verbose() else "WARNING",
    )
    _funder = os.getenv("POLYMARKET_FUNDER") or None
    _drop_quotes_missing_side = _env_truthy("POLYMARKET_DROP_QUOTES_MISSING_SIDE")

    node_restart_iteration = 0
    try:
        while True:
            node_restart_iteration += 1
            _graceful_shutdown_requested = False
            _last_graceful_stop_reason = None

            # TradingNode.dispose() closes the kernel's event loop. The next cycle MUST use a
            # brand-new loop; reusing asyncio's thread-default loop after close breaks node.run()
            # (second session can hang or never trade).
            try:
                _old_loop = asyncio.get_event_loop()
            except RuntimeError:
                _old_loop = None
            if _old_loop is not None and not _old_loop.is_closed():
                try:
                    if _old_loop.is_running():
                        logger.warning(
                            "Event loop still running before new TradingNode; "
                            "skipping close (unexpected — report if you see this)"
                        )
                    else:
                        _old_loop.close()
                except Exception as e:
                    logger.debug(f"While closing prior event loop: {e}")
            _node_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_node_loop)

            # =========================================================================
            # Slug timestamps: 5-min UTC boundaries. Keep slug count modest — Gamma GET URLs
            # embed every slug as a query param; too many → nginx 414 Request-URI Too Large.
            # Tune with BTC_SLUG_HOURS_AHEAD (float hours) and BTC_SLUG_MAX (max list length).
            # Recomputed on each periodic auto-restart so the filter window stays fresh.
            # =========================================================================
            now = datetime.now(timezone.utc)
            _iv = MARKET_INTERVAL_SECONDS
            unix_interval_start = (int(now.timestamp()) // _iv) * _iv
            _hours = float(
                os.getenv("BTC_SLUG_HOURS_AHEAD")
                or os.getenv("SOL_SLUG_HOURS_AHEAD", str(BTC_SLUG_HOURS_AHEAD_DEFAULT))
            )
            _hours = max(2.0, min(_hours, 48.0))
            _cap = int(
                os.getenv("BTC_SLUG_MAX")
                or os.getenv("SOL_SLUG_MAX", str(BTC_SLUG_COUNT_CAP_DEFAULT))
            )
            _cap = max(24, min(_cap, 500))
            _range_stop = int(_hours * 3600 / _iv) + 2
            _range_stop = min(_range_stop, _cap - 1)

            _per_asset_cap = max(12, _cap // max(1, len(ACTIVE_ASSETS)))
            _range_stop = min(_range_stop, _per_asset_cap - 1)
            btc_slugs = build_slug_list(
                interval_seconds=_iv,
                unix_interval_start=unix_interval_start,
                range_stop=_range_stop,
                active_assets=ACTIVE_ASSETS,
            )

            filters = {
                "active": True,
                "closed": False,
                "archived": False,
                "slug": tuple(btc_slugs),
                "limit": max(200, min(500, len(btc_slugs))),
            }

            if node_restart_iteration > 1:
                logger.info(
                    f"Auto-restart iteration {node_restart_iteration}: reloading Gamma slug list "
                    f"({len(btc_slugs)} slugs)"
                )

            logger.info("=" * 80)
            logger.info("LOADING 5-MIN UP/DOWN MARKETS BY SLUG")
            logger.info(
                f"  Assets: {','.join(ACTIVE_ASSETS)} | Interval start: {unix_interval_start} | "
                f"Count: {len(btc_slugs)} (horizon {_hours}h, cap {_cap}, ~{_per_asset_cap}/asset; "
                f"avoid Gamma GET 414 — raise cap/hours only if needed)"
            )
            logger.info(f"  First: {btc_slugs[0]}  Last: {btc_slugs[-1]}")
            logger.info("=" * 80)

            instrument_cfg = PolymarketInstrumentProviderConfig(
                load_all=True,
                filters=filters,
                use_gamma_markets=True,
            )

            poly_data_cfg = PolymarketDataClientConfig(
                private_key=os.getenv("POLYMARKET_PK"),
                api_key=os.getenv("POLYMARKET_API_KEY"),
                api_secret=os.getenv("POLYMARKET_API_SECRET"),
                passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
                signature_type=3,
                funder=_funder,
                instrument_config=instrument_cfg,
                drop_quotes_missing_side=_drop_quotes_missing_side,
            )

            poly_exec_cfg = PolymarketExecClientConfig(
                private_key=os.getenv("POLYMARKET_PK"),
                api_key=os.getenv("POLYMARKET_API_KEY"),
                api_secret=os.getenv("POLYMARKET_API_SECRET"),
                passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
                signature_type=3,
                funder=_funder,
                instrument_config=instrument_cfg,
            )

            config = TradingNodeConfig(
                environment="live",
                trader_id="BTC-5MIN-INTEGRATED-001",
                logging=LoggingConfig(
                    log_level=nautilus_log,
                    log_directory="./logs/nautilus",
                ),
                data_engine=LiveDataEngineConfig(qsize=6000),
                exec_engine=LiveExecEngineConfig(
                    qsize=6000,
                    allow_overfills=True,
                    reconciliation=_exec_reconcile,
                    generate_missing_orders=_exec_gen_missing if _exec_reconcile else False,
                ),
                risk_engine=LiveRiskEngineConfig(bypass=_nautilus_risk_bypass),
                data_clients={POLYMARKET: poly_data_cfg},
                exec_clients={POLYMARKET: poly_exec_cfg},
            )

            strategy = IntegratedBTCStrategy(
                redis_client=redis_client,
                enable_grafana=enable_grafana,
                test_mode=test_mode,
                simulation=simulation,
            )

            print("\nBuilding Nautilus node...")
            node = TradingNode(config=config, loop=_node_loop)
            node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
            node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
            node.trader.add_strategy(strategy)
            node.build()
            set_active_trading_node_for_shutdown(node)
            logger.info("Nautilus node built successfully")

            print()
            print("=" * 80)
            print("BOT STARTING")
            print("=" * 80)

            user_interrupt = False
            reason_after_run: Optional[str] = None
            try:
                node.run()
            except KeyboardInterrupt:
                print("\nShutting down...")
                user_interrupt = True
            finally:
                reason_after_run = _last_graceful_stop_reason
                _dispose_timeout = float(os.getenv("NODE_DISPOSE_TIMEOUT_SEC", "180"))
                logger.info(
                    f"Node run finished (user_interrupt={user_interrupt}); "
                    f"graceful_stop_reason={reason_after_run!r} — disposing "
                    f"(timeout {_dispose_timeout}s)…"
                )
                _dispose_ok = _dispose_trading_node_with_timeout(
                    node,
                    _dispose_timeout,
                    context=f"iter={node_restart_iteration} reason={reason_after_run!r}",
                )
                set_active_trading_node_for_shutdown(None)
                if _dispose_ok:
                    logger.info("Trading node disposed")
                else:
                    logger.warning(
                        "Trading node dispose did not finish in time — next TradingNode will still start"
                    )

            if user_interrupt:
                break
            if reason_after_run in ("auto-restart", "markets-exhausted"):
                logger.info(
                    f"Restart reason: {reason_after_run} — sleeping 2s then building "
                    f"next TradingNode with fresh Gamma slug list"
                )
                time.sleep(2.0)
                continue
            break
    finally:
        if _auto_redeem_stop is not None:
            _auto_redeem_stop.set()
        if _auto_redeem_thread is not None:
            _auto_redeem_thread.join(timeout=15.0)
    logger.info("Bot stopped")

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Integrated BTC 5-Min Trading Bot")
    parser.add_argument("--live", action="store_true",
                        help="Run in LIVE mode (real money at risk!). Default is simulation.")
    parser.add_argument("--no-grafana", action="store_true", help="Disable Grafana metrics")
    parser.add_argument("--test-mode", action="store_true",
                        help="Simulation with live predictor pipeline and log.txt (faster eval/skip defaults)")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Full Loguru + Nautilus INFO logs (default is quiet: important only)",
    )

    args = parser.parse_args()
    if args.verbose:
        os.environ["BOT_VERBOSE"] = "1"
    enable_grafana = not args.no_grafana
    test_mode = args.test_mode

    # --test-mode ALWAYS forces simulation even if --live is also passed
    if args.test_mode:
        simulation = True
    else:
        simulation = not args.live

    if not simulation:
        logger.warning("=" * 80)
        logger.warning("LIVE TRADING MODE — REAL MONEY AT RISK!")
        logger.warning("=" * 80)
    else:
        logger.info("=" * 80)
        if test_mode:
            logger.info(
                "SIMULATION MODE — TEST MODE: predictor + log.txt (same format as --live, no CLOB)"
            )
        else:
            logger.info("SIMULATION MODE — paper trading only")
        logger.info("No real orders will be placed.")
        logger.info("=" * 80)

    run_integrated_bot(simulation=simulation, enable_grafana=enable_grafana, test_mode=test_mode)


if __name__ == "__main__":
    main()