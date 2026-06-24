# -*- coding: utf-8 -*-
"""
soiling_weights.py — 지역 특성 기반 소일링 손실 가중치 계산 모듈.

소일링 손실을 두 항으로 분리:
  1. PM 침적 기반 손실 (pollution_model.py)
  2. 지역 특성 가중치 (본 모듈)
  → 최종 손실 = PM 손실 + 지역 특성 가중치

근거: IEA Technology Collaboration Programme Task 13:21:2022
      "Performance of Soiled PV Modules: Photovoltaic Module Soiling"

TODO: 아래 가중값들은 초안입니다. 도메인 전문가 검토 후 확정 필요.
      현장 모니터링 데이터로 보정 가능합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 지역 특성 규칙 정의
# 각 규칙: id, 이름, 판정 유형, 3단계 가중값(%p), 출처
RULES = {
    "R1": {
        "name": "농업지역",
        "type": "user_input",
        "levels": {"low": 0.3, "mid": 0.55, "high": 0.8},
        "source": "IEA §2.1, 리뷰 §2",
    },
    "R2": {
        "name": "산업/건설 인접",
        "type": "user_input",
        "levels": {"low": 0.3, "mid": 0.5, "high": 0.7},
        "source": "IEA §2.1, §5.3",
    },
    "R3": {
        "name": "철도/주요도로 인접",
        "type": "user_input",
        "levels": {"low": 0.2, "mid": 0.35, "high": 0.5},
        "source": "IEA §5.3",
    },
    "R4": {
        "name": "해안 인접",
        "type": "user_input",
        "levels": {"low": 0.2, "mid": 0.35, "high": 0.5},
        "source": "IEA §2.1, 리뷰 §3.2",
    },
    "R5": {
        "name": "저틸트/하단 집중",
        "type": "user_input",
        "levels": {"low": 0.1, "mid": 0.25, "high": 0.4},
        "source": "IEA §2, §5.3",
    },
    "R6": {
        "name": "봄철 황사/고농도",
        "type": "data_derived",
        "levels": {"low": 0.1, "mid": 0.2, "high": 0.3},
        "source": "리뷰 §3.3, 에어코리아",
    },
    "R7": {
        "name": "강수 자연세척(감산)",
        "type": "data_derived",
        "levels": {"low": -0.2, "mid": -0.35, "high": -0.5},
        "source": "IEA §5.3, 리뷰 §3.2",
    },
}

ADDITIVE_CAP_PPT = 1.5  # 가산 항 상한 (%p) — IEA §5.2 부지내 변동 근거


@dataclass
class RegionalWeightBreakdown:
    """규칙별 적용 내역."""

    rule_id: str
    name: str
    level: Literal["low", "mid", "high"]
    value_ppt: float  # %p 단위
    source: str


@dataclass
class RegionalWeightResult:
    """지역 특성 가중치 계산 결과."""

    total_ppt: float  # 최종 지역특성 가중치 (%p)
    breakdown: list[RegionalWeightBreakdown]  # 규칙별 상세
    capped: bool  # 가산 상한 적용 여부
    note: str


def calc_regional_weight(applied: dict[str, Literal["low", "mid", "high"]]) -> RegionalWeightResult:
    """
    지역 특성 규칙을 적용해 가중치를 계산한다.

    Args:
        applied: {rule_id: level, ...}
                 예) {"R1": "high", "R4": "mid", "R6": "high", "R7": "mid"}

    Returns:
        RegionalWeightResult with:
          - total_ppt: 최종 가중치 (%p)
          - breakdown: 규칙별 상세 (id, name, level, value, source)
          - capped: 가산 상한 적용 여부
          - note: 출처 및 주의사항
    """
    breakdown: list[RegionalWeightBreakdown] = []
    additive_sum = 0.0  # 감산 제외 합
    subtractive_sum = 0.0  # 감산 항 (음수)

    for rule_id, level in applied.items():
        if rule_id not in RULES:
            continue

        rule = RULES[rule_id]
        value_ppt = rule["levels"][level]

        breakdown.append(
            RegionalWeightBreakdown(
                rule_id=rule_id,
                name=rule["name"],
                level=level,
                value_ppt=value_ppt,
                source=rule["source"],
            )
        )

        # 감산(음수) vs 가산(양수) 분리
        if value_ppt < 0:
            subtractive_sum += value_ppt
        else:
            additive_sum += value_ppt

    # 가산 항 상한 적용
    capped = additive_sum > ADDITIVE_CAP_PPT
    additive_sum = min(additive_sum, ADDITIVE_CAP_PPT)

    # 최종값: 가산(상한 후) + 감산
    total_ppt = additive_sum + subtractive_sum

    return RegionalWeightResult(
        total_ppt=round(total_ppt, 3),
        breakdown=breakdown,
        capped=capped,
        note="지역특성 가중치는 IEA 문헌 기반 추정값이며, 부지별·계절별 변동이 발생할 수 있습니다.",
    )


if __name__ == "__main__":
    # 자체 테스트: 서산 예시
    # R1=high(농업), R4=mid(해안), R6=high(봄철황사), R7=mid(강수세척)
    test_applied = {
        "R1": "high",
        "R4": "mid",
        "R6": "high",
        "R7": "mid",
    }

    result = calc_regional_weight(test_applied)

    print("=" * 60)
    print("서산시 지역특성 소일링 가중치 계산 예시")
    print("=" * 60)
    print(f"\n최종 지역특성 가중치: {result.total_ppt:+.3f} %p")
    print(f"가산 상한({ADDITIVE_CAP_PPT}%p) 적용: {result.capped}")
    print(f"\n규칙별 상세:")
    print(f"{'규칙':<6} {'이름':<20} {'강도':<8} {'가중값':<10} {'출처':<30}")
    print("-" * 75)
    for item in result.breakdown:
        print(
            f"{item.rule_id:<6} {item.name:<20} {item.level:<8} "
            f"{item.value_ppt:+.2f}%p {item.source:<30}"
        )
    print("-" * 75)
    print(f"합계: {result.total_ppt:+.3f} %p")
    print(f"\n주의: {result.note}")
