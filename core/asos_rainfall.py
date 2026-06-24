# -*- coding: utf-8 -*-
"""
asos_rainfall.py — ASOS 지상관측 강수량 데이터 통합 모듈.

목적:
  1. ASOS 시간별 CSV → 지점별 일일강수량 테이블 (Step 1)
  2. 지점 → 지역(시군구) 배정 (Step 2, 선택)
  3. 기존 agent.py 강수 로직과 통합

근거: 2023~ ASOS 관측 데이터 (cp949 인코딩, 97개 지점, 시간 단위)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

import pandas as pd

try:
    from .asos_station_meta import ASOS_STATIONS, get_distance_km
except ImportError:
    from asos_station_meta import ASOS_STATIONS, get_distance_km

ASOS_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw_asos"

# ASOS CSV 핵심 칼럼
ASOS_COLS_OF_INTEREST = ["지점", "지점명", "일시", "강수량(mm)", "강수량QC플래그"]


def load_asos_daily_precip(
    csv_path: Path | str, year: int | None = None
) -> dict[str, dict[date, float]]:
    """
    ASOS 시간별 CSV → 지점별 일일강수량 테이블.

    Args:
        csv_path: OBS_ASOS_TIM_YYYY_all.csv 경로
        year: 연도 (검증용, 생략 가능)

    Returns:
        {station_code: {date: mm, ...}, ...}
        예) {"108": {date(2023,1,1): 12.5, ...}, ...}

    핵심 규칙:
      - 비어있는 강수량 = 0mm (결측 아님, "비 오지 않은 시간")
      - QC플래그 = 9 → 결측 = 0mm 처리하되, qc9_hours 별도 카운트
      - 시간별 시계열 → 일별 합산 (YYYY-MM-DD)
      - 반환값 date 키는 모두 0시 자정 기준 (UTC 00:00)
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"ASOS CSV not found: {csv_path}")

    # cp949로 읽기 (utf-8 사용 시 한글 깨짐)
    df = pd.read_csv(csv_path, encoding="cp949", dtype={"지점": str})

    # 필수 칼럼 확인
    required = {"지점", "일시", "강수량(mm)"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns in {csv_path.name}. Need: {required}")

    # NaN 처리: 모든 비어있는 강수량 = 0mm
    df["강수량(mm)"] = df["강수량(mm)"].fillna(0.0)

    # QC플래그 처리
    if "강수량QC플래그" in df.columns:
        df["강수량QC플래그"] = df["강수량QC플래그"].fillna(0).astype(int)
        qc9_mask = df["강수량QC플래그"] == 9
        df.loc[qc9_mask, "강수량(mm)"] = 0.0  # 결측 → 0mm
    else:
        qc9_mask = pd.Series(False, index=df.index)

    # 일시 파싱 (YYYY-MM-DD HH:MM 형식)
    try:
        df["datetime"] = pd.to_datetime(df["일시"], format="%Y-%m-%d %H:%M")
    except Exception as e:
        raise ValueError(f"Failed to parse '일시' column in {csv_path.name}: {e}")

    # 날짜 추출 (YYYY-MM-DD)
    df["date"] = df["datetime"].dt.date

    # 지점별 일별 합산
    result: dict[str, dict[date, float]] = {}

    for station_code, group in df.groupby("지점", sort=False):
        daily_precip: dict[date, float] = {}
        qc9_count: dict[date, int] = {}

        for d, day_group in group.groupby("date"):
            mm_sum = day_group["강수량(mm)"].sum()
            qc9_count_day = (day_group.index.isin(df.index[qc9_mask])).sum()

            daily_precip[d] = round(float(mm_sum), 1)
            if qc9_count_day > 0:
                qc9_count[d] = qc9_count_day

        result[station_code] = daily_precip

    return result


def list_asos_files(data_dir: Path | str | None = None) -> list[Path]:
    """ASOS CSV 파일 목록 (연도순)."""
    root = Path(data_dir) if data_dir else ASOS_DATA_DIR
    if not root.exists():
        return []
    files = sorted(
        root.glob("OBS_ASOS_TIM_*.csv"),
        key=lambda p: p.name,
    )
    return files


def load_asos_range(
    start_year: int, end_year: int, data_dir: Path | str | None = None
) -> dict[str, dict[date, float]]:
    """
    여러 년도의 ASOS 데이터를 로드 & 병합.

    Args:
        start_year, end_year: 포함 범위 (예: 2023, 2025)
        data_dir: ASOS 파일 디렉토리

    Returns:
        {station_code: {date: mm, ...}, ...} (모든 년도 병합)
    """
    files = list_asos_files(data_dir)
    combined: dict[str, dict[date, float]] = {}

    for fpath in files:
        # 파일명에서 연도 추출 (OBS_ASOS_TIM_YYYY_all.csv)
        match = re.search(r"(\d{4})", fpath.name)
        if not match:
            continue
        year = int(match.group(1))
        if not (start_year <= year <= end_year):
            continue

        station_data = load_asos_daily_precip(fpath, year)
        for station_code, daily_dict in station_data.items():
            if station_code not in combined:
                combined[station_code] = {}
            combined[station_code].update(daily_dict)

    return combined


def assign_nearest_station(lat: float, lon: float, exclude_codes: list[str] | None = None) -> tuple[str, float]:
    """
    2단계: 주어진 위경도(지역/발전소)에 가장 근처 ASOS 지점 배정.

    Args:
        lat, lon: 지역 또는 발전소 좌표
        exclude_codes: 제외할 지점코드 리스트

    Returns:
        (nearest_station_code, distance_km)

    예) 서산(36.78, 126.45) → ("108", 0.2km)
    """
    exclude = set(exclude_codes or [])
    nearest_code = None
    min_distance = float("inf")

    for code, info in ASOS_STATIONS.items():
        if code in exclude:
            continue
        dist = get_distance_km(lat, lon, info["lat"], info["lon"])
        if dist < min_distance:
            min_distance = dist
            nearest_code = code

    if nearest_code is None:
        raise ValueError(f"No available ASOS station for ({lat}, {lon})")

    return nearest_code, min_distance


def get_region_daily_precip(
    station_data: dict[str, dict[date, float]],
    lat: float,
    lon: float,
    exclude_codes: list[str] | None = None,
) -> dict[date, float]:
    """
    지역(위경도)에 할당된 ASOS 지점의 강수량.

    Args:
        station_data: load_asos_range() 결과
        lat, lon: 지역 또는 발전소 좌표
        exclude_codes: 제외할 지점코드

    Returns:
        {date: mm, ...}
    """
    nearest_code, dist = assign_nearest_station(lat, lon, exclude_codes)
    daily_precip = station_data.get(nearest_code, {})

    return daily_precip


if __name__ == "__main__":
    # 자체 테스트
    import sys

    if len(sys.argv) > 1:
        test_year = int(sys.argv[1])
    else:
        test_year = 2023

    print(f"\n{'='*70}")
    print(f"ASOS 강수량 통합 테스트 (연도 {test_year})")
    print(f"{'='*70}\n")

    # 1단계
    print("[1단계] ASOS 강수량 추출")
    files = list_asos_files()
    if not files:
        print(f"❌ ASOS CSV files not found in {ASOS_DATA_DIR}")
        sys.exit(1)

    print(f"✓ 발견된 파일 {len(files)}개\n")

    # 해당 연도 파일 처리
    target_file = next((f for f in files if str(test_year) in f.name), None)
    if not target_file:
        print(f"❌ File for {test_year} not found")
        sys.exit(1)

    print(f"처리 중: {target_file.name}")
    station_data = load_asos_daily_precip(target_file, test_year)
    print(f"✓ {len(station_data)}개 지점 로드 완료\n")

    # 지점별 통계 샘플
    print("지점별 통계 (상위 5개):")
    print(f"{'지점':<6} {'지점명':<10} {'연강수(mm)':<12} {'강수일':<6}")
    print("-" * 40)
    for station_code, daily_dict in sorted(station_data.items())[:5]:
        info = ASOS_STATIONS.get(station_code, {})
        total_mm = sum(daily_dict.values())
        rainy_days = sum(1 for v in daily_dict.values() if v >= 1.0)
        print(
            f"{station_code:<6} {info.get('name', '?'):<10} {total_mm:>11.1f} {rainy_days:<6}"
        )

    # 2단계
    print(f"\n[2단계] 지점 → 지역 배정\n")

    # 예시: 서산 (충남 서산시) 좌표
    test_lat, test_lon = 36.7897, 126.4497
    nearest_code, dist_km = assign_nearest_station(test_lat, test_lon)
    station_info = ASOS_STATIONS.get(nearest_code, {})

    print(f"테스트 위치: {test_lat}, {test_lon}")
    print(f"✓ 배정 지점: {nearest_code} ({station_info.get('name', '?')}, "
          f"{station_info.get('sido', '?')} {station_info.get('sigungu', '?')}, "
          f"거리 {dist_km:.1f}km)\n")

    # 해당 지역의 강수량
    region_precip = get_region_daily_precip(station_data, test_lat, test_lon)
    if region_precip:
        sample_dates = sorted(region_precip.keys())[:5]
        print(f"지역 강수량 샘플 (최초 5일):")
        for d in sample_dates:
            print(f"  {d}: {region_precip[d]:.1f}mm")

    print(f"\n✓ 2단계 완료")
    print(f"{'='*70}\n")
