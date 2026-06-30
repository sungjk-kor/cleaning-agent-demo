# -*- coding: utf-8 -*-
"""
HSU 모델 입력 진단: 시간 vs 일 강수 입력 비교.
"""

from datetime import date
import pandas as pd
from core.agent import Site, AgentRequest, _collect_rainfall
from core.airkorea_pm import daily_pm_average, demo_pm_observations
from core.soiling_hsu import run_hsu_model

print("\n" + "="*70)
print("HSU 모델 입력 진단")
print("="*70 + "\n")

site = Site(
    name="충남 서산시",
    sido="충남",
    lat=36.7897,
    lon=126.4497,
)

req = AgentRequest(
    use_asos_rainfall=True,
    use_live_data=False,
)

start = date(2023, 1, 1)
end = date(2023, 12, 31)

# Step 1: 강수 수집
print("[Step 1] 강수 수집")
rainfall_input, notes = _collect_rainfall(site, start, end, req)
print(f"강수 타입: {type(rainfall_input)}")
print(f"강수 길이: {len(rainfall_input)}")
print(f"강수 합계: {rainfall_input.sum():.1f}mm\n")

# Step 2: PM 데이터 생성
print("[Step 2] PM 데이터 수집")
pm_series = demo_pm_observations(start, end, site.name)
pm_by_date = daily_pm_average(pm_series)
print(f"PM 타입: {type(pm_by_date)}")
print(f"PM 길이: {len(pm_by_date)}")
print(f"PM 샘플:\n{list(pm_by_date.items())[:3]}\n")

# Step 3: HSU 모델 실행 (시간 단위 강수 입력)
print("[Step 3] HSU 모델 실행 (시간 단위 강수)")
try:
    hsu_result = run_hsu_model(rainfall_input, pm_by_date, start, end)
    print(f"✓ 성공")
    print(f"  연평균 손실: {hsu_result.annual_loss_pct:.3f}%")
    print(f"  최대 손실: {hsu_result.peak_loss_pct:.3f}%")
    print(f"  일별 손실 샘플: {hsu_result.daily[:5]}\n")
except Exception as e:
    print(f"❌ 실패: {e}\n")
    import traceback
    traceback.print_exc()

# Step 4: 비교용 - 일별 강수 입력
print("[Step 4] HSU 모델 실행 비교 (일별 강수) - 기존 방식")
rainfall_daily = rainfall_input.resample("D").sum().to_dict()
rainfall_daily = {(d.date() if hasattr(d, 'date') else d): v for d, v in rainfall_daily.items()}
print(f"일별 강수 타입: {type(rainfall_daily)}")
print(f"일별 강수 길이: {len(rainfall_daily)}")

try:
    hsu_result_daily = run_hsu_model(rainfall_daily, pm_by_date, start, end)
    print(f"✓ 성공")
    print(f"  연평균 손실: {hsu_result_daily.annual_loss_pct:.3f}%")
    print(f"  최대 손실: {hsu_result_daily.peak_loss_pct:.3f}%\n")
except Exception as e:
    print(f"❌ 실패: {e}\n")

# 비교
print("[비교]")
print(f"시간 단위 입력:")
print(f"  - 연평균 손실: {hsu_result.annual_loss_pct:.3f}%")
print(f"  - 강수 정보: 시간별 rolling window 가능 ✓")

print(f"\n일별 입력:")
print(f"  - 연평균 손실: {hsu_result_daily.annual_loss_pct:.3f}%")
print(f"  - 강수 정보: 시간별 rolling window 불가능 ❌")

if abs(hsu_result.annual_loss_pct - hsu_result_daily.annual_loss_pct) < 0.1:
    print(f"\n⚠️  두 결과가 거의 같음 → HSU 모델이 시간 정보를 제대로 사용하지 않을 수 있음")
else:
    print(f"\n✓ 결과 차이 있음 → HSU 모델이 시간 정보를 사용 중")

print("\n" + "="*70)
print("✓ 진단 완료")
print("="*70 + "\n")
