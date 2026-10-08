"""Polymarket 5-minute up/down asset registry (btc, eth, sol, …)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SUPPORTED_ASSET_IDS: Tuple[str, ...] = (
    "btc",
    "eth",
    "sol",
    "xrp",
    "doge",
    "hype",
    "bnb",
)


@dataclass(frozen=True)
class UpDownAsset:
    asset_id: str
    label: str
    slug_prefix: str
    coinbase_product_id: str


ASSET_REGISTRY: Dict[str, UpDownAsset] = {
    "btc": UpDownAsset("btc", "Bitcoin", "btc-updown-5m", "BTC-USD"),
    "eth": UpDownAsset("eth", "Ethereum", "eth-updown-5m", "ETH-USD"),
    "sol": UpDownAsset("sol", "Solana", "sol-updown-5m", "SOL-USD"),
    "xrp": UpDownAsset("xrp", "XRP", "xrp-updown-5m", "XRP-USD"),
    "doge": UpDownAsset("doge", "Dogecoin", "doge-updown-5m", "DOGE-USD"),
    "hype": UpDownAsset("hype", "HYPE", "hype-updown-5m", "HYPE-USD"),
    "bnb": UpDownAsset("bnb", "BNB", "bnb-updown-5m", "BNB-USD"),
}


def normalize_asset_id(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    if key in ASSET_REGISTRY:
        return key
    return None


def asset_from_slug(slug: str) -> Optional[str]:
    s = (slug or "").strip().lower()
    for aid, meta in ASSET_REGISTRY.items():
        if s.startswith(f"{meta.slug_prefix}-"):
            return aid
    return None


def slug_prefix_for_asset(asset_id: str) -> str:
    meta = ASSET_REGISTRY.get(asset_id)
    if not meta:
        raise ValueError(f"Unknown asset: {asset_id}")
    return meta.slug_prefix


def coinbase_product_for_asset(asset_id: str) -> str:
    meta = ASSET_REGISTRY.get(asset_id)
    if not meta:
        raise ValueError(f"Unknown asset: {asset_id}")
    return meta.coinbase_product_id


def parse_active_assets() -> List[str]:
    """
    Resolve active assets from env.

    Priority:
    1. TRADING_ASSETS (comma-separated)
    2. TRADING_ASSET (single)
    3. Legacy BTC_UPDOWN_SLUG_PREFIX / SOL_UPDOWN_SLUG_PREFIX
    4. Default btc
    """
    raw_list = os.getenv("TRADING_ASSETS", "").strip()
    if raw_list:
        out: List[str] = []
        for part in raw_list.split(","):
            aid = normalize_asset_id(part)
            if aid and aid not in out:
                out.append(aid)
        if out:
            return out

    single = normalize_asset_id(os.getenv("TRADING_ASSET", ""))
    if single:
        return [single]

    legacy_prefix = (
        os.getenv("BTC_UPDOWN_SLUG_PREFIX")
        or os.getenv("SOL_UPDOWN_SLUG_PREFIX")
        or ""
    ).strip().lower()
    if legacy_prefix:
        for aid, meta in ASSET_REGISTRY.items():
            if meta.slug_prefix.lower() == legacy_prefix:
                return [aid]

    override_product = (os.getenv("COINBASE_PRODUCT_ID") or "").strip().upper()
    if override_product and legacy_prefix:
        return ["btc"]

    return ["btc"]


def active_slug_prefixes(active_assets: List[str]) -> List[str]:
    return [slug_prefix_for_asset(a) for a in active_assets]


def build_slug_list(
    *,
    interval_seconds: int,
    unix_interval_start: int,
    range_stop: int,
    active_assets: List[str],
) -> List[str]:
    slugs: List[str] = []
    for i in range(-1, range_stop):
        timestamp = unix_interval_start + (i * interval_seconds)
        for asset_id in active_assets:
            slugs.append(f"{slug_prefix_for_asset(asset_id)}-{timestamp}")
    return slugs


def is_tracked_updown_slug(slug: str, active_assets: List[str]) -> bool:
    s = (slug or "").strip().lower()
    if not s:
        return False
    for asset_id in active_assets:
        pfx = slug_prefix_for_asset(asset_id).lower()
        if s.startswith(f"{pfx}-"):
            return True
    return False


def trade_key_parts(trade_key: tuple, default_asset: str) -> Tuple[str, int]:
    """Return (asset_id, slug_start_ts) from predictor trade_key."""
    if not trade_key:
        return default_asset, 0
    if len(trade_key) >= 2 and isinstance(trade_key[0], str):
        first = str(trade_key[0]).strip().lower()
        if first in ASSET_REGISTRY and not first.isdigit():
            return first, int(trade_key[1])
    return default_asset, int(trade_key[0])


def make_trade_key(asset_id: str, slug_ts: int, multi_asset: bool) -> tuple:
    if multi_asset:
        return (asset_id, slug_ts, 0)
    return (slug_ts, 0)
