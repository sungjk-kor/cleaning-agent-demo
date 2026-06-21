from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from core.agent import AgentRequest, run_cleaning_agent
from core.lcoe import DEFAULT_INPUTS, LcoeInputs


app = FastAPI(title="Cleaning Decision Agent API")


class LcoeInputPayload(BaseModel):
    capacity: float = DEFAULT_INPUTS.capacity
    util_rate: float = DEFAULT_INPUTS.util_rate
    construct_cost: float = DEFAULT_INPUTS.construct_cost
    discount_rate: float = DEFAULT_INPUTS.discount_rate
    lifespan: int = DEFAULT_INPUTS.lifespan
    annual_om: float = DEFAULT_INPUTS.annual_om
    annual_land: float = DEFAULT_INPUTS.annual_land
    inflation: float = DEFAULT_INPUTS.inflation
    degradation: float = DEFAULT_INPUTS.degradation
    failure_rate_10: float = DEFAULT_INPUTS.failure_rate_10
    failure_rate_20: float = DEFAULT_INPUTS.failure_rate_20


class AgentPayload(BaseModel):
    region_name: str = "충남 서산시"
    region1: Optional[str] = "충남"
    region2: Optional[str] = "서산시"
    lat: Optional[float] = None
    lon: Optional[float] = None
    sido: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    end_year: Optional[int] = Field(default=None, ge=2000, le=2100)
    lookback_years: Optional[int] = Field(default=1, ge=1, le=5)
    lookback_days: int = Field(default=365, ge=1, le=3660)
    use_live_data: bool = False
    live_weather_days_limit: int = Field(default=5, ge=1, le=14)
    pm_stats_dir: Optional[str] = None
    top_n: int = Field(default=5, ge=1, le=20)
    lcoe_inputs: LcoeInputPayload = LcoeInputPayload()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/run")
def run_agent(payload: AgentPayload) -> dict:
    if hasattr(payload.lcoe_inputs, "model_dump"):
        lcoe_payload = payload.lcoe_inputs.model_dump()
    else:
        lcoe_payload = payload.lcoe_inputs.dict()
    lcoe_inputs = LcoeInputs(**lcoe_payload)
    region_name = payload.region_name
    if payload.region1 and payload.region2:
        region_name = f"{payload.region1} {payload.region2}".strip()
    end_date = payload.end_date
    if end_date is None and payload.end_year is not None:
        end_date = date(payload.end_year, 12, 31)
    req = AgentRequest(
        region_name=region_name,
        region1=payload.region1,
        region2=payload.region2,
        lat=payload.lat,
        lon=payload.lon,
        sido=payload.sido,
        start_date=payload.start_date,
        end_date=end_date,
        lookback_years=payload.lookback_years,
        lookback_days=payload.lookback_days,
        use_live_data=payload.use_live_data,
        live_weather_days_limit=payload.live_weather_days_limit,
        pm_stats_dir=payload.pm_stats_dir,
        top_n=payload.top_n,
        lcoe_inputs=lcoe_inputs,
    )
    return run_cleaning_agent(req).to_dict()
