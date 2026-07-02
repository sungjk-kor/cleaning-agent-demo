# -*- coding: utf-8 -*-    에이전트 바꾸기 342행
"""
agent_llm.py — LLM-driven cleaning decision agent using Anthropic tool use.
Existing core tools are not modified — only wrapped with tool schemas.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import anthropic
import httpx

from .agent import (
    AgentRequest,
    Site,
    resolve_site as _resolve_site,
    _collect_rainfall,
    _collect_pm,
    build_report,
)
from .lcoe import DEFAULT_INPUTS, calculate_lcoe
from .pollution_model import (
    PollutionModelResult,
    SoilingScenarioRange,
    run_soiling_scenarios,
    simulate_cleaning_decision,
)
from .soiling_knowledge import REPORT_KNOWLEDGE
from .soiling_semiphysical import fsite_from_characteristics


TOOLS: list[dict] = [
    {
        "name": "resolve_site",
        "description": (
            "지역명을 받아 분석할 태양광 발전소 사이트(위경도, 시도)를 확정합니다. "
            "분석 시작 전 반드시 이 도구를 먼저 호출하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region_name": {
                    "type": "string",
                    "description": "지역명 (예: '충남 서산시', '서산', '제주')",
                },
            },
            "required": ["region_name"],
        },
    },
    {
        "name": "get_rainfall",
        "description": (
            "사이트의 강수량 일별 데이터를 수집합니다. "
            "resolve_site 호출 후에만 사용할 수 있습니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "분석 시작일 (YYYY-MM-DD 형식)",
                },
                "end_date": {
                    "type": "string",
                    "description": "분석 종료일 (YYYY-MM-DD 형식)",
                },
                "use_live": {
                    "type": "boolean",
                    "description": "기상청 실조회 여부 (기본값: false, KMA_API_KEY 필요)",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_pm",
        "description": (
            "사이트의 미세먼지(PM10/PM2.5) 일별 데이터를 수집합니다. "
            "resolve_site 호출 후에만 사용할 수 있습니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "분석 시작일 (YYYY-MM-DD 형식)",
                },
                "end_date": {
                    "type": "string",
                    "description": "분석 종료일 (YYYY-MM-DD 형식)",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "run_pollution_model",
        "description": (
            "수집된 강수량·미세먼지 데이터로 오염 모델을 실행하고 "
            "세척 우선순위를 계산합니다. "
            "get_rainfall와 get_pm 호출 후에만 사용할 수 있습니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "capacity_kw": {
                    "type": "number",
                    "description": "태양광 발전 용량 (kW, 기본값: 1000.0)",
                },
                "util_rate_pct": {
                    "type": "number",
                    "description": "이용률 (%, 기본값: 15.4)",
                },
                "top_n": {
                    "type": "integer",
                    "description": "세척 우선순위 상위 N개 (기본값: 5)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_lcoe",
        "description": (
            "오염 손실률을 반영한 LCOE(균등화 발전원가) 분석을 실행합니다. "
            "run_pollution_model 호출 후에만 사용할 수 있습니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pollution_loss_pct": {
                    "type": "number",
                    "description": (
                        "연간 오염 손실률 (%). "
                        "run_pollution_model 결과의 annual_pollution_loss_pct 값을 사용하세요."
                    ),
                },
            },
            "required": ["pollution_loss_pct"],
        },
    },
    {
        "name": "evaluate_regional_characteristics",
        "description": (
            "지역 특성(농업지역, 산업, 해안, 생물오염 등)을 종합하여 "
            "소일링 손실 가중치(F_site)를 계산합니다. "
            "R1~R6(농업/산업/도로/해안/저틸트/생물오염)은 F_site에 반영됩니다. "
            "사용자가 선택한 항목은 그 값이 우선 적용되고, 선택하지 않은 항목은 "
            "AI가 지역명·PM·강수 데이터를 근거로 직접 판정하여 입력하세요. "
            "r6_dust_level(봄철 황사)·r7_rainfall_level(강수세척)은 F_site에 가산하지 않고 "
            "실측 PM·강수로 모델에 이미 반영되는 참고 판정값입니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "r1_agricultural": {
                    "type": "boolean",
                    "description": "농업지역 (낮음/중간/높음)",
                },
                "r1_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R1 강도 (r1_agricultural=true일 때만)",
                },
                "r2_industrial": {
                    "type": "boolean",
                    "description": "산업/건설 인접 (낮음/중간/높음)",
                },
                "r2_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R2 강도 (r2_industrial=true일 때만)",
                },
                "r3_traffic": {
                    "type": "boolean",
                    "description": "철도/주요도로 인접 (낮음/중간/높음)",
                },
                "r3_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R3 강도 (r3_traffic=true일 때만)",
                },
                "r4_coastal": {
                    "type": "boolean",
                    "description": "해안 인접 (낮음/중간/높음)",
                },
                "r4_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R4 강도 (r4_coastal=true일 때만)",
                },
                "r5_tilt": {
                    "type": "boolean",
                    "description": "저틸트/하단 집중 (낮음/중간/높음)",
                },
                "r5_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R5 강도 (r5_tilt=true일 때만)",
                },
                "r6_organic": {
                    "type": "boolean",
                    "description": (
                        "생물오염 인접 — 새분비물/꽃가루/조류 (낮음/중간/높음). "
                        "농지·과수원·철새도래지·수변 인접 시 true. "
                        "IEA: PM-only 모델이 놓치는 대표적 과소평가 요인."
                    ),
                },
                "r6_organic_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "R6 생물오염 강도 (r6_organic=true일 때만)",
                },
                "bird_adjacent": {
                    "type": "boolean",
                    "description": (
                        "철새도래지 인접 (천수만·간척지·하구 등). 새 분비물이 세척효율을 "
                        "약화(잔류↑)시키는 대표 요인. 서산·당진 등 서해안 간척지는 true 검토."
                    ),
                },
                "bird_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": "철새도래지 인접 강도 (bird_adjacent=true일 때만)",
                },
                "r6_dust_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": (
                        "R6 봄철 황사/고농도 강도 (AI가 PM 데이터를 보고 판정). "
                        "PM10 45+ → high, 35-45 → mid, <35 → low"
                    ),
                },
                "r7_rainfall_level": {
                    "type": "string",
                    "enum": ["low", "mid", "high"],
                    "description": (
                        "R7 강수 자연세척 강도 (AI가 강수 데이터를 보고 판정, 감산). "
                        "강수 충분(월평균 100mm+) → high, 보통(50-100mm) → mid, 부족(<50mm) → low"
                    ),
                },
            },
            "required": ["r6_dust_level", "r7_rainfall_level"],
        },
    },
]

_SYSTEM_PROMPT = """당신은 태양광 발전소 패널 세척 판단 전문 에이전트입니다.

