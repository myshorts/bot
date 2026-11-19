#!/usr/bin/env python3
"""
Telegram Weather & Thunderstorm Alert Bot (Multi-Location)
- Hỏi thời tiết bất kỳ địa điểm nào: mưa, nắng/UV/mây, nhiệt độ, gió, nguy cơ dông
- Lưu danh sách địa điểm theo chat_id vào SUBS_FILE và gửi cảnh báo dông tự động
"""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SUBS_FILE = Path(os.environ.get("SUBS_FILE", "subscribers.json"))
OFFSET_FILE = Path(os.environ.get("OFFSET_FILE", ".tg_offset.json"))
USER_AGENT = "MultiPlace-Weather-Thunder-Bot/1.0"

THUNDER_CODES = {95, 96, 99}
COOLDOWN_MINUTES = 25


def vn_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))


# ==============================================================================
# QUẢN LÝ DANH SÁCH ĐĂNG KÝ (SUBS_FILE)
# ==============================================================================
def load_subs() -> Dict[str, Any]:
    if not SUBS_FILE.exists():
        return {}
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_subs(data: Dict[str, Any]) -> None:
    SUBS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_offset() -> Optional[int]:
    if not OFFSET_FILE.exists():
        return None
    try:
        val = json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset")
        return int(val) if val is not None else None
    except Exception:
        return None


def save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


