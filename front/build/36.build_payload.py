# ============================================================================
# 36.build_payload.py
# ============================================================================
# Author:      yjkim
# Purpose:     정적 프론트엔드가 읽을 단지별 가격·환경 payload를 생성한다.
# Description: 런타임 서버 없이 index와 단지별 JSON을 API로 사용한다. 가격 source는
#              최근 동일 셀 실거래, 단지 평균, cold-start 모델, 임대 전용 제외를
#              서로 섞지 않고 분리한다. 일조·조망 지표는 환경 비교 전용으로만
#              보존하며 가격을 연결하는 파생 필드는 만들지 않는다.
# ============================================================================

from __future__ import annotations

import gzip
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"
public_data_dir = work_dir / "front" / "public" / "data"
complex_data_dir = public_data_dir / "complex"

COMPLEX_PATH = output_dir / "23.1.complex.txt"
METRICS_PATH = output_dir / "23.2.complex_metrics.txt"
PROFILE_PATH = output_dir / "23.3.horizon_profile.txt"
CELLS_PATH = output_dir / "32.1.price_cells.txt"
SERIES_PATH = output_dir / "32.2.price_series.txt"
ESTIMATES_PATH = output_dir / "33.1.coldstart_estimates.txt"
EXCLUDED_PATH = output_dir / "33.3.excluded_complexes.txt"
COMPARABLES_PATH = output_dir / "34.1.comparables.txt"
REGULATION_PATH = output_dir / "35.2.regulation_summary.json"
GEOCODED_PATH = output_dir / "14.1.geocoded_master.txt"
INDEX_PATH = public_data_dir / "index.json"

UNKNOWN_ITEMS = ["향", "호수", "실내 상태", "소음 실측", "실제 보행 경로"]
REGULATION_NOTE = "실거주 목적만 매수 가능, 2년 실거주 의무"
PRICE_SOURCES = {"CELL_LAST", "COMPLEX_MEAN", "MODEL", "EXCLUDED"}

# Payload numeric precision rules (schema values and null semantics are unchanged):
# - distances/heights/areas in metres: 1 decimal place;
# - latitude/longitude: 6 decimal places;
# - ratios, times, percentages, angles, and confidence: 2 decimal places;
# - prices in 만원 and counts: integers.
# Field names are deliberately explicit so a newly added numeric field cannot silently
# bypass the compact serialization rule.
COORDINATE_FIELDS = {"lat", "lng"}
DISTANCE_FIELDS = {
    "road_centerline_m", "road_arterial_dist_m", "road_secondary_dist_m",
    "rail_centerline_m", "station_dist_m", "elem_school_m", "mid_school_m",
    "high_school_m", "tertiary_hosp_m", "general_hosp_m", "mart_m",
    "dept_store_m", "supermarket_m", "park_m", "dist_m", "obs_height",
    "park_area_m2",
}
DECIMAL_FIELDS = {
    "sun_hours_avg", "sun_hours_best", "view_open_avg", "river_view_ratio",
    "station_walk_min_est", "station_congestion_peak", "sun_hours_winter",
    "sun_hours_spring", "view_block_pct", "open_angle_mean", "open_span_max",
    "est_confidence", "match_confidence",
}
PRICE_FIELDS = {
    "last_price_manwon", "mean_price_per_m2_24m", "est_price_per_m2",
    "est_low", "est_high", "median_price_per_m2", "price_manwon",
    "price_per_m2", "adj_price_per_m2",
}
COUNT_FIELDS = {
    "built_year", "households", "area_type", "floor_band", "repr_floor",
    "n_trades_24m", "station_ridership_daily", "clinic_1km", "pediatric_1km",
    "cvs_500m", "restaurant_500m", "nightlife_300m", "rank",
}
MAX_TOTAL_BYTES = int(84 * 1024 * 1024 * 0.60)
COMPLEX_SUFFIX = ".json.gz"


def require_unique(frame: pd.DataFrame, keys: list[str], label: str) -> pd.DataFrame:
    """입력 key 중복을 실패로 처리해 임의의 행 선택을 막는다."""
    if frame.duplicated(keys).any():
        raise ValueError(f"{label}: {'×'.join(keys)} key가 유일하지 않습니다.")
    return frame


