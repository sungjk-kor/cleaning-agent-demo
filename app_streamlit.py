from __future__ import annotations

import os
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import streamlit as st

from core.agent import AgentRequest, REGION_CATALOG, run_cleaning_agent
from core.agent_llm import run_llm_agent
from core.lcoe import DEFAULT_INPUTS, LcoeInputs
from core.pm_statistics import (
    RegionPair, available_years, list_pm_stat_files, list_region_pairs,
    pm_stats_dir, pm_cache_status, precompute_pm_cache,
)


st.set_page_config(
    page_title="청소 판단 에이전트",
    layout="wide",
)

# Streamlit Cloud secrets → 환경변수로 주입 (set_page_config 이후에만 안전하게 접근 가능)
try:
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ.setdefault("ANTHROPIC_API_KEY", st.secrets["ANTHROPIC_API_KEY"])
except Exception:
    pass


def _env_status(name: str) -> str:
    return "설정됨" if os.environ.get(name) else "없음"


def _build_lcoe_inputs() -> LcoeInputs:
    defaults = DEFAULT_INPUTS
    with st.sidebar.expander("LCOE 입력", expanded=False):
        capacity = st.number_input("설비용량 kW", min_value=1.0, value=float(defaults.capacity), step=100.0)
        util_rate = st.number_input("이용률 %", min_value=0.1, max_value=40.0, value=float(defaults.util_rate), step=0.1)
        construct_cost = st.number_input("건설비 원/kW", min_value=0.0, value=float(defaults.construct_cost), step=10000.0)
        discount_rate = st.number_input("할인율 %", min_value=0.0, max_value=30.0, value=float(defaults.discount_rate), step=0.1)
        lifespan = st.number_input("경제적 수명 년", min_value=1, max_value=50, value=int(defaults.lifespan), step=1)
        annual_om = st.number_input("연간 O&M 원", min_value=0.0, value=float(defaults.annual_om), step=100000.0)
        annual_land = st.number_input("연간 임대료 원", min_value=0.0, value=float(defaults.annual_land), step=100000.0)
        inflation = st.number_input("인플레이션 %/년", min_value=0.0, max_value=20.0, value=float(defaults.inflation), step=0.05)
        degradation = st.number_input("성능저하율 %/년", min_value=0.0, max_value=5.0, value=float(defaults.degradation), step=0.05)
        failure_rate_10 = st.number_input("10년차 고장모듈 %", min_value=0.0, max_value=100.0, value=float(defaults.failure_rate_10), step=0.5)
        failure_rate_20 = st.number_input("20년차 고장모듈 %", min_value=0.0, max_value=100.0, value=float(defaults.failure_rate_20), step=0.5)

    return replace(
        defaults,
        capacity=capacity,
        util_rate=util_rate,
        construct_cost=construct_cost,
        discount_rate=discount_rate,
        lifespan=int(lifespan),
        annual_om=annual_om,
        annual_land=annual_land,
        inflation=inflation,
        degradation=degradation,
        failure_rate_10=failure_rate_10,
        failure_rate_20=failure_rate_20,
    )


def _fallback_region_pairs() -> tuple[RegionPair, ...]:
    pairs = set()
    for site in REGION_CATALOG.values():
        parts = site.name.split(maxsplit=1)
        if len(parts) == 2:
            pairs.add(RegionPair(parts[0], parts[1]))
        else:
            pairs.add(RegionPair(site.sido, site.name))
    return tuple(sorted(pairs, key=lambda item: (item.region1, item.region2)))


def _daily_frame(result) -> pd.DataFrame:
    df = pd.DataFrame([asdict(row) for row in result.pollution.daily])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def _priority_frame(result) -> pd.DataFrame:
    df = pd.DataFrame([asdict(row) for row in result.pollution.priorities])
    if df.empty:
        return df
    return df[
        [
            "rank",
            "date",
            "soiling_loss_pct",
            "priority_score",
            "rain_14d_mm",
            "pm10_14d",
            "pm25_14d",
            "dry_days",
            "expected_daily_loss_kwh",
            "expected_7d_loss_kwh",
            "reason",
        ]
    ].rename(
        columns={
            "rank": "순위",
            "date": "권장일",
            "soiling_loss_pct": "오염손실(%)",
            "priority_score": "우선순위점수",
            "rain_14d_mm": "14일강수(mm)",
            "pm10_14d": "PM10(14일)",
            "pm25_14d": "PM2.5(14일)",
            "dry_days": "연속건조일",
            "expected_daily_loss_kwh": "일손실(kWh)",
            "expected_7d_loss_kwh": "7일손실(kWh)",
            "reason": "근거",
        }
    )