사용자의 요청을 받으면 반드시 다음 순서로 도구를 호출하여 분석을 수행하세요:
1. resolve_site: 지역 사이트 확정
2. get_rainfall: 강수량 데이터 수집
3. get_pm: 미세먼지 데이터 수집
4. evaluate_regional_characteristics: 지역특성 평가 (사용자 입력값 우선; 미선택 항목은
   AI가 지역명·PM·강수 데이터로 직접 판정. 서해안 간척지(서산·당진 등)는 bird_adjacent 검토)
5. run_pollution_model: 강우사건 기반 소일링·세척 모델 실행 (완화~보수 range)
6. run_lcoe: LCOE 영향 분석

## 사용 모델: 강우사건(R_e) 기반 반물리 소일링·세척 모델 (IEA PVPS / Coello-Boyle 계열)
- PM 분리 → 일퇴적(Δm=0.0864·cosθ·(v_f·PM2.5+v_c·PM_coarse)·F_site, 일<5mm면 +20% 가중)
- 강우사건: 시간강수에서 무강우 6h↑이면 사건 종료. R_e=사건 강수합. 세척은 사건 종료 시 1회.
- 단계세척: R_e<10mm→약(0.05), 10~20mm→완화 부분(0.55)/보수 약(0.05), ≥20mm→강(0.85).
  잔류: η_eff=η_tier·(1−residual), residual=min(0.60, 0.15+철새·염분+봄철꽃가루). 완전초기화 금지.