def json_value(value: Any) -> Any:
    """pandas/NumPy 결측을 JSON null로, NumPy scalar를 표준 scalar로 바꾼다."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if pd.isna(value):
        return None
    return value


def numeric_kind(field: str) -> str | None:
    """Return the compact serialization rule for one numeric payload field."""
    if field in COORDINATE_FIELDS:
        return "coordinate"
    if field in DISTANCE_FIELDS:
        return "distance"
    if field in DECIMAL_FIELDS:
        return "decimal"
    if field in PRICE_FIELDS:
        return "price"
    if field in COUNT_FIELDS:
        return "count"
    return None


def payload_value(field: str, value: Any) -> Any:
    """Convert to a JSON scalar and apply the documented field-specific precision."""
    value = json_value(value)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    kind = numeric_kind(field)
    if kind is None:
        raise ValueError(f"숫자 payload 필드의 반올림 규칙이 없습니다: {field}")
    if kind == "coordinate":
        rounded = round(value, 6)
    elif kind == "distance":
        rounded = round(value, 1)
    elif kind == "decimal":
        rounded = round(value, 2)
    else:
        return int(round(value))
    return int(rounded) if float(rounded).is_integer() else rounded


def output_size_stats() -> dict[str, Any] | None:
    """Return current payload size statistics, if a complete prior output exists."""
    if not INDEX_PATH.exists() or not complex_data_dir.exists():
        return None
    files = sorted(complex_data_dir.glob("*.json*"))
    if not files:
        return None
    sizes = np.asarray([path.stat().st_size for path in files], dtype=np.int64)
    return {
        "total_bytes": INDEX_PATH.stat().st_size + int(sizes.sum()),
        "median_bytes": float(np.median(sizes)),
        "file_count": len(files),
    }


def month_string(value: Any) -> str | None:
    """YYYYMM 또는 YYYY-MM 입력을 API의 YYYY-MM 문자열로 통일한다."""
    value = json_value(value)
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 7 and text[4] == "-":
        return text
    try:
        yyyymm = str(int(float(text)))
    except ValueError as exc:
        raise ValueError(f"월 형식을 해석할 수 없습니다: {value!r}") from exc
    if len(yyyymm) != 6:
        raise ValueError(f"월 형식은 YYYYMM이어야 합니다: {value!r}")
    return f"{yyyymm[:4]}-{yyyymm[4:]}"


def dump_json(path: Path, payload: Any) -> None:
    """Serialize compact JSON, gzip-compressing only per-complex static assets."""
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
    else:
        path.write_bytes(encoded)


def load_json(path: Path) -> Any:
    """Read a regular JSON file or a deterministic gzip-compressed JSON asset."""
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(raw)


def complex_payload_path(apt_seq: str) -> Path:
    """Return the compressed static-asset path for one complex payload."""
    return complex_data_dir / f"{apt_seq}{COMPLEX_SUFFIX}"


def load_inputs() -> tuple[pd.DataFrame, ...]:
    """Task 4의 모든 입력을 읽고 조인 key의 유일성을 확인한다."""
    complex_df = require_unique(pd.read_csv(COMPLEX_PATH, sep="\t", low_memory=False), ["apt_seq"], "23.1.complex")
    metrics = require_unique(pd.read_csv(METRICS_PATH, sep="\t", low_memory=False), ["apt_seq"], "23.2.complex_metrics")
    profile = require_unique(pd.read_csv(PROFILE_PATH, sep="\t", low_memory=False), ["apt_seq", "floor_band"], "23.3.horizon_profile")
    cells = require_unique(pd.read_csv(CELLS_PATH, sep="\t", low_memory=False), ["apt_seq", "area_type", "floor_band"], "32.1.price_cells")
    series = pd.read_csv(SERIES_PATH, sep="\t", low_memory=False)
    estimates = require_unique(pd.read_csv(ESTIMATES_PATH, sep="\t", low_memory=False), ["apt_seq", "area_type", "floor_band"], "33.1.coldstart_estimates")
    excluded = require_unique(pd.read_csv(EXCLUDED_PATH, sep="\t", low_memory=False), ["apt_seq"], "33.3.excluded_complexes")
    comparables = pd.read_csv(COMPARABLES_PATH, sep="\t", low_memory=False)
    geocoded = require_unique(
        pd.read_csv(GEOCODED_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"}),
        ["apt_seq"], "14.1.geocoded_master",
    )

    frames = [complex_df, metrics, profile, cells, series, estimates, excluded, comparables, geocoded]
    for frame in frames:
        frame["apt_seq"] = frame["apt_seq"].astype(str)
    return complex_df, metrics, profile, cells, series, estimates, excluded, comparables, geocoded


def make_price_rows(cells: pd.DataFrame, estimates: pd.DataFrame,
                    excluded: pd.DataFrame) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """가격 source별 계약을 지키는 price 배열을 만들고 source 혼합을 거부한다."""
    by_apt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_keys: set[tuple[str, Any, Any]] = set()

    for row in cells.sort_values(["apt_seq", "area_type", "floor_band"], kind="stable").to_dict("records"):
        source = str(row["price_source"])
        if source not in {"CELL_LAST", "COMPLEX_MEAN"}:
            raise ValueError(f"32.1의 허용되지 않은 price_source: {source}")
        key = (str(row["apt_seq"]), json_value(row["area_type"]), json_value(row["floor_band"]))
        if key in seen_keys:
            raise ValueError(f"가격 셀 중복: {key}")
        seen_keys.add(key)
        item = {
            "area_type": payload_value("area_type", row["area_type"]),
            "floor_band": payload_value("floor_band", row["floor_band"]),
            "source": source,
            "n_trades_24m": payload_value("n_trades_24m", row["n_trades_24m"]),
        }
        if source == "CELL_LAST":
            item.update({
                "last_deal_ym": month_string(row["last_deal_ym"]),
                "last_price_manwon": payload_value("last_price_manwon", row["last_price_manwon"]),
            })
        else:
            item["mean_price_per_m2_24m"] = payload_value("mean_price_per_m2_24m", row["mean_price_per_m2_24m"])
        by_apt[str(row["apt_seq"])].append(item)

    for row in estimates.sort_values(["apt_seq", "area_type", "floor_band"], kind="stable").to_dict("records"):
        key = (str(row["apt_seq"]), json_value(row["area_type"]), json_value(row["floor_band"]))
        if key in seen_keys:
            raise ValueError(f"MODEL과 기존 가격 셀이 겹칩니다: {key}")
        seen_keys.add(key)
        by_apt[str(row["apt_seq"])].append({
            "area_type": payload_value("area_type", row["area_type"]),
            "floor_band": payload_value("floor_band", row["floor_band"]),
            "source": "MODEL",
            "n_trades_24m": None,
            "est_price_per_m2": payload_value("est_price_per_m2", row["est_price_per_m2"]),
            "est_low": payload_value("est_low", row["est_low"]),
            "est_high": payload_value("est_high", row["est_high"]),
            "est_confidence": payload_value("est_confidence", row["est_confidence"]),
        })

    excluded_map = excluded.set_index("apt_seq")["exclude_reason"].to_dict()
    for apt_seq, reason in excluded_map.items():
        if apt_seq in by_apt:
            raise ValueError(f"임대 전용 단지에 가격 행이 있습니다: {apt_seq}")
        by_apt[apt_seq] = [{
            "area_type": None,
            "floor_band": None,
            "source": "EXCLUDED",
            "n_trades_24m": None,
            "reason": json_value(reason),
        }]

    for apt_seq in by_apt:
        by_apt[apt_seq].sort(key=lambda item: (
            str(item["area_type"]), str(item["floor_band"]), item["source"]
        ))
    return by_apt, set(excluded_map)


def make_series(series: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """32.2의 관측 월만 포함한 면적타입별 최근 60개월 시계열을 만든다."""
    by_apt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (apt_seq, area_type), group in series.groupby(["apt_seq", "area_type"], sort=True, observed=True):
        ordered = group.sort_values("deal_ym", kind="stable")
        by_apt[str(apt_seq)].append({
            "area_type": payload_value("area_type", area_type),
            "points": [[month_string(row.deal_ym), payload_value("median_price_per_m2", row.median_price_per_m2)]
                       for row in ordered.itertuples(index=False)],
        })
    return by_apt


def make_comparables(comparables: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """원 거래와 보정 근거를 모두 보존한 비교사례 배열을 만든다."""
    by_apt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ordered = comparables.sort_values(["apt_seq", "area_type", "rank"], kind="stable")
    for row in ordered.to_dict("records"):
        by_apt[str(row["apt_seq"])].append({
            "area_type": payload_value("area_type", row["area_type"]),
            "target_source": json_value(row["target_source"]),
            "rank": payload_value("rank", row["rank"]),
            "comp_id": json_value(row["comp_apt_seq"]),
            "name": json_value(row["comp_name"]),
            "deal_ym": month_string(row["comp_deal_ym"]),
            "price_manwon": payload_value("price_manwon", row["comp_price_manwon"]),
            "price_per_m2": payload_value("price_per_m2", row["comp_price_per_m2"]),
            "adj_price_per_m2": payload_value("adj_price_per_m2", row["adj_price_per_m2"]),
            "adj_reason": json_value(row["adj_reason"]),
            "dist_m": payload_value("dist_m", row["dist_m"]),
        })
    return by_apt


def build_payloads(complex_df: pd.DataFrame, metrics: pd.DataFrame, profile: pd.DataFrame,
                   cells: pd.DataFrame, series: pd.DataFrame, estimates: pd.DataFrame,
                   excluded: pd.DataFrame, comparables: pd.DataFrame,
                   geocoded: pd.DataFrame, regulation: dict[str, Any]) -> tuple[Counter, list[str]]:
    """index와 단지별 static payload를 생성한다."""
    if set(metrics["apt_seq"]) != set(complex_df["apt_seq"]):
        raise ValueError("23.1과 23.2의 단지 universe가 다릅니다.")
    if not set(profile["apt_seq"]).issubset(set(complex_df["apt_seq"])):
        raise ValueError("23.3에 23.1에 없는 단지가 있습니다.")

    complex_df = complex_df.merge(
        metrics, on="apt_seq", how="left", validate="one_to_one", suffixes=("", "_metric")
    ).merge(
        geocoded[["apt_seq", "gu", "umd_name"]], on="apt_seq", how="left", validate="one_to_one"
    )
    price_by_apt, excluded_apts = make_price_rows(cells, estimates, excluded)
    series_by_apt = make_series(series)
    comparable_by_apt = make_comparables(comparables)
    floors_by_apt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    floor_columns = [column for column in profile.columns if column != "apt_seq"]
    for row in profile.sort_values(["apt_seq", "floor_band"], kind="stable").to_dict("records"):
        floors_by_apt[str(row["apt_seq"])].append({
            column: payload_value(column, row[column]) for column in floor_columns
        })

    permit_zone = regulation.get("seoul_apartment_permit_zone")
    if not isinstance(permit_zone, bool):
        raise ValueError("35.2.seoul_apartment_permit_zone은 boolean이어야 합니다.")
    as_of = regulation.get("as_of")
    if not isinstance(as_of, str) or not as_of:
        raise ValueError("35.2.as_of가 없습니다.")

    all_null_env_columns = [
        column for column in metrics.columns
        if column != "apt_seq" and metrics[column].isna().all()
    ]
    # Only columns that are null for every complex are omitted. Per-complex nulls
    # remain in env so the frontend can distinguish unavailable measurement data.
    metric_columns = [
        column for column in metrics.columns
        if column != "apt_seq" and column not in all_null_env_columns
    ]
    index_rows: list[dict[str, Any]] = []
    source_counts: Counter = Counter()
    for index, row in enumerate(complex_df.sort_values("apt_seq", kind="stable").to_dict("records"), start=1):
        apt_seq = str(row["apt_seq"])
        price = price_by_apt.get(apt_seq, [])
        source_counts.update(item["source"] for item in price)
        payload: dict[str, Any] = {
            "id": apt_seq,
            "name": json_value(row["name"]),
            "gu": json_value(row["gu"]),
            "umd_name": json_value(row["umd_name"]),
            "lat": payload_value("lat", row["lat"]),
            "lng": payload_value("lng", row["lng"]),
            "built_year": payload_value("built_year", row["built_year"]),
            "households": payload_value("households", row["total_households"]),
            "match_confidence": payload_value("match_confidence", row["match_confidence"]),
            "env": {column: payload_value(column, row[column]) for column in metric_columns},
            "floors": floors_by_apt.get(apt_seq, []),
            "price": price,
            "series": series_by_apt.get(apt_seq, []),
            "comparables": comparable_by_apt.get(apt_seq, []),
            "unknowns": UNKNOWN_ITEMS,
        }
        if permit_zone:
            payload["regulation"] = {
                "land_permit_zone": permit_zone,
                "as_of": as_of,
                "note": REGULATION_NOTE,
            }
        redevelop_type, redevelop_stage = json_value(row["redevelop_type"]), json_value(row["redevelop_stage"])
        if redevelop_type is not None or redevelop_stage is not None:
            payload["redevelop"] = {"type": redevelop_type, "stage": redevelop_stage}
        if apt_seq in excluded_apts and [item["source"] for item in price] != ["EXCLUDED"]:
            raise ValueError(f"임대 전용 payload의 price source가 EXCLUDED 단독이 아닙니다: {apt_seq}")
        dump_json(complex_payload_path(apt_seq), payload)
        index_rows.append({
            "id": apt_seq,
            "n": json_value(row["name"]),
            "g": json_value(row["gu"]),
            "u": json_value(row["umd_name"]),
            "lat": payload_value("lat", row["lat"]),
            "lng": payload_value("lng", row["lng"]),
            "y": payload_value("built_year", row["built_year"]),
            "h": payload_value("households", row["total_households"]),
        })
        if index % 1000 == 0:
            print(f"  진행: {index:,}/{len(complex_df):,}개 단지 JSON 생성")

    dump_json(INDEX_PATH, {"as_of": as_of, "complexes": index_rows})
    return source_counts, all_null_env_columns


def rounding_tolerance(field: str) -> float:
    """Return the largest expected absolute error after the field's serialization rounding."""
    kind = numeric_kind(field)
    return {
        "coordinate": 0.0000005,
        "distance": 0.05,
        "decimal": 0.005,
        "price": 0.5,
        "count": 0.5,
    }[kind]  # type: ignore[index]


