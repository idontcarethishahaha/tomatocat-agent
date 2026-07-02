import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

ACK_FILE = Path(__file__).parent / "ack_state.json"

WTTR_LOCATION = os.environ.get("WTTR_LOCATION", "Beijing")
WTTR_LANG = os.environ.get("WTTR_LANG", "zh-cn")


def _load_acks() -> dict[str, float]:
    try:
        return json.loads(ACK_FILE.read_text(encoding="utf-8")) if ACK_FILE.exists() else {}
    except Exception:
        return {}


def _save_acks(acks: dict[str, float]) -> None:
    ACK_FILE.write_text(json.dumps(acks, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_weather() -> dict | None:
    location = urllib.parse.quote(WTTR_LOCATION)
    url = f"https://wttr.in/{location}?format=j1&lang={WTTR_LANG}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data
    except Exception:
        pass
    return None


def _parse_current(data: dict) -> dict | None:
    try:
        current = data.get("current_condition", [{}])[0]
        weather_desc = current.get("weatherDesc", [{}])[0].get("value", "")
        lang_desc = current.get("lang_zh-cn", [{}])[0].get("value", "")
        desc = lang_desc or weather_desc
        return {
            "text": desc,
            "temp": current.get("temp_C", ""),
            "feels_like": current.get("FeelsLikeC", ""),
            "humidity": current.get("humidity", ""),
            "wind_dir": current.get("winddir16Point", ""),
            "wind_speed": current.get("windspeedKmph", ""),
        }
    except Exception:
        return None


def _parse_forecast(data: dict) -> list[dict]:
    result = []
    try:
        for day in data.get("weather", [])[:3]:
            date = day.get("date", "")
            maxtemp = day.get("maxtempC", "")
            mintemp = day.get("mintempC", "")
            hourly = day.get("hourly", [])
            midday = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
            weather_desc = midday.get("weatherDesc", [{}])[0].get("value", "")
            lang_desc = midday.get("lang_zh-cn", [{}])[0].get("value", "")
            desc = lang_desc or weather_desc
            result.append({
                "date": date,
                "desc": desc,
                "temp_max": maxtemp,
                "temp_min": mintemp,
            })
    except Exception:
        pass
    return result


@mcp.tool()
def get_proactive_events() -> str:
    acks = _load_acks()
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime())
    event_id = f"weather_daily_{today}"

    if event_id in acks and now < acks[event_id]:
        return json.dumps([], ensure_ascii=False)

    data = _fetch_weather()
    if not data:
        return json.dumps([], ensure_ascii=False)

    current = _parse_current(data)
    forecast = _parse_forecast(data)

    if not current and not forecast:
        return json.dumps([], ensure_ascii=False)

    content_parts = []
    if current:
        content_parts.append(
            f"当前天气：{current['text']}，温度 {current['temp']}°C，"
            f"体感 {current['feels_like']}°C，"
            f"{current['wind_dir']}风 {current['wind_speed']}km/h，"
            f"湿度 {current['humidity']}%"
        )

    if forecast:
        forecasts = []
        for day in forecast[:2]:
            forecasts.append(
                f"{day['date']}：{day['desc']}，{day['temp_min']}~{day['temp_max']}°C"
            )
        if forecasts:
            content_parts.append("未来天气：" + "；".join(forecasts))

    content = "。".join(content_parts) + "。"

    area_name = data.get("nearest_area", [{}])[0].get("areaName", [{}])[0].get("value", WTTR_LOCATION)

    event = {
        "kind": "content",
        "event_id": event_id,
        "source_type": "weather",
        "source_name": "wttr.in",
        "title": f"{time.strftime('%m月%d日', time.localtime())} {area_name} 天气预报",
        "content": content,
        "severity": "normal",
    }

    return json.dumps([event], ensure_ascii=False)


@mcp.tool()
def acknowledge_events(event_ids: list[str], ttl_hours: int = 0) -> str:
    acks = _load_acks()
    until = time.time() + ttl_hours * 3600 if ttl_hours > 0 else float("inf")
    for eid in event_ids:
        acks[eid] = until
    _save_acks(acks)
    return json.dumps({"ok": True, "acked": len(event_ids)})


@mcp.tool()
def get_context() -> str:
    data = _fetch_weather()
    if not data:
        return json.dumps({"available": False}, ensure_ascii=False)

    current = _parse_current(data)
    if not current:
        return json.dumps({"available": False}, ensure_ascii=False)

    context = {
        "available": True,
        "weather_now": current,
    }
    return json.dumps(context, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")