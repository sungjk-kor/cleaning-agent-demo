# Changelog

이 프로젝트의 주요 변경 이력입니다. 형식은 [Keep a Changelog](https://keepachangelog.com/) 관례를 따릅니다.

## [Unreleased] — 2026-07-02

### 강우사건(R_e) 기반 세척 모델 + 완화~보수 range

세척을 R0 단일임계 부분세척에서 **강우사건(R_e) 단계세척**으로 교체하고, 결과를
**완화~보수 두 시나리오 range**로 표출합니다. 검증 정답지:
`20260701_소일링_세척모델_검증.xlsx`(설정/일별_보수 시트). 숫자 하드코딩·목표역산 없음,
`DEPO_CAL=1.0` 유지.

**Changed**
- `core/soiling_semiphysical.py`
  - 강우사건 감지: 시간강수에서 무강우 gap(기본 6h)↑이면 다음 사건. `R_e`=사건 강수합.
  - 단계세척: `R_e<10mm→0.05`, `10~20mm→완화 0.55/보수 0.05`, `≥20mm→0.85`.
  - 잔류 약화: `η_eff=η_tier·(1−residual)`, `residual=min(0.60, 0.15+비계절+봄철)`. 완전초기화 금지.
  - 저강수일(일<5mm) 소일링 +20% 가중. 손실은 세척 직전 질량 `SL=1−exp(−κ·M_before^γ)`.
  - `run_semiphysical_model(scenario=…)` + `run_soiling_scenarios()`(완화·보수 동시) 추가.
    반환 필드 확장: p95, 봄철평균/피크, 강우사건 수, 유효세척 횟수, 최대 무세척일수·시기.
  - `fsite_from_characteristics` → `(f_site, residual_info, breakdown[출처포함])`. 부지 가중요인에
    industrial/coastal(염분)/agricultural(꽃가루)/organic/traffic/tilt + **bird(철새도래지)** 추가,
    각 항목에 (F_site 증분, 잔류 증분, 출처) 부여.
- `core/pollution_model.py` — 4층위 range 로직 제거, `simulate_cleaning_decision`에
  `residual_info`·`scenario`(기본 보수) 추가. 대표값(LCOE·우선순위)은 보수 시나리오 기준.
- `core/agent.py` — `AgentRequest.residual_info` 추가, `run_soiling_scenarios`로 range 산출,
  `build_report`가 완화~보수+봄철피크 렌더.
- `core/agent_llm.py` — 시스템 프롬프트를 강우사건/완화·보수 range로 개편 + **'기상데이터 분석'
  필수 섹션**(강우사건 수·유효세척·무세척 최장기간/시기·PM 계절성·부지 가중요인·장기평균 caveat).
  `evaluate_regional_characteristics`에 bird 판정 추가.
- `app_streamlit.py` — 사이드바 '철새도래지 인접(천수만 등)' 체크박스, 헤드라인
  "연 완화~보수%, 봄철 피크 X%", 완화/보수 audit 표 + 기상분석 지표, 산출식 설명 갱신.

**검증 (서산 2025, F_site=1)** — 완화 0.42% < 보수 0.51%(연평균), 봄철(0.65%)>연평균,
보수 유효세척(25회)<완화(36회), 강우사건 96회. 부지가중(산업+철새+해안, F=2.7·잔류0.2)
적용 시 연 1.4~1.7%·봄철 피크 ~5.0%. 모두 모델 산출값(단일연도, 미보정 시나리오).

---

## [Unreleased] — 2026-07-01 (2차)

### 점추정 → 4층위 시나리오 range 전환 (목표-역산 제거)

DEPO_CAL=14로 일반 지역을 IEA 3~5%에 맞추던 방식(순환논증)을 제거하고, 가정을 명시한
누적 range로 표출하도록 전환했습니다. 숫자는 모두 모델 산출값(하드코딩 없음).

**Changed**
- `core/soiling_semiphysical.py` — `DEPO_CAL` 14.0 → **1.0(미보정, 학술 퇴적속도 그대로)**.
  기본 하한은 F_site=1·미보정이어야 한다는 원칙. 목표 %에 맞춘 역산 삭제.
- `FSITE_INCREMENTS`에 `organic`(생물오염: 새분비물/꽃가루/조류) 추가 →
  `fsite_from_characteristics`·사이드바 R6 체크박스·LLM 도구 연동.
- README/CHANGELOG에서 "validated at 3–5%"·"IEA 세계평균 안착"·"not curve-fitting to a
  target"·"DEPO_CAL=14 검증" 문구 제거 → **field-calibration pending**으로 대체.

**Added**
- `core/pollution_model.py` — `estimate_soiling_range()`: 4층위 시나리오 산출.
  - **Tier0 하한**: F_site=1, 실측강수, 미보정. pvlib HSU(peer-reviewed) 교차검증 병기.
  - **Tier1 국지 오염원**: 산업·생물오염 반영으로 F_site 상향(사이드바 값 우선, 없으면 2.0).
  - **Tier2 건조기 심화**: 유효강수(R≥R0) 하위 40%를 무강수로 치환한 건조 시나리오.
  - **Tier3 일사량 우수**: 오염기 일사량이 연평균 대비 우수하면 발전손실을 일사 가중(ASOS
    일사 MJ/m²), 아니면 미반영.
- `core/asos_rainfall.py` — ASOS 일사량(일별 MJ/m²) 로더 3종 추가.
- `app_streamlit.py` — 헤드라인을 단일값 대신 **range(N.N~M.M%)** + 4층위 audit 표(가정·출처
  상시 노출) + 상한 "실측 소일링 센서 보정 전 시나리오값" 라벨.
- `core/agent_llm.py` — 시스템 프롬프트를 하한→국지오염→건조기→일사량 순으로 손실 상승
  인과를 설명하도록 개편. `run_pollution_model` 결과에 4층위 range 포함.

**서산 2025 하한**: 대기 PM 기준(F_site=1, 미보정) 연 1% 미만 — 검토요약 baseline과 정합.

---

## [Unreleased] — 2026-07-01

### 소일링 모델 교체: 반물리 5단계 모델 (IEA 보고서 기반)

기존 소일링 계산을 `soiling_effect_4reports_model.py`(첨부 "4개 보고서 요약" PDF 근거
반물리 모델)를 바탕으로 전면 교체했습니다.

**변경 이유**
- 기존 HSU(pvlib) 모델은 서산 2025 기준 연손실 0.3~1.7%로, IEA PVPS 보고서가 제시한
  세계평균 3~5%(산업·건조 지역 최대 10%)에 크게 미달.
- 원인: pvlib 학술 퇴적속도가 국내 실측 소일링 발생률보다 낮고, 누적먼지→손실을
  변환하는 κ(비선형) 항이 없었음.

**Added**
- `core/soiling_semiphysical.py` — IEA PVPS / Coello-Boyle 계열 반물리 5단계 모델.
  1) PM 분리 → 2) 일 퇴적(Δm=0.0864·cosθ·(v_f·PM2.5+v_c·PM_coarse)·F_site)
  → 3) 강우 세정(임계 R0=2.5mm, η_max=0.8) → 4) 누적/재비산 → 5) 비선형 손실
  (SL=1−exp(−κ·mᵞ), κ=0.0416). 지역계수 F_site 적용.
  ※ 이 시점의 DEPO_CAL=14(전역 14배)는 **2차 개정에서 1.0(미보정)으로 되돌림** — 아래 참고.
