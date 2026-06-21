"""
pm_statistics.py - monthly PM statistics Excel loader.

The source files are AirKorea-style monthly Excel exports with a ``Data`` sheet
and columns such as 지역, 측정일시, PM10, PM25. The app treats the first token of
지역 as 지역명1 and the remaining text as 지역명2.
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
    """Return the PM statistics directory, optionally overridden by env/config."""
    configured = data_dir or os.environ.get(PM_STATS_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_PM_STATS_DIR


def list_pm_stat_files(data_dir: str | os.PathLike | None = None) -> tuple[Path, ...]:
    """Return all Excel files under the statistics directory."""
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
    """Infer available years from file names such as '2025년 1월.xlsx'."""
    years = set()
    for path in list_pm_stat_files(data_dir):
        match = re.search(r"(20\d{2})", path.name)
        if match:
            years.add(int(match.group(1)))
    return tuple(sorted(years))


def split_region(value: object) -> RegionPair | None:
    """Split a region label such as '충남 서산시' into 지역명1/지역명2."""
    text = " ".join(str(value or "").strip().split())
    if not text or text.lower() == "nan":
        return None
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return RegionPair(parts[0], parts[0])
    return RegionPair(parts[0], parts[1])


def list_region_pairs(data_dir: str | os.PathLike | None = None) -> tuple[RegionPair, ...]:
    """List selectable region pairs found in the statistics Excel files."""
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
        columns = _excel_columns(file_name)
        if {"지역명1", "지역명2"}.issubset(columns):
            df = pd.read_excel(file_name, sheet_name=0, usecols=["지역명1", "지역명2"])
            for region1, region2 in df.dropna(how="all").drop_duplicates().itertuples(index=False):
                if str(region1).strip() and str(region2).strip():
                    pairs.add(RegionPair(str(region1).strip(), str(region2).strip()))
        elif "지역" in columns:
            df = pd.read_excel(file_name, sheet_name=0, usecols=["지역"])
            for value in df["지역"].dropna().unique():
                pair = split_region(value)
                if pair:
                    pairs.add(pair)
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
    frames = []

    for file_name in file_names:
        columns = _excel_columns(file_name)
        region_columns = _region_columns(columns)
        required = set(region_columns) | {"측정일시", "PM10", "PM25"}
        if not required.issubset(columns):
            continue

        df = pd.read_excel(file_name, sheet_name=0, usecols=list(required))
        if df.empty:
            continue

        mask = _region_mask(df, region_columns, region1, region2)
        df = df.loc[mask].copy()
        if df.empty:
            continue

        df["date"] = df["측정일시"].map(_measurement_date)
        df = df[df["date"].notna()]
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if df.empty:
            continue

        df["PM10"] = pd.to_numeric(df["PM10"], errors="coerce")
        df["PM25"] = pd.to_numeric(df["PM25"], errors="coerce")
        df["valid_pm"] = df[["PM10", "PM25"]].notna().any(axis=1)
        frames.append(df[["date", "PM10", "PM25", "valid_pm"]])

    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True)
    grouped = combined.groupby("date", sort=True).agg(
        pm10=("PM10", "mean"),
        pm25=("PM25", "mean"),
        count=("valid_pm", "sum"),
    )

    result: dict[date, dict] = {}
    for day, row in grouped.iterrows():
        result[day] = {
            "pm10": None if pd.isna(row["pm10"]) else float(row["pm10"]),
            "pm25": None if pd.isna(row["pm25"]) else float(row["pm25"]),
            "count": int(row["count"]),
        }
    return result


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
            pair
            and pair.region1 == region1
            and pair.region2 in region2_candidates
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
