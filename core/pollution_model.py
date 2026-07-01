"""
pollution_model.py — explainable soiling and cleaning priority model.

This is intentionally a compact, replaceable heuristic. The business-facing
agent can already produce useful demo reports, while the scientific model can
later be swapped with a calibrated model without touching Streamlit/FastAPI.

지원하는 소일링 모델:
  - "pm": PM 기반 휴리스틱 (기존)
  - "hsu": pvlib HSU (Coello & Boyle 2019, IEEE J. Photovoltaics)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Literal, Mapping

from .soiling_semiphysical import run_semiphysical_model


@dataclass
class DailySoiling:
    date: date
    rainfall_mm: float
    pm10: float | None
    pm25: float | None
    dry_days: int
    pm_based_soiling_pct: float  # PM 침적만 기반 손실
    regional_weight_ppt: float  # 지역특성 가중치 (%p)
    soiling_loss_pct: float  # 최종 = PM 손실 + 지역특성
    priority_score: float


@dataclass
class CleaningPriority:
    rank: int
    date: date
    month: int
    priority_score: float
    soiling_loss_pct: float
    rain_14d_mm: float
    pm10_14d: float | None
    pm25_14d: float | None
    dry_days: int
    reason: str
    expected_daily_loss_kwh: float
    expected_7d_loss_kwh: float


@dataclass
class PollutionModelResult:
    daily: list[DailySoiling]
    priorities: list[CleaningPriority]
    annual_pollution_loss_pct: float
    annual_pm_loss_pct: float  # PM 기반만
    annual_regional_weight_ppt: float  # 지역특성 가중치
    annual_generation_loss_kwh: float
    assumptions: list[str]
    model_name: str = "pm"  # "pm" | "hsu"

    def to_dict(self) -> dict:
        return {
            "daily": [asdict(row) for row in self.daily],
            "priorities": [asdict(row) for row in self.priorities],
            "annual_pollution_loss_pct": self.annual_pollution_loss_pct,
            "annual_pm_loss_pct": self.annual_pm_loss_pct,
            "annual_regional_weight_ppt": self.annual_regional_weight_ppt,
            "annual_generation_loss_kwh": self.annual_generation_loss_kwh,
            "assumptions": self.assumptions,
            "model_name": self.model_name,
        }


def _daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _window_sum(series: Mapping[date, float], end: date, days: int) -> float:
    start = end - timedelta(days=days - 1)
    return sum(float(series.get(d, 0.0) or 0.0) for d in _daterange(start, end))


def _window_mean(series: Mapping[date, float | None], end: date, days: int) -> float | None:
    start = end - timedelta(days=days - 1)
    return _mean([series.get(d) for d in _daterange(start, end)])


def _rain_washoff_factor(rainfall_mm: float) -> float:
    """Return the remaining soiling fraction after natural rainfall cleaning."""
    if rainfall_mm >= 20:
        return 0.12
    if rainfall_mm >= 10:
        return 0.25
    if rainfall_mm >= 5:
        return 0.55
    if rainfall_mm >= 1:
        return 0.85
    return 1.0


def build_daily_soiling(
    rainfall_by_date: Mapping[date, float],
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
    regional_weight_ppt: float = 0.0,
    max_loss_pct: float = 9.0,
) -> list[DailySoiling]:
    """
    Create a daily soiling trajectory from rain and particulate matter.

    최종 소일링 손실 = PM 기반 손실 + 지역특성 가중치

    Heuristic:
      - PM10 and PM2.5 add a daily deposition increment.
      - Strong rain naturally washes accumulated soiling.
      - Long dry streaks amplify the cleaning priority score.
      - Regional characteristics (agriculture, coastal, etc.) add fixed weight.
    """
    daily: list[DailySoiling] = []
    dry_days = 0
    pm_soiling = 0.0

    for d in _daterange(start, end):
        rainfall = float(rainfall_by_date.get(d, 0.0) or 0.0)
        pm = pm_by_date.get(d, {})
        pm10 = pm.get("pm10")
        pm25 = pm.get("pm25")

        pm10_for_model = float(pm10) if pm10 is not None else 35.0
        pm25_for_model = float(pm25) if pm25 is not None else 18.0
        deposition = max(0.0, (pm10_for_model * 0.012 + pm25_for_model * 0.020) / 10)

        if rainfall >= 1:
            dry_days = 0
            pm_soiling *= _rain_washoff_factor(rainfall)
        else:
            dry_days += 1

        pm_soiling = min(max_loss_pct, pm_soiling + deposition)
        final_soiling = pm_soiling + (regional_weight_ppt / 100.0)
        dry_multiplier = 1 + min(dry_days, 45) / 45
        pm_multiplier = 1 + max(0.0, pm10_for_model - 35) / 120 + max(0.0, pm25_for_model - 20) / 90
        priority_score = final_soiling * dry_multiplier * pm_multiplier

        daily.append(
            DailySoiling(
                date=d,
                rainfall_mm=round(rainfall, 2),
                pm10=round(pm10, 2) if pm10 is not None else None,
                pm25=round(pm25, 2) if pm25 is not None else None,
                dry_days=dry_days,
                pm_based_soiling_pct=round(pm_soiling, 3),
                regional_weight_ppt=round(regional_weight_ppt, 3),
                soiling_loss_pct=round(final_soiling, 3),
                priority_score=round(priority_score, 3),
            )
        )
    return daily


def pick_cleaning_priorities(
    daily: list[DailySoiling],
    capacity_kw: float,
    util_rate_pct: float,
    top_n: int = 5,
    min_spacing_days: int = 21,
) -> list[CleaningPriority]:
    """Pick the top cleaning dates while avoiding duplicate adjacent peaks."""
    if not daily:
        return []

    rain = {row.date: row.rainfall_mm for row in daily}
    pm10 = {row.date: row.pm10 for row in daily}
    pm25 = {row.date: row.pm25 for row in daily}
    base_daily_gen = capacity_kw * 24 * (util_rate_pct / 100)

    candidates = sorted(daily, key=lambda row: row.priority_score, reverse=True)
    selected: list[DailySoiling] = []
    for candidate in candidates:
        if all(abs((candidate.date - row.date).days) >= min_spacing_days for row in selected):
            selected.append(candidate)
        if len(selected) >= top_n:
            break
    if len(selected) < top_n:
        selected_dates = {row.date for row in selected}
        for candidate in candidates:
            if candidate.date not in selected_dates:
                selected.append(candidate)
                selected_dates.add(candidate.date)
            if len(selected) >= top_n:
                break

    selected.sort(key=lambda row: row.date)
    priorities = []
    for rank, row in enumerate(sorted(selected, key=lambda x: x.priority_score, reverse=True), start=1):
        rain_14d = _window_sum(rain, row.date, 14)
        pm10_14d = _window_mean(pm10, row.date, 14)
        pm25_14d = _window_mean(pm25, row.date, 14)
        expected_daily_loss = base_daily_gen * (row.soiling_loss_pct / 100)
        expected_7d_loss = expected_daily_loss * 7
        reason_parts = []
        if row.dry_days >= 10:
            reason_parts.append(f"{row.dry_days}일 연속 건조")
        if rain_14d < 5:
            reason_parts.append("최근 14일 강수 부족")
        if pm10_14d and pm10_14d >= 45:
            reason_parts.append("PM10 누적 높음")
        if pm25_14d and pm25_14d >= 25:
            reason_parts.append("PM2.5 누적 높음")
        if not reason_parts:
            reason_parts.append("오염 누적 지수 상위")

        priorities.append(
            CleaningPriority(
                rank=rank,
                date=row.date,
                month=row.date.month,
                priority_score=row.priority_score,
                soiling_loss_pct=row.soiling_loss_pct,
                rain_14d_mm=round(rain_14d, 2),
                pm10_14d=round(pm10_14d, 1) if pm10_14d is not None else None,
                pm25_14d=round(pm25_14d, 1) if pm25_14d is not None else None,
                dry_days=row.dry_days,
                reason=", ".join(reason_parts),
                expected_daily_loss_kwh=round(expected_daily_loss, 1),
                expected_7d_loss_kwh=round(expected_7d_loss, 1),
            )
        )
    return priorities


def simulate_cleaning_decision(
    rainfall_by_date,  # pd.Series(hourly) 권장 | Mapping[date, float]
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
    capacity_kw: float,
    util_rate_pct: float,
    top_n: int = 5,
    regional_weight_ppt: float = 0.0,  # (구) 가산식 — 신모델에서는 미사용, 호환 유지
    model_name: str = "semiphysical",
    f_site: float = 1.0,
) -> PollutionModelResult:
    """
    반물리 5단계 소일링 모델로 연손실과 세척 우선순위 산출.

    최종 손실 = 반물리 모델(F_site 적용)
    audit 분리: base = F_site 1.0(PM/강수 기반), regional = F_site 효과 증가분

    Args:
        rainfall_by_date: pd.Series(hourly, DatetimeIndex) 권장 | dict[date: mm]
        f_site: 지역특성 계수 (1.0=일반, 산업/건조는 배수). 지역특성에서 매핑됨.
        model_name: "semiphysical" (반물리 5단계, IEA 보고서 기반)
    """
    # 전체 손실 (실제 F_site 적용)
    total_result = run_semiphysical_model(
        rainfall_by_date, pm_by_date, start, end, f_site_override=f_site
    )
    # 기저 손실 (F_site=1, PM/강수만) — audit 분리용
    if abs(f_site - 1.0) < 1e-9:
        base_result = total_result
    else:
        base_result = run_semiphysical_model(
            rainfall_by_date, pm_by_date, start, end, f_site_override=1.0
        )
    base_by_date = {item["date"]: item["loss_pct"] for item in base_result.daily}

    # 반물리 결과를 DailySoiling 형태로 변환 (base/regional 분리)
    daily: list[DailySoiling] = []
    for item in total_result.daily:
        d = item["date"]
        total_loss = item["loss_pct"]
        base_loss = base_by_date.get(d, total_loss)
        regional_ppt = total_loss - base_loss  # F_site로 늘어난 손실 (%p)
        rainfall_mm = item.get("rainfall_mm", 0.0)
        pm = pm_by_date.get(d, {})
        pm10 = pm.get("pm10")
        pm25 = pm.get("pm25")

        daily.append(
            DailySoiling(
                date=d,
                rainfall_mm=round(rainfall_mm, 2),
                pm10=round(pm10, 2) if pm10 is not None else None,
                pm25=round(pm25, 2) if pm25 is not None else None,
                dry_days=0,
                pm_based_soiling_pct=round(base_loss, 3),       # F_site=1 기저
                regional_weight_ppt=round(regional_ppt, 3),     # F_site 증가분
                soiling_loss_pct=round(total_loss, 3),          # 최종(F_site 적용)
                priority_score=round(total_loss, 3),
            )
        )

    # Step 2: 세척 우선순위 계산
    priorities = pick_cleaning_priorities(
        daily=daily,
        capacity_kw=capacity_kw,
        util_rate_pct=util_rate_pct,
        top_n=top_n,
    )

    # Step 3: 연평균 손실 계산
    if daily:
        annual_pollution_loss_pct = sum(row.soiling_loss_pct for row in daily) / len(daily)
        annual_pm_loss_pct = sum(row.pm_based_soiling_pct for row in daily) / len(daily)
        annual_regional_ppt = sum(row.regional_weight_ppt for row in daily) / len(daily)
    else:
        annual_pollution_loss_pct = 0.0
        annual_pm_loss_pct = 0.0
        annual_regional_ppt = 0.0
    annual_base_gen = capacity_kw * 8760 * (util_rate_pct / 100)
    annual_generation_loss = annual_base_gen * (annual_pollution_loss_pct / 100)

    # 반물리 모델 가정사항 (신모델에서 직접 가져옴)
    assumptions = list(total_result.assumptions)

    return PollutionModelResult(
        daily=daily,
        priorities=priorities,
        annual_pollution_loss_pct=round(annual_pollution_loss_pct, 3),
        annual_pm_loss_pct=round(annual_pm_loss_pct, 3),
        annual_regional_weight_ppt=round(annual_regional_ppt, 3),
        annual_generation_loss_kwh=round(annual_generation_loss, 1),
        assumptions=assumptions,
        model_name=model_name,
    )
