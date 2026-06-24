# -*- coding: utf-8 -*-
"""
soiling_hsu.py — pvlib HSU (Coello & Boyle 2019) 소일링 모델.

기존 PM 기반 휴리스틱 대신, IEEE 논문 기반 물리 모델 사용.
입력: ASOS 강수(mm) + 시군구 PM2.5/PM10(µg/m³)
출력: 시계열 소일링 손실률(%)

참고:
  - Coello, C., & Boyle, L. (2019). IEEE J. Photovoltaics 9(5):1382-1387
  - pvlib.soiling.hsu ≥0.10
  - 단위 주의: PM µg/m³ → g/m³ (×1e-6)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

import pandas as pd

try:
    from pvlib import soiling
except ImportError:
    raise ImportError("pvlib >= 0.10 required. Install: pip install pvlib")


# HSU 모델 설정 (IEEE 논문 + 한국 환경 기반)
HSU_CONFIG = {
    "surface_tilt_deg": 30,        # 한국 고정 설치 경사각 (30°)
    "cleaning_threshold_mm": 0.5,  # 강우로 인한 자연 세척 임계값 (Coello 0.5mm)
    "rain_accum_period": "1h",     # pvlib 기본값 (시간 단위 적분)
    # depo_veloc=None → pvlib 기본값 {'2_5': 0.0009, '10': 0.004} m/s
}


@dataclass
class HsuSoilingResult:
    """HSU 모델 결과."""

    daily: list[dict]  # [{date, loss_pct}, ...]
    annual_loss_pct: float  # 연평균 손실률
    peak_loss_pct: float  # 최대 손실률
    p95_loss_pct: float  # 95 백분위수
    spring_loss_pct: float | None  # 봄철(3~5월) 평균 손실률
    days_exceed_2pct: int  # 손실률 > 2%인 날 수
    assumptions: list[str]


def run_hsu_model(
    rainfall_by_date: Mapping[date, float],
    pm_by_date: Mapping[date, dict],
    start: date,
    end: date,
) -> HsuSoilingResult:
    """
    HSU 소일링 모델 실행.

    Args:
        rainfall_by_date: {date: mm, ...}
        pm_by_date: {date: {"pm10": µg/m³, "pm25": µg/m³}, ...}
        start, end: 분석 기간

    Returns:
        HsuSoilingResult with daily soiling, annual stats, assumptions.

    주의사항:
      - PM 단위: µg/m³ → g/m³ (×1e-6) 반드시 수행
      - rainfall은 DatetimeIndex Series 필요 (내부에서 rolling 사용)
      - pm10은 "전체값" (coarse = pm10-pm25는 pvlib이 처리)
      - 비어있는 강수: 0mm, 비어있는 PM: 날씨 기본값 사용 (아래)
    """
    # Step 1: 시계열 데이터 구성 (hourly)
    date_range = pd.date_range(start=start, end=end, freq="D")
    rainfall_series = []
    pm25_series = []
    pm10_series = []

    for d in date_range:
        # 강수: 없으면 0mm
        rain_mm = float(rainfall_by_date.get(d, 0.0) or 0.0)
        rainfall_series.append(rain_mm)

        # PM: 없으면 기본값 (기존 model과 동일)
        pm_dict = pm_by_date.get(d, {})
        pm25_ugm3 = float(pm_dict.get("pm25", 18.0))  # 기본값 18 µg/m³
        pm10_ugm3 = float(pm_dict.get("pm10", 35.0))  # 기본값 35 µg/m³

        pm25_series.append(pm25_ugm3)
        pm10_series.append(pm10_ugm3)

    # DataFrame 구성 (DatetimeIndex 필수)
    df = pd.DataFrame(
        {
            "rainfall_mm": rainfall_series,
            "pm25_ugm3": pm25_series,
            "pm10_ugm3": pm10_series,
        },
        index=date_range,
    )

    # Step 2: 단위 변환 (µg/m³ → g/m³)
    rain = df["rainfall_mm"].astype(float).fillna(0.0)
    pm25 = df["pm25_ugm3"].astype(float) * 1e-6  # µg/m³ → g/m³
    pm10 = df["pm10_ugm3"].astype(float) * 1e-6  # µg/m³ → g/m³ (전체값)

    # Step 3: HSU 모델 실행
    try:
        soiling_ratio = soiling.hsu(
            rain,
            HSU_CONFIG["cleaning_threshold_mm"],
            HSU_CONFIG["surface_tilt_deg"],
            pm25,
            pm10,
            depo_veloc=None,  # pvlib 기본값 사용
            rain_accum_period=pd.Timedelta(HSU_CONFIG["rain_accum_period"]),
        )
    except Exception as e:
        raise ValueError(f"HSU model failed: {e}")

    # Step 4: 손실률 계산 및 일별 집계
    loss_pct = (1 - soiling_ratio) * 100  # 소일링 손실 %
    daily_loss = loss_pct.resample("D").mean()  # 일별 평균

    # Step 5: 통계 계산
    annual_loss = daily_loss.mean()
    peak_loss = daily_loss.max()
    p95_loss = daily_loss.quantile(0.95)

    # 봄철(3~5월) 평균
    spring_mask = daily_loss.index.month.isin([3, 4, 5])
    spring_loss = daily_loss[spring_mask].mean() if spring_mask.any() else None

    # 손실률 > 2%인 날 수
    days_exceed_2 = int((daily_loss > 2).sum())

    # 일별 상세 데이터
    daily_data = []
    for d, loss in daily_loss.items():
        daily_data.append(
            {
                "date": d.date(),
                "loss_pct": round(float(loss), 3),
            }
        )

    return HsuSoilingResult(
        daily=daily_data,
        annual_loss_pct=round(float(annual_loss), 3),
        peak_loss_pct=round(float(peak_loss), 3),
        p95_loss_pct=round(float(p95_loss), 3),
        spring_loss_pct=round(float(spring_loss), 3) if spring_loss is not None else None,
        days_exceed_2pct=days_exceed_2,
        assumptions=[
            "HSU 모델 (Coello & Boyle 2019, IEEE J. Photovoltaics 9(5):1382-1387)",
            f"경사각: {HSU_CONFIG['surface_tilt_deg']}° (고정설치)",
            f"세척 임계값: {HSU_CONFIG['cleaning_threshold_mm']}mm (강우)",
            "침적 속도: pvlib 기본값 (PM2.5: 0.0009, PM10: 0.004 m/s)",
            "PM 단위: AirKorea µg/m³ → pvlib g/m³ (×1e-6) 변환",
            "강수: ASOS 일강수량(mm)",
        ],
    )


if __name__ == "__main__":
    # 자체 테스트
    print("\n" + "="*70)
    print("1단계 테스트: HSU 소일링 모델 (pvlib)")
    print("="*70 + "\n")

    # 테스트 데이터 (2023년 1월, 서산)
    test_rainfall = {
        date(2023, 1, 1): 0.0,
        date(2023, 1, 2): 5.2,
        date(2023, 1, 3): 0.0,
        date(2023, 1, 4): 0.0,
        date(2023, 1, 5): 12.5,
    }
    test_pm = {
        date(2023, 1, 1): {"pm10": 50, "pm25": 30},
        date(2023, 1, 2): {"pm10": 45, "pm25": 28},
        date(2023, 1, 3): {"pm10": 55, "pm25": 32},
        date(2023, 1, 4): {"pm10": 60, "pm25": 35},
        date(2023, 1, 5): {"pm10": 40, "pm25": 25},
    }

    result = run_hsu_model(
        test_rainfall,
        test_pm,
        date(2023, 1, 1),
        date(2023, 1, 5),
    )

    print(f"분석 기간: 2023-01-01 ~ 2023-01-05")
    print(f"\n✓ HSU 모델 실행 완료:")
    print(f"  연평균 손실률: {result.annual_loss_pct:.3f}%")
    print(f"  최대 손실률: {result.peak_loss_pct:.3f}%")
    print(f"  95 백분위: {result.p95_loss_pct:.3f}%")
    print(f"  손실률 > 2%인 날: {result.days_exceed_2pct}일\n")

    print("일별 손실률:")
    for item in result.daily:
        print(f"  {item['date']}: {item['loss_pct']:.3f}%")

    print(f"\n✓ 가정사항:")
    for assum in result.assumptions:
        print(f"  - {assum}")

    print("\n✓ 1단계 완료")
    print("="*70 + "\n")
