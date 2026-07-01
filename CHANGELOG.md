# Changelog

이 프로젝트의 주요 변경 이력입니다. 형식은 [Keep a Changelog](https://keepachangelog.com/) 관례를 따릅니다.

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
  (SL=1−exp(−κ·mᵞ), κ=0.0416). 전역 퇴적보정 DEPO_CAL=14 + 지역계수 F_site.
- `core/soiling_knowledge.py` — 참고 보고서 핵심 지식(LLM 설명용, 시스템 프롬프트 주입).
- `fsite_from_characteristics()` — 사이드바 지역특성(R1~R5) → F_site 가산 매핑.

**Changed**
- `core/pollution_model.py` — `simulate_cleaning_decision`가 HSU 대신 반물리 모델 사용.
  base(F_site=1)/regional(F_site 증가분) audit 분리 유지.
- `core/agent.py` — `AgentRequest.f_site` 추가, model_name="semiphysical".
- `core/agent_llm.py` — 시스템 프롬프트에 반물리 모델 설명 + 보고서 지식 주입,
  `evaluate_regional_characteristics`를 F_site 기반으로 전환(구 가산식 제거).
- `app_streamlit.py` — 산출식 표시를 반물리 5단계로 교체, 지역특성 F_site 연동·표시.

**검증 (서산 2025 실측 PM + ASOS 강수)**

| 지역유형 | F_site | 연손실 | 피크 |
|---|---|---|---|
| 일반 | 1.0 | 3.36% | 13.0% |
| 산업단지 | 2.0 | 6.50% | 24.4% |
| 극심 오염원 | 3.0 | 9.45% | 34.2% |

→ IEA 세계평균 3~5%, 산업·건조 ~10% 범위에 부합. 강제 보정이 아니라 PDF 5단계 모델
그대로 + 퇴적속도 국내보정(DEPO_CAL=14)으로 자연 안착.

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
