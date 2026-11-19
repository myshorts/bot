#!/usr/bin/env python3
"""
On-Demand Interactive Telegram Weather & Complex Weather Bot
- Chỉ phản hồi khi bạn nhắn tin / chọn địa điểm trên Telegram
- Hỏi bất kỳ địa điểm nào -> trả về mưa, nắng/mây/UV, nhiệt độ, gió + lưu ý thời tiết phức tạp
- Có bàn phím nút chọn nhanh dưới khung chat
"""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
STATE_FILE = Path(os.environ.get("STATE_FILE", "bot_user_state.json"))
USER_AGENT = "OnDemand-Weather-Bot/1.0"

MAIN_KEYBOARD = {
    "keyboard": [
        ["📍 Phù Cát (VVPC)", "🏙️ Hà Nội", "🌊 Đà Nẵng"],
        ["🌴 TP. Hồ Chí Minh", "⛰️ Đà Lạt", "⭐ Khu vực đã chọn"],
        ["⚠️ Lưu ý thời tiết phức tạp", "❓ Hướng dẫn"],
    ],
    "resize_keyboard": True,
}


def vn_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] Không lưu được state: {exc}", file=sys.stderr)


def get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Thiếu biến môi trường TELEGRAM_BOT_TOKEN.")
    return token


def tg_send(chat_id: str, text: str, with_keyboard: bool = True) -> None:
    token = get_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if with_keyboard:
        payload["reply_markup"] = MAIN_KEYBOARD

    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code == 400:
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


