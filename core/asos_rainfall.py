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


def load_asos_hourly_precip(
    csv_path: Path | str, year: int | None = None
) -> dict[str, pd.Series]:
    """
    ASOS 시간별 CSV → 지점별 시간 강수량 시계열 (DatetimeIndex).

    Args:
        csv_path: OBS_ASOS_TIM_YYYY_all.csv 경로
        year: 연도 (검증용, 생략 가능)

    Returns:
        {station_code: pd.Series(datetime_index, values=mm), ...}
        예) {"108": Series(8760 rows, hourly)}

    핵심:
      - 시간별 강수량 유지 (절대 일별 집계 금지)
      - 결측(NaN)은 0mm 처리
      - QC플래그 = 9 → 0mm (결측 표지)
      - DatetimeIndex로 시간 정렬 보장
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"ASOS CSV not found: {csv_path}")

    # cp949로 읽기
    df = pd.read_csv(csv_path, encoding="cp949", dtype={"지점": str}, low_memory=False)

    # 필수 칼럼 확인
    required = {"지점", "일시", "강수량(mm)"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns in {csv_path.name}. Need: {required}")

    # 일시 파싱 (YYYY-MM-DD HH:MM 형식)
    try:
        df["datetime"] = pd.to_datetime(df["일시"], format="%Y-%m-%d %H:%M")
    except Exception as e:
        raise ValueError(f"Failed to parse '일시' column in {csv_path.name}: {e}")

    # 강수량 처리: 결측과 무강수 구별
    # - NaN (실제 결측) → 0mm
    # - QC=9 (결측 표지) → 0mm
    # - 0.0 (측정된 무강수) → 0.0 유지
    df["강수량(mm)"] = df["강수량(mm)"].fillna(0.0)

    # QC플래그 확인: 결측(QC=9) 마킹
    if "강수량QC플래그" in df.columns:
        df["강수량QC플래그"] = df["강수량QC플래그"].fillna(0).astype(int)
        qc9_mask = df["강수량QC플래그"] == 9
        df.loc[qc9_mask, "강수량(mm)"] = 0.0

    # 지점별 시간 시계열
    result: dict[str, pd.Series] = {}

    for station_code, group in df.groupby("지점", sort=False):
        # datetime으로 정렬 후 Series 구성
        group = group.sort_values("datetime").reset_index(drop=True)
        series = pd.Series(
            group["강수량(mm)"].astype(float).values,
            index=pd.DatetimeIndex(group["datetime"]),
            name=f"rain_{station_code}",
        )
        result[station_code] = series

    return result


def load_asos_daily_precip(
    csv_path: Path | str, year: int | None = None
) -> dict[str, dict[date, float]]:
    """
    ASOS 시간별 CSV → 지점별 일일강수량 테이블 (후방성 호환성).

    주의: 이 함수는 시간 정보를 손실합니다.
    HSU 모델용으로는 load_asos_hourly_precip을 사용하세요.

    Returns:
        {station_code: {date: mm, ...}, ...}
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"ASOS CSV not found: {csv_path}")

    # cp949로 읽기
    df = pd.read_csv(csv_path, encoding="cp949", dtype={"지점": str}, low_memory=False)

    # 필수 칼럼 확인
    required = {"지점", "일시", "강수량(mm)"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns in {csv_path.name}. Need: {required}")

    # 일시 파싱
    try:
        df["datetime"] = pd.to_datetime(df["일시"], format="%Y-%m-%d %H:%M")
    except Exception as e:
        raise ValueError(f"Failed to parse '일시' column in {csv_path.name}: {e}")

    # 강수량 처리
    df["강수량(mm)"] = df["강수량(mm)"].fillna(0.0)

    # QC플래그 처리
    if "강수량QC플래그" in df.columns:
        df["강수량QC플래그"] = df["강수량QC플래그"].fillna(0).astype(int)
        qc9_mask = df["강수량QC플래그"] == 9
        df.loc[qc9_mask, "강수량(mm)"] = 0.0

    # 날짜 추출
    df["date"] = df["datetime"].dt.date

    # 지점별 일별 합산
    result: dict[str, dict[date, float]] = {}

    for station_code, group in df.groupby("지점", sort=False):
        daily_precip: dict[date, float] = {}

        for d, day_group in group.groupby("date"):
            mm_sum = day_group["강수량(mm)"].sum()
            daily_precip[d] = round(float(mm_sum), 1)

        result[station_code] = daily_precip

    return result


def load_asos_daily_insolation(
    csv_path: Path | str, year: int | None = None
) -> dict[str, dict[date, float]]:
    """
    ASOS 시간별 CSV → 지점별 일일 일사량(MJ/m²/day) 테이블.

    일사(MJ/m2) 칼럼(시간 단위)을 일별로 합산. Tier3(일사량 우수) 시나리오에서
    소일링 손실의 일사 가중에 사용.

    Returns:
        {station_code: {date: MJ/m²/day, ...}, ...}
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"ASOS CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, encoding="cp949", dtype={"지점": str}, low_memory=False)

    insol_col = "일사(MJ/m2)"
    required = {"지점", "일시", insol_col}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns in {csv_path.name}. Need: {required}")

    try:
        df["datetime"] = pd.to_datetime(df["일시"], format="%Y-%m-%d %H:%M")
    except Exception as e:
        raise ValueError(f"Failed to parse '일시' column in {csv_path.name}: {e}")

    # 일사량 결측(NaN)·QC=9 → 0 (일별 합산 기준)
    df[insol_col] = pd.to_numeric(df[insol_col], errors="coerce").fillna(0.0)
    if "일사 QC플래그" in df.columns:
        qc = pd.to_numeric(df["일사 QC플래그"], errors="coerce").fillna(0).astype(int)
        df.loc[qc == 9, insol_col] = 0.0

    df["date"] = df["datetime"].dt.date

    result: dict[str, dict[date, float]] = {}
    for station_code, group in df.groupby("지점", sort=False):
        daily_insol: dict[date, float] = {}
        for d, day_group in group.groupby("date"):
            daily_insol[d] = round(float(day_group[insol_col].sum()), 3)
        result[station_code] = daily_insol
    return result


def load_asos_insolation_range(
    start_year: int, end_year: int, data_dir: Path | str | None = None
) -> dict[str, dict[date, float]]:
    """
    여러 년도의 ASOS 일사량(일별 합산)을 로드 & 병합.

    Returns:
        {station_code: {date: MJ/m²/day, ...}, ...}
    """
    files = list_asos_files(data_dir)
    combined: dict[str, dict[date, float]] = {}

    for fpath in files:
        match = re.search(r"(\d{4})", fpath.name)
        if not match:
            continue
        year = int(match.group(1))
        if not (start_year <= year <= end_year):
            continue
        try:
            station_data = load_asos_daily_insolation(fpath, year)
            for station_code, daily in station_data.items():
                combined.setdefault(station_code, {}).update(daily)
        except Exception as e:
            print(f"⚠️  {fpath.name} 일사량 처리 실패: {e}")
            continue

    return combined


def get_region_daily_insolation(
    station_data: dict[str, dict[date, float]],
    lat: float,
    lon: float,
    exclude_codes: list[str] | None = None,
) -> dict[date, float]:
    """
    지역(위경도)에 할당된 ASOS 지점의 일별 일사량(MJ/m²/day).

    Returns:
        {date: MJ/m²/day, ...}
    """
    nearest_code, _dist = assign_nearest_station(lat, lon, exclude_codes)
    return station_data.get(nearest_code, {})


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
) -> dict[str, pd.Series]:
    """
    여러 년도의 ASOS 시간별 데이터를 로드 & 병합.

    Args:
        start_year, end_year: 포함 범위 (예: 2023, 2025)
        data_dir: ASOS 파일 디렉토리

    Returns:
        {station_code: pd.Series(hourly, datetime_index), ...} (모든 년도 병합)
    """
    files = list_asos_files(data_dir)
    combined: dict[str, list] = {}  # {station_code: [series, series, ...]}

    for fpath in files:
        # 파일명에서 연도 추출 (OBS_ASOS_TIM_YYYY_all.csv)
        match = re.search(r"(\d{4})", fpath.name)
        if not match:
            continue
        year = int(match.group(1))
        if not (start_year <= year <= end_year):
            continue

        try:
            station_data = load_asos_hourly_precip(fpath, year)
            for station_code, hourly_series in station_data.items():
                if station_code not in combined:
                    combined[station_code] = []
                combined[station_code].append(hourly_series)
        except Exception as e:
            print(f"⚠️  {fpath.name} 처리 실패: {e}")
            continue

    # 시계열 병합 (년도별 Series를 시간순 정렬 & concat)
    result: dict[str, pd.Series] = {}
    for station_code, series_list in combined.items():
        if not series_list:
            continue
        # 모든 년도 데이터를 시간순으로 정렬 후 concat
        merged = pd.concat(series_list, axis=0).sort_index()
        result[station_code] = merged

    return result


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


def get_region_hourly_precip(
    station_data: dict[str, pd.Series],
    lat: float,
    lon: float,
    exclude_codes: list[str] | None = None,
) -> pd.Series:
    """
    지역(위경도)에 할당된 ASOS 지점의 시간별 강수량.

    Args:
        station_data: load_asos_range() 결과 (시간별 Series)
        lat, lon: 지역 또는 발전소 좌표
        exclude_codes: 제외할 지점코드

    Returns:
        pd.Series(hourly, datetime_index)
    """
    nearest_code, dist = assign_nearest_station(lat, lon, exclude_codes)
    hourly_precip = station_data.get(nearest_code, pd.Series(dtype=float))

    return hourly_precip


def get_region_daily_precip(
    station_data: dict[str, dict[date, float]],
    lat: float,
    lon: float,
    exclude_codes: list[str] | None = None,
) -> dict[date, float]:
    """
    지역(위경도)에 할당된 ASOS 지점의 일별 강수량 (후방성 호환성).

    주의: 이 함수는 시간 정보를 사용하지 않습니다.
    HSU 모델용으로는 get_region_hourly_precip을 사용하세요.

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
