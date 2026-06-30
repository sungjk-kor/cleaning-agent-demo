"""
agent.py — cleaning decision agent orchestration.

The agent owns the workflow:
  1. resolve region/site
  2. collect or synthesize weather and PM observations
  3. estimate soiling and cleaning priorities
  4. run LCOE impact simulation
  5. produce a compact business report
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

import os

from .airkorea_pm import daily_pm_average, demo_pm_observations, normalize_sido
from .asos_rainfall import get_region_daily_precip, get_region_hourly_precip, load_asos_range
from .kma_weather import daily_rainfall_mm, fetch_kma_surface
from .lcoe import DEFAULT_INPUTS, LcoeInputs, calculate_lcoe
from .pm_statistics import list_pm_stat_files, load_daily_pm_statistics, pm_stats_dir
from .pollution_model import PollutionModelResult, simulate_cleaning_decision


@dataclass
class Site:
    name: str
    sido: str
    lat: float
    lon: float


@dataclass
class AgentRequest:
    region_name: str = "충남 서산시"
    region1: str | None = "충남"
    region2: str | None = "서산시"
    lat: float | None = None
    lon: float | None = None
    sido: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    lookback_years: int | None = 1
    lookback_days: int = 365
    use_live_data: bool = False
    live_weather_days_limit: int = 10
    use_asos_rainfall: bool = True  # ASOS 실측 강수 사용 여부 (기본값: True)
    pm_stats_dir: str | None = None
    top_n: int = 5
    lcoe_inputs: LcoeInputs = field(default_factory=lambda: replace(DEFAULT_INPUTS))


@dataclass
class AgentResult:
    site: Site
    start_date: date
    end_date: date
    data_notes: list[str]
    rainfall_by_date: dict[date, float]
    pm_by_date: dict[date, dict]
    pollution: PollutionModelResult
    lcoe: Any
    report_markdown: str

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


REGION_CATALOG = {
    "서산": Site("충남 서산", "충남", 36.7849, 126.4503),
    "서산시": Site("충남 서산시", "충남", 36.7849, 126.4503),
    "충남 서산": Site("충남 서산", "충남", 36.7849, 126.4503),
    "충남 서산시": Site("충남 서산시", "충남", 36.7849, 126.4503),
    "당진": Site("충남 당진", "충남", 36.8931, 126.6283),
    "당진시": Site("충남 당진시", "충남", 36.8931, 126.6283),
    "충남 당진": Site("충남 당진", "충남", 36.8931, 126.6283),
    "충남 당진시": Site("충남 당진시", "충남", 36.8931, 126.6283),
    "태안": Site("충남 태안군", "충남", 36.7456, 126.2980),
    "태안군": Site("충남 태안군", "충남", 36.7456, 126.2980),
    "충남 태안군": Site("충남 태안군", "충남", 36.7456, 126.2980),
    "서울": Site("서울", "서울", 37.5665, 126.9780),
    "부산": Site("부산", "부산", 35.1796, 129.0756),
    "대구": Site("대구", "대구", 35.8714, 128.6014),
    "인천": Site("인천", "인천", 37.4563, 126.7052),
    "광주": Site("광주", "광주", 35.1595, 126.8526),
    "대전": Site("대전", "대전", 36.3504, 127.3845),
    "울산": Site("울산", "울산", 35.5384, 129.3114),
    "세종": Site("세종", "세종", 36.4800, 127.2890),
    "수원": Site("경기 수원", "경기", 37.2636, 127.0286),
    "청주": Site("충북 청주", "충북", 36.6424, 127.4890),
    "전주": Site("전북 전주", "전북", 35.8242, 127.1480),
    "목포": Site("전남 목포", "전남", 34.8118, 126.3922),
    "포항": Site("경북 포항", "경북", 36.0190, 129.3435),
    "창원": Site("경남 창원", "경남", 35.2279, 128.6811),
    "제주": Site("제주", "제주", 33.4996, 126.5312),
}


def resolve_site(
    region_name: str,
    lat: float | None = None,
    lon: float | None = None,
    sido: str | None = None,
    region1: str | None = None,
    region2: str | None = None,
) -> Site:
    """Resolve a Korean region label to a site. Explicit coordinates win."""
    if region1 and region2:
        clean_name = f"{region1.strip()} {region2.strip()}".strip()
    else:
        clean_name = (region_name or "").strip() or "충남 서산시"
    resolved = REGION_CATALOG.get(clean_name)
    if resolved is None:
        for key, value in REGION_CATALOG.items():
            if key in clean_name or clean_name in key:
                resolved = value
                break
    if resolved is None:
        resolved = Site(clean_name, normalize_sido(sido or region1 or clean_name), 36.7849, 126.4503)
    elif clean_name and clean_name != resolved.name:
        resolved = Site(clean_name, normalize_sido(sido or region1 or resolved.sido), resolved.lat, resolved.lon)

    if lat is not None and lon is not None:
        return Site(clean_name, normalize_sido(sido or region1 or resolved.sido), float(lat), float(lon))
    return resolved


def _default_period(req: AgentRequest) -> tuple[date, date]:
    if req.lookback_years is not None:
        years = max(1, min(5, int(req.lookback_years)))
        end = req.end_date or date(date.today().year, 12, 31)
        start = req.start_date or date(end.year - years + 1, 1, 1)
        return start, end

    end = req.end_date or (date.today() - timedelta(days=1))
    start = req.start_date or (end - timedelta(days=max(1, req.lookback_days) - 1))
    return start, end


def _demo_rainfall(start: date, end: date, seed_text: str) -> dict[date, float]:
    rainfall: dict[date, float] = {}
    cur = start
    while cur <= end:
        day_of_year = cur.timetuple().tm_yday
        rng = random.Random(f"{seed_text}-{cur.isoformat()}")
        monsoon = 0.24 + 0.42 * math.exp(-((day_of_year - 205) / 48) ** 2)
        spring_dry = -0.11 * math.exp(-((day_of_year - 92) / 42) ** 2)
        autumn = 0.12 * math.exp(-((day_of_year - 260) / 35) ** 2)
        rain_probability = min(0.82, max(0.04, monsoon + spring_dry + autumn))
        if rng.random() < rain_probability:
            intensity = rng.gammavariate(1.7, 5.2)
            if 175 <= day_of_year <= 240 and rng.random() < 0.18:
                intensity += rng.uniform(20, 55)
            rainfall[cur] = round(intensity, 1)
        else:
            rainfall[cur] = 0.0
        cur += timedelta(days=1)
    return rainfall


def _collect_rainfall(site: Site, start: date, end: date, req: AgentRequest):
    """
    강수량 수집: ASOS 실측(시간 단위) → 데모 시뮬레이션 → KMA API (최근 N일).

    우선순위:
      1. use_asos_rainfall=True → ASOS 시간 강수(권장)
      2. 아니면 → 데모 강수 시계열(일 단위)
      3. use_live_data=True → KMA API로 최근 N일 덮어씀(일 단위)

    Returns:
        (rainfall_input, notes) where:
          rainfall_input: pd.Series(hourly, DatetimeIndex) if ASOS
                         or dict[date, float] if demo/KMA
          notes: list[str]
    """
    import pandas as pd

    rainfall_input = None
    notes: list[str] = []

    # Step 1: ASOS 실측 강수 시도 (시간 단위)
    if req.use_asos_rainfall:
        try:
            asos_data_hourly = load_asos_range(start.year, end.year)
            if asos_data_hourly and site.lat and site.lon:
                asos_rainfall_hourly = get_region_hourly_precip(asos_data_hourly, site.lat, site.lon)
                # 분석 기간 내의 ASOS 데이터만 사용
                if len(asos_rainfall_hourly) > 0:
                    mask = (asos_rainfall_hourly.index.date >= start) & (asos_rainfall_hourly.index.date <= end)
                    rainfall_input = asos_rainfall_hourly[mask]
                    if len(rainfall_input) > 0:
                        rainy_hours = (rainfall_input > 0.0).sum()
                        notes.append(
                            f"ASOS 시간 강수(실측값)을 {start} ~ {end} 구간에 적용했습니다 "
                            f"({len(rainfall_input)}시간, {rainy_hours}시간 강우)."
                        )
                    else:
                        rainfall_input = None
                        notes.append("ASOS 데이터를 로드했으나 분석 기간에 데이터가 없습니다.")
                else:
                    notes.append("ASOS 데이터를 로드했으나 지역에 매칭되는 지점이 없습니다.")
        except Exception as exc:
            notes.append(f"ASOS 실측값 로드 실패: {exc}")

    # Step 2: 데모 강수 (ASOS 없거나 실패 시 보충) - 일 단위
    if rainfall_input is None:
        rainfall_input = _demo_rainfall(start, end, site.name)
        notes.append("기상 데이터: 기본 데모 강수 시계열을 생성했습니다 (일별).")

    # Step 3: KMA API로 최근 N일 덮어씀 (선택사항) - 일 단위만 가능
    if not req.use_live_data:
        return rainfall_input, notes

    if not os.environ.get("KMA_API_KEY"):
        notes.append("KMA_API_KEY가 없어 기상청 실조회를 건너뜁니다.")
        return rainfall_input, notes

    # rainfall_input이 Series면 일별로 변환 후 KMA와 병합
    if isinstance(rainfall_input, pd.Series):
        rainfall_dict = rainfall_input.resample("D").sum().to_dict()
        rainfall_dict = {d: v for d, v in rainfall_dict.items() if isinstance(d, date) or hasattr(d, 'date')}
        # Timestamp → date 변환
        rainfall_dict = {d.date() if hasattr(d, 'date') else d: v for d, v in rainfall_dict.items()}
    else:
        rainfall_dict = rainfall_input.copy()

    live_days = max(1, min(req.live_weather_days_limit, (end - start).days + 1))
    live_start = end - timedelta(days=live_days - 1)
    try:
        rows = fetch_kma_surface(
            site.lat,
            site.lon,
            datetime.combine(live_start, datetime.min.time()).strftime("%Y%m%d%H%M"),
            datetime.combine(end, datetime.max.time()).strftime("%Y%m%d%H%M"),
            sleep_sec=0.15,
        )
        live_rain = daily_rainfall_mm(rows)
        rainfall_dict.update({d: float(v) for d, v in live_rain.items() if start <= d <= end})
        notes.append(
            f"기상청 KMA 지상관측을 최근 {live_days}일 구간에 반영했습니다."
        )
        rainfall_input = rainfall_dict  # KMA 적용 후 일별로 반환
    except Exception as exc:
        notes.append(f"기상청 실조회 실패: {exc}")

    return rainfall_input, notes


def _region_pair_from_request(site: Site, req: AgentRequest) -> tuple[str, str]:
    region1 = (req.region1 or req.sido or site.sido or "").strip()
    region2 = (req.region2 or "").strip()

    if not region2:
        source = (req.region_name or site.name or "").strip()
        parts = source.split(maxsplit=1)
        if len(parts) == 2:
            region1 = region1 or normalize_sido(parts[0])
            region2 = parts[1]
        else:
            site_parts = site.name.split(maxsplit=1)
            if len(site_parts) == 2:
                region1 = region1 or normalize_sido(site_parts[0])
                region2 = site_parts[1]

    return region1 or site.sido, region2 or site.name


def _collect_pm(site: Site, start: date, end: date, req: AgentRequest) -> tuple[dict[date, dict], list[str]]:
    region1, region2 = _region_pair_from_request(site, req)
    demo_rows = demo_pm_observations(start, end, seed_text=site.sido)
    pm_by_date = daily_pm_average(demo_rows)
    notes: list[str] = []
    files = list_pm_stat_files(req.pm_stats_dir)

    if not files:
        notes.append(
            f"미세먼지 데이터: PM 통계 폴더({pm_stats_dir(req.pm_stats_dir)})에 .xlsx 파일이 없어 데모 PM 데이터를 사용했습니다."
        )
        return pm_by_date, notes

    try:
        stats_pm = load_daily_pm_statistics(region1, region2, start, end, req.pm_stats_dir)
        if stats_pm:
            pm_by_date.update(stats_pm)
            total_days = (end - start).days + 1
            notes.append(
                f"미세먼지 데이터: PM 통계 엑셀에서 {region1}-{region2} PM10/PM2.5 일평균 {len(stats_pm):,}일을 반영했습니다."
            )
            if len(stats_pm) < total_days:
                notes.append(f"통계 파일에 없는 {total_days - len(stats_pm):,}일은 데모 PM 데이터로 보완했습니다.")
        else:
            notes.append(f"미세먼지 데이터: {region1}-{region2}의 선택 기간 통계가 없어 데모 PM 데이터를 사용했습니다.")
    except Exception as exc:
        notes.append(f"미세먼지 통계 엑셀 로딩 실패로 데모 PM 데이터를 사용했습니다: {exc}")
    return pm_by_date, notes


def run_cleaning_agent(req: AgentRequest) -> AgentResult:
    """Run the cleaning decision agent end to end."""
    start, end = _default_period(req)
    site = resolve_site(req.region_name, req.lat, req.lon, req.sido, req.region1, req.region2)

    rainfall_by_date, weather_notes = _collect_rainfall(site, start, end, req)
    pm_by_date, pm_notes = _collect_pm(site, start, end, req)

    lcoe_base_inputs = req.lcoe_inputs
    pollution = simulate_cleaning_decision(
        rainfall_by_date=rainfall_by_date,
        pm_by_date=pm_by_date,
        start=start,
        end=end,
        capacity_kw=lcoe_base_inputs.capacity,
        util_rate_pct=lcoe_base_inputs.util_rate,
        top_n=req.top_n,
        regional_weight_ppt=0.0,
        model_name="hsu",  # HSU 모델만 사용 (근거 있는 IEEE 논문 기반)
    )

    lcoe_inputs = replace(
        lcoe_base_inputs,
        pollution_loss=max(0.0, min(30.0, pollution.annual_pollution_loss_pct)),
    )
    lcoe_result = calculate_lcoe(lcoe_inputs)

    report = build_report(site, start, end, pollution, lcoe_result)
    return AgentResult(
        site=site,
        start_date=start,
        end_date=end,
        data_notes=weather_notes + pm_notes,
        rainfall_by_date=rainfall_by_date,
        pm_by_date=pm_by_date,
        pollution=pollution,
        lcoe=lcoe_result,
        report_markdown=report,
    )


def build_report(site: Site, start: date, end: date, pollution: PollutionModelResult, lcoe_result: Any) -> str:
    """Build a concise Korean report for the web app and API."""
    lines = [
        f"### {site.name} 청소 판단 리포트",
        f"- 분석 기간: {start.isoformat()} ~ {end.isoformat()}",
        f"- 연평균 오염 손실률 추정: {pollution.annual_pollution_loss_pct:.2f}%",
        f"- 연간 발전량 감소 추정: {pollution.annual_generation_loss_kwh:,.0f} kWh",
        f"- LCOE 영향: {lcoe_result.base_lcoe:.2f} → {lcoe_result.ref_lcoe:.2f} 원/kWh (+{lcoe_result.lcoe_increase:.2f}%)",
        "",
        "#### 세척 우선순위 Top 5",
    ]
    for item in pollution.priorities:
        lines.append(
            f"{item.rank}. {item.date.isoformat()} "
            f"(오염손실 {item.soiling_loss_pct:.2f}%, 7일 손실 {item.expected_7d_loss_kwh:,.0f} kWh) — {item.reason}"
        )
    return "\n".join(lines)


def _jsonable(value):
    """Convert dataclass/date-heavy result objects into JSON-safe values."""
    if isinstance(value, dict):
        return {str(_jsonable(k)): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return value
