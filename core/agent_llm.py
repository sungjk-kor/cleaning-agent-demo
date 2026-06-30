# -*- coding: utf-8 -*-
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

from .agent import (
    AgentRequest,
    Site,
    resolve_site as _resolve_site,
    _collect_rainfall,
    _collect_pm,
    build_report,
)
from .lcoe import DEFAULT_INPUTS, LcoeResult, calculate_lcoe
from .pollution_model import PollutionModelResult, simulate_cleaning_decision
from .soiling_weights import calc_regional_weight


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
            "지역 특성(농업지역, 산업, 해안 등)을 종합하여 "
            "소일링 손실 가중치를 계산합니다. "
            "사용자가 R1~R5를 선택하고, AI가 R6(황사/고농도)·R7(강수세척)을 판정합니다. "
            "수집된 PM·강수 데이터를 기반으로 강도를 판정하세요."
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
4. evaluate_regional_characteristics: 지역특성 평가 (R1~R5는 사용자 입력값, R6~R7은 PM·강수 데이터로 판정)
5. run_pollution_model: 오염 모델 실행 및 세척 우선순위 계산 (지역특성 가중치 반영)
6. run_lcoe: LCOE 영향 분석

모든 도구 호출이 완료되면 분석 결과를 한국어 마크다운 리포트로 요약하세요.
각 단계의 결과를 바탕으로 구체적인 세척 권고안을 제시하세요."""


@dataclass
class LLMAgentResult:
    report_markdown: str
    trace: list[dict]
    site: Site | None = None
    start_date: date | None = None
    end_date: date | None = None
    pollution: PollutionModelResult | None = None
    lcoe: Any = None
    regional_weight: Any = None  # RegionalWeightResult
    data_notes: list[str] = field(default_factory=list)


def _make_agent_request(
    site: Site,
    start: date,
    end: date,
    use_live: bool = False,
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
        use_live_data=use_live,
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

    client = anthropic.Anthropic(api_key=api_key)

    # Python-side state: full data lives here, LLM sees only summaries
    state: dict[str, Any] = {
        "site": None,
        "start_date": start_date,
        "end_date": end_date,
        "rainfall_by_date": None,
        "pm_by_date": None,
        "pollution": None,
        "lcoe": None,
        "regional_weight": None,
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
        use_live = bool(inp.get("use_live", False))

        default_start, default_end = _default_period()
        start = date.fromisoformat(start_str) if start_str else default_start
        end = date.fromisoformat(end_str) if end_str else default_end

        state["start_date"] = start
        state["end_date"] = end

        req = _make_agent_request(
            state["site"], start, end, use_live, state["pm_stats_dir"]
        )
        rainfall, notes = _collect_rainfall(state["site"], start, end, req)
        state["rainfall_by_date"] = rainfall
        state["data_notes"].extend(notes)

        total_days = (end - start).days + 1
        rainy_days = sum(1 for v in rainfall.values() if v > 0)
        total_mm = sum(rainfall.values())
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

        req = _make_agent_request(
            state["site"], start, end, False, state["pm_stats_dir"]
        )
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

        # 지역특성 가중치 적용 (있으면 사용, 없으면 0.0)
        regional_weight_ppt = 0.0
        if state.get("regional_weight") is not None:
            regional_weight_ppt = state["regional_weight"].total_ppt

        pollution = simulate_cleaning_decision(
            rainfall_by_date=state["rainfall_by_date"],
            pm_by_date=state["pm_by_date"],
            start=state["start_date"],
            end=state["end_date"],
            capacity_kw=cap,
            util_rate_pct=util,
            top_n=n,
            regional_weight_ppt=regional_weight_ppt,
        )
        state["pollution"] = pollution

        priority_lines = [
            f"  {p.rank}. {p.date.isoformat()} "
            f"(오염손실 {p.soiling_loss_pct:.2f}%, "
            f"7일 손실 {p.expected_7d_loss_kwh:,.0f}kWh) — {p.reason}"
            for p in pollution.priorities
        ]

        regional_note = ""
        if regional_weight_ppt > 0.001:
            regional_note = f"\n- 지역특성 가중치: +{regional_weight_ppt:.3f}%p (PM 손실 {pollution.annual_pm_loss_pct:.3f}% + 지역특성)"

        return (
            f"오염 모델 실행 완료:\n"
            f"- 연평균 오염 손실률: {pollution.annual_pollution_loss_pct:.2f}%\n"
            f"- 연간 발전량 감소 추정: {pollution.annual_generation_loss_kwh:,.0f} kWh{regional_note}\n"
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
        applied = {}

        # R1~R5: 사용자 입력값 (state에서 가져오기)
        user_chars = state.get("regional_characteristics", {})
        if user_chars.get("r1_agricultural"):
            level = user_chars.get("r1_level", "low")
            if level in ("low", "mid", "high"):
                applied["R1"] = level

        if user_chars.get("r2_industrial"):
            level = user_chars.get("r2_level", "low")
            if level in ("low", "mid", "high"):
                applied["R2"] = level

        if user_chars.get("r3_traffic"):
            level = user_chars.get("r3_level", "low")
            if level in ("low", "mid", "high"):
                applied["R3"] = level

        if user_chars.get("r4_coastal"):
            level = user_chars.get("r4_level", "low")
            if level in ("low", "mid", "high"):
                applied["R4"] = level

        if user_chars.get("r5_tilt"):
            level = user_chars.get("r5_level", "low")
            if level in ("low", "mid", "high"):
                applied["R5"] = level

        # R6~R7: AI 판정값 (LLM이 PM·강수 데이터를 보고 판정)
        r6_level = inp.get("r6_dust_level", "low")
        if r6_level in ("low", "mid", "high"):
            applied["R6"] = r6_level

        r7_level = inp.get("r7_rainfall_level", "low")
        if r7_level in ("low", "mid", "high"):
            applied["R7"] = r7_level

        result = calc_regional_weight(applied)
        state["regional_weight"] = result

        breakdown_lines = [
            f"  {item.rule_id} {item.name}: {item.level} → {item.value_ppt:+.2f}%p"
            for item in result.breakdown
        ]

        return (
            f"지역특성 평가 완료:\n"
            f"- 최종 가중치: {result.total_ppt:+.3f}%p\n"
            f"- 가산 상한 적용: {'예' if result.capped else '아니오'}\n"
            f"- 규칙별:\n" + "\n".join(breakdown_lines) + "\n"
            f"- {result.note}"
        )

    _executors = {
        "resolve_site": _exec_resolve_site,
        "get_rainfall": _exec_get_rainfall,
        "get_pm": _exec_get_pm,
        "run_pollution_model": _exec_run_pollution_model,
        "run_lcoe": _exec_run_lcoe,
        "evaluate_regional_characteristics": _exec_evaluate_regional_characteristics,
    }

    # Build initial user message
    default_start, default_end = _default_period()
    user_message = (
        f"{region_name} 지역의 태양광 패널 세척 판단 분석을 수행해 주세요. "
        f"분석 기간: {default_start.isoformat()} ~ {default_end.isoformat()}, "
        f"설비 용량: {capacity_kw:,.0f}kW, 상위 우선순위: {top_n}건."
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
        regional_weight=state.get("regional_weight"),
        data_notes=list(state["data_notes"]),
    )
