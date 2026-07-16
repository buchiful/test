"""通知済み物件の記録。同じ物件を毎回通知しないための重複排除。

価格が5%以上変動した場合は再通知の対象にする。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Listing

logger = logging.getLogger(__name__)

PRICE_CHANGE_THRESHOLD = 0.05


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state ファイルの読み込みに失敗 (%s)。空の状態から開始します", exc)
        return {}


def save(path: str | Path, state: dict) -> None:
    Path(path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def select_new(listings: list[Listing], state: dict) -> list[Listing]:
    """未通知、または価格が大きく変動した物件だけを返す。"""
    new: list[Listing] = []
    for l in listings:
        prev = state.get(l.id)
        if prev is None:
            new.append(l)
            continue
        prev_price = prev.get("price_per_night", 0) or 0
        if prev_price and abs(l.price_per_night - prev_price) / prev_price >= PRICE_CHANGE_THRESHOLD:
            new.append(l)
    return new


def mark_notified(listings: list[Listing], state: dict) -> None:
    for l in listings:
        state[l.id] = {
            "name": l.name,
            "price_per_night": l.price_per_night,
        }
