"""共通のデータモデル。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    """ホテル / Airbnb を横断する物件情報。"""

    id: str                      # プロバイダ内で一意なID (例: "hotel:xxx", "airbnb:12345")
    source: str                  # "hotel" | "airbnb"
    name: str
    url: str
    price_per_night: float       # PHP
    rating: float | None
    lat: float | None
    lng: float | None
    has_air_conditioning: bool | None  # None = 不明
    drive_minutes: float | None = None
    extra: dict = field(default_factory=dict)
