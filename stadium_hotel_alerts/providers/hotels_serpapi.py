"""SerpAPI の Google Hotels エンジンでホテルの空室を検索する。

環境変数 SERPAPI_API_KEY が必要 (https://serpapi.com/ 無料枠あり)。
返ってくるのは指定日程で予約可能な物件のみなので、結果 = 空室あり。
"""

from __future__ import annotations

import logging
import os

import requests

from ..models import Listing

logger = logging.getLogger(__name__)

SEARCH_URL = "https://serpapi.com/search.json"
MAX_PAGES = 3

AIRCON_KEYWORDS = ("air conditioning", "air-conditioned", "air conditioned")


def _has_aircon(prop: dict) -> bool | None:
    amenities = prop.get("amenities") or []
    if not amenities:
        return None  # 不明
    joined = " | ".join(a.lower() for a in amenities if isinstance(a, str))
    return any(k in joined for k in AIRCON_KEYWORDS)


def search(config: dict, check_in: str, check_out: str) -> list[Listing]:
    """指定日程で予約可能なホテルを返す。"""
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        logger.warning("SERPAPI_API_KEY が未設定のためホテル検索をスキップします")
        return []

    s = config["search"]
    f = config["filters"]
    loc = config["location"]

    params = {
        "engine": "google_hotels",
        "q": f"hotels near {loc['name']} Bulacan Philippines",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": s["adults"],
        "currency": s.get("currency", "PHP"),
        "gl": "ph",
        "hl": "en",
        "min_price": f["min_price_per_night"],
        "max_price": f["max_price_per_night"],
        "api_key": api_key,
    }

    listings: list[Listing] = []
    next_page_token = None
    for _ in range(MAX_PAGES):
        page_params = dict(params)
        if next_page_token:
            page_params["next_page_token"] = next_page_token
        try:
            resp = requests.get(SEARCH_URL, params=page_params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("SerpAPI request failed: %s", exc)
            break

        for prop in data.get("properties", []):
            rate = prop.get("rate_per_night") or {}
            price = rate.get("extracted_lowest")
            if price is None:
                continue  # 料金が取れない = 空室情報なしとして除外
            gps = prop.get("gps_coordinates") or {}
            prop_token = prop.get("property_token") or prop.get("name", "")
            try:
                review_count = int(prop["reviews"]) if prop.get("reviews") is not None else None
            except (TypeError, ValueError):
                review_count = None
            listings.append(Listing(
                id=f"hotel:{prop_token}",
                source="hotel",
                name=prop.get("name", "(名称不明)"),
                url=prop.get("link") or prop.get("serpapi_property_details_link", ""),
                price_per_night=float(price),
                rating=prop.get("overall_rating"),
                review_count=review_count,
                lat=gps.get("latitude"),
                lng=gps.get("longitude"),
                has_air_conditioning=_has_aircon(prop),
                extra={"hotel_class": prop.get("hotel_class")},
            ))

        next_page_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not next_page_token:
            break

    logger.info("SerpAPI hotels (%s〜%s): %d 件取得", check_in, check_out,
                len(listings))
    return listings
