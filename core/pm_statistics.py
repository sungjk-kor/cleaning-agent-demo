# -*- coding: utf-8 -*-
"""
pm_statistics.py - monthly PM statistics Excel loader with parquet cache.

Source files are AirKorea-style monthly Excel exports with a ``Data`` sheet
and columns: 지역, 측정일시 (YYYYMMDDHH), PM10, PM25.

On first access the Excel files are slow to load (488K rows/file).
Call ``precompute_pm_cache()`` once to build fast parquet caches under
``<pm_stats_dir>/_cache/``. Subsequent loads are near-instant.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_PM_STATS_DIR = Path(__file__).resolve().parents[1] / "data" / "pm_stats"
PM_STATS_ENV = "PM_STATS_DIR"


@dataclass(frozen=True)
class RegionPair:
    region1: str
    region2: str

    @property
    def label(self) -> str:
        return f"{self.region1}-{self.region2}"

    @property
    def region_name(self) -> str:
        return f"{self.region1} {self.region2}".strip()


def pm_stats_dir(data_dir: str | os.PathLike | None = None) -> Path:
    configured = data_dir or os.environ.get(PM_STATS_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_PM_STATS_DIR


def list_pm_stat_files(data_dir: str | os.PathLike | None = None) -> tuple[Path, ...]:
    root = pm_stats_dir(data_dir)
    if not root.exists():
        return ()
    return tuple(
        sorted(
            path
            for path in root.rglob("*.xlsx")
            if path.is_file() and not path.name.startswith("~$")
        )
    )


def available_years(data_dir: str | os.PathLike | None = None) -> tuple[int, ...]:
    years = set()
    for path in list_pm_stat_files(data_dir):
        match = re.search(r"(20\d{2})", path.name)
        if match:
            years.add(int(match.group(1)))
    return tuple(sorted(years))


def split_region(value: object) -> RegionPair | None:
    text = " ".join(str(value or "").strip().split())
    if not text or text.lower() == "nan":
        return None
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return RegionPair(parts[0], parts[0])
    return RegionPair(parts[0], parts[1])


def list_region_pairs(data_dir: str | os.PathLike | None = None) -> tuple[RegionPair, ...]:
    files = list_pm_stat_files(data_dir)
    return _list_region_pairs_cached(_cache_key(files), tuple(str(p) for p in files))


def load_daily_pm_statistics(
    region1: str,
    region2: str,
    start: date,
    end: date,
    data_dir: str | os.PathLike | None = None,
) -> dict[date, dict]:
    """
    Load PM10/PM2.5 daily averages for a selected region pair.

    Uses parquet cache when available (see ``precompute_pm_cache``).
    Multiple stations inside the same 지역 are averaged together by day.
    """
    files = _files_for_period(list_pm_stat_files(data_dir), start.year, end.year)
    return _load_daily_pm_statistics_cached(
        _cache_key(files),
        tuple(str(p) for p in files),
        str(region1).strip(),
        str(region2).strip(),
        start.isoformat(),
        end.isoformat(),
    )


# ── Parquet cache helpers ────────────────────────────────────────────────────

def _parquet_cache_path(excel_path: Path) -> Path:
    return excel_path.parent / "_cache" / (excel_path.stem + ".parquet")


def _cache_is_fresh(excel_path: Path, cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    return cache_path.stat().st_mtime >= excel_path.stat().st_mtime


def _build_daily_region_cache(excel_path: Path) -> pd.DataFrame:
    """
    Read one monthly Excel file and aggregate to daily PM averages per region.
    Returns DataFrame with columns: region1, region2, date_str, pm10, pm25, count.
    """
    df = pd.read_excel(
        excel_path,
        sheet_name=0,
        usecols=["지역", "측정일시", "PM10", "PM25"],
    )

    # Split "충남 서산시" → region1="충남", region2="서산시"
    region_split = df["지역"].astype(str).str.strip().str.split(n=1, expand=True)
    df["region1"] = region_split[0].fillna("")
    df["region2"] = region_split[1].fillna("") if region_split.shape[1] > 1 else region_split[0].fillna("")

    # Parse YYYYMMDDHH → date string YYYY-MM-DD
    df["date_str"] = df["측정일시"].map(_measurement_date_str)
    df = df.dropna(subset=["date_str"])

    df["PM10"] = pd.to_numeric(df["PM10"], errors="coerce")
    df["PM25"] = pd.to_numeric(df["PM25"], errors="coerce")

    agg = (
        df.groupby(["region1", "region2", "date_str"])
        .agg(pm10=("PM10", "mean"), pm25=("PM25", "mean"), count=("PM10", "count"))
        .reset_index()
    )
    return agg


def _load_or_build_cache(excel_path: Path, build_if_missing: bool = False) -> pd.DataFrame | None:
    """Return the pre-aggregated daily cache DataFrame.

    If the parquet cache exists and is fresh, loads it instantly.
    If ``build_if_missing`` is True, builds the cache from Excel when missing.
    Otherwise returns None so the caller can fall back to demo data.
    """
    cache_path = _parquet_cache_path(excel_path)
    if _cache_is_fresh(excel_path, cache_path):
        return pd.read_parquet(cache_path)
    if not build_if_missing:
        return None
    df = _build_daily_region_cache(excel_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    return df


def pm_cache_status(data_dir: str | os.PathLike | None = None) -> tuple[int, int]:
    """Return (cached_count, total_count) for display in the UI."""
    files = list_pm_stat_files(data_dir)
    cached = sum(
        1 for f in files if _cache_is_fresh(f, _parquet_cache_path(f))
    )
    return cached, len(files)


def precompute_pm_cache(
    data_dir: str | os.PathLike | None = None,
    progress_callback=None,
) -> int:
    """
    Build parquet caches for all Excel files that don't have a fresh cache.

    ``progress_callback(done, total)`` is called after each file if provided.
    Returns the number of files rebuilt.
    """
    files = list_pm_stat_files(data_dir)
    rebuilt = 0
    for i, excel_path in enumerate(files):
        cache_path = _parquet_cache_path(excel_path)
        if not _cache_is_fresh(excel_path, cache_path):
            _load_or_build_cache(excel_path, build_if_missing=True)
            rebuilt += 1
        if progress_callback:
            progress_callback(i + 1, len(files))
    # Invalidate lru_cache so subsequent loads use the new parquet files
    _list_region_pairs_cached.cache_clear()
    _load_daily_pm_statistics_cached.cache_clear()
    return rebuilt


# ── Internal helpers ─────────────────────────────────────────────────────────

def _cache_key(files: Iterable[Path]) -> str:
    parts = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts)


def _files_for_period(files: tuple[Path, ...], start_year: int, end_year: int) -> tuple[Path, ...]:
    selected = []
    for path in files:
        match = re.search(r"(20\d{2})", path.name)
        if not match:
            selected.append(path)
            continue
        year = int(match.group(1))
        if start_year <= year <= end_year:
            selected.append(path)
    return tuple(selected)


@lru_cache(maxsize=16)
def _list_region_pairs_cached(cache_key: str, file_names: tuple[str, ...]) -> tuple[RegionPair, ...]:
    del cache_key
    pairs: set[RegionPair] = set()
    for file_name in file_names:
        excel_path = Path(file_name)
        cache_path = _parquet_cache_path(excel_path)
        try:
            if _cache_is_fresh(excel_path, cache_path):
                df = pd.read_parquet(cache_path, columns=["region1", "region2"])
                for r1, r2 in df.drop_duplicates().itertuples(index=False):
                    if str(r1).strip() and str(r2).strip():
                        pairs.add(RegionPair(str(r1).strip(), str(r2).strip()))
                continue
        except Exception:
            pass

        # No parquet cache yet — skip this file to avoid slow Excel read.
        # Run precompute_pm_cache() to populate caches for all files.

    return tuple(sorted(pairs, key=lambda item: (item.region1, item.region2)))


@lru_cache(maxsize=32)
def _load_daily_pm_statistics_cached(
    cache_key: str,
    file_names: tuple[str, ...],
    region1: str,
    region2: str,
    start_iso: str,
    end_iso: str,
) -> dict[date, dict]:
    del cache_key
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    region2_candidates = _region2_candidates(region2)
    frames = []

    for file_name in file_names:
        excel_path = Path(file_name)
        try:
            # build_if_missing=False: skip uncached files during analysis.
            # Use precompute_pm_cache() to build caches explicitly.
            df = _load_or_build_cache(excel_path, build_if_missing=False)
            if df is None:
                continue  # no cache yet — fall through to demo data
        except Exception:
            df = _fallback_read_excel(file_name, region1, region2, start, end)
            if df is not None and not df.empty:
                frames.append(df)
            continue

        # Filter by region
        mask = df["region1"].eq(region1) & df["region2"].isin(region2_candidates)
        df = df.loc[mask].copy()
        if df.empty:
            continue

        # Filter by date range
        df["_date"] = pd.to_datetime(df["date_str"]).dt.date
        df = df[(df["_date"] >= start) & (df["_date"] <= end)]
        if df.empty:
            continue

        frames.append(df[["_date", "pm10", "pm25", "count"]].rename(columns={"_date": "date"}))

    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True)
    grouped = combined.groupby("date", sort=True).agg(
        pm10=("pm10", "mean"),
        pm25=("pm25", "mean"),
        count=("count", "sum"),
    )

    result: dict[date, dict] = {}
    for day, row in grouped.iterrows():
        result[day] = {
            "pm10": None if pd.isna(row["pm10"]) else float(row["pm10"]),
            "pm25": None if pd.isna(row["pm25"]) else float(row["pm25"]),
            "count": int(row["count"]),
        }
    return result


def _fallback_read_excel(
    file_name: str,
    region1: str,
    region2: str,
    start: date,
    end: date,
) -> pd.DataFrame | None:
    """Direct Excel read as a fallback when cache build fails."""
    try:
        columns = _excel_columns(file_name)
        region_columns = _region_columns(columns)
        required = set(region_columns) | {"측정일시", "PM10", "PM25"}
        if not required.issubset(columns):
            return None
        df = pd.read_excel(file_name, sheet_name=0, usecols=list(required))
        if df.empty:
            return None
        mask = _region_mask(df, region_columns, region1, region2)
        df = df.loc[mask].copy()
        if df.empty:
            return None
        df["date"] = df["측정일시"].map(_measurement_date)
        df = df[df["date"].notna()]
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if df.empty:
            return None
        df["PM10"] = pd.to_numeric(df["PM10"], errors="coerce")
        df["PM25"] = pd.to_numeric(df["PM25"], errors="coerce")
        return df.groupby("date").agg(
            pm10=("PM10", "mean"), pm25=("PM25", "mean"), count=("PM10", "count")
        ).reset_index()
    except Exception:
        return None


def _excel_columns(file_name: str) -> set[str]:
    df = pd.read_excel(file_name, sheet_name=0, nrows=0)
    return {str(col).strip() for col in df.columns}


def _region_columns(columns: set[str]) -> list[str]:
    if {"지역명1", "지역명2"}.issubset(columns):
        return ["지역명1", "지역명2"]
    return ["지역"]


def _region_mask(df: pd.DataFrame, region_columns: list[str], region1: str, region2: str) -> pd.Series:
    if region_columns == ["지역명1", "지역명2"]:
        return (
            df["지역명1"].astype(str).str.strip().eq(region1)
            & df["지역명2"].astype(str).str.strip().eq(region2)
        )
    pairs = df["지역"].map(split_region)
    region2_candidates = _region2_candidates(region2)
    return pairs.map(
        lambda pair: bool(
            pair and pair.region1 == region1 and pair.region2 in region2_candidates
        )
    )


def _region2_candidates(region2: str) -> set[str]:
    text = str(region2 or "").strip()
    candidates = {text}
    if text.endswith(("시", "군", "구")):
        candidates.add(text[:-1])
    else:
        candidates.update({f"{text}시", f"{text}군", f"{text}구"})
    return {item for item in candidates if item}


def _measurement_date(value: object) -> date | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _measurement_date_str(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        return None
    try:
        d = date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
        return d.isoformat()
    except ValueError:
        return None