- 손실(세척 직전 질량): SL=1−exp(−κ·M^γ), κ=0.0416. DEPO_CAL=1.0(미보정).
목표 %에 맞춘 역산은 하지 않으며, 절대값 확정에는 실측 소일링 센서 보정이 필요합니다.

## 핵심 표출: 단일값이 아닌 완화~보수 시나리오 range
run_pollution_model은 두 시나리오를 산출합니다. 헤드라인은 "연 [완화]~[보수]%, 봄철 피크 X%".
- **완화**: 10~20mm 강우를 부분세척(0.55)으로 인정 → 세척 많음 → 낮은 손실.
- **보수**: 10~20mm를 유효세척으로 인정하지 않음(≥20mm만) → 세척 적음 → 높은 손실.
상한(보수·봄철 피크)은 항상 "실측 소일링 센서 보정 전 시나리오값"임을 명시하세요.

{REPORT_KNOWLEDGE}

## 리포트 작성 지침
모든 도구 호출 후 한국어 마크다운 리포트를 작성하세요. 다음을 반드시 포함하세요:
- **결과 요약**: 연손실을 **range(완화~보수 N.N~M.M%)** + 봄철 피크로 제시(단일값 금지).
  발전량 손실, 세척 우선순위 Top 항목.
- **기상데이터 분석(필수 섹션)**:
  · 강우사건 분석: 연간 강우사건 수, 유효세척(η_eff≥0.3) 횟수, 완화 대비 보수에서 유효세척이
    줄어드는 이유(10~20mm 사건을 세척으로 인정하지 않음).
  · 무세척 기간: 최대 무세척 연속일수와 그 시기(월). '과거 관측 최장 건조기 = 설계 건조기'
    가정을 함께 표기. 봄철·겨울철 1~3개월 무세척 가능성 명시.
  · PM 계절성: 봄철(3~5월) PM 상승 + 꽃가루·황사·철새 잔류로 세척효율이 저하되어 봄철 피크가
    연평균을 크게 상회함을 서술.
  · 부지 가중요인: 적용된 F_site·잔류 요인(산업/철새/해안/꽃가루 등)과 각 출처를 나열.
- **세척 권고안**: 우선순위 날짜와 근거(누적먼지·무강수·고농도).
- **caveat(필수)**: "본 결과는 단일연도(예: 2025) 기준. 장기평균은 10년(최소)~30년(기후평년)
  반복계산이 필요하다. 연강수 총량만으로 소일링을 저평가하지 말 것." + DEPO_CAL=1.0 미보정·
  상한은 시나리오값이라는 점.
