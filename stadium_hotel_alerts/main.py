"""Philippine Sports Stadium 周辺のホテル・Airbnb 空室監視。

実行方法:
    python -m stadium_hotel_alerts.main [--dry-run] [--config PATH]

--dry-run を付けるとメール送信・state 更新を行わず結果を標準出力に表示する。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

from . import emailer, fx, state as state_mod, travel_time
from .models import Listing
from .providers import airbnb_provider, hotels_serpapi

logger = logging.getLogger(__name__)


def apply_filters(listings: list[Listing], config: dict) -> list[Listing]:
    """予算・評価・エアコンの条件でフィルタする (距離は別途)。"""
    f = config["filters"]
    out: list[Listing] = []
    for l in listings:
        if not (f["min_price_per_night"] <= l.price_per_night <= f["max_price_per_night"]):
            continue
        if l.rating is None or l.rating < f["min_rating"]:
            continue
        if f.get("require_air_conditioning") and l.has_air_conditioning is False:
            # True または None(不明) は通す。明確に「なし」のみ除外
            continue
        out.append(l)
    return out


def attach_drive_times(listings: list[Listing], config: dict) -> None:
    loc = config["location"]
    t = config.get("travel", {})
    dests = [
        (l.lat, l.lng) if l.lat is not None and l.lng is not None else None
        for l in listings
    ]
    minutes = travel_time.drive_minutes(
        (loc["lat"], loc["lng"]),
        dests,
        traffic_factor=t.get("traffic_factor", 1.4),
        fallback_speed_kmh=t.get("fallback_speed_kmh", 30.0),
    )
    for l, m in zip(listings, minutes):
        l.drive_minutes = m


def split_by_distance(listings: list[Listing], config: dict
                      ) -> tuple[list[Listing], list[Listing]]:
    """(20分以内の優先リスト, 20〜40分のリスト) に分ける。圏外は捨てる。"""
    f = config["filters"]
    preferred, others = [], []
    for l in listings:
        if l.drive_minutes is None:
            continue  # 所要時間が計算できない物件は除外
        if l.drive_minutes <= f["preferred_drive_minutes"]:
            preferred.append(l)
        elif l.drive_minutes <= f["max_drive_minutes"]:
            others.append(l)
    key = lambda l: (l.drive_minutes, l.price_per_night)
    return sorted(preferred, key=key), sorted(others, key=key)


def attach_max_stay(matched: list[Listing], config: dict) -> None:
    """宿泊候補期間の範囲内 (stay_range_start〜stay_range_end) での
    最大連続宿泊数を求める。

    各物件はすでに検索ループで少なくとも1つの候補期間 (l.available_windows)
    で予約可能と分かっているので、まずその中で最長のものを基準値とする。
    さらにその基準値が範囲全体の泊数に届いていない物件だけ、範囲全体を
    一括予約できるか追加で確認する (ヒットがあったときだけ1回検索が走る)。
    """
    s = config["search"]
    range_start = date.fromisoformat(s["stay_range_start"])
    range_end = date.fromisoformat(s["stay_range_end"])
    full_nights = (range_end - range_start).days

    for l in matched:
        best_nights, best_window = 0, None
        for ci, co in l.available_windows:
            nights = (date.fromisoformat(co) - date.fromisoformat(ci)).days
            if nights > best_nights:
                best_nights, best_window = nights, (ci, co)
        l.max_stay_nights = best_nights
        l.max_stay_check_in, l.max_stay_check_out = best_window or (None, None)

    if full_nights <= 0:
        return
    to_check = [l for l in matched if l.max_stay_nights < full_nights]
    if not to_check:
        return

    range_start_s, range_end_s = s["stay_range_start"], s["stay_range_end"]
    available: set[str] = set()
    try:
        if config["providers"].get("hotels") and any(l.source == "hotel" for l in to_check):
            available |= {x.id for x in
                          hotels_serpapi.search(config, range_start_s, range_end_s)}
        if config["providers"].get("airbnb") and any(l.source == "airbnb" for l in to_check):
            available |= {x.id for x in
                          airbnb_provider.search(config, range_start_s, range_end_s)}
    except Exception as exc:
        logger.warning("連泊確認 (%s〜%s) に失敗: %s", range_start_s, range_end_s, exc)
        return

    for l in to_check:
        if l.id in available:
            l.max_stay_nights = full_nights
            l.max_stay_check_in, l.max_stay_check_out = range_start_s, range_end_s


def dedupe(listings: list[Listing]) -> list[Listing]:
    """同一IDの重複をまとめる。価格は最安値を採用し、予約可能だった
    宿泊期間 (available_windows) はすべて合算する。"""
    seen: dict[str, Listing] = {}
    for l in listings:
        prev = seen.get(l.id)
        if prev is None:
            seen[l.id] = l
            continue
        prev.available_windows |= l.available_windows
        if l.price_per_night < prev.price_per_night:
            l.available_windows = prev.available_windows
            seen[l.id] = l
    return list(seen.values())


def run(config_path: Path, dry_run: bool) -> int:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    listings: list[Listing] = []
    for stay in config["search"]["stays"]:
        ci, co = stay["check_in"], stay["check_out"]
        batch: list[Listing] = []
        if config["providers"].get("hotels"):
            batch += hotels_serpapi.search(config, ci, co)
        if config["providers"].get("airbnb"):
            batch += airbnb_provider.search(config, ci, co)
        for l in batch:
            l.available_windows = {(ci, co)}
        listings += batch
    listings = dedupe(listings)
    logger.info("取得合計: %d 件", len(listings))

    listings = apply_filters(listings, config)
    logger.info("予算・評価・エアコン条件の通過: %d 件", len(listings))

    attach_drive_times(listings, config)
    preferred, others = split_by_distance(listings, config)
    logger.info("距離条件の通過: 優先圏内 %d 件 / それ以外 %d 件",
                len(preferred), len(others))

    # search_all はゲスト数を指定できないため、候補に残った Airbnb 物件だけ
    # 詳細 API で人数分の定員があるか検証する
    preferred = airbnb_provider.verify_guests(preferred, config)
    others = airbnb_provider.verify_guests(others, config)
    matched = preferred + others

    attach_max_stay(matched, config)

    state_path = config_path.parent / config.get("state_file", "state.json")
    state = state_mod.load(state_path)
    new_preferred = state_mod.select_new(preferred, state)
    new_others = state_mod.select_new(others, state)

    if not new_preferred and not new_others:
        logger.info("新規の物件はありません。メールは送信しません")
        return 0

    e = config["email"]
    fx_rate = fx.get_rate(config["search"].get("currency", "PHP"),
                          e.get("second_currency", "JPY"),
                          e.get("fallback_php_to_jpy", 3.4))
    subject, text_body, html_body = emailer.build_email(
        new_preferred, new_others, config, fx_rate)

    if dry_run:
        print("=" * 60)
        print("DRY RUN (メールは送信しません)")
        print("Subject:", subject)
        print("-" * 60)
        print(text_body)
        return 0

    emailer.send(subject, text_body, html_body, config)
    state_mod.mark_notified(matched, state)
    state_mod.save(state_path, state)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).parent / "config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="メール送信せず結果を表示する")
    args = parser.parse_args()
    return run(args.config, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
