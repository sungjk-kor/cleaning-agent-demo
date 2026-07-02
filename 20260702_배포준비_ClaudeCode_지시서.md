# cleaning-agent-demo 배포 준비 작업 지시서 (Claude Code용)

작성일 2026-07-02 · 대상 저장소: `github.com/sungjk-kor/cleaning-agent-demo`

## 이 지시서 사용법
Claude Code(터미널/데스크톱)에서 이 저장소를 연 뒤, 아래처럼 지시하면 됩니다.
> "저장소 루트의 `20260702_배포준비_ClaudeCode_지시서.md` 의 지시대로 순서대로 진행하고, 마지막에 완료 리포트를 작성해줘."

또는 이 파일 내용을 그대로 복사해 Claude Code 프롬프트에 붙여넣어도 됩니다.
(전제: 저장소가 로컬에 clone 되어 있고, `git push` 권한이 있음)

---

## 목표
이 앱(태양광 소일링 시뮬레이터 + LLM 해설)을 **Streamlit Community Cloud**에 **실제 공개 데이터로 구동**되도록 배포 준비한다.
- 고객은 앱을 배포받지 않는다. **사장(운영자)이 단일 인스턴스를 호스팅**한다.
- 고객·앱은 KMA/에어코리아에서 데이터를 실시간으로 받지 않는다. **운영자가 미리 수집해 `data/`에 커밋한 파일**로만 동작한다.
- 사용하는 PM·강수·일사 데이터는 이미 공개된 정부 데이터이므로 **공개 저장소에 커밋해도 된다.**

---

## ⚠️ 반드시 지킬 제약 (DO NOT)
이 저장소의 소일링 모델은 이미 엄격히 검증되었다(엑셀 14,261식 검증·유닛테스트 통과·"목표역산 금지" 원칙 확립). **배포 준비만 하고 계산 로직은 건드리지 말 것.**

1. `core/soiling_semiphysical.py` 의 계산 로직·`CONFIG` 상수·강우사건/단계세척/잔류 로직을 **변경하지 말 것.**
2. `DEPO_CAL` 등 목표값에 맞추는 보정계수를 **다시 도입하지 말 것**(현재 `depo_cal=1.0` 유지).
3. `test_soiling_events.py` 유닛테스트가 **계속 전부 통과**해야 한다. 로직 변경 없이 통과 상태를 유지할 것.
4. `README.md` / `CHANGELOG.md` 의 검증·정직성 관련 문구를 훼손하지 말 것.
5. 확신이 안 서는 로직 변경이 필요하면, 바꾸지 말고 **완료 리포트에 질문으로 남길 것.**

---

## 작업 목록 (순서대로 수행)

### 1. Anthropic API 키 보안 점검·수정
- [ ] 저장소 전체를 검색해 API 키가 하드코딩되어 있는지 확인한다. (`sk-ant-` 문자열, `api_key="..."` 직접 대입 등)
- [ ] Anthropic 클라이언트의 키 로딩을 아래로 통일한다. 코드에 키 문자열이 남지 않게 한다.
  ```python
  import os
  import streamlit as st
  import anthropic

  api_key = st.secrets.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
  client = anthropic.Anthropic(api_key=api_key)
  ```
- [ ] `.gitignore` 에 `.streamlit/secrets.toml` 이 포함되어 로컬 비밀값이 커밋되지 않도록 한다.
- [ ] **(검증)** `git grep -nE "sk-ant-|ANTHROPIC_API_KEY\s*=\s*[\"']sk"` 결과가 **0건**임을 확인한다.
- [ ] 만약 과거 커밋 이력에 실제 키가 들어간 흔적이 있으면, **리포트에 명시**한다. (근본 해결은 사장이 콘솔에서 해당 키를 폐기·재발급하는 것. 이력 청소는 부차적.)

### 2. `data/` 폴더 커밋 → 실데이터로 구동
- [ ] 먼저 `data/` 안에 비밀값·고객정보 등 민감정보가 없는지 확인한다(공개 정부 데이터만 있어야 함). 있으면 커밋하지 말고 리포트에 알린다.
- [ ] `.gitignore` 에서 `data/` (및 관련 데이터 파일 패턴) 제외 규칙을 제거한다.
- [ ] `data/` 파일을 `git add` 한다. 각 파일 크기를 확인한다.
  - 50MB 초과 파일이 있으면 리포트에 목록을 남긴다(사장 판단 필요).
  - 100MB 초과 파일은 GitHub가 거부하므로, 필요한 도시·연도만 남기거나 Git LFS 사용을 제안한다.
- [ ] 데이터 로딩 경로를 점검한다. 데모데이터로 폴백하는 분기(플래그/파일존재 체크)가 있으면, **커밋된 실데이터를 우선 사용**하도록 확인·수정한다.
- [ ] 런타임에 KMA/에어코리아 API를 **실시간 호출**하는 코드가 앱 실행 경로에 있으면, 배포 앱은 **커밋된 `data/` 파일만으로 동작**하도록 정리한다. (실시간 수집이 필요하면 운영자용 오프라인 스크립트/관리자 경로로만 두고, 일반 앱 실행에는 KMA 키가 필요 없게 한다.)
- [ ] **(검증)** 로컬에서 앱을 실행해 데모데이터가 아니라 **서산/당진 실데이터 결과**가 나오는지 확인한다.