숫자만 나열하지 말고, 보고서 지식으로 해석과 맥락을 제공하는 것이 핵심입니다."""

# 보고서 지식을 시스템 프롬프트에 주입
_SYSTEM_PROMPT = _SYSTEM_PROMPT.replace("{REPORT_KNOWLEDGE}", REPORT_KNOWLEDGE)


@dataclass
class LLMAgentResult:
    report_markdown: str
    trace: list[dict]
    site: Site | None = None
    start_date: date | None = None
    end_date: date | None = None
    pollution: PollutionModelResult | None = None
    lcoe: Any = None
    f_site_info: dict | None = None  # {f_site, residual_info, breakdown, r6/r7 levels}
    data_notes: list[str] = field(default_factory=list)
    soiling_range: SoilingScenarioRange | None = None


def _make_agent_request(
    site: Site,
    start: date,
    end: date,
    pm_stats_dir: str | None = None,
) -> AgentRequest:
    parts = site.name.split(maxsplit=1)
    region1 = parts[0] if len(parts) >= 1 else site.sido
    region2 = parts[1] if len(parts) >= 2 else ""
    return AgentRequest(
        region_name=site.name,
        region1=region1,
        region2=region2,
        lat=site.lat,
        lon=site.lon,
        sido=site.sido,
        start_date=start,
        end_date=end,
        pm_stats_dir=pm_stats_dir,
    )


def run_llm_agent(
    region_name: str = "충남 서산시",
    start_date: date | None = None,
    end_date: date | None = None,
    lookback_years: int = 1,
    capacity_kw: float = 1000.0,
    top_n: int = 5,
    model: str = "claude-sonnet-4-6",
    pm_stats_dir: str | None = None,
    regional_characteristics: dict | None = None,
) -> LLMAgentResult:
    """LLM tool-use 에이전트를 실행하여 세척 판단 리포트를 생성합니다."""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")

    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=10.0),
    )

    # Python-side state: full data lives here, LLM sees only summaries
    state: dict[str, Any] = {
        "site": None,
        "start_date": start_date,
        "end_date": end_date,
        "rainfall_by_date": None,
        "pm_by_date": None,
        "pollution": None,
        "soiling_range": None,
        "residual_info": None,
        "lcoe": None,
        "f_site_info": None,
        "data_notes": [],
        "capacity_kw": capacity_kw,
        "util_rate_pct": DEFAULT_INPUTS.util_rate,
        "top_n": top_n,
        "pm_stats_dir": pm_stats_dir,
        "regional_characteristics": regional_characteristics or {},
    }

    trace: list[dict] = []

    def _default_period() -> tuple[date, date]:
        end = state["end_date"] or date(date.today().year, 12, 31)
        start = state["start_date"] or date(end.year - lookback_years + 1, 1, 1)
        return start, end

    def _exec_resolve_site(inp: dict) -> str:
        rname = inp.get("region_name", region_name)
        site = _resolve_site(rname)
        state["site"] = site
        return (
            f"사이트 확정: {site.name} "
            f"(시도: {site.sido}, 위도: {site.lat}, 경도: {site.lon})"
        )

    def _exec_get_rainfall(inp: dict) -> str:
        if state["site"] is None:
            return "오류: resolve_site를 먼저 호출하세요."

        start_str = inp.get("start_date")
        end_str = inp.get("end_date")

        default_start, default_end = _default_period()
        start = date.fromisoformat(start_str) if start_str else default_start
        end = date.fromisoformat(end_str) if end_str else default_end

        state["start_date"] = start
        state["end_date"] = end

        req = _make_agent_request(state["site"], start, end, state["pm_stats_dir"])
        rainfall, notes = _collect_rainfall(state["site"], start, end, req)
        state["rainfall_by_date"] = rainfall
        state["data_notes"].extend(notes)

        total_days = (end - start).days + 1
        import pandas as pd
        rain_vals = list(rainfall) if isinstance(rainfall, pd.Series) else list(rainfall.values())
        rainy_days = sum(1 for v in rain_vals if v > 0)
        total_mm = sum(rain_vals)
        return (
            f"강수량 수집 완료: {start} ~ {end} ({total_days}일), "
            f"강수일 {rainy_days}일, 총 강수량 {total_mm:.1f}mm. "
            f"노트: {'; '.join(notes)}"
        )

    def _exec_get_pm(inp: dict) -> str:
        if state["site"] is None:
            return "오류: resolve_site를 먼저 호출하세요."

        start_str = inp.get("start_date")
        end_str = inp.get("end_date")

        default_start, default_end = _default_period()
        start = date.fromisoformat(start_str) if start_str else (state["start_date"] or default_start)
        end = date.fromisoformat(end_str) if end_str else (state["end_date"] or default_end)

        req = _make_agent_request(state["site"], start, end, state["pm_stats_dir"])
        pm, notes = _collect_pm(state["site"], start, end, req)
        state["pm_by_date"] = pm
        state["data_notes"].extend(notes)

        days_with_pm10 = sum(1 for v in pm.values() if v.get("pm10") is not None)
        pm10_values = [v["pm10"] for v in pm.values() if v.get("pm10") is not None]
        avg_pm10 = sum(pm10_values) / len(pm10_values) if pm10_values else 0.0
        return (
            f"미세먼지 수집 완료: {len(pm)}일 데이터, "
            f"PM10 유효 {days_with_pm10}일, 평균 PM10 {avg_pm10:.1f}μg/m³. "
            f"노트: {'; '.join(notes)}"
        )

    def _exec_run_pollution_model(inp: dict) -> str:
        if state["rainfall_by_date"] is None or state["pm_by_date"] is None:
            return "오류: get_rainfall와 get_pm을 먼저 호출하세요."

        cap = float(inp.get("capacity_kw", state["capacity_kw"]))
        util = float(inp.get("util_rate_pct", state["util_rate_pct"]))
        n = int(inp.get("top_n", state["top_n"]))

        state["capacity_kw"] = cap
        state["util_rate_pct"] = util
        state["top_n"] = n

        # 지역특성 → (F_site, residual) (evaluate 단계 결과 재사용, 없으면 직접 계산)
        if state.get("f_site_info"):
            f_site_val = state["f_site_info"]["f_site"]
            residual_info = state["f_site_info"].get("residual_info") or state.get("residual_info")
        else:
            f_site_val, residual_info, _ = fsite_from_characteristics(
                state.get("regional_characteristics")
            )
        state["residual_info"] = residual_info

        pollution = simulate_cleaning_decision(
            rainfall_by_date=state["rainfall_by_date"],
            pm_by_date=state["pm_by_date"],
            start=state["start_date"],
            end=state["end_date"],
            capacity_kw=cap,
            util_rate_pct=util,
            top_n=n,
            model_name="semiphysical",
            f_site=f_site_val,
            residual_info=residual_info,
            scenario="conservative",
        )
        state["pollution"] = pollution

        # 완화~보수 두 시나리오 range + 기상분석 통계
        soiling_range = run_soiling_scenarios(
            rainfall_input=state["rainfall_by_date"],
            pm_by_date=state["pm_by_date"],
            start=state["start_date"],
            end=state["end_date"],
            f_site=f_site_val,
            residual_info=residual_info,
        )
        state["soiling_range"] = soiling_range
        rel, con = soiling_range.relaxed, soiling_range.conservative

        priority_lines = [
            f"  {p.rank}. {p.date.isoformat()} "
            f"(오염손실 {p.soiling_loss_pct:.2f}%, "
            f"7일 손실 {p.expected_7d_loss_kwh:,.0f}kWh) — {p.reason}"
            for p in pollution.priorities
        ]

        return (
            f"오염 모델 실행 완료 — 점추정이 아닌 완화~보수 시나리오 range로 보고합니다.\n"
            f"- 연손실 range: {soiling_range.low_pct:.2f} ~ {soiling_range.high_pct:.2f}% "
            f"(완화~보수), 봄철 피크 {soiling_range.spring_peak_pct:.2f}% "
            f"(실측 소일링 센서 보정 전 시나리오값)\n"
            f"- 완화(10~20mm 부분세척 인정): 연 {rel.annual_loss_pct:.2f}%, "
            f"유효세척 {rel.effective_wash_count}회, 최대 무세척 {rel.max_no_wash_days}일\n"
            f"- 보수(≥20mm만 유효세척): 연 {con.annual_loss_pct:.2f}%, 봄철평균 {con.spring_loss_pct}%, "
            f"유효세척 {con.effective_wash_count}회, 최대 무세척 {con.max_no_wash_days}일({con.max_no_wash_month}월)\n"
            f"[기상분석 필수 서술] 강우사건 {con.rain_event_count}회, "
            f"완화 대비 보수에서 유효세척이 줄어드는 이유(10~20mm 사건을 세척으로 인정하지 않음)와 "
            f"최대 무세척 기간·봄철 PM/꽃가루 잔류로 인한 손실 상승을 인과적으로 설명하세요.\n"
            f"- 적용 F_site={f_site_val:g}, 잔류 residual={residual_info}\n"
            f"- 연간 발전량 감소 추정(보수): {pollution.annual_generation_loss_kwh:,.0f} kWh\n"
            f"- 세척 우선순위 Top {n}:\n" + "\n".join(priority_lines)
        )

    def _exec_run_lcoe(inp: dict) -> str:
        # Prefer state value over LLM-supplied value to avoid rounding drift
        if state["pollution"] is not None:
            pollution_loss = state["pollution"].annual_pollution_loss_pct
        else:
            pollution_loss = float(inp.get("pollution_loss_pct", 0.0))

        lcoe_inputs = replace(
            DEFAULT_INPUTS,
            capacity=state["capacity_kw"],
            util_rate=state["util_rate_pct"],
            pollution_loss=max(0.0, min(30.0, pollution_loss)),
        )
        lcoe_result = calculate_lcoe(lcoe_inputs)
        state["lcoe"] = lcoe_result

        return (
            f"LCOE 분석 완료:\n"
            f"- 기준 LCOE: {lcoe_result.base_lcoe:.2f} 원/kWh\n"
            f"- 오염 반영 LCOE: {lcoe_result.ref_lcoe:.2f} 원/kWh\n"
            f"- LCOE 증가율: +{lcoe_result.lcoe_increase:.2f}%\n"
            f"- 발전량 감소율: {lcoe_result.gen_decrease:.2f}%"
        )

    def _exec_evaluate_regional_characteristics(inp: dict) -> str:
        user_chars = state.get("regional_characteristics", {})

        # 각 R1~R5마다: 사용자가 명시적으로 체크한 항목은 그 값 우선,
        # 체크하지 않은 항목은 AI(inp)가 지역명·PM·강수 데이터 기반으로 판정.
        _r_map = [
            ("r1_agricultural", "r1_level"),
            ("r2_industrial",   "r2_level"),
            ("r3_traffic",      "r3_level"),
            ("r4_coastal",      "r4_level"),
            ("r5_tilt",         "r5_level"),
            ("r6_organic",      "r6_organic_level"),
            ("bird_adjacent",   "bird_level"),
        ]
        chars_to_use: dict = {}
        user_filled: list[str] = []
        ai_filled: list[str] = []

        for bool_key, level_key in _r_map:
            if user_chars.get(bool_key):
                chars_to_use[bool_key] = True
                chars_to_use[level_key] = user_chars.get(level_key, "mid")
                user_filled.append(bool_key)
            else:
                ai_val = bool(inp.get(bool_key, False))
                chars_to_use[bool_key] = ai_val
                chars_to_use[level_key] = inp.get(level_key, "low")
                if ai_val:
                    ai_filled.append(bool_key)

        if not user_filled:
            source_label = "AI 판정"
        elif ai_filled:
            source_label = "사용자 입력 + AI 보완"
        else:
            source_label = "사용자 입력"

        state["regional_characteristics"] = chars_to_use
        f_site, residual_info, breakdown = fsite_from_characteristics(chars_to_use)
        state["residual_info"] = residual_info

        # 봄철 황사·강수세척은 AI 판정 — 실측 PM·강수가 이미 반영하므로 F_site엔 가산 안 함
        r6_level = inp.get("r6_dust_level", "low")
        r7_level = inp.get("r7_rainfall_level", "low")

        state["f_site_info"] = {
            "f_site": f_site,
            "residual_info": residual_info,
            "breakdown": breakdown,
            "r6_dust_level": r6_level,
            "r7_rainfall_level": r7_level,
            "source": source_label,
        }

        if breakdown:
            bd_lines = [
                f"  {b['label']}: {b['level']} → F_site +{b['increment']:.2f}"
                + (f", 잔류 +{b['res_nonseasonal']:.2f}" if b['res_nonseasonal'] else "")
                + (f", 봄철잔류 +{b['res_spring']:.2f}" if b['res_spring'] else "")
                + f"  [출처: {b['source']}]"
                for b in breakdown
            ]
            bd_text = "\n".join(bd_lines)
        else:
            bd_text = "  (해당 지역특성 없음 → 일반 지역)"

        return (
            f"지역특성 평가 완료 ({source_label}):\n"
            f"- 최종 F_site: {f_site:.2f} (일반=1.0, 산업/해안/철새/생물오염은 배수)\n"
            f"- 세척효율 약화 잔류: 비계절 +{residual_info['nonseasonal']:.2f}, "
            f"봄철 +{residual_info['spring']:.2f} (base 0.15와 합산, 상한 0.60)\n"
            f"- 부지 가중요인 내역(각 출처 포함):\n{bd_text}\n"
            f"- 봄철 황사 판정: {r6_level}, 강수세척 판정: {r7_level} "
            f"(실측 PM·강수로 모델에 내재 반영)\n"
            f"- F_site·잔류는 학술 퇴적속도(DEPO_CAL=1.0, 미보정)에 곱해집니다. "
            f"절대값 확정에는 실측 소일링 센서 보정이 필요합니다."
        )

    _executors = {
        "resolve_site": _exec_resolve_site,
        "get_rainfall": _exec_get_rainfall,
        "get_pm": _exec_get_pm,
        "run_pollution_model": _exec_run_pollution_model,
        "run_lcoe": _exec_run_lcoe,
        "evaluate_regional_characteristics": _exec_evaluate_regional_characteristics,
    }

    # Build initial user message — include user's sidebar selections so LLM
    # passes the correct values to evaluate_regional_characteristics.
    default_start, default_end = _default_period()

    _level_kr = {"low": "저", "mid": "중", "high": "고"}
    _char_labels = {
        "r1": ("r1_agricultural", "r1_level", "농업지역"),
        "r2": ("r2_industrial",   "r2_level", "산업/건설"),
        "r3": ("r3_traffic",      "r3_level", "철도/도로"),
        "r4": ("r4_coastal",      "r4_level", "해안"),
        "r5": ("r5_tilt",         "r5_level", "저틸트"),
        "r6": ("r6_organic",      "r6_organic_level", "생물오염"),
        "bird": ("bird_adjacent", "bird_level", "철새도래지 인접"),
    }
    rc = regional_characteristics or {}
    chars_parts = []
    for _, (bool_key, level_key, label) in _char_labels.items():
        if rc.get(bool_key):
            lvl = rc.get(level_key, "mid")
            chars_parts.append(f"{label}({_level_kr.get(lvl, lvl)})")
    if chars_parts:
        chars_desc = (
            f" 사용자 선택 지역특성: {', '.join(chars_parts)}. "
            f"evaluate_regional_characteristics 호출 시 위 값을 그대로 전달하세요."
        )
    else:
        chars_desc = (
            " 사용자가 지역특성을 선택하지 않았습니다. "
            "evaluate_regional_characteristics 호출 시 AI가 지역명과 수집된 "
            "PM·강수 데이터를 근거로 지역특성(철새도래지 포함)을 직접 판정하여 입력하세요."
        )

    user_message = (
        f"{region_name} 지역의 태양광 패널 세척 판단 분석을 수행해 주세요. "
        f"분석 기간: {default_start.isoformat()} ~ {default_end.isoformat()}, "
        f"설비 용량: {capacity_kw:,.0f}kW, 상위 우선순위: {top_n}건.{chars_desc}"
    )

    messages: list[dict] = [{"role": "user", "content": user_message}]

    # Agent loop: grow messages until stop_reason == "end_turn"
    final_response = None
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        final_response = response

        # Collect text and tool_use blocks from this turn
        text_parts: list[str] = []
        tool_use_blocks = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_use_blocks.append(block)

        if text_parts:
            trace.append({
                "type": "llm_response",
                "text": "\n".join(text_parts),
                "stop_reason": response.stop_reason,
            })

        # Append full assistant content to message history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        # Execute each requested tool
        tool_results: list[dict] = []
        for tool_block in tool_use_blocks:
            tool_name = tool_block.name
            tool_input = dict(tool_block.input)

            trace.append({
                "type": "tool_call",
                "tool": tool_name,
                "input": tool_input,
            })

            executor = _executors.get(tool_name)
            if executor is None:
                result_str = f"알 수 없는 도구: {tool_name}"
            else:
                try:
                    result_str = executor(tool_input)
                except Exception as exc:
                    result_str = f"도구 실행 오류 ({tool_name}): {exc}"

            trace.append({
                "type": "tool_result",
                "tool": tool_name,
                "result": result_str,
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    # Extract final text from last response
    report_markdown = ""
    if final_response is not None:
        for block in final_response.content:
            if block.type == "text" and block.text.strip():
                report_markdown = block.text
                break

    # Fallback: construct a minimal report if LLM text is empty but data is present
    if not report_markdown and state["site"] and state["pollution"] and state["lcoe"]:
        report_markdown = build_report(
            state["site"],
            state["start_date"],
            state["end_date"],
            state["pollution"],
            state["lcoe"],
        )

    return LLMAgentResult(
        report_markdown=report_markdown,
        trace=trace,
        site=state["site"],
        start_date=state["start_date"],
        end_date=state["end_date"],
        pollution=state["pollution"],
        lcoe=state["lcoe"],
        f_site_info=state.get("f_site_info"),
        data_notes=list(state["data_notes"]),
        soiling_range=state.get("soiling_range"),
    )
