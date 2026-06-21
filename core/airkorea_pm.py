"""
airkorea_pm.py — AirKorea PM10/PM2.5 collection helpers.

The public demo uses AirKorea ground observation data for particulate matter.
This module keeps the API details outside the app layer and returns plain
Python dictionaries so it can be reused by Streamlit, FastAPI, tests, or a
future scheduled collector.

Environment:
    AIRKOREA_API_KEY or AIRKOREA_SERVICE_KEY

Notes:
    - The OpenAPI service key can be either decoded or URL-encoded. requests
      handles the query encoding.
    - If a key is missing, callers can use ``demo_pm_observations`` to keep the
      GitHub demo runnable without secrets.
"""

from __future__ import annotations

import math
import os
import random
import time
from datetime import date, datetime, timedelta
from typing import Iterable

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime for lightweight demos
    requests = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


AIRKOREA_STATS_URL = (
    "https://apis.data.go.kr/B552584/ArpltnStatsSvc/getCtprvnMesureSidoLIst"
)

SIDO_ALIASES = {
    "서울": "서울",
    "서울특별시": "서울",
    "부산": "부산",
    "부산광역시": "부산",
    "대구": "대구",
    "대구광역시": "대구",
    "인천": "인천",
    "인천광역시": "인천",
    "광주": "광주",
    "광주광역시": "광주",
    "대전": "대전",
    "대전광역시": "대전",
    "울산": "울산",
    "울산광역시": "울산",
    "세종": "세종",
    "세종특별자치시": "세종",
    "경기": "경기",
    "경기도": "경기",
    "강원": "강원",
    "강원특별자치도": "강원",
    "충북": "충북",
    "충청북도": "충북",
    "충남": "충남",
    "충청남도": "충남",
    "전북": "전북",
    "전라북도": "전북",
    "전북특별자치도": "전북",
    "전남": "전남",
    "전라남도": "전남",
    "경북": "경북",
    "경상북도": "경북",
    "경남": "경남",
    "경상남도": "경남",
    "제주": "제주",
    "제주특별자치도": "제주",
}


def normalize_sido(value: str) -> str:
    """Return the AirKorea sido name used by the OpenAPI."""
    text = (value or "").strip()
    return SIDO_ALIASES.get(text, text[:2] if len(text) >= 2 else text)


def get_api_key(explicit: str | None = None) -> str:
    """Return the configured AirKorea API key or raise a helpful error."""
    key = explicit or os.environ.get("AIRKOREA_API_KEY") or os.environ.get(
        "AIRKOREA_SERVICE_KEY"
    )
    if not key:
        raise RuntimeError(
            "환경변수 AIRKOREA_API_KEY 또는 AIRKOREA_SERVICE_KEY 가 설정되지 않았습니다."
        )
    return key


def _to_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "통신장애", "점검및교정", "자료이상", "자료없음"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_airkorea_items(items: Iterable[dict]) -> list[dict]:
    """Normalize AirKorea JSON items to records with datetime, station, pm10, pm25."""
    rows = []
    for item in items:
        ts = _parse_datetime(item.get("dataTime") or item.get("dataDate", ""))
        if ts is None:
            continue
        rows.append(
            {
                "datetime": ts,
                "date": ts.date(),
                "station": item.get("stationName") or item.get("cityName") or "",
                "sido": item.get("sidoName") or "",
                "pm10": _to_float(item.get("pm10Value") or item.get("pm10Value24")),
                "pm25": _to_float(item.get("pm25Value") or item.get("pm25Value24")),
            }
        )
    return rows


def fetch_sido_pm(
    sido_name: str,
    search_condition: str = "DAILY",
    api_key: str | None = None,
    num_rows: int = 100,
    page_no: int = 1,
    timeout: int = 20,
    retries: int = 3,
    retry_sleep: float = 2.0,
) -> list[dict]:
    """
    Fetch AirKorea PM observations for a sido.

    ``search_condition`` is usually DAILY, HOUR, or MONTH. DAILY is best for
    the first public demo because it is compact and easy to combine with daily
    rainfall.
    """
    if requests is None:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")

    service_key = get_api_key(api_key)
    params = {
        "serviceKey": service_key,
        "returnType": "json",
        "numOfRows": num_rows,
        "pageNo": page_no,
        "sidoName": normalize_sido(sido_name),
        "searchCondition": search_condition,
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                AIRKOREA_STATS_URL,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            body = data.get("response", {}).get("body", {})
            items = body.get("items", [])
            if isinstance(items, dict):
                items = items.get("item", [])
            return parse_airkorea_items(items)
        except Exception as exc:  # requests/json/openapi shape errors
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep)
    raise RuntimeError(f"AirKorea PM 데이터 조회 실패: {last_error}")


def daily_pm_average(rows: Iterable[dict]) -> dict[date, dict]:
    """Aggregate observation rows into {date: {pm10, pm25, count}}."""
    buckets: dict[date, dict] = {}
    for row in rows:
        d = row["date"] if "date" in row else row["datetime"].date()
        bucket = buckets.setdefault(d, {"pm10_values": [], "pm25_values": []})
        if row.get("pm10") is not None:
            bucket["pm10_values"].append(float(row["pm10"]))
        if row.get("pm25") is not None:
            bucket["pm25_values"].append(float(row["pm25"]))

    result: dict[date, dict] = {}
    for d, bucket in buckets.items():
        pm10_values = bucket["pm10_values"]
        pm25_values = bucket["pm25_values"]
        result[d] = {
            "pm10": sum(pm10_values) / len(pm10_values) if pm10_values else None,
            "pm25": sum(pm25_values) / len(pm25_values) if pm25_values else None,
            "count": max(len(pm10_values), len(pm25_values)),
        }
    return dict(sorted(result.items()))


def demo_pm_observations(
    start: date,
    end: date,
    seed_text: str = "demo",
) -> list[dict]:
    """
    Generate deterministic demo PM observations.

    The seasonal shape intentionally rises in late winter/spring and eases in
    summer, which lets the decision model demonstrate non-trivial priorities
    without external API keys.
    """
    rng = random.Random(seed_text)
    rows = []
    cur = start
    while cur <= end:
        day_of_year = cur.timetuple().tm_yday
        spring_peak = 18 * math.exp(-((day_of_year - 85) / 45) ** 2)
        winter_peak = 10 * math.exp(-((day_of_year - 20) / 38) ** 2)
        summer_relief = -6 * math.exp(-((day_of_year - 205) / 55) ** 2)
        weekly_wave = 4 * math.sin(day_of_year / 7)
        pm10 = max(8, 33 + spring_peak + winter_peak + summer_relief + weekly_wave + rng.uniform(-5, 5))
        pm25 = max(4, pm10 * 0.52 + rng.uniform(-3, 3))
        rows.append(
            {
                "datetime": datetime.combine(cur, datetime.min.time()),
                "date": cur,
                "station": "demo",
                "sido": normalize_sido(seed_text),
                "pm10": round(pm10, 1),
                "pm25": round(pm25, 1),
            }
        )
        cur += timedelta(days=1)
    return rows


def selftest() -> None:
    rows = parse_airkorea_items(
        [
            {
                "dataTime": "2026-06-20 00:00",
                "stationName": "샘플",
                "sidoName": "충남",
                "pm10Value": "42",
                "pm25Value": "21",
            }
        ]
    )
    assert rows[0]["pm10"] == 42
    assert rows[0]["pm25"] == 21
    assert normalize_sido("충청남도") == "충남"
    print("[selftest] AirKorea parser OK")


if __name__ == "__main__":
    selftest()