### 3. `requirements.txt` 슬림화
- [ ] `app_streamlit.py` 와 `core/*.py` 가 실제로 `import` 하는 패키지만 남긴다. 사용하지 않는 무거운 패키지(학습용 딥러닝 등 실행에 불필요한 것)를 제거한다.
- [ ] 버전을 고정한다. Streamlit 호환 제약(예: `protobuf>=3.20,<6`)을 지킨다.
- [ ] **(검증)** 깨끗한 가상환경에서 `pip install -r requirements.txt` 후 `streamlit run app_streamlit.py` 가 import 에러 없이 뜨는지 확인한다.

### 4. 비용 방어 코드 (계산은 항상 / Claude는 버튼+한도)
- [ ] '계산'(순수 파이썬)은 버튼 없이 항상 실행되고, **Claude 호출은 'AI 분석 받기' 버튼 클릭 시에만** 일어나도록 확인·수정한다. (시뮬레이션·페이지 로드 시 자동 호출 금지)
- [ ] 세션 기준 하루 호출 한도(기본 `DAILY_LIMIT = 5`)를 `st.session_state` + 날짜로 추가한다. 한도 초과 시 안내 메시지 표시.
- [ ] 기본 해설 모델은 `claude-haiku-4-5-20251001`(가장 저렴), `max_tokens=600` 등 상한을 둔다.
- [ ] **(선택)** 공개 데모용 간단 비밀번호 게이트를 `st.secrets.get("APP_PASSWORD")` 기반으로 추가하되, 비밀번호가 설정되지 않았으면 게이트를 건너뛰도록 한다. (사장이 'Private 앱'을 쓸지 '비번 게이트'를 쓸지 이후 선택)
- [ ] **(검증)** 버튼을 누르지 않고 계산만 했을 때 Claude 호출이 **0건**, 버튼을 6번째 눌렀을 때 차단되는지 확인한다.

### 5. Streamlit 배포 설정
- [ ] `.streamlit/config.toml` 을 확인/작성한다.
  ```toml
  [server]
  headless = true

  [theme]
  base = "light"
  # primaryColor 등은 홈페이지 색과 맞추고 싶으면 이후 지정
  ```
- [ ] **주의:** 이 앱은 서브경로(`/soiling`) 배포가 아니라 Community Cloud 단독 배포이므로, `baseUrlPath` 는 넣지 말 것. `config.toml` 에 비밀값을 넣지 말 것.

### 6. 커밋·푸시
- [ ] 의미 단위로 나눠 커밋한다. 예)
  - `security: load Anthropic key from secrets/env, stop hardcoding`
  - `data: commit public PM/ASOS data so live runs on real data`
  - `deps: slim requirements.txt for deployment`
  - `feat: gate Claude calls behind button + per-session daily limit`
- [ ] `main` 브랜치에 푸시한다. (실제 배포 트리거는 사장이 share.streamlit.io에서 수동 수행)

---

## 작업 후, 사람이 직접 해야 하는 일 (코드로는 불가 — 리포트에 안내로 포함)
1. `share.streamlit.io` → GitHub 로그인 → New app → `sungjk-kor/cleaning-agent-demo`, `main`, `app_streamlit.py` 로 배포.
2. Advanced settings → **Secrets** 에 입력:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-실제키"
   # 비번 게이트를 쓸 경우:
   APP_PASSWORD = "원하는-비밀번호"
   ```
3. **(권장)** 앱 Settings → Sharing → **Private** 로 설정하고 투자자·실증파트너 이메일(Google) 초대. → 익명 사용자의 API 비용 차단.
4. Anthropic 콘솔: **선불 크레딧 충전 + auto-reload OFF**(물리적 상한) + Settings → Limits에서 **월 지출 한도** 설정. (이 시뮬레이터 전용 Workspace 권장)

---

## 완료 리포트에 포함할 것
- 변경한 파일 목록과 커밋 해시
- `data/` 로 커밋된 파일 개수·총 용량 (50MB 초과 파일이 있으면 목록)
- `requirements.txt` 정리 전/후 패키지 개수
- API 키 하드코딩 검색 결과(0건 확인) 및 과거 이력에 키 흔적 여부
- 로컬 `streamlit run` 실행 결과 + `test_soiling_events.py` 유닛테스트 통과 여부
- 앱이 데모데이터가 아니라 실데이터로 동작함을 확인한 근거(예: 화면 값/로그)
- 위 '사람이 직접 해야 하는 일' 체크리스트
- 로직 관련 판단이 필요해 보류한 사항이 있으면 질문 형태로 정리
