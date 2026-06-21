from __future__ import annotations

import os
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import streamlit as st

from core.agent import AgentRequest, REGION_CATALOG, run_cleaning_agent
from core.lcoe import DEFAULT_INPUTS, LcoeInputs
from core.pm_statistics import RegionPair, available_years, list_pm_stat_files, list_region_pairs, pm_stats_dir


st.set_page_config(
    page_title="청소 판단 에이전트",
    layout="wide",
)


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

    st.caption(f"PM 통계 폴더: {pm_stats_dir()}")
    st.caption(f"PM 통계 파일: {len(pm_files)}개")
    st.caption(f"KMA_API_KEY: {_env_status('KMA_API_KEY')}")

    run = st.button("분석 실행", type="primary", use_container_width=True)


if "last_result" not in st.session_state or run:
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


result = st.session_state["last_result"]
daily_df = _daily_frame(result)
priority_df = _priority_frame(result)

metric_cols = st.columns(4)
metric_cols[0].metric("연평균 오염 손실", f"{result.pollution.annual_pollution_loss_pct:.2f}%")
metric_cols[1].metric("연간 발전량 손실", f"{result.pollution.annual_generation_loss_kwh:,.0f} kWh")
metric_cols[2].metric("반영 후 LCOE", f"{result.lcoe.ref_lcoe:.2f} 원/kWh", f"+{result.lcoe.lcoe_increase:.2f}%")
metric_cols[3].metric("분석 지점", result.site.name)

left, right = st.columns([1.2, 0.8], gap="large")
with left:
    st.subheader("세척 우선순위")
    st.dataframe(priority_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("데이터 상태")
    for note in result.data_notes:
        st.write(f"- {note}")

st.subheader("오염 손실 추세")
st.line_chart(daily_df[["soiling_loss_pct", "priority_score"]])

chart_cols = st.columns(2)
with chart_cols[0]:
    st.subheader("일 강수량")
    st.bar_chart(daily_df[["rainfall_mm"]])
with chart_cols[1]:
    st.subheader("미세먼지")
    st.line_chart(daily_df[["pm10", "pm25"]])

st.subheader("리포트")
st.markdown(result.report_markdown)

with st.expander("모델 가정"):
    for item in result.pollution.assumptions:
        st.write(f"- {item}")
