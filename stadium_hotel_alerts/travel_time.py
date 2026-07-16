"""スタジアムから各物件までの車での所要時間を求める。

OSRM のデモサーバー (router.project-osrm.org) を使い、失敗した場合は
直線距離 ÷ 平均時速のフォールバック推定を使う。OSRM は渋滞を考慮しない
自由流の時間を返すため、traffic_factor を掛けて補正する。
"""

from __future__ import annotations

import logging
import math

import requests

logger = logging.getLogger(__name__)

OSRM_BASE = "https://router.project-osrm.org"
# OSRM の table サービスに一度に渡す座標数の上限 (デモサーバーの制限に配慮)
BATCH_SIZE = 80


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _osrm_batch(origin: tuple[float, float],
                dests: list[tuple[float, float]]) -> list[float | None]:
    """OSRM table API で origin→各 dest の所要時間(分)を返す。失敗時は例外。"""
    coords = [f"{origin[1]},{origin[0]}"] + [f"{lng},{lat}" for lat, lng in dests]
    url = (
        f"{OSRM_BASE}/table/v1/driving/{';'.join(coords)}"
        f"?sources=0&annotations=duration"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('code')}")
    durations = data["durations"][0][1:]  # 先頭は origin 自身
    return [d / 60.0 if d is not None else None for d in durations]


def drive_minutes(origin: tuple[float, float],
                  destinations: list[tuple[float, float] | None],
                  traffic_factor: float = 1.4,
                  fallback_speed_kmh: float = 30.0) -> list[float | None]:
    """origin から各 destination までの推定所要時間(分)のリストを返す。

    destination が None (座標不明) の場合は None を返す。
    """
    results: list[float | None] = [None] * len(destinations)
    valid = [(i, d) for i, d in enumerate(destinations) if d is not None]
    if not valid:
        return results

    use_fallback = False
    for start in range(0, len(valid), BATCH_SIZE):
        batch = valid[start:start + BATCH_SIZE]
        dests = [d for _, d in batch]
        minutes: list[float | None]
        factor = traffic_factor
        if not use_fallback:
            try:
                minutes = _osrm_batch(origin, dests)
            except Exception as exc:  # ネットワーク/レート制限など
                logger.warning("OSRM unavailable (%s); falling back to haversine", exc)
                use_fallback = True
                minutes = []
        if use_fallback:
            # フォールバックの平均時速は渋滞込みの想定なので係数は掛けない
            factor = 1.0
            minutes = [
                haversine_km(origin[0], origin[1], lat, lng) / fallback_speed_kmh * 60
                for lat, lng in dests
            ]
        for (idx, _), m in zip(batch, minutes):
            if m is not None:
                results[idx] = round(m * factor, 1)
    return results
