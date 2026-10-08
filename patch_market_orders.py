"""
Patch for PolymarketExecutionClient to support $1 market buys.


The Polymarket adapter normally requires quote_quantity=True for BUY market orders,
but our strategy sends token quantities (quote_quantity=False).
This patch intercepts BUY market orders and forces them to use the configured
USD amount ($1 default) via the create_market_order API call.


How Polymarket market orders work:
  - BUY:  amount = USD to spend  (e.g. 1.0 = spend $1)
  - SELL: amount = tokens to sell (e.g. 5.0 = sell 5 tokens)


Minimum order: $1 USD for market BUY orders (NOT 5 tokens).
The 5-token minimum only applies to LIMIT orders.


CLOB "no match" (PolyException): py_clob_client_v2 could not find resting liquidity on the
relevant side of the book (e.g. empty asks for a market BUY). Retries help with brief
API/latency gaps; a persistently empty book means no one is offering that token on the CLOB.


HTTP 400 after signing — "no orders found to match with FAK order": the signed IOC/FAK hit
the wire but nothing matched at submit time. We re-sign (fresh order id) and re-post using the
same MARKET_ORDER_CLOB_RETRIES / MARKET_ORDER_CLOB_RETRY_MS knobs without emitting
generate_order_submitted again or rejecting until retries are exhausted.

Transient network errors (read timeout, Request exception) use the same re-sign path.
"""


import asyncio
import logging
import os


logger = logging.getLogger(__name__)


_patch_applied = False




