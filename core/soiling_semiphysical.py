# -*- coding: utf-8 -*-
"""
soiling_semiphysical.py — IEA 보고서 기반 반물리 5단계 소일링 모델.

근거 문서:
  - IEA PVPS 보고서 (Coello & Boyle 계열 반물리 모델)
  - "소일링 발전손실 계산 - 4개 보고서 요약" (첨부 PDF)
  - 원형 구현: soiling_effect_4reports_model.py

5단계 구조:
  1단계 PM 분리:   PM_coarse = max(PM10 - PM2.5, 0)
  2단계 일퇴적:    dm = 0.0864 * cos(t) * (v_f*PM2.5 + v_c*PM_coarse) * F_site   [g/m2/day]
  3단계 강우세정:  eta_rain = 0 (R<R0) / eta_max*[1-exp(-k_R*(R-R0))] (R>=R0)
  4단계 누적/재비산: m_before = max(0, m_prev + dm - rho*m_prev);  m = m_before*(1-eta_rain)*(1-eta_manual)
  5단계 비선형손실: SR = exp(-kappa * m^gamma);  SL = 1 - SR

연손실 = mean(SL) * 100  (일사 가중은 향후 POA 연동 시 적용)

보정 철학 (PDF 1페이지 근거):
  - PDF: "퇴적속도 설정에 따라 결과가 크게 달라지며, 실제 발전소 손실 추정에는 보정이 필요"
  - v_f, v_c는 pvlib HSU 학술 기본값(0.0009, 0.004)을 유지하고,
    국내 실측 소일링 발생률로의 보정을 DEPO_CAL(전역계수)에 격리.
  - F_site는 PDF 정의대로 '일반=1.0', 지역특성(산업/건조/해안)에 배수 적용.
  - DEPO_CAL=14는 일반 지역(F_site=1)을 IEA 세계평균(3~5%)에 안착시키는 보정값.
    (서산 2025 실측 검증: 일반 3.4%, 산업 6.5%, 건조농업 8.0%, 극심 9.4%)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

import numpy as np
import pandas as pd


# ── 모델 설정 (PDF 권장값 + 국내 보정) ──────────────────────────────
CONFIG = {
    "tilt_deg": 30.0,      # 모듈 경사각 (한국 고정설치 표준)
    "v_f": 0.0009,         # PM2.5 유효 퇴적속도 (m/s, pvlib HSU 학술값)
    "v_c": 0.004,          # 조대입자 유효 퇴적속도 (m/s, pvlib HSU 학술값)
    "depo_cal": 14.0,      # 전역 퇴적 보정 (학술값 -> 국내 실측 소일링 발생률)
    "rho": 0.0,            # 일별 재비산률 (바람), 0=미적용
    "k_R": 0.3,            # 강우 세정 민감도
    "gamma": 1.0,          # 비선형 지수 (현장보정 전 1.0)
    "eta_max": 0.8,        # 최대 자연 세정률 (PDF 권장 0.8)
    "R0": 2.5,             # 유효 강우 임계치 (mm, PDF 시나리오 중앙값)
    "kappa": 0.0416,       # 먼지질량->손실 변환 (PDF: 10g/m2 -> 34% 손실)
    "eta_manual": 0.0,     # 인공 청소율 (0=청소 없음)
}

# ── 지역 유형별 F_site 계수 (PDF: 일반=1.0) ─────────────────────────
REGION_FSITE = {
    "general": 1.0,            # 일반 (도시/주거)
    "coastal": 1.3,           # 해안 (염분)
    "agricultural": 1.5,      # 농업 (꽃가루/토양)
    "industrial": 2.0,        # 산업단지 인접
    "dry_agricultural": 2.5,  # 건조 농업지역
    "heavy_pollution": 3.0,   # 극심 오염원 인접
}

REGION_LABELS = {
    "general": "일반",
    "coastal": "해안",
    "agricultural": "농업",
    "industrial": "산업단지 인접",
    "dry_agricultural": "건조 농업지역",
    "heavy_pollution": "극심 오염원 인접",
}

# ── 지역특성(R1~R5) -> F_site 가산 증분 ─────────────────────────────
# 사이드바 체크박스(농업/산업/도로/해안/저틸트) × 강도(저/중/고)를
# F_site 증분으로 변환. 여러 특성 동시 적용 가능(가산), 상한 3.0.
FSITE_INCREMENTS = {
    "agricultural": {"low": 0.25, "mid": 0.50, "high": 0.75},  # R1 농업
    "industrial":   {"low": 0.50, "mid": 1.00, "high": 1.50},  # R2 산업/건설
    "traffic":      {"low": 0.20, "mid": 0.40, "high": 0.60},  # R3 철도/도로
    "coastal":      {"low": 0.15, "mid": 0.30, "high": 0.45},  # R4 해안
    "tilt":         {"low": 0.10, "mid": 0.20, "high": 0.30},  # R5 저틸트
}
FSITE_CAP = 3.0  # F_site 상한 (극심 오염원 수준)


def fsite_from_characteristics(regional_characteristics: dict | None) -> tuple[float, list[dict]]:
    """
    지역특성 dict(R1~R5 체크+강도) -> F_site 값 + 내역.

    Args:
        regional_characteristics: {
            "r1_agricultural": bool, "r1_level": "low|mid|high",
            "r2_industrial": bool, "r2_level": ...,
            "r3_traffic": ..., "r4_coastal": ..., "r5_tilt": ...,
        }

    Returns:
        (f_site, breakdown)
        f_site: 1.0(일반) ~ 3.0(상한)
        breakdown: [{"key","label","level","increment"}, ...]
    """
    keymap = [
        ("agricultural", "r1_agricultural", "r1_level", "농업"),
        ("industrial", "r2_industrial", "r2_level", "산업/건설 인접"),
        ("traffic", "r3_traffic", "r3_level", "철도/도로 인접"),
        ("coastal", "r4_coastal", "r4_level", "해안 인접"),
        ("tilt", "r5_tilt", "r5_level", "저틸트/하단집중"),
    ]
    rc = regional_characteristics or {}
    total = 1.0
    breakdown: list[dict] = []
    for key, flag_field, level_field, label in keymap:
        if not rc.get(flag_field):
            continue
        level = rc.get(level_field, "mid")
        inc = FSITE_INCREMENTS[key].get(level, FSITE_INCREMENTS[key]["mid"])
        total += inc
        breakdown.append({"key": key, "label": label, "level": level, "increment": inc})

    f_site = min(round(total, 3), FSITE_CAP)
    return f_site, breakdown


@dataclass
class SemiPhysicalResult:
    """반물리 모델 결과 (HsuSoilingResult와 호환 형태)."""

    daily: list[dict]           # [{date, loss_pct, rainfall_mm, mass_g}, ...]
    annual_loss_pct: float      # 연평균 손실률
    peak_loss_pct: float        # 최대 손실률
    p95_loss_pct: float         # 95 백분위수
    spring_loss_pct: float | None  # 봄철(3~5월) 평균
    days_exceed_2pct: int       # 손실률 > 2%인 날 수
    max_mass_g: float           # 최대 누적 먼지량 (g/m2)
    region_type: str            # 적용 지역 유형
    f_site: float               # 적용 F_site 값
    assumptions: list[str]


def _daily_deposition(pm25: np.ndarray, pm10: np.ndarray, f_site_eff: float) -> np.ndarray:
    """2단계: 일별 퇴적량 (g/m2/day). f_site_eff = depo_cal * F_site."""
    coarse = np.maximum(pm10 - pm25, 0.0)  # 1단계: PM 분리
    cos_t = np.cos(np.radians(CONFIG["tilt_deg"]))
    return 0.0864 * cos_t * (CONFIG["v_f"] * pm25 + CONFIG["v_c"] * coarse) * f_site_eff


def _eta_rain(R: np.ndarray) -> np.ndarray:
    """3단계: 강우 세정률."""
    R0, eta_max, k_R = CONFIG["R0"], CONFIG["eta_max"], CONFIG["k_R"]
    return np.where(R < R0, 0.0, eta_max * (1.0 - np.exp(-k_R * np.maximum(R - R0, 0.0))))


def _build_daily_frame(
    rainfall_input,
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
) -> pd.DataFrame:
    """강수(시간 Series 또는 일별 dict) + PM(일별 dict) -> 일별 DataFrame."""
    # 강수 -> 일별 집계
    if isinstance(rainfall_input, pd.Series):
        rain_daily = rainfall_input.resample("D").sum()
        rain_lookup = {ts.date(): float(v) for ts, v in rain_daily.items()}
    else:
        rain_lookup = {d: float(v or 0.0) for d, v in rainfall_input.items()}

    date_range = pd.date_range(start=start, end=end, freq="D")
    rows = []
    for ts in date_range:
        d = ts.date()
        pm = pm_by_date.get(d, {})
        rows.append({
            "date": ts,
            "pm25": float(pm.get("pm25", 18.0)),  # 기본값 18 ug/m3
            "pm10": float(pm.get("pm10", 35.0)),  # 기본값 35 ug/m3
            "rain": rain_lookup.get(d, 0.0),
        })
    return pd.DataFrame(rows).set_index("date")


def run_semiphysical_model(
    rainfall_input,
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
    region_type: str = "general",
    f_site_override: float | None = None,
) -> SemiPhysicalResult:
    """
    반물리 5단계 소일링 모델 실행.

    Args:
        rainfall_input: pd.Series(시간, DatetimeIndex) 또는 {date: mm}
        pm_by_date: {date: {"pm10": ug/m3, "pm25": ug/m3}, ...}
        start, end: 분석 기간
        region_type: 지역 유형 (REGION_FSITE 키). 기본 "general"
        f_site_override: F_site 직접 지정 (region_type 무시)

    Returns:
        SemiPhysicalResult
    """
    daily = _build_daily_frame(rainfall_input, pm_by_date, start, end)

    # F_site 결정
    if f_site_override is not None:
        f_site = float(f_site_override)
    else:
        f_site = REGION_FSITE.get(region_type, 1.0)
    f_site_eff = CONFIG["depo_cal"] * f_site

    # 2~3단계 벡터화
    dep = _daily_deposition(daily["pm25"].values, daily["pm10"].values, f_site_eff)
    er = _eta_rain(daily["rain"].values)

    # 4~5단계 순차 누적
    n = len(daily)
    mass = np.zeros(n)
    SL = np.zeros(n)
    prev = 0.0
    rho, eta_manual, kappa, gamma = (
        CONFIG["rho"], CONFIG["eta_manual"], CONFIG["kappa"], CONFIG["gamma"],
    )
    for i in range(n):
        m_before = max(0.0, prev + dep[i] - rho * prev)
        m_after = m_before * (1.0 - er[i]) * (1.0 - eta_manual)
        SL[i] = 1.0 - np.exp(-kappa * (m_before ** gamma))  # 세정 직전 질량 기준
        mass[i] = m_after
        prev = m_after

    daily["mass_g"] = mass
    daily["SL"] = SL
    loss_pct_series = pd.Series(SL * 100.0, index=daily.index)

    # 통계
    annual_loss = float(loss_pct_series.mean())
    peak_loss = float(loss_pct_series.max())
    p95_loss = float(loss_pct_series.quantile(0.95))
    spring_mask = loss_pct_series.index.month.isin([3, 4, 5])
    spring_loss = float(loss_pct_series[spring_mask].mean()) if spring_mask.any() else None
    days_exceed_2 = int((loss_pct_series > 2.0).sum())

    # 일별 상세
    daily_data = []
    for ts, row in daily.iterrows():
        daily_data.append({
            "date": ts.date(),
            "loss_pct": round(float(row["SL"] * 100.0), 3),
            "rainfall_mm": round(float(row["rain"]), 1),
            "mass_g": round(float(row["mass_g"]), 4),
        })

    return SemiPhysicalResult(
        daily=daily_data,
        annual_loss_pct=round(annual_loss, 3),
        peak_loss_pct=round(peak_loss, 3),
        p95_loss_pct=round(p95_loss, 3),
        spring_loss_pct=round(spring_loss, 3) if spring_loss is not None else None,
        days_exceed_2pct=days_exceed_2,
        max_mass_g=round(float(mass.max()), 4),
        region_type=region_type,
        f_site=f_site,
        assumptions=[
            "반물리 5단계 소일링 모델 (IEA PVPS Task 13 / Coello-Boyle 계열)",
            f"경사각 {CONFIG['tilt_deg']}도, 퇴적속도 v_f={CONFIG['v_f']} v_c={CONFIG['v_c']} m/s",
            f"전역 퇴적보정 DEPO_CAL={CONFIG['depo_cal']} (학술값 -> 국내 실측 소일링 보정)",
            f"지역계수 F_site={f_site} ({REGION_LABELS.get(region_type, region_type)})",
            f"강우세정 eta_max={CONFIG['eta_max']}, R0={CONFIG['R0']}mm, k_R={CONFIG['k_R']}",
            f"손실변환 kappa={CONFIG['kappa']} (10g/m2->34% 손실 기준), gamma={CONFIG['gamma']}",
            "PM 단위 ug/m3, 강수 ASOS 일강수량(mm)",
            "[참고보고서 1] IEA PVPS T13-21:2022, Soiling Losses - Impact on the "
            "Performance of Photovoltaic Power Plants (2022)",
            "[참고보고서 2] Systematic review of soiling mitigation strategies for "
            "solar photovoltaic panels (2026)",
            "[모델 원출처] Coello & Boyle (2019), IEEE J. Photovoltaics 9(5):1382-1387",
        ],
    )


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from core.pm_statistics import load_daily_pm_statistics
    from core.agent import Site, AgentRequest, _collect_rainfall

    print("\n" + "=" * 72)
    print("반물리 5단계 모델 자체 테스트 (서산 2025)")
    print("=" * 72 + "\n")

    site = Site(name="충남 서산시", sido="충남", lat=36.7897, lon=126.4497)
    req = AgentRequest(use_asos_rainfall=True, use_live_data=False)
    start, end = date(2025, 1, 1), date(2025, 12, 31)

    pm_by_date = load_daily_pm_statistics("충남", "서산시", start, end)
    rainfall_input, _ = _collect_rainfall(site, start, end, req)

    print(f"{'지역유형':<18} {'F_site':<8} {'연손실%':<10} {'피크%':<10} {'봄철%':<10}")
    print("-" * 60)
    for rt in REGION_FSITE:
        r = run_semiphysical_model(rainfall_input, pm_by_date, start, end, region_type=rt)
        spring = f"{r.spring_loss_pct:.2f}" if r.spring_loss_pct is not None else "-"
        print(f"{REGION_LABELS[rt]:<18} {r.f_site:<8} {r.annual_loss_pct:<10.2f} "
              f"{r.peak_loss_pct:<10.2f} {spring:<10}")

    print("\n[일반 지역 상세]")
    r = run_semiphysical_model(rainfall_input, pm_by_date, start, end, region_type="general")
    print(f"  연평균 {r.annual_loss_pct}%  피크 {r.peak_loss_pct}%  "
          f"P95 {r.p95_loss_pct}%  최대먼지 {r.max_mass_g}g/m2  손실>2% {r.days_exceed_2pct}일")
    print("\n  가정사항:")
    for a in r.assumptions:
        print(f"   - {a}")
    print("\n" + "=" * 72 + "\n")