# ==============================================================================
# TÌM ĐỊA ĐIỂM & LẤY DỮ LIỆU THỜI TIẾT
# ==============================================================================
def geocode_place(query: str) -> Optional[Dict[str, Any]]:
    resp = requests.get(
        GEOCODE_URL,
        params={"name": query, "count": 1, "language": "vi", "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    return {
        "name": top.get("name") or query,
        "admin1": top.get("admin1") or "",
        "country": top.get("country") or "",
        "latitude": float(top["latitude"]),
        "longitude": float(top["longitude"]),
    }


def format_place_label(place: Dict[str, Any]) -> str:
    parts = [place.get("name", "")]
    if place.get("admin1"):
        parts.append(place["admin1"])
    if place.get("country"):
        parts.append(place["country"])
    return ", ".join(p for p in parts if p)


def deg_to_dir_vi(deg: Optional[float]) -> str:
    if deg is None:
        return "—"
    dirs = [
        "Bắc", "Bắc Đông Bắc", "Đông Bắc", "Đông Đông Bắc",
        "Đông", "Đông Đông Nam", "Đông Nam", "Nam Đông Nam",
        "Nam", "Nam Tây Nam", "Tây Nam", "Tây Tây Nam",
        "Tây", "Tây Tây Bắc", "Tây Bắc", "Bắc Tây Bắc",
    ]
    return dirs[int((float(deg) + 11.25) // 22.5) % 16]


def weather_code_desc(code: Optional[int]) -> str:
    mapping = {
        0: "Trời quang nắng ráo",
        1: "Ít mây, có nắng",
        2: "Mây rải rác, nắng gián đoạn",
        3: "Nhiều mây / âm u",
        45: "Sương mù",
        48: "Sương mù đóng băng",
        51: "Mưa phùn nhẹ",
        53: "Mưa phùn vừa",
        55: "Mưa phùn dày",
        61: "Mưa nhỏ",
        63: "Mưa vừa",
        65: "Mưa to",
        80: "Mưa rào nhẹ",
        81: "Mưa rào vừa",
        82: "Mưa rào rất to",
        95: "Dông (có thể kèm mưa rào)",
        96: "Dông kèm mưa đá nhẹ",
        99: "Dông kèm mưa đá mạnh",
    }
    return mapping.get(int(code) if code is not None else -1, f"Mã thời tiết {code}")


def fetch_weather_snapshot(lat: float, lon: float, now_dt: dt.datetime) -> Dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,rain,showers,weather_code,cloud_cover,"
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        ),
        "hourly": "precipitation_probability,cape,uv_index",
        "forecast_days": 1,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    resp = requests.get(FORECAST_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    curr = data.get("current", {})
    hourly = data.get("hourly", {})

    idx = now_dt.hour
    precip_prob = None
    cape = None
    uv = None

    probs = hourly.get("precipitation_probability") or []
    capes = hourly.get("cape") or []
    uvs = hourly.get("uv_index") or []
    if idx < len(probs):
        precip_prob = probs[idx]
    if idx < len(capes):
        cape = capes[idx]
    if idx < len(uvs):
        uv = uvs[idx]

    return {
        "temp_c": curr.get("temperature_2m"),
        "feels_like_c": curr.get("apparent_temperature"),
        "humidity_pct": curr.get("relative_humidity_2m"),
        "precip_mm": float(curr.get("precipitation") or 0.0),
        "precip_prob_pct": precip_prob,
        "weather_code": curr.get("weather_code"),
        "cloud_cover_pct": curr.get("cloud_cover"),
        "wind_speed_kmh": curr.get("wind_speed_10m"),
        "wind_gust_kmh": curr.get("wind_gusts_10m"),
        "wind_dir_deg": curr.get("wind_direction_10m"),
        "cape_jkg": cape,
        "uv_index": uv,
    }


def assess_thunder_alert(snap: Dict[str, Any]) -> Optional[Dict[str, str]]:
    code = int(snap.get("weather_code") or 0)
    cape = float(snap.get("cape_jkg") or 0.0)
    precip = float(snap.get("precip_mm") or 0.0)
    gust = float(snap.get("wind_gust_kmh") or 0.0)

    if code in THUNDER_CODES or (cape >= 1800.0 and precip >= 0.5):
        return {
            "level": "CAO",
            "title": "🚨 *CẢNH BÁO DÔNG MẠNH*",
            "reason": f"Mã dông ({code}) / CAPE={cape:.0f} J/kg, mưa {precip:.1f} mm/h, gió giật {gust:.0f} km/h.",
        }
    if cape >= 1200.0 and (code in {80, 81, 82, 63, 65} or precip >= 1.0):
        return {
            "level": "TRUNG BÌNH",
            "title": "⚠️ *CẢNH BÁO NGUY CƠ DÔNG & MƯA RÀO*",
            "reason": f"Đối lưu khả dụng cao (CAPE={cape:.0f} J/kg), lượng mưa {precip:.1f} mm/h.",
        }
    return None


def sun_status_text(snap: Dict[str, Any], now_dt: dt.datetime) -> str:
    cloud = snap.get("cloud_cover_pct")
    uv = snap.get("uv_index")
    precip = float(snap.get("precip_mm") or 0.0)
    is_day = 6 <= now_dt.hour <= 17

    if precip > 0:
        base = "Đang có mưa, không nắng"
    elif not is_day:
        base = "Ban đêm / trời tối"
    elif cloud is not None and float(cloud) <= 25:
        base = "Nắng rõ / ít mây"
    elif cloud is not None and float(cloud) <= 60:
        base = "Nắng xen kẽ mây"
    else:
        base = "Nhiều mây, nắng yếu/che khuất"

    if cloud is not None:
        base += f" (mây {float(cloud):.0f}%)"
    if uv is not None and is_day:
        base += f" | UV {float(uv):.1f}"
    return base


def build_weather_report(place: Dict[str, Any], snap: Dict[str, Any], now_dt: dt.datetime) -> str:
    label = format_place_label(place)
    alert = assess_thunder_alert(snap)
    cond = weather_code_desc(snap.get("weather_code"))

    temp = f"{float(snap['temp_c']):.1f}°C" if snap.get("temp_c") is not None else "—"
    feels = f"{float(snap['feels_like_c']):.1f}°C" if snap.get("feels_like_c") is not None else "—"
    hum = f"{float(snap['humidity_pct']):.0f}%" if snap.get("humidity_pct") is not None else "—"

    precip = f"{float(snap['precip_mm']):.1f} mm"
    prob = f" ({float(snap['precip_prob_pct']):.0f}% khả năng)" if snap.get("precip_prob_pct") is not None else ""

    wspd = f"{float(snap['wind_speed_kmh']):.0f} km/h" if snap.get("wind_speed_kmh") is not None else "—"
    wgst = f", giật {float(snap['wind_gust_kmh']):.0f} km/h" if snap.get("wind_gust_kmh") is not None else ""
    wdir = deg_to_dir_vi(snap.get("wind_dir_deg"))

    cape_str = f"{float(snap['cape_jkg']):.0f} J/kg" if snap.get("cape_jkg") is not None else "—"

    lines: List[str] = []
    if alert:
        lines.append(f"{alert['title']} — *{label}*")
        lines.append(f"Lý do: _{alert['reason']}_")
        lines.append("")
    else:
        lines.append(f"🌤️ *Thời tiết: {label}*")

    lines.append(f"🕒 `{now_dt.strftime('%H:%M %d/%m/%Y')}` (+07) — *{cond}*")
    lines.append(f"🌡️ *Nhiệt độ:* {temp} (cảm nhận {feels}, ẩm {hum})")
    lines.append(f"🌧️ *Mưa:* {precip}{prob}")
    lines.append(f"☀️ *Nắng & mây:* {sun_status_text(snap, now_dt)}")
    lines.append(f"💨 *Gió:* {wspd}{wgst} — hướng *{wdir}*")
    lines.append(f"⚡ *Chỉ số đối lưu (CAPE):* {cape_str}")
    if not alert:
        lines.append("⚡ *Dông:* Chưa ghi nhận cảnh báo dông tức thời.")

    return "\n".join(lines)


# ==============================================================================
# TELEGRAM BOT API
# ==============================================================================
def get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Thiếu biến môi trường TELEGRAM_BOT_TOKEN.")
    return token


def tg_send(chat_id: str, text: str) -> None:
    token = get_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code == 400:
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


def handle_message(chat_id: str, text: str, subs: Dict[str, Any], now_dt: dt.datetime) -> None:
    raw = text.strip()
    if not raw:
        return

    if raw in {"/start", "/help"}:
        tg_send(
            chat_id,
            "🤖 *Bot Thời Tiết & Cảnh Báo Dông*\n"
            "• Gửi *tên bất kỳ địa điểm nào* (vd: `Hà Nội`, `Đà Nẵng`, `Phù Cát`, `Tokyo`) để xem mưa, nắng, nhiệt độ, gió & nguy cơ dông.\n"
            "• `/theodoi <địa điểm>` — Đăng ký nhận cảnh báo dông tự động.\n"
            "• `/botdõi <địa điểm>` hoặc `/huy <địa điểm>` — Hủy theo dõi địa điểm.\n"
            "• `/danhsach` — Xem các địa điểm đang theo dõi.",
        )
        return

    if raw.startswith("/theodoi"):
        q = raw[len("/theodoi"):].strip()
        if not q:
            tg_send(chat_id, "Ví dụ: `/theodoi Quy Nhơn`")
            return
        place = geocode_place(q)
        if not place:
            tg_send(chat_id, f"Không tìm thấy địa điểm: *{q}*")
            return
        user_entry = subs.setdefault(chat_id, {"places": [], "last_alert": {}})
        places: List[Dict[str, Any]] = user_entry.setdefault("places", [])
        key = f"{place['latitude']:.3f},{place['longitude']:.3f}"
        if not any(f"{p['latitude']:.3f},{p['longitude']:.3f}" == key for p in places):
            places.append(place)
            save_subs(subs)
        tg_send(chat_id, f"✅ Đã thêm theo dõi cảnh báo dông tại: *{format_place_label(place)}*")
        return

    if raw.startswith("/huy"):
        q = raw[len("/huy"):].strip().lower()
        user_entry = subs.get(chat_id) or {}
        places = user_entry.get("places") or []
        if not q:
            tg_send(chat_id, "Ví dụ: `/huy Quy Nhơn`")
            return
        new_places = [p for p in places if q not in format_place_label(p).lower()]
        if len(new_places) == len(places):
            tg_send(chat_id, f"Không thấy địa điểm *{q}* trong danh sách theo dõi.")
            return
        user_entry["places"] = new_places
        subs[chat_id] = user_entry
        save_subs(subs)
        tg_send(chat_id, f"🗑️ Đã hủy theo dõi địa điểm khớp *{q}*.")
        return

    if raw.startswith("/danhsach"):
        places = (subs.get(chat_id) or {}).get("places") or []
        if not places:
            tg_send(chat_id, "Bạn chưa đăng ký địa điểm nào. Dùng `/theodoi <địa điểm>`.")
            return
        msg = "📌 *Địa điểm đang theo dõi cảnh báo dông:*\n" + "\n".join(
            f"• {format_place_label(p)}" for p in places
        )
        tg_send(chat_id, msg)
        return

    query = raw[len("/thoitiet"):].strip() if raw.startswith("/thoitiet") else raw
    place = geocode_place(query)
    if not place:
        tg_send(chat_id, f"Không tìm thấy địa điểm *{query}*. Hãy thử tên rõ hơn (vd: `Nha Trang`, `Huế`).")
        return

    snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
    tg_send(chat_id, build_weather_report(place, snap, now_dt))


def poll_updates(subs: Dict[str, Any], now_dt: dt.datetime) -> None:
    token = get_token()
    offset = load_offset()
    params: Dict[str, Any] = {"timeout": 10}
    if offset is not None:
        params["offset"] = offset

    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=20)
    resp.raise_for_status()
    updates = resp.json().get("result") or []
    max_id: Optional[int] = None

    for upd in updates:
        uid = upd.get("update_id")
        if isinstance(uid, int):
            max_id = uid if max_id is None else max(max_id, uid)
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = msg.get("text")
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if text and chat_id is not None:
            try:
                handle_message(str(chat_id), str(text), subs, now_dt)
            except Exception as exc:
                print(f"[WARN] Lỗi xử lý tin nhắn từ {chat_id}: {exc}", file=sys.stderr)

    if max_id is not None:
        save_offset(max_id + 1)


def run_thunder_alerts(subs: Dict[str, Any], now_dt: dt.datetime) -> None:
    changed = False
    for chat_id, entry in subs.items():
        places = entry.get("places") or []
        last_alert: Dict[str, str] = entry.setdefault("last_alert", {})
        for place in places:
            try:
                snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
                alert = assess_thunder_alert(snap)
                if not alert:
                    continue
                p_key = f"{place['latitude']:.3f},{place['longitude']:.3f}:{alert['level']}"
                prev_iso = last_alert.get(p_key)
                if prev_iso:
                    elapsed_min = (now_dt - dt.datetime.fromisoformat(prev_iso)).total_seconds() / 60.0
                    if elapsed_min < COOLDOWN_MINUTES:
                        continue
                tg_send(chat_id, build_weather_report(place, snap, now_dt))
                last_alert[p_key] = now_dt.isoformat()
                changed = True
            except Exception as exc:
                print(f"[WARN] Lỗi kiểm tra dông cho {chat_id} ({place.get('name')}): {exc}", file=sys.stderr)
    if changed:
        save_subs(subs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-location Weather & Thunder Telegram Bot")
    parser.add_argument("--ask", type=str, help="Hỏi nhanh 1 địa điểm qua CLI (không gửi Telegram)")
    parser.add_argument("--alerts-only", action="store_true", help="Chỉ quét cảnh báo dông cho danh sách đã đăng ký")
    args = parser.parse_args()

    now_dt = vn_now()

    if args.ask:
        place = geocode_place(args.ask)
        if not place:
            print(f"Không tìm thấy địa điểm: {args.ask}")
            sys.exit(1)
        snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
        print(build_weather_report(place, snap, now_dt))
        return

    subs = load_subs()
    if not args.alerts_only:
        poll_updates(subs, now_dt)
    run_thunder_alerts(subs, now_dt)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
