"""Philippine Sports Stadium 周辺のホテル・Airbnb 空室監視。

実行方法:
    python -m stadium_hotel_alerts.main [--dry-run] [--config PATH]

--dry-run を付けるとメール送信・state 更新を行わず結果を標準出力に表示する。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import emailer, state as state_mod, travel_time
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
    """必須期間 (check_in〜check_out) を含む最大連続宿泊数を求める。

    earliest_check_in から check_in まで1日ずつ遡り、長い滞在から順に
    「そのチェックイン日〜check_out で予約可能か」をプロバイダに問い合わせる。
    一括予約が可能 = その期間ずっと連続で泊まれる、とみなす。
    条件に合致した物件 (matched) についてのみ確認するので、追加の検索は
    ヒットがあったときにしか走らない。
    """
    s = config["search"]
    base_in = date.fromisoformat(s["check_in"])
    base_out = date.fromisoformat(s["check_out"])
    for l in matched:
        l.max_stay_nights = (base_out - base_in).days
        l.max_stay_check_in = s["check_in"]

    earliest = s.get("earliest_check_in")
    if not earliest or not matched:
        return

    remaining = list(matched)
    d = date.fromisoformat(earliest)
    while d < base_in and remaining:
        check_in = d.isoformat()
        nights = (base_out - d).days
        available: set[str] = set()
        try:
            if config["providers"].get("hotels") and \
                    any(l.source == "hotel" for l in remaining):
                available |= {x.id for x in
                              hotels_serpapi.search(config, check_in, s["check_out"])}
            if config["providers"].get("airbnb") and \
                    any(l.source == "airbnb" for l in remaining):
                available |= {x.id for x in
                              airbnb_provider.search(config, check_in, s["check_out"])}
        except Exception as exc:
            logger.warning("連泊確認 (%s〜) に失敗: %s", check_in, exc)
        for l in [r for r in remaining if r.id in available]:
            l.max_stay_nights = nights
            l.max_stay_check_in = check_in
            remaining.remove(l)
        d += timedelta(days=1)


def dedupe(listings: list[Listing]) -> list[Listing]:
    seen: dict[str, Listing] = {}
    for l in listings:
        if l.id not in seen or l.price_per_night < seen[l.id].price_per_night:
            seen[l.id] = l
    return list(seen.values())


def run(config_path: Path, dry_run: bool) -> int:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    listings: list[Listing] = []
    if config["providers"].get("hotels"):
        listings += hotels_serpapi.search(config)
    if config["providers"].get("airbnb"):
        listings += airbnb_provider.search(config)
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

    subject, text_body, html_body = emailer.build_email(
        new_preferred, new_others, config)

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
