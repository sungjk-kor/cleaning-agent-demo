"""
lcoe.py — 태양광 LCOE / 모듈 오염 경제성 시뮬레이션 (순수 계산 모듈)

출처: lcoe.ts (vigilai_portal_replit 의 LcoeSimulator.tsx 추출본)를 Python으로 포팅.
      수식·기본값을 그대로 보존했으므로 결과 수치는 lcoe.ts 와 동일합니다.
      (기준 117.05 / 반영 127.85 원/kWh, 발전량 감소 8.45%, LCOE 증가 9.23%)

기본값 근거: KEEI 2024-22, 1MW 지상형 기준.
의존성 없음(표준 라이브러리만). `from lcoe import calculate_lcoe` 로 사용.
검증 실행: python lcoe.py
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field, asdict


# ────────────────────────────────────────────────────────────
# 입력 (단위 주석 필수 확인)
# ────────────────────────────────────────────────────────────
@dataclass
class LcoeInputs:
    capacity: float = 1000          # 설비용량 (kW)
    util_rate: float = 15.4         # 이용률 (%)
    construct_cost: float = 1_281_000   # 건설비 (원/kW)
    discount_rate: float = 4.5      # 할인율 (%)
    lifespan: int = 20              # 경제적 수명 (년)
    annual_om: float = 21_198_000   # 연간 운영·유지비 (원/년, 1년차 기준, 이후 인플레 적용)
    annual_land: float = 25_000_000 # 연간 임대료 (원/년, 1년차 기준, 이후 인플레 적용)
    inflation: float = 1.85         # 인플레이션 (%/년)
    degradation: float = 0.45       # 성능저하율 (%/년, 매년 누적 적용)
    pollution_loss: float = 5.0     # 오염 손실율 (%, 단순 차감·비누적)
                                    #   ↑ 청소 판단 에이전트가 강수 기반으로 추정해 넣는 값
    failure_rate_10: float = 3.0    # 10년차 고장모듈 발생률 (%)
    failure_rate_20: float = 20.0   # 20년차 고장모듈 발생률 (%)


# KEEI 2024-22, 1MW 지상형 기준 기본값
DEFAULT_INPUTS = LcoeInputs()


# ────────────────────────────────────────────────────────────
# 출력
# ────────────────────────────────────────────────────────────
@dataclass
class YearRow:
    y: int                 # 연도
    base_annual_gen: float # 기준 연간 발전량 (kWh)
    degraded_gen: float    # 성능저하 반영 발전량 (kWh)
    soiled_gen: float      # 오염 반영 발전량 (kWh)
    fail_rate: float       # 해당 연도 고장률 (%)
    ref_final_gen: float   # 최종 발전량 (오염+고장 반영, kWh)
    nominal_om: float      # 명목 운영비 (원)
    nominal_land: float    # 명목 임대료 (원)
    total_nominal: float   # 명목 비용 합계 (원)
    discount_factor: float # 할인계수
    discounted_cost: float # 할인 비용 (원)
    ref_disc_gen: float    # 할인 발전량 (kWh)


@dataclass
class LcoeResult:
    year_data: list = field(default_factory=list)
    base_lcoe: float = 0.0             # 기준 LCOE (오염 0%·고장 0%, 원/kWh)
    ref_lcoe: float = 0.0              # 반영 후 LCOE (오염+고장, 원/kWh)
    gen_decrease: float = 0.0          # 발전량 감소 (%)
    lcoe_increase: float = 0.0         # LCOE 증가 (%)
    total_nominal_cost: float = 0.0    # 누적 명목비용 (원)
    total_discounted_cost: float = 0.0 # 누적 할인비용 (원)
    total_discounted_gen: float = 0.0  # 누적 할인발전 (kWh)
    initial_construction_cost: float = 0.0  # 초기 건설비 (원)
    base_total_gen: float = 0.0        # 기준 누적 할인발전 (kWh)


# ────────────────────────────────────────────────────────────
# 보조 함수: 연도별 고장모듈 발생률
#   9년차까지 0, 10~20년차 사이 지수 보간, 20년차 이상 failure_rate_20
# ────────────────────────────────────────────────────────────
def get_failure_rate(year: int, failure_rate_10: float, failure_rate_20: float) -> float:
    if year <= 9:
        return 0.0
    if year >= 20:
        return failure_rate_20
    t = (year - 10) / 10
    return failure_rate_10 * math.exp(math.log(failure_rate_20 / failure_rate_10) * t)


# ────────────────────────────────────────────────────────────
# 핵심 계산
# ────────────────────────────────────────────────────────────
def calculate_lcoe(inp: LcoeInputs = DEFAULT_INPUTS) -> LcoeResult:
    initial_construction_cost = inp.capacity * inp.construct_cost

    year_data: list[YearRow] = []
    total_nominal_cost = 0.0
    total_discounted_cost = 0.0
    total_discounted_gen = 0.0   # 비교 시나리오(오염+고장) 누적 할인발전
    base_total_gen = 0.0         # 기준 시나리오(오염0·고장0) 누적 할인발전

    for y in range(1, inp.lifespan + 1):
        discount_factor = 1 / math.pow(1 + inp.discount_rate / 100, y)

        # 기준 연간 발전량
        base_annual_gen = inp.capacity * 8760 * (inp.util_rate / 100)
        # 성능저하 반영 (누적)
        degraded_gen = base_annual_gen * math.pow(1 - inp.degradation / 100, y - 1)

        # 기준 시나리오: 오염 0%·고장 0% → 성능저하만
        baseline_disc_gen = degraded_gen * discount_factor
        base_total_gen += baseline_disc_gen

        # 비교 시나리오: 오염(단순 차감) → 고장 반영
        soiled_gen = degraded_gen * (1 - inp.pollution_loss / 100)
        fail_rate = get_failure_rate(y, inp.failure_rate_10, inp.failure_rate_20)
        ref_final_gen = soiled_gen * (1 - fail_rate / 100)
        ref_disc_gen = ref_final_gen * discount_factor

        # 비용 (인플레 적용 후 할인)
        nominal_om = inp.annual_om * math.pow(1 + inp.inflation / 100, y - 1)
        nominal_land = inp.annual_land * math.pow(1 + inp.inflation / 100, y - 1)
        total_nominal = nominal_om + nominal_land
        discounted_cost = total_nominal * discount_factor

        year_data.append(YearRow(
            y=y, base_annual_gen=base_annual_gen, degraded_gen=degraded_gen,
            soiled_gen=soiled_gen, fail_rate=fail_rate, ref_final_gen=ref_final_gen,
            nominal_om=nominal_om, nominal_land=nominal_land, total_nominal=total_nominal,
            discount_factor=discount_factor, discounted_cost=discounted_cost,
            ref_disc_gen=ref_disc_gen,
        ))

        total_nominal_cost += total_nominal
        total_discounted_cost += discounted_cost
        total_discounted_gen += ref_disc_gen

    # 비용은 두 시나리오 동일(발전량만 다름). 분모만 달라짐.
    numerator = initial_construction_cost + total_discounted_cost
    base_lcoe = numerator / base_total_gen if base_total_gen > 0 else 0.0
    ref_lcoe = numerator / total_discounted_gen if total_discounted_gen > 0 else 0.0

    gen_decrease = ((base_total_gen - total_discounted_gen) / base_total_gen * 100
                    if base_total_gen > 0 else 0.0)
    lcoe_increase = ((ref_lcoe - base_lcoe) / base_lcoe * 100
                     if base_lcoe > 0 else 0.0)

    return LcoeResult(
        year_data=year_data, base_lcoe=base_lcoe, ref_lcoe=ref_lcoe,
        gen_decrease=gen_decrease, lcoe_increase=lcoe_increase,
        total_nominal_cost=total_nominal_cost, total_discounted_cost=total_discounted_cost,
        total_discounted_gen=total_discounted_gen,
        initial_construction_cost=initial_construction_cost, base_total_gen=base_total_gen,
    )


# ────────────────────────────────────────────────────────────
# 검증용 예시 (직접 실행 시 콘솔 출력)
# ────────────────────────────────────────────────────────────
def run_example() -> None:
    r = calculate_lcoe(DEFAULT_INPUTS)
    print('=== LCOE 시뮬레이션 (KEEI 2024-22, 1MW 지상형 기본값) ===')
    print('기준 LCOE      :', f'{r.base_lcoe:.2f}', '원/kWh')
    print('반영 후 LCOE   :', f'{r.ref_lcoe:.2f}', '원/kWh')
    print('발전량 감소    :', f'{r.gen_decrease:.2f}', '%')
    print('LCOE 증가      :', f'{r.lcoe_increase:.2f}', '%')
    print('누적 할인비용  :', f'{round(r.total_discounted_cost):,}', '원')
    print('누적 할인발전  :', f'{round(r.total_discounted_gen):,}', 'kWh')


if __name__ == '__main__':
    run_example()