@st.dialog("분석 보고서", width="large")
def _show_report_dialog(report_markdown: str) -> None:
    st.markdown(report_markdown)


def _render_trace(trace: list[dict]) -> None:
    if not trace:
        st.info("추론 과정이 기록되지 않았습니다.")
        return

    st.subheader("에이전트 추론 과정")
    tool_step = 0
    i = 0
    while i < len(trace):
        item = trace[i]
        kind = item.get("type")

        if kind == "llm_response":
            text = item.get("text") or ""
            is_final = item.get("stop_reason") == "end_turn"
            if is_final:
                # 최종 보고서는 팝업 버튼으로 표시 — 여기서는 생략
                i += 1
                continue
            if text.strip():
                with st.expander("💭 LLM 추론", expanded=False):
                    st.markdown(text)
            i += 1

        elif kind == "tool_call":
            tool_name = item["tool"]
            tool_input = item.get("input", {})
            result_text = ""
            consume = 1
            if i + 1 < len(trace) and trace[i + 1].get("type") == "tool_result":
                result_text = trace[i + 1].get("result", "")
                consume = 2

            tool_step += 1
            with st.expander(f"🔧 Step {tool_step}: {tool_name}", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**입력**")
                    st.json(tool_input)
                with col2:
                    st.markdown("**결과**")
                    st.text(result_text)
            i += consume

        else:
            i += 1


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.title("청소 판단 에이전트")

with st.sidebar:
    st.header("분석 조건")
    pm_files = list_pm_stat_files()
    stats_region_pairs = list_region_pairs() if pm_files else ()
    region_pairs = stats_region_pairs or _fallback_region_pairs()

    region1_options = sorted({pair.region1 for pair in region_pairs})
    default_region1 = "충남" if "충남" in region1_options else region1_options[0]
    region1 = st.selectbox(
        "지역명1",
        options=region1_options,
        index=region1_options.index(default_region1),
    )

    region2_options = sorted(pair.region2 for pair in region_pairs if pair.region1 == region1)
    preferred_region2 = "서산시" if "서산시" in region2_options else "서산"
    default_region2 = preferred_region2 if preferred_region2 in region2_options else region2_options[0]
    region2 = st.selectbox(
        "지역명2",
        options=region2_options,
        index=region2_options.index(default_region2),
    )
    final_region = f"{region1} {region2}".strip()

    use_custom_coord = st.checkbox("좌표 직접 지정", value=False)
    lat = lon = None
    if use_custom_coord:
        col_lat, col_lon = st.columns(2)
        lat = col_lat.number_input("위도", min_value=30.0, max_value=45.0, value=36.7849, step=0.0001, format="%.4f")
        lon = col_lon.number_input("경도", min_value=120.0, max_value=135.0, value=126.4503, step=0.0001, format="%.4f")

    year_options = list(available_years()) or [date.today().year]
    end_year = st.selectbox("기준 연도", options=year_options, index=len(year_options) - 1)
    lookback_years = st.selectbox("분석 기간", [1, 2, 3, 4, 5], index=0, format_func=lambda v: f"최근 {v}년")
    end_date = date(int(end_year), 12, 31)
    use_live_data = st.toggle("KMA 실 API 강수 반영", value=False)
    live_weather_days_limit = st.slider("KMA 실조회 일수", min_value=1, max_value=14, value=5)
    top_n = st.slider("세척 후보 수", min_value=3, max_value=10, value=5)

    lcoe_inputs = _build_lcoe_inputs()

    # 지역특성 입력 (R1~R5: 사용자 입력, R6~R7: AI 판정)
    regional_characteristics = {}
    with st.sidebar.expander("🌍 지역특성 (선택)", expanded=False):
        st.caption("다음 조건이 해당되면 체크하세요. 강도는 저/중/고 중 선택.")

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r1_agr = st.checkbox("농업지역", value=False, key="r1_agr")
        if r1_agr:
            with col2:
                regional_characteristics["r1_agricultural"] = True
                regional_characteristics["r1_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r1_level")
        else:
            regional_characteristics["r1_agricultural"] = False

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r2_ind = st.checkbox("산업/건설 인접", value=False, key="r2_ind")
        if r2_ind:
            with col2:
                regional_characteristics["r2_industrial"] = True
                regional_characteristics["r2_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r2_level")
        else:
            regional_characteristics["r2_industrial"] = False

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r3_tra = st.checkbox("철도/주요도로 인접", value=False, key="r3_tra")
        if r3_tra:
            with col2:
                regional_characteristics["r3_traffic"] = True
                regional_characteristics["r3_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r3_level")
        else:
            regional_characteristics["r3_traffic"] = False

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r4_cos = st.checkbox("해안 인접", value=False, key="r4_cos")
        if r4_cos:
            with col2:
                regional_characteristics["r4_coastal"] = True
                regional_characteristics["r4_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r4_level")
        else:
            regional_characteristics["r4_coastal"] = False

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r5_tilt = st.checkbox("저틸트/하단 집중", value=False, key="r5_tilt")
        if r5_tilt:
            with col2:
                regional_characteristics["r5_tilt"] = True
                regional_characteristics["r5_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r5_level")
        else:
            regional_characteristics["r5_tilt"] = False

        st.caption("※ R6(봄철 황사), R7(강수세척)은 AI가 PM·강수 데이터를 보고 판정합니다.")

    st.divider()
    agent_mode = st.toggle(
        "에이전트 모드 (LLM)",
        value=False,
        help="Anthropic API를 호출해 LLM이 도구 순서를 직접 결정합니다. ANTHROPIC_API_KEY 필요. 30~60초 소요.",
    )

    st.caption(f"PM 통계 폴더: {pm_stats_dir()}")
    st.caption(f"PM 통계 파일: {len(pm_files)}개")
    st.caption(f"KMA_API_KEY: {_env_status('KMA_API_KEY')}")
    if agent_mode:
        st.caption(f"ANTHROPIC_API_KEY: {_env_status('ANTHROPIC_API_KEY')}")

    # PM 캐시 빌드 (파일이 있을 때만 표시)
    if pm_files:
        cached_n, total_n = pm_cache_status()
        if cached_n < total_n:
            with st.expander(f"⚠️ PM 캐시 미완성 ({cached_n}/{total_n})", expanded=True):
                st.caption(
                    f"Excel 파일 {total_n - cached_n}개가 캐시되지 않았습니다. "
                    "캐시를 빌드하면 분석 속도가 크게 빨라집니다 (파일당 ~30초 소요)."
                )
                if st.button("PM 캐시 빌드", use_container_width=True):
                    progress_bar = st.progress(0.0, text="캐시 빌드 중...")
                    def _progress(done: int, total: int) -> None:
                        progress_bar.progress(done / total, text=f"처리 중 {done}/{total}...")
                    precompute_pm_cache(progress_callback=_progress)
                    progress_bar.empty()
                    st.success("캐시 빌드 완료! 분석이 빨라집니다.")
                    st.rerun()
        else:
            st.caption(f"PM 캐시: {cached_n}/{total_n} 완료")

    run = st.button("분석 실행", type="primary", use_container_width=True)


# ── Run logic ─────────────────────────────────────────────────────────────────

if "last_result" not in st.session_state or run:
    if agent_mode:
        start_date = date(int(end_year) - lookback_years + 1, 1, 1)
        with st.spinner("LLM 에이전트 분석 중 (30~60초 소요됩니다)..."):
            st.session_state["last_result"] = run_llm_agent(
                region_name=final_region,
                start_date=start_date,
                end_date=end_date,
                lookback_years=lookback_years,
                capacity_kw=lcoe_inputs.capacity,
                top_n=top_n,
                regional_characteristics=regional_characteristics,
            )
        st.session_state["last_agent_mode"] = True
    else:
        with st.spinner("에이전트 분석 중"):
            request = AgentRequest(
                region_name=final_region,
                region1=region1,
                region2=region2,
                lat=lat,
                lon=lon,
                end_date=end_date,
                lookback_years=lookback_years,
                use_live_data=use_live_data,
                live_weather_days_limit=live_weather_days_limit,
                top_n=top_n,
                lcoe_inputs=lcoe_inputs,
            )
            st.session_state["last_result"] = run_cleaning_agent(request)
        st.session_state["last_agent_mode"] = False


# ── Main display ──────────────────────────────────────────────────────────────

result = st.session_state["last_result"]
is_agent_result = st.session_state.get("last_agent_mode", False)

if result.pollution is not None and result.lcoe is not None and result.site is not None:
    daily_df = _daily_frame(result)
    priority_df = _priority_frame(result)

    metric_cols = st.columns(4)
    metric_cols[0].metric("연평균 오염 손실", f"{result.pollution.annual_pollution_loss_pct:.2f}%")
    metric_cols[1].metric("연간 발전량 손실", f"{result.pollution.annual_generation_loss_kwh:,.0f} kWh")
    metric_cols[2].metric("반영 후 LCOE", f"{result.lcoe.ref_lcoe:.2f} 원/kWh", f"+{result.lcoe.lcoe_increase:.2f}%")
    metric_cols[3].metric("분석 지점", result.site.name)

    # HSU 모델 산출식 및 출처
    with st.expander("📐 HSU 모델 산출식 및 출처", expanded=False):
        st.markdown("""
### HSU 소일링 모델 (Coello & Boyle 2019)

**기본 원리:**
```
소일링 손실(%) = (1 - SR) × 100
여기서 SR = soiling_ratio (0~1, 1=완전 세척)
```

**침적 계산:**
- PM2.5 침적 속도: 0.0009 m/s
- PM10 침적 속도: 0.004 m/s
- 일일 누적 침적량 계산

**자연 세척:**
- 강우 임계값: 0.5mm 이상 시 자동 세척
- 일시간 단위 강우 누적으로 판정

**적용 파라미터:**
- 경사각: 30° (한국 고정설치 표준)
- PM 단위: AirKorea µg/m³ → pvlib g/m³ (×1e-6)
- 강수: ASOS 관측 일강수량(mm)

**출처:**
- Coello, C., & Boyle, L. (2019).
- "Performance of soiled photovoltaic modules"
- IEEE Journal of Photovoltaics, 9(5), 1382-1387
- DOI: 10.1109/JPHOTOV.2019.2914786
- Implementation: pvlib-python soiling.hsu()
        """)

    left, right = st.columns([1.2, 0.8], gap="large")
    with left:
        st.subheader("세척 우선순위")
        st.dataframe(priority_df, use_container_width=True, hide_index=True)

    with right:
        st.subheader("데이터 상태")
        for note in result.data_notes:
            st.write(f"- {note}")

    real_rain_used = any(
        "KMA" in note and "반영" in note for note in (result.data_notes or [])
    )
    if not real_rain_used:
        st.info(
            "본 데모의 강수 데이터는 KMA 실측 연동 전 단계로, "
            "통계 시뮬레이션 값을 사용합니다. 실측 API 연동은 로드맵에 포함돼 있습니다.",
            icon="ℹ️",
        )

    st.subheader("오염 손실 추세")
    st.line_chart(daily_df[["soiling_loss_pct", "priority_score"]])

    chart_cols = st.columns(2)
    with chart_cols[0]:
        st.subheader("일 강수량")
        st.bar_chart(daily_df[["rainfall_mm"]])
    with chart_cols[1]:
        st.subheader("미세먼지")
        st.line_chart(daily_df[["pm10", "pm25"]])

    if is_agent_result:
        if st.button("📋 보고서 열기", type="secondary"):
            _show_report_dialog(result.report_markdown)
    else:
        st.subheader("리포트")
        st.markdown(result.report_markdown)

    with st.expander("모델 가정"):
        for item in result.pollution.assumptions:
            st.write(f"- {item}")

    # 에이전트 모드이고 지역특성 결과가 있으면 표시
    if is_agent_result and result.regional_weight is not None:
        with st.expander("🌍 지역특성 분석"):
            st.markdown(f"**최종 가중치: {result.regional_weight.total_ppt:+.3f}%p**")
            st.markdown(f"가산 상한 적용: {'예' if result.regional_weight.capped else '아니오'}")

            # Breakdown 테이블
            breakdown_data = []
            for item in result.regional_weight.breakdown:
                breakdown_data.append({
                    "규칙": item.rule_id,
                    "이름": item.name,
                    "강도": {"low": "저", "mid": "중", "high": "고"}.get(item.level, item.level),
                    "가중값(%p)": f"{item.value_ppt:+.2f}",
                    "출처": item.source,
                })
            if breakdown_data:
                breakdown_df = pd.DataFrame(breakdown_data)
                st.dataframe(breakdown_df, use_container_width=True, hide_index=True)

            st.caption(result.regional_weight.note)

else:
    if is_agent_result:
        if st.button("📋 보고서 열기", type="secondary"):
            _show_report_dialog(result.report_markdown or "")
    else:
        st.subheader("리포트")
        st.markdown(result.report_markdown or "분석 결과가 없습니다.")

# ── Agent trace panel ──────────────────────────────────────────────────────────

if is_agent_result and hasattr(result, "trace"):
    _render_trace(result.trace)
