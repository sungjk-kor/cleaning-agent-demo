# 청소 판단 에이전트 데모

태양광 설비 지점의 강수와 미세먼지 데이터를 바탕으로 모듈 오염 누적, 세척 우선순위, 발전량 손실, LCOE 영향을 계산하는 Streamlit 웹앱입니다.

## 구조

```text
core/
  lcoe.py
  kma_weather.py
  airkorea_pm.py
  pollution_model.py
  agent.py
app_streamlit.py
app_fastapi.py
```

`core`는 배포 방식과 무관한 순수 로직입니다. Streamlit과 FastAPI는 `core.agent`만 import합니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app_streamlit.py
```

API 키 없이도 데모 데이터로 실행됩니다. 실 API를 쓰려면 `.env`를 만들고 아래 값을 넣습니다.

```bash
KMA_API_KEY=...
AIRKOREA_API_KEY=...
```

FastAPI 실행:

```bash
uvicorn app_fastapi:app --reload
```

## 데이터

- 기상: `core/kma_weather.py`의 KMA 지상관측 조회 함수 사용
- 미세먼지: `core/airkorea_pm.py`의 AirKorea 시도별 PM10/PM2.5 조회 함수 사용
- 경제성: `core/lcoe.py`의 LCOE 계산 함수 사용

현재 오염 모델은 공개 데모용 설명 가능 휴리스틱입니다. 현장 발전량, 실측 오염도, 실제 세척 이력 데이터가 확보되면 `core/pollution_model.py`만 교체하거나 보정하면 됩니다.

