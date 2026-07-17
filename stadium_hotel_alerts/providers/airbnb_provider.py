"""pyairbnb (非公式 API) で Airbnb の空室を検索する。

API キーは不要だが、Airbnb 側の仕様変更で動かなくなる可能性があるため、
失敗してもホテル検索側に影響しないよう防御的に書いている。
検索結果は指定日程で予約可能な物件のみ = 空室あり。
"""

from __future__ import annotations

import logging
import math

from ..models import Listing

logger = logging.getLogger(__name__)

# Airbnb のアメニティ ID: 5 = Air conditioning
AIRCON_AMENITY_ID = 5


def _bounding_box(lat: float, lng: float, radius_km: float) -> dict:
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * math.cos(math.radians(lat)))
    return {
        "ne_lat": lat + dlat,
        "ne_long": lng + dlng,
        "sw_lat": lat - dlat,
        "sw_long": lng - dlng,
    }


def _get(d: dict, *keys, default=None):
    """ネストした dict から最初に見つかったキーの値を返す。"""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def _parse_result(r: dict, nights: int) -> Listing | None:
    room_id = _get(r, "room_id", "id", "listing_id")
    if room_id is None:
        room_id = _get(r.get("listing", {}) if isinstance(r.get("listing"), dict) else {}, "id")
    if room_id is None:
        return None

    name = _get(r, "name", "title", default="")
    if not name and isinstance(r.get("listing"), dict):
        name = _get(r["listing"], "name", "title", default="(名称不明)")

    rating = _get(r, "rating")
    if isinstance(rating, dict):
        rating = _get(rating, "value", "guestSatisfaction", "average")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    # 価格: pyairbnb は price.unit / price.total などの形で返す
    price_per_night = None
    price = r.get("price")
    if isinstance(price, dict):
        unit = price.get("unit")
        if isinstance(unit, dict):
            price_per_night = _get(unit, "amount", "price")
        if price_per_night is None:
            total = price.get("total")
            if isinstance(total, dict):
                amount = _get(total, "amount", "price")
                if amount is not None and nights > 0:
                    price_per_night = float(amount) / nights
    elif isinstance(price, (int, float)):
        price_per_night = price
    if price_per_night is None:
        return None
    try:
        price_per_night = float(price_per_night)
    except (TypeError, ValueError):
        return None

    coords = r.get("coordinates") or {}
    lat = _get(coords, "latitude", "latitud", "lat") or _get(r, "lat", "latitude")
    lng = _get(coords, "longitude", "longitud", "lng", "long") or _get(r, "lng", "longitude")

    return Listing(
        id=f"airbnb:{room_id}",
        source="airbnb",
        name=name or "(名称不明)",
        url=f"https://www.airbnb.com/rooms/{room_id}",
        price_per_night=round(price_per_night, 0),
        rating=rating,
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
        # アメニティ ID でエアコン絞り込み済みの検索結果なら True
        has_air_conditioning=True,
    )


def search(config: dict, check_in: str | None = None,
           check_out: str | None = None) -> list[Listing]:
    """指定日程 (省略時は config の日程) で予約可能な Airbnb を返す。"""
    try:
        import pyairbnb
    except ImportError:
        logger.warning("pyairbnb が未インストールのため Airbnb 検索をスキップします")
        return []

    s = config["search"]
    f = config["filters"]
    loc = config["location"]
    box = _bounding_box(loc["lat"], loc["lng"], loc.get("search_radius_km", 30))

    check_in = check_in or s["check_in"]
    check_out = check_out or s["check_out"]
    from datetime import date
    nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days

    kwargs = dict(
        check_in=check_in,
        check_out=check_out,
        ne_lat=box["ne_lat"],
        ne_long=box["ne_long"],
        sw_lat=box["sw_lat"],
        sw_long=box["sw_long"],
        zoom_value=10,
        price_min=f["min_price_per_night"],
        price_max=f["max_price_per_night"],
        currency=s.get("currency", "PHP"),
        language="en",
        proxy_url="",
    )

    aircon_filtered = True
    try:
        try:
            results = pyairbnb.search_all(amenities=[AIRCON_AMENITY_ID], **kwargs)
        except TypeError:
            # 古い pyairbnb は amenities 引数をサポートしない
            aircon_filtered = False
            results = pyairbnb.search_all(**kwargs)
    except Exception as exc:
        logger.error("Airbnb 検索に失敗しました: %s", exc)
        return []

    listings: list[Listing] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        listing = _parse_result(r, nights)
        if listing is None:
            continue
        if not aircon_filtered:
            listing.has_air_conditioning = None  # 不明として扱う
        listings.append(listing)

    logger.info("Airbnb (%s〜%s): %d 件取得", check_in, check_out, len(listings))
    return listings