- `core/soiling_knowledge.py` — 참고 보고서 핵심 지식(LLM 설명용, 시스템 프롬프트 주입).
- `fsite_from_characteristics()` — 사이드바 지역특성(R1~R5) → F_site 가산 매핑.

**Changed**
- `core/pollution_model.py` — `simulate_cleaning_decision`가 HSU 대신 반물리 모델 사용.
  base(F_site=1)/regional(F_site 증가분) audit 분리 유지.
- `core/agent.py` — `AgentRequest.f_site` 추가, model_name="semiphysical".
- `core/agent_llm.py` — 시스템 프롬프트에 반물리 모델 설명 + 보고서 지식 주입,
  `evaluate_regional_characteristics`를 F_site 기반으로 전환(구 가산식 제거).
- `app_streamlit.py` — 산출식 표시를 반물리 5단계로 교체, 지역특성 F_site 연동·표시.

**주의 (2차 개정에서 철회됨):** 이 초기 표(일반 3.36% 등)는 DEPO_CAL=14로 전역 14배를
곱한 값으로, IEA 3~5%에 맞춘 목표-역산 결과였습니다. 순환논증이라 실사 방어가 어려워
2차 개정에서 DEPO_CAL=1.0(미보정)으로 되돌리고, 점추정 대신 가정을 명시한 4층위 시나리오
range로 표출하도록 변경했습니다. (위 "2차" 항목 참고)

**참고 보고서**
- IEA PVPS T13-21:2022, *Soiling Losses – Impact on the Performance of Photovoltaic
  Power Plants* (2022)
- *Systematic review of soiling mitigation strategies for solar photovoltaic panels* (2026)
- Coello & Boyle (2019), *IEEE J. Photovoltaics* 9(5):1382-1387

**보존 (재작성하지 않음)**
- `core/soiling_hsu.py`, `core/soiling_weights.py` — 기존 검증 자산으로 유지.

---

### 그 이전 작업 (같은 세션)
- ASOS 지상관측 시간 강수 통합(`core/asos_rainfall.py`, `core/asos_station_meta.py`):
  시간 단위 강수 시계열을 Haversine 최근접 지점으로 배정. 일별 집계 시 세정 미작동으로
  손실이 과대(7%)되던 버그 수정 → 시간 단위 정렬로 정상화.
