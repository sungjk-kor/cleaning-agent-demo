# -*- coding: utf-8 -*-
"""
agent.py의 _collect_rainfall이 시간 단위 데이터를 반환하는지 확인.
"""

from datetime import date
from core.agent import Site, AgentRequest, _collect_rainfall

print("\n" + "="*70)
print("agent._collect_rainfall 진단")
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

print(f"사이트: {site.name} ({site.lat}, {site.lon})")
print(f"기간: {start} ~ {end}\n")

rainfall_input, notes = _collect_rainfall(site, start, end, req)

print(f"반환 타입: {type(rainfall_input)}")
print(f"노트: {notes}\n")

if isinstance(rainfall_input, dict):
    print("❌ 일별 데이터 (dict)가 반환됨 - 시간 단위 아님")
    print(f"   길이: {len(rainfall_input)}")
    sample_dates = sorted(rainfall_input.keys())[:5]
    for d in sample_dates:
        print(f"   {d}: {rainfall_input[d]}mm")

else:
    import pandas as pd
    print("✓ 시간 단위 데이터 (Series) 반환됨")
    print(f"   길이: {len(rainfall_input)}")
    print(f"   인덱스 타입: {type(rainfall_input.index)}")
    print(f"   인덱스 샘플: {rainfall_input.index[:5].tolist()}")
    print(f"   값 샘플: {rainfall_input.head().tolist()}")
    print(f"   연강수: {rainfall_input.sum():.1f}mm")
    print(f"   강우 시간: {(rainfall_input > 0).sum()}시간")

print("\n" + "="*70)
print("✓ 진단 완료")
print("="*70 + "\n")
