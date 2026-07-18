"""表示用の為替レート取得。

無料の為替 API (frankfurter.app, ECB 基準レート) を使い、失敗した場合は
config の固定レートにフォールバックする。travel_time.py の
OSRM→直線距離フォールバックと同じ設計。
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

FX_URL = "https://api.frankfurter.app/latest"


def get_rate(base: str, target: str, fallback: float) -> float:
    """1 base 通貨あたりの target 通貨レートを返す。失敗時は fallback。"""
    try:
        resp = requests.get(FX_URL, params={"from": base, "to": target}, timeout=15)
        resp.raise_for_status()
        rate = resp.json()["rates"][target]
        return float(rate)
    except Exception as exc:
        logger.warning("為替レート取得に失敗 (%s→%s): %s — 固定レート %.4f を使用します",
                       base, target, exc, fallback)
        return fallback