def apply_market_order_patch():
    """Apply monkey patch to PolymarketExecutionClient."""
    global _patch_applied


    if _patch_applied:
        logger.info("Market order patch already applied")
        return True


    try:
        from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient
        from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_token_id
        from nautilus_trader.adapters.polymarket.common.constants import (
            POLYMARKET_NAUTILUS_BUILDER_CODE,
        )
        from nautilus_trader.adapters.polymarket.http.conversion import convert_tif_to_polymarket_order_type
        from nautilus_trader.model.enums import OrderSide, order_side_to_str
        from nautilus_trader.common.enums import LogColor
        from py_clob_client_v2.clob_types import MarketOrderArgsV2, PartialCreateOrderOptions
        from py_clob_client_v2.exceptions import PolyException


        # --- Read USD amount from environment (default $10) ---
        _DEFAULT_USD_AMOUNT = float(os.getenv("MARKET_BUY_USD", "1000"))
        # FAK no-match + HTTP timeouts: more attempts + longer gaps help thin books right after round open.
        _RETRIES = max(1, min(16, int(os.getenv("MARKET_ORDER_CLOB_RETRIES", "8"))))
        _RETRY_MS = max(50, min(8000, int(os.getenv("MARKET_ORDER_CLOB_RETRY_MS", "1000"))))
        logger.info(
            f"Market BUY USD amount configured to: ${_DEFAULT_USD_AMOUNT:.2f} "
            f"| CLOB liquidity retries: {_RETRIES} @ {_RETRY_MS}ms"
        )


        def _is_no_book_liquidity(exc: BaseException) -> bool:
            raw = getattr(exc, "msg", None)
            msg = (str(raw) if raw is not None else "") + str(exc)
            msg = msg.lower()
            return "no match" in msg or "no orderbook" in msg


        def _is_transient_network_error(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return (
                "timed out" in msg
                or "timeout" in msg
                or "request exception" in msg
                or ("connection" in msg and ("reset" in msg or "refused" in msg or "aborted" in msg))
            )


        def _is_fak_post_no_match(reason: str) -> bool:
            r = (reason or "").lower()
            if not r or r == "none":
                return False
            return "no orders found to match" in r or (
                "fak" in r and "match" in r and "no orders" in r
            )


        def _is_transient_submit_failure(reason: str) -> bool:
            """Re-sign and re-post for FAK miss, HTTP read timeouts, and generic request failures."""
            r = (reason or "").lower()
            if _is_fak_post_no_match(reason):
                return True
            if "request exception" in r:
                return True
            if "timed out" in r or "timeout" in r:
                return True
            if "read operation" in r and "timed out" in r:
                return True
            if "connection" in r and ("reset" in r or "refused" in r or "aborted" in r):
                return True
            return False


        class _FAKPostRetry(Exception):
            __slots__ = ("reason",)


            def __init__(self, reason: str) -> None:
                self.reason = reason


        _orig_post_signed_order = PolymarketExecutionClient._post_signed_order


        async def _patched_post_signed_order(
            self,
            order,
            signed_order,
            post_only: bool = False,
            order_type_override=None,
            base_quantity=None,
        ):
            from nautilus_trader.model.enums import OrderType


            if order.order_type != OrderType.MARKET:
                return await _orig_post_signed_order(
                    self,
                    order,
                    signed_order,
                    post_only=post_only,
                    order_type_override=order_type_override,
                    base_quantity=base_quantity,
                )


            resign_map = getattr(self, "_patch_market_resign_by_coid", None) or {}
            resign_fn = resign_map.get(order.client_order_id)
            current = signed_order


            for attempt in range(1, _RETRIES + 1):
                orig_reject = self.generate_order_rejected


                def make_wrapped(att: int):
                    def wrapped_reject(**kwargs):
                        reason = str(kwargs.get("reason", "") or "")
                        if (
                            att < _RETRIES
                            and resign_fn is not None
                            and _is_transient_submit_failure(reason)
                        ):
                            raise _FAKPostRetry(reason)
                        return orig_reject(**kwargs)


                    return wrapped_reject


                self.generate_order_rejected = make_wrapped(attempt)
                try:
                    await _orig_post_signed_order(
                        self,
                        order,
                        current,
                        post_only=post_only,
                        order_type_override=order_type_override,
                        base_quantity=base_quantity,
                    )
                    return
                except _FAKPostRetry as e:
                    delay_s = min(8.0, (_RETRY_MS / 1000.0) * (1.0 + 0.35 * (attempt - 1)))
                    self._log.warning(
                        f"[PATCH] Market post failed ({e.reason!s}); "
                        f"re-sign & retry {attempt}/{_RETRIES} after {delay_s * 1000:.0f}ms",
                        LogColor.YELLOW,
                    )
                    await asyncio.sleep(delay_s)
                    if resign_fn is None:
                        orig_reject(
                            strategy_id=order.strategy_id,
                            instrument_id=order.instrument_id,
                            client_order_id=order.client_order_id,
                            reason=e.reason,
                            ts_event=self._clock.timestamp_ns(),
                        )
                        return
                    try:
                        current = await resign_fn()
                    except Exception as resign_exc:
                        self._log.error(f"[PATCH] Re-sign after FAK miss failed: {resign_exc!s}")
                        orig_reject(
                            strategy_id=order.strategy_id,
                            instrument_id=order.instrument_id,
                            client_order_id=order.client_order_id,
                            reason=str(resign_exc),
                            ts_event=self._clock.timestamp_ns(),
                        )
                        return
                finally:
                    self.generate_order_rejected = orig_reject


            # Unreachable if _RETRIES >= 1 and reject paths return
            return


        async def _create_market_order_with_retry(self, market_order_args, options):
            last_exc = None
            for attempt in range(1, _RETRIES + 1):
                try:
                    return await asyncio.to_thread(
                        self._http_client.create_market_order,
                        market_order_args,
                        options=options,
                    )
                except PolyException as e:
                    last_exc = e
                    retryable = _is_no_book_liquidity(e) or _is_transient_network_error(e)
                    if not retryable or attempt >= _RETRIES:
                        raise
                    self._log.warning(
                        f"[PATCH] CLOB error during market order ({e!s}); "
                        f"retry {attempt}/{_RETRIES} after {_RETRY_MS}ms",
                        LogColor.YELLOW,
                    )
                    await asyncio.sleep(_RETRY_MS / 1000.0)
                except Exception as e:
                    last_exc = e
                    retryable = _is_no_book_liquidity(e) or _is_transient_network_error(e)
                    if not retryable or attempt >= _RETRIES:
                        raise
                    self._log.warning(
                        f"[PATCH] Market order transient error ({e!s}); "
                        f"retry {attempt}/{_RETRIES} after {_RETRY_MS}ms",
                        LogColor.YELLOW,
                    )
                    await asyncio.sleep(_RETRY_MS / 1000.0)
            if last_exc:
                raise last_exc
            raise RuntimeError("create_market_order_with_retry: empty loop")


        async def _patched_submit_market_order(self, command, instrument):
            """
            Patched market order handler.


            For BUY orders:  always use USD amount (default $1) via create_market_order.
            For SELL orders: use token quantity as normal (base-denominated).
            """
            order = command.order


            if order.side == OrderSide.BUY:
                # Read amount each call so live env changes take effect
                usd_amount = float(os.getenv("MARKET_BUY_USD", str(_DEFAULT_USD_AMOUNT)))


                self._log.info(
                    f"[PATCH] BUY market order → using ${usd_amount:.2f} USD "
                    f"(token qty ignored: {float(order.quantity):.6f})",
                    LogColor.MAGENTA,
                )


                order_type = convert_tif_to_polymarket_order_type(order.time_in_force)


                user_usdc_balance = await self._get_collateral_balance_pusd()
                market_order_args = MarketOrderArgsV2(
                    token_id=get_polymarket_token_id(order.instrument_id),
                    amount=usd_amount,  # USD to spend
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                    user_usdc_balance=user_usdc_balance,
                    builder_code=POLYMARKET_NAUTILUS_BUILDER_CODE,
                )


                neg_risk = self._get_neg_risk_for_instrument(instrument)
                options = PartialCreateOrderOptions(neg_risk=neg_risk)


                signing_start = self._clock.timestamp()
                signed_order = await _create_market_order_with_retry(
                    self, market_order_args, options
                )
                interval = self._clock.timestamp() - signing_start
                self._log.info(
                    f"[PATCH] Signed market BUY in {interval:.3f}s (${usd_amount:.2f})",
                    LogColor.BLUE,
                )


                self.generate_order_submitted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    ts_event=self._clock.timestamp_ns(),
                )


                async def resign():
                    return await _create_market_order_with_retry(
                        self, market_order_args, options
                    )


                if not hasattr(self, "_patch_market_resign_by_coid"):
                    self._patch_market_resign_by_coid = {}
                self._patch_market_resign_by_coid[order.client_order_id] = resign
                try:
                    await self._post_signed_order(order, signed_order)
                finally:
                    self._patch_market_resign_by_coid.pop(order.client_order_id, None)


            else:
                # SELL: use token quantity (base-denominated), standard behavior
                if order.is_quote_quantity:
                    self._deny_market_order_quantity(
                        order,
                        "Polymarket market SELL orders require base-denominated quantities; "
                        "resubmit with `quote_quantity=False`",
                    )
                    return


                amount = float(order.quantity)
                order_type = convert_tif_to_polymarket_order_type(order.time_in_force)


                market_order_args = MarketOrderArgsV2(
                    token_id=get_polymarket_token_id(order.instrument_id),
                    amount=amount,
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                    builder_code=POLYMARKET_NAUTILUS_BUILDER_CODE,
                )


                neg_risk = self._get_neg_risk_for_instrument(instrument)
                options = PartialCreateOrderOptions(neg_risk=neg_risk)


                signing_start = self._clock.timestamp()
                signed_order = await _create_market_order_with_retry(
                    self, market_order_args, options
                )
                interval = self._clock.timestamp() - signing_start
                self._log.info(f"Signed Polymarket market SELL in {interval:.3f}s", LogColor.BLUE)


                self.generate_order_submitted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    ts_event=self._clock.timestamp_ns(),
                )


                async def resign():
                    return await _create_market_order_with_retry(
                        self, market_order_args, options
                    )


                if not hasattr(self, "_patch_market_resign_by_coid"):
                    self._patch_market_resign_by_coid = {}
                self._patch_market_resign_by_coid[order.client_order_id] = resign
                try:
                    await self._post_signed_order(order, signed_order)
                finally:
                    self._patch_market_resign_by_coid.pop(order.client_order_id, None)


        # Nautilus RetryManager logs PolyApiException exhaustion as ERROR even when our outer
        # FAK re-sign loop will recover. Downgrade only submit_order + known FAK no-match text.
        from nautilus_trader.live.retry import RetryManager


        def _patched_retry_manager_log_error(self) -> None:
            message = f"Failed on {self.name}{self._details_str()}"
            if self.error_logger:
                self.error_logger(message, self.last_exception)
                return
            if (
                self.name == "submit_order"
                and self.last_exception is not None
                and _is_transient_submit_failure(str(self.last_exception))
            ):
                self.log.warning(message)
                return
            self.log.error(message)


        RetryManager._log_error = _patched_retry_manager_log_error


        # Apply the patch
        PolymarketExecutionClient._post_signed_order = _patched_post_signed_order
        PolymarketExecutionClient._submit_market_order = _patched_submit_market_order
        _patch_applied = True
        logger.info(
            "Market order patch applied — BUY orders will use $MARKET_BUY_USD (default $1); "
            "FAK no-match RetryManager noise → WARNING; transient submit failures too"
        )


        try:
            from patch_polymarket_trade_report_noise import apply_polymarket_trade_report_noise_patch


            apply_polymarket_trade_report_noise_patch()
        except Exception as e:
            logger.warning(f"Bundled trade-report noise patch failed (non-fatal): {e}")


        return True


    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to apply market order patch: {e}")
        import traceback
        traceback.print_exc()
        return False