def geocode_place(query: str) -> Optional[Dict[str, Any]]:
    cleaned = query.strip()
    for prefix in ("📍 ", "🏙️ ", "🌊 ", "🌴 ", "⛰️ "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    if cleaned.lower().startswith("phù cát"):
        return {
            "name": "Phù Cát (VVPC)",
            "admin1": "Bình Định",
            "country": "Việt Nam",
            "latitude": 13.9550,
            "longitude": 109.0422,
        }

    resp = requests.get(
        GEOCODE_URL,
        params={"name": cleaned, "count": 1, "language": "vi", "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    return {
        "name": top.get("name") or cleaned,
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
    return mapping.get(int(code) if code is not None else -1, f"Mã {code}")


def fetch_weather_snapshot(lat: float, lon: float, now_dt: dt.datetime) -> Dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,rain,showers,weather_code,cloud_cover,"
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        ),
        "hourly": "precipitation_probability,cape,uv_index,visibility",
        "forecast_days": 1,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    resp = requests.get(FORECAST_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    curr = data.get("current", {})
    hourly = data.get("hourly", {})

    idx = now_dt.hour
    probs = hourly.get("precipitation_probability") or []
    capes = hourly.get("cape") or []
    uvs = hourly.get("uv_index") or []
    vis_list = hourly.get("visibility") or []

    vis_km = None
    if idx < len(vis_list) and vis_list[idx] is not None:
        vis_km = round(float(vis_list[idx]) / 1000.0, 1)

    return {
        "temp_c": curr.get("temperature_2m"),
        "feels_like_c": curr.get("apparent_temperature"),
        "humidity_pct": curr.get("relative_humidity_2m"),
        "precip_mm": float(curr.get("precipitation") or 0.0),
        "precip_prob_pct": probs[idx] if idx < len(probs) else None,
        "weather_code": curr.get("weather_code"),
        "cloud_cover_pct": curr.get("cloud_cover"),
        "wind_speed_kmh": curr.get("wind_speed_10m"),
        "wind_gust_kmh": curr.get("wind_gusts_10m"),
        "wind_dir_deg": curr.get("wind_direction_10m"),
        "cape_jkg": capes[idx] if idx < len(capes) else None,
        "uv_index": uvs[idx] if idx < len(uvs) else None,
        "visibility_km": vis_km,
    }


def complex_weather_notes(snap: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    code = int(snap.get("weather_code") or 0)
    cape = float(snap.get("cape_jkg") or 0.0)
    precip = float(snap.get("precip_mm") or 0.0)
    prob = float(snap.get("precip_prob_pct") or 0.0)
    gust = float(snap.get("wind_gust_kmh") or 0.0)
    wspd = float(snap.get("wind_speed_kmh") or 0.0)
    vis = snap.get("visibility_km")
    temp = snap.get("temp_c")
    uv = snap.get("uv_index")

    if code in {95, 96, 99}:
        notes.append("⚡ *Dông / đối lưu mạnh:* Có khả năng kèm sấm sét, mưa rào/mưa đá và gió giật mạnh.")
    elif cape >= 1500.0:
        notes.append(f"🌩️ *Đối lưu cao (CAPE {cape:.0f} J/kg):* Dễ phát sinh dông hoặc mưa rào bất chợt.")

    if gust >= 40.0 or wspd >= 28.0:
        notes.append(f"💨 *Gió mạnh / gió giật:* Gió {wspd:.0f} km/h, giật {gust:.0f} km/h — chú ý an toàn ngoài trời.")

    if precip >= 5.0 or code in {63, 65, 81, 82}:
        notes.append(f"🌧️ *Mưa vừa đến mưa to ({precip:.1f} mm/h):* Đường trơn trượt, nguy cơ ngập cục bộ và giảm tầm nhìn.")
    elif prob >= 70.0 and precip == 0.0:
        notes.append(f"☔ *Khả năng mưa cao ({prob:.0f}%):* Chuẩn bị phương án che chắn khi di chuyển.")

    if code in {45, 48} or (vis is not None and float(vis) < 3.0):
        vis_txt = f"{float(vis):.1f} km" if vis is not None else "thấp"
        notes.append(f"🌫️ *Tầm nhìn hạn chế ({vis_txt}):* Có sương mù hoặc mây/mưa thấp che khuất tầm nhìn.")

    if temp is not None and float(temp) >= 36.0:
        notes.append(f"🔥 *Nắng nóng gay gắt ({float(temp):.1f}°C):* Chú ý say nắng, mất nước khi hoạt động ngoài trời.")
    if uv is not None and float(uv) >= 8.0:
        notes.append(f"☀️ *Chỉ số UV rất cao ({float(uv):.1f}):* Cần che chắn, bảo vệ da và mắt khi ra nắng.")

    return notes


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


def build_report(place: Dict[str, Any], snap: Dict[str, Any], now_dt: dt.datetime) -> str:
    label = format_place_label(place)
    cond = weather_code_desc(snap.get("weather_code"))
    notes = complex_weather_notes(snap)

    temp = f"{float(snap['temp_c']):.1f}°C" if snap.get("temp_c") is not None else "—"
    feels = f"{float(snap['feels_like_c']):.1f}°C" if snap.get("feels_like_c") is not None else "—"
    hum = f"{float(snap['humidity_pct']):.0f}%" if snap.get("humidity_pct") is not None else "—"

    precip = f"{float(snap['precip_mm']):.1f} mm"
    prob = f" ({float(snap['precip_prob_pct']):.0f}% khả năng)" if snap.get("precip_prob_pct") is not None else ""

    wspd = f"{float(snap['wind_speed_kmh']):.0f} km/h" if snap.get("wind_speed_kmh") is not None else "—"
    wgst = f", giật {float(snap['wind_gust_kmh']):.0f} km/h" if snap.get("wind_gust_kmh") is not None else ""
    wdir = deg_to_dir_vi(snap.get("wind_dir_deg"))
    vis = f"{float(snap['visibility_km']):.1f} km" if snap.get("visibility_km") is not None else "—"
    cape = f"{float(snap['cape_jkg']):.0f} J/kg" if snap.get("cape_jkg") is not None else "—"

    lines: List[str] = [
        f"📍 *{label}*",
        f"🕒 `{now_dt.strftime('%H:%M %d/%m/%Y')}` (+07) — *{cond}*",
        "",
        f"🌡️ *Nhiệt độ:* {temp} (cảm nhận {feels}, ẩm {hum})",
        f"🌧️ *Mưa:* {precip}{prob}",
        f"☀️ *Nắng & mây:* {sun_status_text(snap, now_dt)}",
        f"💨 *Gió:* {wspd}{wgst} — hướng *{wdir}*",
        f"👁️ *Tầm nhìn:* {vis} | ⚡ *CAPE:* {cape}",
        "",
        "⚠️ *Lưu ý điều kiện thời tiết phức tạp:*",
    ]

    if notes:
        lines.extend(f"• {n}" for n in notes)
    else:
        lines.append("• Hiện chưa ghi nhận hình thái thời tiết phức tạp đáng kể tại khu vực này.")

    return "\n".join(lines)


def handle_message(chat_id: str, text: str, state: Dict[str, Any], now_dt: dt.datetime) -> None:
    raw = text.strip()
    if not raw:
        return

    user_data = state.setdefault(chat_id, {"selected_place": None})

    if raw in {"/start", "/help", "❓ Hướng dẫn"}:
        tg_send(
            chat_id,
            "🌤️ *Bot Thời Tiết Theo Yêu Cầu*\n"
            "• Nhập *tên bất kỳ địa điểm nào* (vd: `Quy Nhơn`, `Phù Cát`, `Sapa`, `Tokyo`) hoặc bấm nút bên dưới để xem ngay.\n"
            "• Bot chỉ trả lời khi bạn hỏi/chọn địa điểm, đồng thời liệt kê *lưu ý điều kiện thời tiết phức tạp* (dông, gió giật, mưa lớn, tầm nhìn kém, CAPE cao, nắng nóng/UV).\n"
            "• Bấm *⭐ Khu vực đã chọn* để xem lại nhanh khu vực bạn vừa chọn.",
        )
        return

    if raw == "⭐ Khu vực đã chọn":
        place = user_data.get("selected_place")
        if not place:
            tg_send(chat_id, "Bạn chưa chọn khu vực nào. Hãy gửi tên một địa điểm bất kỳ (vd: `Hà Nội`).")
            return
        snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
        tg_send(chat_id, build_report(place, snap, now_dt))
        return

    if raw == "⚠️ Lưu ý thời tiết phức tạp":
        place = user_data.get("selected_place")
        if not place:
            tg_send(chat_id, "Hãy chọn hoặc nhập tên một địa điểm trước để xem lưu ý thời tiết phức tạp.")
            return
        snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
        notes = complex_weather_notes(snap)
        header = f"⚠️ *Lưu ý thời tiết phức tạp — {format_place_label(place)}*\n"
        body = "\n".join(f"• {n}" for n in notes) if notes else "• Chưa ghi nhận điều kiện thời tiết phức tạp nổi bật lúc này."
        tg_send(chat_id, header + body)
        return

    place = geocode_place(raw)
    if not place:
        tg_send(chat_id, f"❌ Không tìm thấy địa điểm: *{raw}*. Hãy thử tên rõ hơn (vd: `Đà Nẵng`, `Huế`, `Phù Cát`).")
        return

    user_data["selected_place"] = place
    save_state(state)

    snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
    tg_send(chat_id, build_report(place, snap, now_dt))


def run_on_demand_loop() -> None:
    token = get_token()
    base_url = f"https://api.telegram.org/bot{token}"
    offset: Optional[int] = None
    state = load_state()

    print("[READY] Bot đang chờ bạn nhắn tin/chọn địa điểm trên Telegram (Ctrl+C để dừng).")
    while True:
        try:
            params: Dict[str, Any] = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=35)
            resp.raise_for_status()
            updates = resp.json().get("result") or []

            now_dt = vn_now()
            for upd in updates:
                uid = upd.get("update_id")
                if isinstance(uid, int):
                    offset = uid + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                text = msg.get("text")
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if text and chat_id is not None:
                    try:
                        handle_message(str(chat_id), str(text), state, now_dt)
                    except Exception as exc:
                        print(f"[WARN] Lỗi xử lý tin nhắn từ {chat_id}: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[STOP] Đã dừng bot.")
            break
        except Exception as exc:
            print(f"[WARN] Lỗi kết nối Telegram: {exc}", file=sys.stderr)
            time.sleep(2.0)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--ask":
        query = " ".join(sys.argv[2:])
        place = geocode_place(query)
        if not place:
            print(f"Không tìm thấy địa điểm: {query}")
            sys.exit(1)
        now_dt = vn_now()
        snap = fetch_weather_snapshot(place["latitude"], place["longitude"], now_dt)
        print(build_report(place, snap, now_dt))
        return

    run_on_demand_loop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
