# -*- coding: utf-8 -*-
"""
soiling_semiphysical.py — 강우사건(R_e) 기반 반물리 소일링·세척 모델.

근거 문서:
  - IEA PVPS 보고서 (Coello & Boyle 계열 반물리 모델)
  - "소일링 발전손실 계산 - 4개 보고서 요약" (첨부 PDF)
  - 검증 정답지: 20260701_소일링_세척모델_검증.xlsx (설정/일별_보수 시트)

모델 구조 (강우사건 기반 세척):
  1) PM 분리:     PM_coarse = max(PM10 - PM2.5, 0)
  2) 일 퇴적:     Δm = 0.0864·cosθ·(v_f·PM2.5 + v_c·PM_coarse)·F_site·DEPO_CAL   [g/m²/day]
                  일 누적강수 < 5mm이면 소일링 20% 가중 (Δm ×1.2).
  3) 강우사건:    시간단위 강수에서 무강우 gap(기본 6h) 이상이면 다음 사건으로 초기화.
                  R_e = 한 사건 내 강수 합. 세척은 '사건 종료 시' 그 R_e로 1회 적용.
  4) 단계 세척:   R_e < T1(10) → η_weak(0.05)
                  T1 ≤ R_e < T2(20) → 완화 η_partial(0.55) / 보수 η_weak(0.05)
                  R_e ≥ T2(20) → η_strong(0.85)
                  잔류 약화: η_eff = η_tier·(1 − residual),  완전초기화 금지.
                  residual = min(0.60, 0.15 + 비계절(철새·염분·도로) + 봄철(꽃가루))
  5) 누적/손실:   M_before = M_prev + Δm;  M_after = M_before·(1 − η_eff)
                  손실은 세척 직전 질량 기준:  SL = 1 − exp(−κ·M_before^γ)

두 시나리오(완화/보수)를 각각 돌려 range로 표출:
  헤드라인 = "연 [완화]~[보수]%, 봄철 피크 [보수 peak]%"

보정 철학 (현장 보정 전, field-calibration pending):
  - v_f, v_c는 pvlib HSU 학술 기본값(0.0009, 0.004)을 그대로 사용.
  - DEPO_CAL 기본값 1.0 = 미보정(학술값 그대로). 목표 %에 맞춘 역산 없음.
  - F_site·잔류(residual)는 지역특성/부지가중으로 산정하되 각 항목에 출처를 명시.
  - 절대값 확정에는 실측 소일링 센서 보정이 필요. 본 산출은 단일연도 시나리오값이며
    장기평균은 10년(최소)~30년(기후평년) 반복계산이 필요하다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Mapping

import numpy as np
import pandas as pd


# ── 모델 설정 (엑셀 '설정' 시트와 동일값) ──────────────────────────────
CONFIG = {
    "tilt_deg": 30.0,      # 모듈 경사각 (한국 고정설치 표준)
    "v_f": 0.0009,         # PM2.5 유효 퇴적속도 (m/s, pvlib HSU 학술값)
    "v_c": 0.004,          # 조대입자 유효 퇴적속도 (m/s, pvlib HSU 학술값)
    "depo_cal": 1.0,       # 전역 퇴적 보정 (1.0=미보정/학술값 그대로, 현장 센서 보정 전)
    "kappa": 0.0416,       # 먼지질량->손실 변환 (10g/m2 -> 34% 손실 기준)
    "gamma": 1.0,          # 비선형 지수 (현장보정 전 1.0)

    # 강우사건(R_e) 세척
    "event_gap_hours": 6,  # 사건 종료 판정: 무강우 지속 시간(h)
    "T1_mm": 10.0,         # 약한/부분 세척 경계 (mm)
    "T2_mm": 20.0,         # 부분/강한 세척 경계 (mm)
    "eta_weak": 0.05,      # R_e < T1 (또는 보수: T1<=R_e<T2)
    "eta_partial": 0.55,   # T1 <= R_e < T2 (완화)
    "eta_strong": 0.85,    # R_e >= T2

    # 잔류(세척효율 약화)
    "residual_base": 0.15,
    "residual_cap": 0.60,

    # 저강수 소일링 가중
    "low_rain_mm": 5.0,           # 일 누적강수 < 5mm
    "low_rain_soil_weight": 0.20, # 소일링 20% 가중

    # 유효세척 판정 임계 (기상분석 통계용)
    "effective_eta": 0.30,
}

# ── 지역 유형별 F_site 계수 (일반=1.0) ─────────────────────────────
REGION_FSITE = {
    "general": 1.0,
    "coastal": 1.3,
    "agricultural": 1.5,
    "industrial": 2.0,
    "dry_agricultural": 2.5,
    "heavy_pollution": 3.0,
}

REGION_LABELS = {
    "general": "일반",
    "coastal": "해안",
    "agricultural": "농업",
    "industrial": "산업단지 인접",
    "dry_agricultural": "건조 농업지역",
    "heavy_pollution": "극심 오염원 인접",
}

# ── 지역특성 -> F_site 가산 증분 (레벨별) ─────────────────────────────
FSITE_INCREMENTS = {
    "agricultural": {"low": 0.25, "mid": 0.50, "high": 0.75},  # R1 농업(꽃가루)
    "industrial":   {"low": 0.50, "mid": 1.00, "high": 1.50},  # R2 산업/제철
    "traffic":      {"low": 0.20, "mid": 0.40, "high": 0.60},  # R3 철도/도로
    "coastal":      {"low": 0.15, "mid": 0.30, "high": 0.45},  # R4 해안(염분)
    "tilt":         {"low": 0.10, "mid": 0.20, "high": 0.30},  # R5 저틸트
    "organic":      {"low": 0.20, "mid": 0.40, "high": 0.60},  # R6 생물오염
    "bird":         {"low": 0.20, "mid": 0.40, "high": 0.60},  # R7 철새도래지
}
FSITE_CAP = 3.0

# ── 지역특성별 잔류(세척효율 약화) 증분 + 출처 ────────────────────────
# nonseasonal: 상시 적용,  spring: 봄철(3~5월)만 적용.
FACTOR_RESIDUAL = {
    "agricultural": {"nonseasonal": 0.00, "spring": 0.15,
                     "source": "봄철 꽃가루·토양 비산 (농업지역)"},
    "industrial":   {"nonseasonal": 0.00, "spring": 0.00,
                     "source": "제철/산업 분진 (부착성)"},
    "traffic":      {"nonseasonal": 0.05, "spring": 0.00,
                     "source": "도로/철도 분진·브레이크 입자"},
    "coastal":      {"nonseasonal": 0.10, "spring": 0.00,
                     "source": "해안 염분 (조해성 고착)"},
    "tilt":         {"nonseasonal": 0.05, "spring": 0.00,
                     "source": "저틸트 하단 집중 (세척 배수 불량)"},
    "organic":      {"nonseasonal": 0.10, "spring": 0.05,
                     "source": "새분비물·꽃가루·조류"},
    "bird":         {"nonseasonal": 0.10, "spring": 0.00,
                     "source": "철새도래지 인접(천수만 등) 분비물"},
}


def fsite_from_characteristics(
    regional_characteristics: dict | None,
) -> tuple[float, dict, list[dict]]:
    """
    지역특성 dict -> (F_site, residual_info, breakdown).

    Args:
        regional_characteristics: {
            "r1_agricultural": bool, "r1_level": "low|mid|high",
            "r2_industrial": ..., "r3_traffic": ..., "r4_coastal": ...,
            "r5_tilt": ..., "r6_organic": ..., "bird_adjacent": bool, "bird_level": ...,
        }

    Returns:
        f_site: 1.0(일반) ~ 3.0(상한)
        residual_info: {"nonseasonal": float, "spring": float}  세척효율 약화 잔류
        breakdown: [{"key","label","level","increment","res_nonseasonal","res_spring","source"}, ...]
    """
    keymap = [
        ("agricultural", "r1_agricultural", "r1_level", "농업(꽃가루)"),
        ("industrial", "r2_industrial", "r2_level", "산업/제철 인접"),
        ("traffic", "r3_traffic", "r3_level", "철도/도로 인접"),
        ("coastal", "r4_coastal", "r4_level", "해안(염분)"),
        ("tilt", "r5_tilt", "r5_level", "저틸트/하단집중"),
        ("organic", "r6_organic", "r6_organic_level", "생물오염(새·꽃가루·조류)"),
        ("bird", "bird_adjacent", "bird_level", "철새도래지 인접(천수만)"),
    ]
    rc = regional_characteristics or {}
    total = 1.0
    res_nonseasonal = 0.0
    res_spring = 0.0
    breakdown: list[dict] = []
    for key, flag_field, level_field, label in keymap:
        if not rc.get(flag_field):
            continue
        level = rc.get(level_field, "mid")
        inc = FSITE_INCREMENTS[key].get(level, FSITE_INCREMENTS[key]["mid"])
        total += inc
        resd = FACTOR_RESIDUAL.get(key, {"nonseasonal": 0.0, "spring": 0.0, "source": ""})
        res_nonseasonal += resd["nonseasonal"]
        res_spring += resd["spring"]
        breakdown.append({
            "key": key, "label": label, "level": level, "increment": inc,
            "res_nonseasonal": resd["nonseasonal"], "res_spring": resd["spring"],
            "source": resd["source"],
        })

    f_site = min(round(total, 3), FSITE_CAP)
    residual_info = {
        "nonseasonal": round(res_nonseasonal, 3),
        "spring": round(res_spring, 3),
    }
    return f_site, residual_info, breakdown


@dataclass
class SemiPhysicalResult:
    """단일 시나리오(완화 또는 보수) 결과."""

    daily: list[dict]              # [{date, loss_pct, rainfall_mm, mass_g, r_e_mm, eta_eff}, ...]
    scenario: str                  # "relaxed" | "conservative"
    annual_loss_pct: float         # 연평균 손실률
    peak_loss_pct: float           # 최대 손실률
    p95_loss_pct: float            # 95 백분위수
    spring_loss_pct: float | None  # 봄철(3~5월) 평균
    spring_peak_loss_pct: float | None  # 봄철(3~5월) 피크
    days_exceed_2pct: int          # 손실률 > 2%인 날 수
    max_mass_g: float              # 최대 누적 먼지량 (g/m2)
    rain_event_count: int          # 강우사건 수
    effective_wash_count: int      # 유효세척(η_eff>=0.30) 횟수
    max_no_wash_days: int          # 최대 무세척 연속일수
    max_no_wash_month: int | None  # 그 시기(월)
    f_site: float                  # 적용 F_site
    residual_info: dict            # 적용 잔류
    region_type: str
    assumptions: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_rain_events(
    rainfall_input, start: date, end: date, gap_hours: int
) -> tuple[dict, dict, int]:
    """
    강수 입력 -> (event_re_by_end_date, daily_rain, event_count).

    시간 Series면 무강우 gap_hours 이상으로 사건을 분리하고 R_e(사건 강수합)를 산정.
    일별 dict면 강수가 있는 각 날을 하나의 사건으로 취급(R_e=당일 강수).

    event_re_by_end_date: {date: R_e}  같은 날 여러 사건이 끝나면 가장 강한 R_e 채택.
    """
    if isinstance(rainfall_input, pd.Series) and len(rainfall_input) > 0:
        s = rainfall_input.astype(float).fillna(0.0).sort_index()
        events: list[tuple] = []  # (end_ts, R_e)
        cur_sum = 0.0
        cur_end = None
        gap_sec = gap_hours * 3600
        for ts, v in s.items():
            if v > 0:
                cur_sum += float(v)
                cur_end = ts
            else:
                if cur_sum > 0 and cur_end is not None:
                    if (ts - cur_end).total_seconds() >= gap_sec:
                        events.append((cur_end, cur_sum))
                        cur_sum = 0.0
                        cur_end = None
        if cur_sum > 0 and cur_end is not None:
            events.append((cur_end, cur_sum))

        event_re: dict = {}
        for end_ts, re in events:
            d = end_ts.date()
            # 같은 날 복수 사건 종료 시 가장 강한 사건이 세척 지배
            event_re[d] = max(event_re.get(d, 0.0), re)

        daily = s.resample("D").sum()
        daily_rain = {ts.date(): float(v) for ts, v in daily.items()}
        return event_re, daily_rain, len(events)

    # 일별 dict fallback (시간 정보 없음)
    rain_daily = {d: float(v or 0.0) for d, v in (rainfall_input or {}).items()}
    event_re = {d: mm for d, mm in rain_daily.items() if mm > 0}
    return event_re, rain_daily, len(event_re)


def _eta_tier(r_e: float, scenario: str) -> float:
    """R_e(mm)와 시나리오 -> 단계 세척효율 η_tier."""
    T1, T2 = CONFIG["T1_mm"], CONFIG["T2_mm"]
    if r_e >= T2:
        return CONFIG["eta_strong"]
    if r_e >= T1:
        # 완화: 10~20mm를 부분세척으로 인정 / 보수: 약한 세척으로 취급
        return CONFIG["eta_partial"] if scenario == "relaxed" else CONFIG["eta_weak"]
    return CONFIG["eta_weak"]


def run_semiphysical_model(
    rainfall_input,
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
    region_type: str = "general",
    f_site_override: float | None = None,
    residual_info: dict | None = None,
    scenario: str = "conservative",
) -> SemiPhysicalResult:
    """
    강우사건 기반 반물리 소일링 모델 (단일 시나리오).

    Args:
        rainfall_input: pd.Series(시간, DatetimeIndex) 권장 | {date: mm}
        pm_by_date: {date: {"pm10","pm25"}}
        scenario: "relaxed"(완화) | "conservative"(보수)
        residual_info: {"nonseasonal","spring"} 세척효율 약화 잔류 (없으면 base만)
    """
    if f_site_override is not None:
        f_site = float(f_site_override)
    else:
        f_site = REGION_FSITE.get(region_type, 1.0)
    f_site_eff = CONFIG["depo_cal"] * f_site
    resid = residual_info or {"nonseasonal": 0.0, "spring": 0.0}

    event_re, daily_rain, event_count = _detect_rain_events(
        rainfall_input, start, end, CONFIG["event_gap_hours"]
    )

    cos_t = float(np.cos(np.radians(CONFIG["tilt_deg"])))
    v_f, v_c = CONFIG["v_f"], CONFIG["v_c"]
    kappa, gamma = CONFIG["kappa"], CONFIG["gamma"]
    low_rain_mm, low_w = CONFIG["low_rain_mm"], CONFIG["low_rain_soil_weight"]
    res_base, res_cap = CONFIG["residual_base"], CONFIG["residual_cap"]
    eff_eta = CONFIG["effective_eta"]

    date_range = pd.date_range(start=start, end=end, freq="D")
    M_prev = 0.0
    days_since_wash = 0
    max_no_wash = 0
    max_no_wash_month: int | None = None
    eff_wash_count = 0

    daily_data: list[dict] = []
    for ts in date_range:
        d = ts.date()
        pm = pm_by_date.get(d, {})
        pm25 = float(pm.get("pm25", 18.0))
        pm10 = float(pm.get("pm10", 35.0))
        coarse = max(pm10 - pm25, 0.0)

        dep = 0.0864 * cos_t * (v_f * pm25 + v_c * coarse) * f_site_eff
        dr = daily_rain.get(d, 0.0)
        if dr < low_rain_mm:
            dep *= (1.0 + low_w)  # 저강수일 소일링 가중

        M_before = M_prev + dep
        SL = 1.0 - np.exp(-kappa * (M_before ** gamma))

        r_e = event_re.get(d, 0.0)
        eta_eff = 0.0
        washed_effective = False
        if r_e > 0:
            eta_tier = _eta_tier(r_e, scenario)
            residual = min(
                res_cap,
                res_base + resid["nonseasonal"]
                + (resid["spring"] if ts.month in (3, 4, 5) else 0.0),
            )
            eta_eff = eta_tier * (1.0 - residual)
            M_after = M_before * (1.0 - eta_eff)
            if eta_eff >= eff_eta:
                eff_wash_count += 1
                washed_effective = True
        else:
            M_after = M_before

        if washed_effective:
            days_since_wash = 0
        else:
            days_since_wash += 1
            if days_since_wash > max_no_wash:
                max_no_wash = days_since_wash
                max_no_wash_month = ts.month

        daily_data.append({
            "date": d,
            "loss_pct": round(float(SL * 100.0), 3),
            "rainfall_mm": round(float(dr), 1),
            "mass_g": round(float(M_after), 4),
            "r_e_mm": round(float(r_e), 1),
            "eta_eff": round(float(eta_eff), 3),
        })
        M_prev = M_after

    loss = pd.Series([r["loss_pct"] for r in daily_data],
                     index=pd.DatetimeIndex([r["date"] for r in daily_data]))
    annual_loss = float(loss.mean()) if len(loss) else 0.0
    peak_loss = float(loss.max()) if len(loss) else 0.0
    p95_loss = float(loss.quantile(0.95)) if len(loss) else 0.0
    spring_mask = loss.index.month.isin([3, 4, 5])
    spring_loss = float(loss[spring_mask].mean()) if spring_mask.any() else None
    spring_peak = float(loss[spring_mask].max()) if spring_mask.any() else None
    days_exceed_2 = int((loss > 2.0).sum())
    max_mass = max((r["mass_g"] for r in daily_data), default=0.0)

    scenario_kr = "완화(10~20mm 부분세척 인정)" if scenario == "relaxed" else "보수(≥20mm만 유효세척)"
    assumptions = [
        "강우사건(R_e) 기반 반물리 소일링·세척 모델 (IEA PVPS / Coello-Boyle 계열)",
        f"시나리오: {scenario_kr}",
        f"경사각 {CONFIG['tilt_deg']}도, 퇴적속도 v_f={v_f} v_c={v_c} m/s, "
        f"DEPO_CAL={CONFIG['depo_cal']}(미보정)",
        f"강우사건 분리 gap={CONFIG['event_gap_hours']}h, 세척경계 T1={CONFIG['T1_mm']} "
        f"T2={CONFIG['T2_mm']}mm, η=({CONFIG['eta_weak']}/{CONFIG['eta_partial']}/{CONFIG['eta_strong']})",
        f"잔류 residual=min({res_cap}, {res_base}+비계절{resid['nonseasonal']}"
        f"+봄철{resid['spring']}), 완전초기화 금지",
        f"저강수일(일<{low_rain_mm}mm) 소일링 +{int(low_w*100)}% 가중",
        f"손실변환 kappa={kappa}(10g/m²->34%), gamma={gamma}; F_site={f_site}",
        "단일연도 시나리오값 — 장기평균은 10년(최소)~30년(기후평년) 반복계산 필요",
        "[근거] IEA PVPS T13-21:2022 / Coello & Boyle (2019) IEEE J. Photovoltaics 9(5):1382-1387",
    ]

    return SemiPhysicalResult(
        daily=daily_data,
        scenario=scenario,
        annual_loss_pct=round(annual_loss, 3),
        peak_loss_pct=round(peak_loss, 3),
        p95_loss_pct=round(p95_loss, 3),
        spring_loss_pct=round(spring_loss, 3) if spring_loss is not None else None,
        spring_peak_loss_pct=round(spring_peak, 3) if spring_peak is not None else None,
        days_exceed_2pct=days_exceed_2,
        max_mass_g=round(float(max_mass), 4),
        rain_event_count=event_count,
        effective_wash_count=eff_wash_count,
        max_no_wash_days=max_no_wash,
        max_no_wash_month=max_no_wash_month,
        f_site=f_site,
        residual_info=resid,
        region_type=region_type,
        assumptions=assumptions,
    )


@dataclass
class SoilingScenarioRange:
    """완화~보수 두 시나리오 range + 기상분석 통계."""

    relaxed: SemiPhysicalResult
    conservative: SemiPhysicalResult
    low_pct: float           # 완화 연평균 (세척 많음 -> 낮음)
    high_pct: float          # 보수 연평균 (세척 적음 -> 높음)
    spring_peak_pct: float   # 보수 봄철 피크
    f_site: float
    residual_info: dict

    def to_dict(self) -> dict:
        return {
            "relaxed": self.relaxed.to_dict(),
            "conservative": self.conservative.to_dict(),
            "low_pct": self.low_pct,
            "high_pct": self.high_pct,
            "spring_peak_pct": self.spring_peak_pct,
            "f_site": self.f_site,
            "residual_info": self.residual_info,
        }


def run_soiling_scenarios(
    rainfall_input,
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
    f_site: float = 1.0,
    residual_info: dict | None = None,
    region_type: str = "general",
) -> SoilingScenarioRange:
    """완화·보수 두 시나리오를 각각 돌려 range로 반환."""
    relaxed = run_semiphysical_model(
        rainfall_input, pm_by_date, start, end,
        region_type=region_type, f_site_override=f_site,
        residual_info=residual_info, scenario="relaxed",
    )
    conservative = run_semiphysical_model(
        rainfall_input, pm_by_date, start, end,
        region_type=region_type, f_site_override=f_site,
        residual_info=residual_info, scenario="conservative",
    )
    return SoilingScenarioRange(
        relaxed=relaxed,
        conservative=conservative,
        low_pct=relaxed.annual_loss_pct,
        high_pct=conservative.annual_loss_pct,
        spring_peak_pct=conservative.spring_peak_loss_pct or conservative.peak_loss_pct,
        f_site=f_site,
        residual_info=residual_info or {"nonseasonal": 0.0, "spring": 0.0},
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
    print("강우사건 기반 세척 모델 자체 테스트 (서산 2025)")
    print("=" * 72 + "\n")

    site = Site(name="충남 서산시", sido="충남", lat=36.7897, lon=126.4497)
    req = AgentRequest(region_name="충남 서산시", region1="충남", region2="서산시",
                       lat=site.lat, lon=site.lon)
    start, end = date(2025, 1, 1), date(2025, 12, 31)

    pm_by_date = load_daily_pm_statistics("충남", "서산시", start, end)
    rainfall_input, _ = _collect_rainfall(site, start, end, req)

    rng = run_soiling_scenarios(rainfall_input, pm_by_date, start, end, f_site=1.0)
    print(f"연 {rng.low_pct:.2f}~{rng.high_pct:.2f}% (완화~보수), 봄철 피크 {rng.spring_peak_pct:.2f}%")
    for r in (rng.relaxed, rng.conservative):
        print(f"  [{r.scenario:12s}] 연 {r.annual_loss_pct:.2f}% 피크 {r.peak_loss_pct:.2f}% "
              f"봄철 {r.spring_loss_pct} 강우사건 {r.rain_event_count} "
              f"유효세척 {r.effective_wash_count} 최대무세척 {r.max_no_wash_days}일")
    print("\n" + "=" * 72 + "\n")
