"""
소일링(모듈 오염) 발전손실 계산 알고리즘 — 요약보고서 5단계 반물리 모델 구현
=============================================================================
1단계 PM 분리: PM_coarse = max(PM10 - PM2.5, 0)
2단계 일퇴적: Δm = 0.0864·cosθ·(v_f·PM2.5 + v_c·PM_coarse)·F_site   [g/m²/day]
3단계 강우세정(부분): η_rain = 0(R<R0) / η_max·[1-exp(-k_R·(R-R0))](R≥R0)
4단계 누적+재비산: m⁻ = max(0, m_{d-1} + Δm - ρ·m_{d-1}); m = m⁻·(1-η_rain)·(1-η_manual)
5단계 비선형 손실: SR = exp(-κ·m^γ),  SL = 1 - SR
연손실(일사가중): SL_ann = Σ(POA_d·SL_d) / Σ(POA_d) × 100

※ 절대값 확정에는 v_f, v_c, F_site, κ, γ, R0 를 실측 소일링 센서로 보정해야 함(보고서 명시).
   F_site=1·고정 파라미터일 때 결과는 '도시 간 상대 비교'에 가장 신뢰도가 높음.
"""
import numpy as np, pandas as pd

C_UNIT = 0.0864  # 86400 s/day × 1e-6 (μg→g)

PARAMS = dict(           # 고정(물리·부지) 파라미터
    tilt_deg=30.0, v_f=0.0009, v_c=0.004, F_site=1.0,
    rho=0.0, k_R=0.3, gamma=1.0, eta_manual=0.0,
)
SCEN = dict(             # 미확정 → 민감도 분석 대상
    eta_max=[0.7, 0.8, 0.9], R0=[1.0, 2.5, 5.0], kappa=[0.0416],  # κ=0.0416은 '상한 스트레스'값
)

def daily_deposition(pm25, pm10, P):
    coarse = np.maximum(pm10 - pm25, 0.0)
    return C_UNIT*np.cos(np.radians(P['tilt_deg']))*(P['v_f']*pm25 + P['v_c']*coarse)*P['F_site']

def eta_rain(R, R0, eta_max, k_R):
    return np.where(R < R0, 0.0, eta_max*(1.0 - np.exp(-k_R*np.maximum(R-R0, 0.0))))

def run_model(daily, P, eta_max, R0, kappa):
    """daily: index=날짜, columns=[pm25, pm10, rain, poa(optional)] → 일별 결과 DataFrame"""
    dep = daily_deposition(daily['pm25'].values, daily['pm10'].values, P)
    er  = eta_rain(daily['rain'].values, R0, eta_max, P['k_R'])
    n=len(daily); m=np.zeros(n); SL=np.zeros(n); prev=0.0
    for i in range(n):
        m_before = max(0.0, prev + dep[i] - P['rho']*prev)
        m_after  = m_before*(1.0-er[i])*(1.0-P['eta_manual'])
        SL[i]    = 1.0 - np.exp(-kappa*(m_before**P['gamma']))   # 당일 손실=세정 직전 질량 기준
        m[i]=m_after; prev=m_after
    out=daily.copy()
    out['dep_g']=dep; out['eta_rain']=er; out['mass_g']=m; out['SL']=SL
    return out

def annual_loss(out, poa_col='poa'):
    """일사 가중 연(기간)손실률 %. POA 없으면 단순평균."""
    if poa_col in out.columns and np.nansum(out[poa_col].values) > 0:
        w=np.nan_to_num(out[poa_col].values)
        return 100.0*np.sum(w*out['SL'].values)/np.sum(w)
    return 100.0*out['SL'].mean()

def energy_loss_kwh(out, P_DC_kw, PR0=0.8, GSTC=1.0):
    """경제적 손실 kWh = Σ E0_d·SL_d,  E0_d = P_DC·(POA/GSTC)·PR0"""
    E0 = P_DC_kw*(out['poa'].values/GSTC)*PR0
    return float(np.sum(E0*out['SL'].values))

# ── 데이터 로더(에어코리아 PM + ASOS 강수·일사 → 일자료) ──────────────
def load_city_daily(pm_xlsx, asos_csv, region, station, start, end):
    pm=pd.read_excel(pm_xlsx, sheet_name=0, usecols=['지역','측정일시','PM10','PM25'])
    pm=pm[pm['지역']==region].copy()
    pm['date']=pd.to_datetime(pm['측정일시'].astype(str).str[:8], format='%Y%m%d')
    pmd=pm.groupby('date')[['PM10','PM25']].mean().rename(columns={'PM10':'pm10','PM25':'pm25'})
    rn=pd.read_csv(asos_csv, encoding='cp949', usecols=['지점','일시','강수량(mm)','일사(MJ/m2)'])
    rn=rn[rn['지점']==station].copy(); rn['date']=pd.to_datetime(rn['일시']).dt.normalize()
    rn['rain']=pd.to_numeric(rn['강수량(mm)'],errors='coerce').fillna(0.0)   # 빈칸=무강수
    rn['sol'] =pd.to_numeric(rn['일사(MJ/m2)'],errors='coerce').fillna(0.0)  # 야간=0
    rnd=rn.groupby('date').agg(rain=('rain','sum'), poa=('sol','sum')); rnd['poa']/=3.6  # MJ→kWh/m²
    d=pmd.join(rnd, how='inner')
    return d[(d.index>=start)&(d.index<end)].dropna()

if __name__=="__main__":
    PM='/mnt/user-data/uploads/2025년_4월.xlsx'; AS='/mnt/user-data/uploads/OBS_ASOS_TIM_2025_all.csv'
    d=load_city_daily(PM,AS,'충남 당진시',129,'2025-04-01','2025-05-01')
    out=run_model(d, PARAMS, eta_max=0.8, R0=2.5, kappa=0.0416)
    print(f"[당진 4월] 일사가중 손실 {annual_loss(out):.2f}%  피크 {out['SL'].max()*100:.2f}%  누적먼지 {out['mass_g'].max():.3f}g/m²")
    print(f"[F_site=10 보정 시] {annual_loss(run_model(d, dict(PARAMS,F_site=10), 0.8, 2.5, 0.0416)):.2f}%")
