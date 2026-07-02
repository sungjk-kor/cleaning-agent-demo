# -*- coding: utf-8 -*-
"""
test_soiling_events.py — 강우사건(R_e) 기반 세척 모델 유닛테스트.

pytest 없이도 실행 가능:  python test_soiling_events.py
pytest 설치 시:            pytest test_soiling_events.py

검증 대상:
  1) 사건 분리 + 단계세척:
     3일 연속강우 합 25mm → 12h 무강우 → 단발 8mm 시계열에서
     (a) 사건 2개 분리, (b) 첫 사건 R_e=25 → 강세척(η_strong),
     (c) 둘째 사건 R_e=8 → 약세척(완화·보수 모두).
  2) 연 단위 합성입력에서 완화<보수, 봄철>연평균, 보수 세척횟수<완화.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from core.soiling_semiphysical import (
    CONFIG,
    _detect_rain_events,
    _eta_tier,
    run_soiling_scenarios,
)


# ── 합성 시계열 생성기 ────────────────────────────────────────────────
def _make_two_event_series() -> pd.Series:
    """3일 연속강우(합 25mm) → 12h 무강우 → 단발 8mm."""
    # 사건 1: 72시간(3일) 연속 강우, 시간당 균등, 합 25mm (6h 이상 공백 없음)
    e1 = pd.date_range("2026-03-01 00:00", periods=72, freq="h")
    s1 = pd.Series(25.0 / 72, index=e1)
    # 12h 무강우 (>=6h → 사건 분리)
    gap = pd.date_range(e1[-1] + pd.Timedelta(hours=1), periods=12, freq="h")
    sgap = pd.Series(0.0, index=gap)
    # 사건 2: 단발 8mm (1시간)
    e2 = pd.date_range(gap[-1] + pd.Timedelta(hours=1), periods=1, freq="h")
    s2 = pd.Series(8.0, index=e2)
    # 꼬리 무강우 (사건 2 종료 확정용)
    tail = pd.date_range(e2[-1] + pd.Timedelta(hours=1), periods=12, freq="h")
    stail = pd.Series(0.0, index=tail)
    return pd.concat([s1, sgap, s2, stail])


def _make_synthetic_year() -> tuple[pd.Series, dict]:
    """
    합성 1년(2026) 시간강수 + 일별 PM.
      - 7일마다 강우사건: 봄철(3~5월)=15mm(중간대), 그 외=25mm(강).
      - 봄철 PM 2배(퇴적↑).
    의도: 완화는 15mm를 부분세척(0.55)으로 인정하지만 보수는 인정 안 함(0.05)
          → 봄철 보수 미세척으로 누적↑ → 완화<보수, 봄철>연평균, 보수 세척횟수<완화.
    """
    idx = pd.date_range("2026-01-01 00:00", "2026-12-31 23:00", freq="h")
    rain = pd.Series(0.0, index=idx)
    d = pd.Timestamp("2026-01-02 10:00")
    while d <= idx[-1]:
        rain.loc[d] = 15.0 if d.month in (3, 4, 5) else 25.0
        d += pd.Timedelta(days=7)

    pm: dict = {}
    dd = date(2026, 1, 1)
    while dd <= date(2026, 12, 31):
        if dd.month in (3, 4, 5):
            pm[dd] = {"pm10": 80.0, "pm25": 40.0}
        else:
            pm[dd] = {"pm10": 40.0, "pm25": 20.0}
        dd += timedelta(days=1)
    return rain, pm


# ── 테스트 1: 사건 분리 + 단계세척 ────────────────────────────────────
def test_rain_event_splitting():
    series = _make_two_event_series()
    event_re, daily_rain, count = _detect_rain_events(
        series, series.index[0].date(), series.index[-1].date(),
        CONFIG["event_gap_hours"],
    )

    # (a) 사건 2개로 분리
    assert count == 2, f"강우사건이 2개로 분리되어야 함 (got {count})"

    re_values = sorted(round(v, 1) for v in event_re.values())
    assert re_values == [8.0, 25.0], f"R_e는 {{8, 25}}여야 함 (got {re_values})"

    # (b) 첫 사건 R_e=25 → 강세척 (완화·보수 동일)
    assert _eta_tier(25.0, "relaxed") == CONFIG["eta_strong"]
    assert _eta_tier(25.0, "conservative") == CONFIG["eta_strong"]

    # (c) 둘째 사건 R_e=8 → 약세척 (완화·보수 모두, R_e<T1=10)
    assert _eta_tier(8.0, "relaxed") == CONFIG["eta_weak"]
    assert _eta_tier(8.0, "conservative") == CONFIG["eta_weak"]

    # 경계 확인: 10~20mm는 완화=부분세척, 보수=약세척
    assert _eta_tier(15.0, "relaxed") == CONFIG["eta_partial"]
    assert _eta_tier(15.0, "conservative") == CONFIG["eta_weak"]


# ── 테스트 2: 연 단위 시나리오 대소관계 ───────────────────────────────
def test_annual_scenarios():
    rain, pm = _make_synthetic_year()
    rng = run_soiling_scenarios(rain, pm, date(2026, 1, 1), date(2026, 12, 31), f_site=1.0)
    rel, con = rng.relaxed, rng.conservative

    # 완화 < 보수 (완화가 10~20mm를 세척으로 인정 → 손실 낮음)
    assert rel.annual_loss_pct < con.annual_loss_pct, (
        f"완화({rel.annual_loss_pct}) < 보수({con.annual_loss_pct}) 여야 함"
    )
    # 봄철 > 연평균 (보수: 봄철 미세척 + PM 상승)
    assert con.spring_loss_pct is not None
    assert con.spring_loss_pct > con.annual_loss_pct, (
        f"봄철({con.spring_loss_pct}) > 연평균({con.annual_loss_pct}) 여야 함"
    )
    # 보수 유효세척 횟수 < 완화 (보수는 봄철 15mm를 세척으로 인정 안 함)
    assert con.effective_wash_count < rel.effective_wash_count, (
        f"보수 세척({con.effective_wash_count}) < 완화 세척({rel.effective_wash_count}) 여야 함"
    )


def _run_all() -> int:
    tests = [test_rain_event_splitting, test_annual_scenarios]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {exc!r}")
    return failed


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("강우사건 기반 세척 모델 유닛테스트")
    print("=" * 60)

    # 진단 출력 (실패 시 원인 파악용)
    _series = _make_two_event_series()
    _er, _dr, _cnt = _detect_rain_events(
        _series, _series.index[0].date(), _series.index[-1].date(),
        CONFIG["event_gap_hours"],
    )
    print(f"[진단] 사건수={_cnt}, R_e={sorted(round(v,1) for v in _er.values())}")

    _rain, _pm = _make_synthetic_year()
    _rng = run_soiling_scenarios(_rain, _pm, date(2026, 1, 1), date(2026, 12, 31), f_site=1.0)
    print(f"[진단] 완화 연 {_rng.relaxed.annual_loss_pct:.3f}% / 보수 연 {_rng.conservative.annual_loss_pct:.3f}%")
    print(f"[진단] 보수 봄철평균 {_rng.conservative.spring_loss_pct}% / 봄철피크 {_rng.conservative.spring_peak_loss_pct}%")
    print(f"[진단] 유효세척 완화 {_rng.relaxed.effective_wash_count}회 / 보수 {_rng.conservative.effective_wash_count}회")
    print("-" * 60)

    n_failed = _run_all()
    print("=" * 60)
    print("결과:", "모두 통과 ✅" if n_failed == 0 else f"{n_failed}건 실패 ❌")
    print("=" * 60 + "\n")
    sys.exit(1 if n_failed else 0)
