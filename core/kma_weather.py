"""
kma_weather.py — KMA 지상관측(강수 포함) 조회 모듈 (순수 함수, 재사용용)

출처: solar-project-archive 의 scripts/collect_kma_sfc.py 에서
      API 호출·응답 파싱 로직만 추출. 파이프라인 의존(경로/CSV 저장/내부 검증)을
      제거하고, '위경도 단발 조회' 함수로 정리. pandas 의존도 제거(list[dict] 반환).

API : KMA API Hub  https://apihub.kma.go.kr/api/typ01/url/sfc_nc_var.php
      - 위경도(lat/lon)를 직접 받는다  → 격자(nx, ny) 변환 불필요.
      - 강수 컬럼: rn_15m(15분), rn_60m(60분 누적), rn_day(당일 누적), rn_ox(유무).
키   : 환경변수 KMA_API_KEY (.env 지원). 코드에 키를 넣지 말 것.

사용 예:
    from kma_weather import fetch_kma_surface, daily_rainfall_mm
    rows = fetch_kma_surface(36.980, 126.474, "202506190000", "202506192359")
    rain = daily_rainfall_mm(rows)   # {date: 일강수(mm)}

검증 실행(키 필요): KMA_API_KEY 설정 후  python kma_weather.py
파서만 검증(키 불필요):                   python kma_weather.py --selftest
"""

import os
import sys
import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


KMA_SFC_URL = "https://apihub.kma.go.kr/api/typ01/url/sfc_nc_var.php"

# 응답 컬럼 순서 (첫 컬럼=시각 다음부터 이 순서로 들어옴) — 원본 그대로
OBS_COLS = [
    "ta", "hm", "td", "wd_10m", "ws_10m", "uu", "vv",
    "pa", "ps", "rn_ox", "rn_15m", "rn_60m", "rn_day",
    "vs", "ta_chi", "sd_tot", "sd_day", "sd_3hr", "sd_24h",
]

# KMA 결측값 코드 → None
_MISSING = {-9.0, -99.0, -999.0, -9999.0, 9999.0}


def _get_api_key(explicit=None) -> str:
    key = explicit or os.environ.get("KMA_API_KEY", "")
    if not key:
        raise RuntimeError(
            "환경변수 KMA_API_KEY 가 설정되지 않았습니다 (.env 또는 export)."
        )
    return key


def parse_response(text: str) -> list[dict]:
    """KMA sfc 응답 텍스트 → 레코드 리스트. 첫 컬럼=시각(YYYYMMDDHHmm), 주석(#)·빈 줄 무시."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            ts = datetime.strptime(parts[0], "%Y%m%d%H%M")
        except ValueError:
            continue
        row = {"datetime": ts}
        for i, col in enumerate(OBS_COLS):
            idx = i + 1
            if idx >= len(parts):
                row[col] = None
                continue
            try:
                val = float(parts[idx])
                row[col] = None if val in _MISSING else val
            except ValueError:
                row[col] = None
        rows.append(row)
    return rows


def _fetch_window(lat, lon, tm1, tm2, api_key, itv=15, timeout=30, retries=3, retry_sleep=10):
    """1시간 구간(tm1~tm2) 단발 호출 + 재시도. 원본 fetch_hour 로직 보존."""
    if requests is None:
        raise RuntimeError("requests 패키지가 필요합니다: pip install requests")
    params = {
        "tm1": tm1, "tm2": tm2,
        "lat": lat, "lon": lon,
        "obs": ",".join(OBS_COLS),
        "itv": itv, "help": 0, "authKey": api_key,
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                KMA_SFC_URL, params=params, timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            text = resp.content.decode("euc-kr", errors="replace")  # KMA는 EUC-KR
            return parse_response(text)
        except requests.RequestException:
            if attempt < retries:
                time.sleep(retry_sleep)
            else:
                raise
    return []


def fetch_kma_surface(lat, lon, start, end, api_key=None, itv=15, sleep_sec=0.5) -> list[dict]:
    """
    위경도 지점의 지상관측(강수 포함)을 start~end 동안 조회.
    start/end : "YYYYMMDDHHmm" 문자열 또는 datetime.
    KMA는 1시간 단위 호출이 안정적이라 시간 단위로 끊어 호출 후 합친다.
    반환 : 레코드 리스트(dict), 각 row에 'datetime' + OBS_COLS.
    """
    api_key = _get_api_key(api_key)
    if isinstance(start, str):
        start = datetime.strptime(start, "%Y%m%d%H%M")
    if isinstance(end, str):
        end = datetime.strptime(end, "%Y%m%d%H%M")

    rows = []
    cur = start.replace(minute=0)
    while cur <= end:
        tm1 = cur.strftime("%Y%m%d%H%M")
        tm2 = (cur + timedelta(minutes=59)).strftime("%Y%m%d%H%M")
        rows.extend(_fetch_window(lat, lon, tm1, tm2, api_key, itv=itv))
        time.sleep(sleep_sec)
        cur += timedelta(hours=1)

    return [r for r in rows if start <= r["datetime"] <= end]


def daily_rainfall_mm(rows, col="rn_day") -> dict:
    """
    레코드 리스트 → {date: 일강수(mm)}.
    기본은 rn_day(당일 누적 강수)의 '일 최대값'을 일강수로 사용한다.
    ※ 주의: rn_day 리셋 시각(자정/09시 등) 관례는 지점·기간에 따라 확인 필요.
      오염 모델의 '자연 세척' 임계 판단에 쓸 때 이 가정을 점검하세요.
    """
    daily = {}
    for r in rows:
        v = r.get(col)
        if v is None:
            continue
        d = r["datetime"].date()
        daily[d] = max(daily.get(d, 0.0), v)
    return dict(sorted(daily.items()))


# ── 파서 자체 검증용 합성 응답 (키·네트워크 불필요) ───────────────────────────
_SAMPLE_RESPONSE = """#START7777
# sfc_nc_var sample
202506190000, 21.3, 88.0, 19.2, 270, 1.4, -1.2, 0.6, 1004.1, 1011.2, 1, 0.5, 2.0, 12.5, 8000, 20.9, -9.0, -9.0, -9.0, -9.0
202506190015, 21.1, 90.0, 19.4, 268, 1.1, -1.0, 0.4, 1004.0, 1011.1, 1, 0.8, 2.8, 13.3, 7000, 20.7, -9.0, -9.0, -9.0, -9.0
#7777END
"""


def _selftest_parser():
    rows = parse_response(_SAMPLE_RESPONSE)
    assert len(rows) == 2, f"행 수 오류: {len(rows)}"
    assert rows[0]["datetime"] == datetime(2025, 6, 19, 0, 0)
    assert abs(rows[0]["rn_60m"] - 2.0) < 1e-9, "rn_60m 파싱 오류"
    assert rows[0]["sd_tot"] is None, "결측(-9.0) → None 처리 오류"
    print("[selftest] 파서 OK — 2행 파싱, 강수/결측 처리 정상")
    print("  예) ", rows[1]["datetime"], "rn_60m=", rows[1]["rn_60m"], "mm")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest_parser()
        sys.exit(0)

    # 실 조회 검증 (KMA_API_KEY 필요)
    try:
        _get_api_key()
    except RuntimeError as e:
        sys.exit(f"{e}\n(파서만 검증하려면: python kma_weather.py --selftest)")

    rows = fetch_kma_surface(36.980, 126.474, "202506190000", "202506192359")
    print(f"조회 레코드: {len(rows)}건")
    for d, mm in daily_rainfall_mm(rows).items():
        print(f"  {d}  일강수(rn_day 최대) {mm:.1f} mm")
