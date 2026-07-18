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
    if not l.max_stay_nights or l.max_stay_check_in is None or l.max_stay_check_out is None:
        return None
    return (f"最大{l.max_stay_nights}連泊可 "
            f"({_fmt_md(l.max_stay_check_in)}〜{_fmt_md(l.max_stay_check_out)})")


def _format_price(l: Listing, config: dict, fx_rate: float | None) -> str:
    price = f"{l.price_per_night:,.0f} PHP"
    if fx_rate is not None:
        second = config["email"].get("second_currency", "JPY")
        price += f" (約{l.price_per_night * fx_rate:,.0f} {second})"
    return price


def _guests_label(l: Listing) -> str | None:
    return f"定員{l.max_guests}名" if l.max_guests is not None else None


def _format_listing_text(l: Listing, config: dict, fx_rate: float | None) -> str:
    rating = f"{l.rating:.1f}" if l.rating is not None else "不明"
    drive = f"車で約{l.drive_minutes:.0f}分" if l.drive_minutes is not None else "所要時間不明"
    kind = "ホテル" if l.source == "hotel" else "Airbnb"
    detail = f"1泊 {_format_price(l, config, fx_rate)} / 評価 {rating} / {drive}"
    guests = _guests_label(l)
    if guests:
        detail += f" / {guests}"
    lines = [
        f"- [{kind}] {l.name}",
        f"  {detail}",
    ]
    stay = _stay_label(l, config)
    if stay:
        lines.append(f"  連泊: {stay}")
    lines.append(f"  {l.url}")
    if l.maps_url:
        lines.append(f"  地図: {l.maps_url}")
    return "\n".join(lines)


def _format_listing_html(l: Listing, config: dict, fx_rate: float | None) -> str:
    rating = f"{l.rating:.1f}" if l.rating is not None else "不明"
    drive = f"車で約{l.drive_minutes:.0f}分" if l.drive_minutes is not None else "所要時間不明"
    kind = "ホテル" if l.source == "hotel" else "Airbnb"
    maps = f' ・ <a href="{l.maps_url}">📍 地図</a>' if l.maps_url else ""
    guests = _guests_label(l)
    guests_html = f" ・ 👥 {guests}" if guests else ""
    stay = _stay_label(l, config)
    stay_html = f"<br>🛏 {stay}" if stay else ""
    return (
        f'<li><a href="{l.url}"><b>{l.name}</b></a> [{kind}]<br>'
        f"1泊 {_format_price(l, config, fx_rate)} ・ 評価 {rating} ・ {drive}{maps}{guests_html}"
        f"{stay_html}</li>"
    )


def build_email(preferred: list[Listing], others: list[Listing],
                config: dict, fx_rate: float | None = None) -> tuple[str, str, str]:
    """(subject, text_body, html_body) を返す。

    fx_rate: 1 PHP あたりの第二通貨レート。None なら併記しない。
    """
    s = config["search"]
    total = len(preferred) + len(others)
    range_start, range_end = s["stay_range_start"], s["stay_range_end"]
    subject = (
        f"{config['email'].get('subject_prefix', '[Stadium Hotel Alert]')} "
        f"{range_start}〜{range_end} 空室 {total}件"
    )

    stays_desc = " または ".join(
        f"{st['check_in']}〜{st['check_out']}" for st in s["stays"])
    header = (
        f"Philippine Sports Stadium 周辺の空室が見つかりました。\n"
        f"宿泊候補: {stays_desc} / {s['adults']}名\n"
        f"(「連泊」は {range_start}〜{range_end} の範囲内で"
        f"泊まれる最大連続日数です)\n"
    )
    text_parts = [header]
    html_parts = [f"<p>{header.replace(chr(10), '<br>')}</p>"]

    f = config["filters"]
    pref_min = f.get("preferred_drive_minutes", 20)
    max_min = f.get("max_drive_minutes", 30)
    if preferred:
        text_parts.append(f"■ 車で{pref_min}分以内(優先)\n" +
                          "\n".join(_format_listing_text(l, config, fx_rate) for l in preferred))
        html_parts.append(f"<h3>🚗 車で{pref_min}分以内(優先)</h3><ul>" +
                          "".join(_format_listing_html(l, config, fx_rate) for l in preferred) + "</ul>")
    if others:
        text_parts.append(f"■ 車で{pref_min}〜{max_min}分\n" +
                          "\n".join(_format_listing_text(l, config, fx_rate) for l in others))
        html_parts.append(f"<h3>車で{pref_min}〜{max_min}分</h3><ul>" +
                          "".join(_format_listing_html(l, config, fx_rate) for l in others) + "</ul>")

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
