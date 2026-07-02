from __future__ import annotations

import os
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import streamlit as st

from core.agent import REGION_CATALOG, AgentRequest, run_cleaning_agent
from core.agent_llm import run_llm_agent
from core.lcoe import DEFAULT_INPUTS, LcoeInputs
from core.soiling_semiphysical import fsite_from_characteristics
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


@st.cache_data(ttl=60)
def _list_asos_csv() -> list[str]:
    d = os.path.join("data", "raw_asos")
    if not os.path.isdir(d):
        return []
    return [f for f in os.listdir(d) if f.lower().endswith(".csv")]


@st.cache_data(ttl=60)
def _asos_available_years() -> list[int]:
    import re
    years = []
    for f in _list_asos_csv():
        m = re.search(r"(20\d{2})", f)
        if m:
            years.append(int(m.group(1)))
    return sorted(years)


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


def _render_soiling_audit(sr) -> None:
    """완화/보수 시나리오 audit 표 + 기상분석 통계 (항상 표시)."""
    if sr is None:
        return

    def _row(label, cond, r):
        return {
            "시나리오": label,
            "조건": cond,
            "연평균%": f"{r.annual_loss_pct:.2f}",
            "봄철평균%": f"{r.spring_loss_pct:.2f}" if r.spring_loss_pct is not None else "-",
            "봄철피크%": f"{r.spring_peak_loss_pct:.2f}" if r.spring_peak_loss_pct is not None else "-",
            "유효세척(회)": r.effective_wash_count,
            "최대무세척(일)": r.max_no_wash_days,
        }

    rel, con = sr.relaxed, sr.conservative
    rows = [
        _row("완화(relaxed)", "10~20mm 부분세척 인정", rel),
        _row("보수(conservative)", "≥20mm만 유효세척", con),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 기상데이터 분석 요약
    c1, c2, c3 = st.columns(3)
    c1.metric("강우사건 수", f"{con.rain_event_count}회")
    c2.metric("최대 무세척(보수)", f"{con.max_no_wash_days}일",
              f"{con.max_no_wash_month}월경" if con.max_no_wash_month else None)
    c3.metric("유효세척 완화→보수", f"{rel.effective_wash_count}→{con.effective_wash_count}회")
    st.caption(
        f"F_site={sr.f_site:g}, 잔류 비계절 {sr.residual_info.get('nonseasonal', 0):.2f}·"
        f"봄철 {sr.residual_info.get('spring', 0):.2f}. "
        "완화→보수는 10~20mm 강우를 유효세척으로 인정하지 않아 손실↑. 봄철 피크는 PM·꽃가루·"
        "철새 잔류로 세척효율이 저하되어 연평균을 상회. **모든 값은 단일연도 모델 산출값이며, "
        "절대값 확정에는 실측 소일링 센서 보정이 필요**합니다(장기평균은 10~30년 반복계산 필요)."
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

    asos_years = _asos_available_years()
    if asos_years:
        max_lookback = max(1, min(5, int(end_year) - min(asos_years) + 1))
    else:
        max_lookback = 5
    lookback_options = list(range(1, max_lookback + 1))
    lookback_years = st.selectbox("분석 기간", lookback_options, index=0, format_func=lambda v: f"최근 {v}년")
    if asos_years and max_lookback < 5:
        st.caption(f"ASOS 실측 강수: {min(asos_years)}~{max(asos_years)}년 · 최대 {max_lookback}년")
    end_date = date(int(end_year), 12, 31)
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

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r6_org = st.checkbox("생물오염(새·꽃가루·조류)", value=False, key="r6_org")
        if r6_org:
            with col2:
                regional_characteristics["r6_organic"] = True
                regional_characteristics["r6_organic_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="r6_organic_level")
        else:
            regional_characteristics["r6_organic"] = False

        col1, col2 = st.columns([1.5, 1])
        with col1:
            r_bird = st.checkbox("철새도래지 인접(천수만 등)", value=False, key="r_bird")
        if r_bird:
            with col2:
                regional_characteristics["bird_adjacent"] = True
                regional_characteristics["bird_level"] = st.selectbox("강도", ["low", "mid", "high"], format_func=lambda x: {"low":"저", "mid":"중", "high":"고"}[x], key="bird_level")
        else:
            regional_characteristics["bird_adjacent"] = False

        st.caption("※ 봄철 황사·강수세척은 AI가 PM·강수 데이터를 보고 판정합니다.")

    st.divider()
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    run_quick = st.button("빠른 결과 보기 (AI 없이)", use_container_width=True)
    st.caption("ASOS·PM 데이터로 즉시 계산합니다 · 예상 소요: 3~5초")

    st.caption(f"PM 통계 폴더: {pm_stats_dir()}")
    st.caption(f"PM 통계 파일: {len(pm_files)}개")
    st.caption(f"KMA_API_KEY: {_env_status('KMA_API_KEY')}")
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

    st.divider()
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    st.write("")
    run = st.button("인공지능 보고서 생성하기", type="primary", use_container_width=True)
    st.caption("AI가 지역특성을 판단하고 심층 보고서를 생성합니다 · 예상 소요: 30~60초")

    # 빠른 결과 보기 버튼만 파란색으로 (expander 안의 버튼은 제외)
    # 사이드바 간격 축소
    st.markdown("""
<style>
section[data-testid="stSidebar"] .stButton button[kind="secondary"] {
    background-color: #1565C0;
    color: white;
    border: 1px solid #0D47A1;
}
section[data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
    background-color: #0D47A1;
    border-color: #003c8f;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] .stButton button[kind="secondary"] {
    background-color: #e8e8e8;
    color: #333;
    border: 1px solid #ccc;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] .stButton button[kind="secondary"]:hover {
    background-color: #d0d0d0;
    border-color: #bbb;
}

/* 사이드바 메뉴 간격 축소 */
section[data-testid="stSidebar"] {
    gap: 0.1rem !important;
}
section[data-testid="stSidebar"] > div:first-child {
    gap: 0.1rem !important;
}
section[data-testid="stSidebar"] .element-container {
    margin-bottom: 0.1rem !important;
    margin-top: 0 !important;
    padding: 0 !important;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.1rem !important;
}
section[data-testid="stSidebar"] .stCaption {
    margin-bottom: 0.15rem !important;
    margin-top: -0.3rem !important;
}
section[data-testid="stSidebar"] hr {
    margin: 0.3rem 0 !important;
}
</style>
""", unsafe_allow_html=True)


# ── Run logic ─────────────────────────────────────────────────────────────────

if run:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. "
            ".env 파일 또는 Streamlit Secrets를 확인하세요."
        )
        st.stop()
    start_date = date(int(end_year) - lookback_years + 1, 1, 1)
    with st.spinner("AI 에이전트 분석 중 (30~60초 소요됩니다)..."):
        try:
            st.session_state["last_result"] = run_llm_agent(
                region_name=final_region,
                start_date=start_date,
                end_date=end_date,
                lookback_years=lookback_years,
                capacity_kw=lcoe_inputs.capacity,
                top_n=top_n,
                regional_characteristics=regional_characteristics,
            )
            st.session_state["last_is_llm"] = True
        except Exception as exc:
            st.error(f"에이전트 실행 오류: {exc}")
            st.stop()

elif run_quick:
    # 누락 항목 확인 — 분석은 항상 실행하되, 데모 데이터 사용 시 안내
    missing_msgs: list[str] = []
    if not pm_files:
        missing_msgs.append(
            "PM 통계 엑셀 파일 없음 → data/pm_stats/ 폴더에 .xlsx 파일을 추가해 주세요"
        )
    if not _list_asos_csv():
        missing_msgs.append(
            "ASOS 강수 CSV 없음 → data/raw_asos/ 폴더에 CSV 파일을 추가해 주세요"
        )
    if missing_msgs:
        st.warning(
            "아래 항목이 없어 **데모 데이터**로 대체합니다. "
            "정확한 분석을 원하시면 항목을 추가하고 다시 클릭해 주세요.\n\n"
            + "\n\n".join(f"- {m}" for m in missing_msgs)
        )

    f_site_val, residual_info, _ = fsite_from_characteristics(regional_characteristics)
    request = AgentRequest(
        region_name=final_region,
        region1=region1,
        region2=region2,
        lat=lat,
        lon=lon,
        end_date=end_date,
        lookback_years=lookback_years,
        f_site=f_site_val,
        residual_info=residual_info,
        top_n=top_n,
        lcoe_inputs=lcoe_inputs,
    )
    with st.spinner("빠른 분석 중..."):
        st.session_state["last_result"] = run_cleaning_agent(request)
    st.session_state["last_is_llm"] = False

if "last_result" not in st.session_state:
    st.info(
        "분석 버튼을 클릭하세요.\n\n"
        "- **인공지능 보고서 생성하기**: AI가 지역특성을 판단하고 심층 보고서를 작성합니다 (30~60초)\n"
        "- **빠른 결과 보기**: AI 없이 데이터 기반으로 즉시 계산합니다 (3~5초)"
    )
    st.stop()


# ── Main display ──────────────────────────────────────────────────────────────

result = st.session_state["last_result"]

if result.pollution is not None and result.lcoe is not None and result.site is not None:
    daily_df = _daily_frame(result)
    priority_df = _priority_frame(result)

    sr = getattr(result, "soiling_range", None)

    # 헤드라인: 단일값이 아닌 완화~보수 시나리오 range + 봄철 피크
    if sr is not None:
        st.markdown(
            f"## 🎯 연손실 {sr.low_pct:.1f} ~ {sr.high_pct:.1f}% "
            f"(완화~보수) · 봄철 피크 {sr.spring_peak_pct:.1f}%"
        )
        st.caption(
            f"완화 {sr.low_pct:.1f}%(10~20mm 부분세척 인정) ~ 보수 {sr.high_pct:.1f}%(≥20mm만 유효세척) · "
            f"봄철 피크 {sr.spring_peak_pct:.1f}%는 **실측 소일링 센서 보정 전 시나리오값**"
        )

    metric_cols = st.columns(4)
    if sr is not None:
        metric_cols[0].metric("연손실 range", f"{sr.low_pct:.1f}~{sr.high_pct:.1f}%", f"봄철피크 {sr.spring_peak_pct:.1f}%")
    else:
        metric_cols[0].metric("연평균 오염 손실", f"{result.pollution.annual_pollution_loss_pct:.2f}%")
    metric_cols[1].metric("연간 발전량 손실", f"{result.pollution.annual_generation_loss_kwh:,.0f} kWh")
    metric_cols[2].metric("반영 후 LCOE", f"{result.lcoe.ref_lcoe:.2f} 원/kWh", f"+{result.lcoe.lcoe_increase:.2f}%")
    metric_cols[3].metric("분석 지점", result.site.name)

    # 완화~보수 시나리오 audit 표 + 기상분석 (항상 표시)
    if sr is not None:
        st.markdown("#### 시나리오 근거 (완화 vs 보수)")
        _render_soiling_audit(sr)

    # 반물리 5단계 모델 산출식 및 출처
    with st.expander("📐 강우사건 기반 소일링·세척 모델 산출식 및 출처", expanded=False):
        st.markdown(r"""
### 강우사건(R_e) 기반 반물리 소일링·세척 모델 (IEA PVPS / Coello-Boyle 계열)

대기 PM → 표면 퇴적 → 강우사건 단계세척 → 누적 → 비선형 발전손실.

**1단계 — 미세/조대입자 분리**
```
PM_coarse = max(PM10 − PM2.5, 0)
```

**2단계 — 일 퇴적량** (g/m²/day, 일<5mm면 ×1.2 가중)
```
Δm = 0.0864 · cosθ · (v_f·PM2.5 + v_c·PM_coarse) · F_site · DEPO_CAL
```
- v_f=0.0009, v_c=0.004 m/s, DEPO_CAL=1.0 (미보정, 학술 퇴적속도 그대로)
- F_site: 지역특성 계수 (일반=1.0, 산업/해안/철새/생물오염은 배수)

**3단계 — 강우사건(R_e) 단계세척** (사건 종료 시 1회)
```
사건 분리: 무강우 6h↑ → 다음 사건.  R_e = 사건 강수합
R_e < 10mm         → η_weak    = 0.05
10 ≤ R_e < 20mm    → 완화 0.55 / 보수 0.05
R_e ≥ 20mm         → η_strong  = 0.85
η_eff = η_tier · (1 − residual)   (완전초기화 금지)
residual = min(0.60, 0.15 + 철새·염분·도로 + 봄철 꽃가루)
```

**4단계 — 누적**
```
M_before = M_{d-1} + Δm
M_after  = M_before · (1 − η_eff)   (사건 종료일에만 세척)
```

**5단계 — 비선형 발전손실 (세척 직전 질량 기준)**
```
SL = 1 − exp(−κ·M_before^γ),  κ=0.0416, γ=1.0
```

**두 시나리오 range**: 완화(10~20mm 부분세척 인정) ~ 보수(≥20mm만 유효세척),
헤드라인 = "연 완화~보수%, 봄철 피크 X%".

**참고 보고서:**
1. **IEA PVPS T13-21:2022** — *Soiling Losses – Impact on the Performance of PV Plants*
2. Coello, C. & Boyle, L. (2019), *IEEE J. Photovoltaics* 9(5):1382-1387

**보정 상태:** DEPO_CAL=1.0(미보정). 특정 목표 %에 맞춘 역산은 하지 않으며, 절대값
확정에는 **실측 소일링 센서 보정이 필요**합니다(field-calibration pending). 단일연도
산출값이며 장기평균은 10년(최소)~30년(기후평년) 반복계산이 필요합니다.
        """)

    left, right = st.columns([1.2, 0.8], gap="large")
    with left:
        st.subheader("세척 우선순위")
        st.dataframe(priority_df, use_container_width=True, hide_index=True)

    with right:
        st.subheader("데이터 상태")
        for note in result.data_notes:
            st.write(f"- {note}")

    using_demo_rain = any("데모 강수" in note for note in (result.data_notes or []))
    if using_demo_rain:
        st.info(
            "강수 데이터: ASOS CSV(data/raw_asos/)가 없고 KMA_API_KEY도 미설정 — "
            "데모 시뮬레이션 값을 사용합니다. "
            "data/raw_asos/ 폴더에 ASOS 관측 CSV를 추가하면 실측 강수가 자동 반영됩니다.",
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

    if st.session_state.get("last_is_llm", False):
        if st.button("📋 보고서 열기", type="secondary"):
            _show_report_dialog(result.report_markdown)
    else:
        with st.expander("📋 분석 리포트", expanded=True):
            st.markdown(result.report_markdown)

    with st.expander("모델 가정"):
        for item in result.pollution.assumptions:
            st.write(f"- {item}")

    if getattr(result, "f_site_info", None) is not None:
        info = result.f_site_info
        source_label = info.get("source", "사용자 입력")
        resid = info.get("residual_info") or {}
        with st.expander(f"🌍 부지 가중요인 (F_site·잔류) — {source_label}"):
            st.markdown(
                f"**최종 F_site: {info['f_site']:.2f}** · "
                f"세척효율 약화 잔류: 비계절 +{resid.get('nonseasonal', 0):.2f}, "
                f"봄철 +{resid.get('spring', 0):.2f} (base 0.15와 합산, 상한 0.60)"
            )

            level_kr = {"low": "저", "mid": "중", "high": "고"}
            breakdown_data = []
            for b in info.get("breakdown", []):
                res_txt = []
                if b.get("res_nonseasonal"):
                    res_txt.append(f"잔류+{b['res_nonseasonal']:.2f}")
                if b.get("res_spring"):
                    res_txt.append(f"봄철잔류+{b['res_spring']:.2f}")
                breakdown_data.append({
                    "부지요인": b["label"],
                    "강도": level_kr.get(b["level"], b["level"]),
                    "F_site 증분": f"+{b['increment']:.2f}",
                    "잔류 증분": ", ".join(res_txt) or "-",
                    "출처": b.get("source", "-"),
                })
            if breakdown_data:
                breakdown_df = pd.DataFrame(breakdown_data)
                st.dataframe(breakdown_df, use_container_width=True, hide_index=True)
            else:
                st.caption("해당 부지 가중요인 없음 → 일반 지역(F_site=1.0)")

            st.caption(
                f"봄철 황사 판정: {level_kr.get(info.get('r6_dust_level'), '-')}, "
                f"강수세척 판정: {level_kr.get(info.get('r7_rainfall_level'), '-')} "
                "— 실측 PM·강수로 모델에 내재 반영 (F_site 별도 가산 없음)"
            )

else:
    with st.expander("📋 분석 리포트", expanded=True):
        st.markdown(result.report_markdown or "분석 결과가 없습니다.")

# ── Agent trace panel ──────────────────────────────────────────────────────────

if st.session_state.get("last_is_llm", False) and hasattr(result, "trace"):
    _render_trace(result.trace)
