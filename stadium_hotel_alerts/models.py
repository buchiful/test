"""共通のデータモデル。"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus


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
    # 必須期間を含む最大連続宿泊 (attach_max_stay で設定)
    max_stay_nights: int | None = None
    max_stay_check_in: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def maps_url(self) -> str | None:
        """Google マップで物件の場所を開く URL。

        ホテルは施設名で検索 (店舗ページが開き、レビュー等も見られる)。
        Airbnb は物件名が汎用的で名前検索が当たらないため座標ピンを使う。
        """
        if self.source == "hotel" and self.name and self.name != "(名称不明)":
            return ("https://www.google.com/maps/search/?api=1&query="
                    + quote_plus(self.name))
        if self.lat is not None and self.lng is not None:
            return (f"https://www.google.com/maps/search/?api=1"
                    f"&query={self.lat},{self.lng}")
        return None