def validate(complex_df: pd.DataFrame, metrics: pd.DataFrame, excluded: pd.DataFrame,
             source_counts: Counter, all_null_env_columns: list[str],
             before_stats: dict[str, Any] | None) -> None:
    """Validate schema contracts, compression targets, and sampled rounding accuracy."""
    print("\n===== 4. 자체 검증 =====")
    files = sorted(complex_data_dir.glob(f"*{COMPLEX_SUFFIX}"))
    expected_count = len(complex_df)
    gzip_size = len(gzip.compress(INDEX_PATH.read_bytes(), compresslevel=9))
    sizes = np.asarray([path.stat().st_size for path in files], dtype=np.int64)
    total_bytes = INDEX_PATH.stat().st_size + int(sizes.sum())
    rng = random.Random(20260911)
    sample_ids = rng.sample(complex_df["apt_seq"].astype(str).tolist(), 20)
    metric_lookup = metrics.set_index("apt_seq").to_dict("index")
    rounding_matches = True
    for apt_seq in sample_ids:
        payload = load_json(complex_payload_path(apt_seq))
        for column, expected in metric_lookup[apt_seq].items():
            expected = json_value(expected)
            if column in all_null_env_columns:
                if column in payload["env"]:
                    rounding_matches = False
                    break
                continue
            actual = payload["env"].get(column)
            if isinstance(expected, bool):
                if actual is not expected:
                    rounding_matches = False
                    break
            elif expected is None:
                if actual is not None:
                    rounding_matches = False
                    break
            elif not isinstance(actual, (int, float)) or isinstance(actual, bool) or \
                    abs(float(actual) - float(expected)) > rounding_tolerance(column) + 1e-10:
                rounding_matches = False
                break
        if not rounding_matches:
            break

    model_null_bounds = 0
    cell_last_null_price = 0
    excluded_all_correct = True
    env_null_schema_preserved = True
    non_permanent_env_columns = [
        column for column in metrics.columns
        if column != "apt_seq" and column not in all_null_env_columns
    ]
    for path in files:
        payload = load_json(path)
        expected_env = metric_lookup[payload["id"]]
        for column in all_null_env_columns:
            env_null_schema_preserved &= column not in payload["env"]
        for column in non_permanent_env_columns:
            expected = json_value(expected_env[column])
            actual = payload["env"].get(column)
            if expected is None:
                env_null_schema_preserved &= column in payload["env"] and actual is None
            else:
                env_null_schema_preserved &= column in payload["env"]
        for item in payload["price"]:
            if item["source"] == "MODEL" and (item.get("est_low") is None or item.get("est_high") is None):
                model_null_bounds += 1
            if item["source"] == "CELL_LAST" and item.get("last_price_manwon") is None:
                cell_last_null_price += 1
        if payload["id"] in set(excluded["apt_seq"].astype(str)):
            excluded_all_correct &= bool(payload["price"]) and all(
                item["source"] == "EXCLUDED" for item in payload["price"]
            )

    checks = [
        ("생성 단지 JSON 수 == 23.1 단지 수", len(files) == expected_count,
         f"{len(files):,} / {expected_count:,}"),
        ("총 용량 <= 현재 84MB의 60%", total_bytes <= MAX_TOTAL_BYTES,
         f"{total_bytes / 1024 / 1024:.1f}MB / {MAX_TOTAL_BYTES / 1024 / 1024:.1f}MB"),
        ("index.json gzip 크기 <= 500KB", gzip_size <= 500 * 1024,
         f"{gzip_size / 1024:.1f}KB"),
        ("단지 JSON 중앙값 크기 <= 4KB", float(np.median(sizes)) <= 4 * 1024,
         f"중앙값 {np.median(sizes) / 1024:.1f}KB, 최대 {sizes.max() / 1024:.1f}KB"),
        ("무작위 20개 env가 23.2 원값과 허용오차 내 일치", rounding_matches,
         ", ".join(sample_ids)),
        ("env의 개별 null 유지 및 전량 NULL 컬럼만 제거", env_null_schema_preserved,
         f"전량 NULL 제거 {len(all_null_env_columns)}개"),
        ("MODEL의 est_low/est_high 결측 0건", model_null_bounds == 0,
         f"결측 {model_null_bounds:,}건"),
        ("CELL_LAST의 last_price_manwon 결측 0건", cell_last_null_price == 0,
         f"결측 {cell_last_null_price:,}건"),
        ("임대 전용 단지 109개의 price가 EXCLUDED", excluded_all_correct and len(excluded) == 109,
         f"검사 {len(excluded):,}개"),
    ]
    for label, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")
    print("\n  참고: price source 분포")
    for source in sorted(PRICE_SOURCES):
        print(f"    {source}: {source_counts[source]:,}")
    comparable_counts = Counter()
    for path in files:
        comparable_counts[bool(load_json(path)["comparables"])] += 1
    print(f"  참고: comparables 0곳 단지: {comparable_counts[False]:,}개")
    if before_stats is not None:
        print("  참고: 압축 전후 용량: "
              f"{before_stats['total_bytes'] / 1024 / 1024:.1f}MB → {total_bytes / 1024 / 1024:.1f}MB")
        print("  참고: 압축 전후 단지 JSON 중앙값: "
              f"{before_stats['median_bytes'] / 1024:.1f}KB → {np.median(sizes) / 1024:.1f}KB")
    print(f"  참고: 제거된 전량 NULL env 컬럼: {', '.join(all_null_env_columns) or '(없음)'}")

    assert all(passed for _, passed, _ in checks), "자체 검증 실패: 위 [FAIL] 항목을 확인하십시오."


def main() -> None:
    print("===== 1. 입력 로드 =====")
    (complex_df, metrics, profile, cells, series, estimates, excluded,
     comparables, geocoded) = load_inputs()
    with REGULATION_PATH.open(encoding="utf-8") as handle:
        regulation = json.load(handle)
    print(f"  23.1 단지: {len(complex_df):,}개 / 32.1 가격 셀: {len(cells):,}개 / 33.1 모델 추정: {len(estimates):,}개")

    print("\n===== 2. 출력 경로 준비 =====")
    before_stats = output_size_stats()
    public_data_dir.mkdir(parents=True, exist_ok=True)
    if complex_data_dir.exists():
        shutil.rmtree(complex_data_dir)
    complex_data_dir.mkdir()
    print(f"  저장 경로: {public_data_dir.relative_to(work_dir)}")

    print("\n===== 3. 정적 payload 생성 =====")
    source_counts, all_null_env_columns = build_payloads(
        complex_df, metrics, profile, cells, series, estimates, excluded,
        comparables, geocoded, regulation,
    )
    print(f"  저장: {INDEX_PATH.relative_to(work_dir)}")

    validate(complex_df, metrics, excluded, source_counts, all_null_env_columns, before_stats)


if __name__ == "__main__":
    main()
