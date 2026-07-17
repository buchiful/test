"""条件に合致した物件をメールで通知する。

必要な環境変数:
  SMTP_USERNAME  送信元アカウント (例: Gmail アドレス)
  SMTP_PASSWORD  パスワード (Gmail の場合はアプリパスワード)
任意:
  SMTP_HOST      デフォルト smtp.gmail.com
  SMTP_PORT      デフォルト 465 (SSL)
  ALERT_EMAIL_TO 送信先の上書き (デフォルトは config の email.to)
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .models import Listing

logger = logging.getLogger(__name__)


def _fmt_md(iso_date: str) -> str:
    """"2027-03-13" → "3/13"。"""
    _, m, d = iso_date.split("-")
    return f"{int(m)}/{int(d)}"


def _stay_label(l: Listing, config: dict) -> str | None:
    """連続宿泊の表示。例: "最大4連泊可 (3/13〜3/17)"。"""
    if l.max_stay_nights is None or l.max_stay_check_in is None:
        return None
    check_out = _fmt_md(config["search"]["check_out"])
    return f"最大{l.max_stay_nights}連泊可 ({_fmt_md(l.max_stay_check_in)}〜{check_out})"


def _format_listing_text(l: Listing, config: dict) -> str:
    rating = f"{l.rating:.1f}" if l.rating is not None else "不明"
    drive = f"車で約{l.drive_minutes:.0f}分" if l.drive_minutes is not None else "所要時間不明"
    kind = "ホテル" if l.source == "hotel" else "Airbnb"
    lines = [
        f"- [{kind}] {l.name}",
        f"  1泊 {l.price_per_night:,.0f} PHP / 評価 {rating} / {drive}",
    ]
    stay = _stay_label(l, config)
    if stay:
        lines.append(f"  連泊: {stay}")
    lines.append(f"  {l.url}")
    if l.maps_url:
        lines.append(f"  地図: {l.maps_url}")
    return "\n".join(lines)


def _format_listing_html(l: Listing, config: dict) -> str:
    rating = f"{l.rating:.1f}" if l.rating is not None else "不明"
    drive = f"車で約{l.drive_minutes:.0f}分" if l.drive_minutes is not None else "所要時間不明"
    kind = "ホテル" if l.source == "hotel" else "Airbnb"
    maps = f' ・ <a href="{l.maps_url}">📍 地図</a>' if l.maps_url else ""
    stay = _stay_label(l, config)
    stay_html = f"<br>🛏 {stay}" if stay else ""
    return (
        f'<li><a href="{l.url}"><b>{l.name}</b></a> [{kind}]<br>'
        f"1泊 {l.price_per_night:,.0f} PHP ・ 評価 {rating} ・ {drive}{maps}"
        f"{stay_html}</li>"
    )


def build_email(preferred: list[Listing], others: list[Listing],
                config: dict) -> tuple[str, str, str]:
    """(subject, text_body, html_body) を返す。"""
    s = config["search"]
    total = len(preferred) + len(others)
    subject = (
        f"{config['email'].get('subject_prefix', '[Stadium Hotel Alert]')} "
        f"{s['check_in']}〜{s['check_out']} 空室 {total}件"
    )

    header = (
        f"Philippine Sports Stadium 周辺の空室が見つかりました。\n"
        f"宿泊日: {s['check_in']} 〜 {s['check_out']} / {s['adults']}名\n"
        f"(「連泊」は {s.get('earliest_check_in', s['check_in'])} まで遡って"
        f"連続で泊まれる最大日数です)\n"
    )
    text_parts = [header]
    html_parts = [f"<p>{header.replace(chr(10), '<br>')}</p>"]

    f = config["filters"]
    pref_min = f.get("preferred_drive_minutes", 20)
    max_min = f.get("max_drive_minutes", 30)
    if preferred:
        text_parts.append(f"■ 車で{pref_min}分以内(優先)\n" +
                          "\n".join(_format_listing_text(l, config) for l in preferred))
        html_parts.append(f"<h3>🚗 車で{pref_min}分以内(優先)</h3><ul>" +
                          "".join(_format_listing_html(l, config) for l in preferred) + "</ul>")
    if others:
        text_parts.append(f"■ 車で{pref_min}〜{max_min}分\n" +
                          "\n".join(_format_listing_text(l, config) for l in others))
        html_parts.append(f"<h3>車で{pref_min}〜{max_min}分</h3><ul>" +
                          "".join(_format_listing_html(l, config) for l in others) + "</ul>")

    text_parts.append(
        "※ 価格・空室状況は変動します。予約前に必ずリンク先でご確認ください。"
    )
    html_parts.append(
        "<p style='color:#888'>※ 価格・空室状況は変動します。"
        "予約前に必ずリンク先でご確認ください。</p>"
    )
    return subject, "\n\n".join(text_parts), "\n".join(html_parts)


def send(subject: str, text_body: str, html_body: str, config: dict) -> None:
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "SMTP_USERNAME / SMTP_PASSWORD が未設定のためメールを送信できません"
        )
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    to_addr = os.environ.get("ALERT_EMAIL_TO") or config["email"]["to"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(host, port, timeout=60) as server:
        server.login(username, password)
        server.sendmail(username, [to_addr], msg.as_string())
    logger.info("メールを送信しました: %s", to_addr)
