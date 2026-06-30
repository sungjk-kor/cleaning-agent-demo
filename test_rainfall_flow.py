# -*- coding: utf-8 -*-
"""
강수 데이터 흐름 진단: ASOS → agent → HSU 모델.
"""

from datetime import date
import pandas as pd
from core.asos_rainfall import load_asos_hourly_precip, load_asos_daily_precip, list_asos_files

print("\n" + "="*70)
print("강수 데이터 진단")
print("="*70 + "\n")

# Step 1: ASOS 파일 확인
print("[Step 1] ASOS 파일 확인")
files = list_asos_files()
print(f"✓ 발견된 파일: {len(files)}개")
for f in files:
    print(f"  - {f.name}")

if not files:
    print("❌ ASOS 파일 없음")
    exit(1)

# Step 2: 시간 단위 로드 테스트
print(f"\n[Step 2] 시간 단위 강수 로드 (load_asos_hourly_precip)")
target_file = files[0]
print(f"파일: {target_file.name}")

try:
    hourly_data = load_asos_hourly_precip(target_file)
    print(f"✓ {len(hourly_data)}개 지점 로드")

    # 첫 지점 확인
    first_code = list(hourly_data.keys())[0]
    first_series = hourly_data[first_code]
    print(f"\n지점 {first_code}:")
    print(f"  타입: {type(first_series)}")
    print(f"  길이: {len(first_series)} (시간 단위)")
    print(f"  인덱스 타입: {type(first_series.index)}")
    print(f"  인덱스: {first_series.index[:5].tolist()}")
    print(f"  값 샘플: {first_series.head().tolist()}")

    # 강수 발생 시간 확인
    rainy_hours = (first_series > 0.0).sum()
    print(f"  강우 시간: {rainy_hours}시간 (전체 {len(first_series)}시간)")
    print(f"  최대 시간 강수: {first_series.max():.1f}mm")

except Exception as e:
    print(f"❌ 시간 단위 로드 실패: {e}")
    import traceback
    traceback.print_exc()

# Step 3: 일 단위 로드 비교 (기존)
print(f"\n[Step 3] 일 단위 강수 로드 (load_asos_daily_precip) - 비교용")
try:
    daily_data = load_asos_daily_precip(target_file)
    print(f"✓ {len(daily_data)}개 지점 로드")

    first_daily = daily_data[first_code]
    print(f"\n지점 {first_code}:")
    print(f"  타입: {type(first_daily)}")
    print(f"  길이: {len(first_daily)} (일 단위)")

    sample_dates = sorted(first_daily.keys())[:5]
    print(f"  샘플 날짜들: {sample_dates}")
    print(f"  샘플 값: {[first_daily[d] for d in sample_dates]}")

except Exception as e:
    print(f"❌ 일 단위 로드 실패: {e}")

# Step 4: 시간 vs 일 비교
print(f"\n[Step 4] 시간 vs 일 단위 데이터 비교")
print(f"시간 단위 데이터:")
print(f"  - 타입: {type(hourly_data[first_code])}")
print(f"  - 길이: {len(hourly_data[first_code])}")
print(f"  - 연강수: {hourly_data[first_code].sum():.1f}mm")

print(f"\n일 단위 데이터:")
print(f"  - 타입: {type(daily_data[first_code])}")
print(f"  - 길이: {len(daily_data[first_code])}")
print(f"  - 연강수: {sum(daily_data[first_code].values()):.1f}mm")

# 일별로 재계산해서 비교
hourly_daily = hourly_data[first_code].resample("D").sum()
print(f"\n시간 데이터를 일별로 재집계: {hourly_daily.sum():.1f}mm")

print("\n" + "="*70)
print("✓ 진단 완료")
print("="*70 + "\n")
