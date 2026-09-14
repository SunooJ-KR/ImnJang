#!/usr/bin/env python
# ============================================================================
# 38.load_db.py
# ============================================================================
# Author:      yjkim
# Purpose:     배치 산출물을 Railway Postgres 스냅샷으로 적재한다.
# Description: output/*.txt와 규제 JSON을 staging에 먼저 COPY한 뒤 app 테이블에
#              새 snapshot_id로 삽입하고, 마지막에 active snapshot을 전환한다.
#              앱/모델은 DATABASE_READONLY_URL만 사용하고, 이 스크립트만
#              DATABASE_LOADER_URL을 사용한다.
# ============================================================================

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


work_dir = Path(__file__).resolve().parents[1]
output_dir = work_dir / "output"

INPUTS = {
    "complex": output_dir / "23.1.complex.txt",
    "complex_metrics": output_dir / "23.2.complex_metrics.txt",
    "horizon_profile": output_dir / "23.3.horizon_profile.txt",
    "price_cell": output_dir / "32.1.price_cells.txt",
    "price_series": output_dir / "32.2.price_series.txt",
    "estimate": output_dir / "33.1.coldstart_estimates.txt",
    "comparable": output_dir / "34.1.comparables.txt",
    "regulation": output_dir / "35.2.regulation_summary.json",
}


def load_env(key: str) -> str:
    env_path = work_dir / ".env"
    if not env_path.exists():
        raise SystemExit(".env 파일이 없습니다.")
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f".env에 {key} 없음")


def mask_key(text: str) -> str:
    return text.replace(load_env("DATABASE_LOADER_URL"), "[DATABASE_LOADER_URL]")


def require_inputs() -> None:
    missing = [str(path) for path in INPUTS.values() if not path.exists()]
    if missing:
        raise SystemExit("필수 입력 파일 없음:\n" + "\n".join(missing))


def psql_copy_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def read_regulation() -> tuple[str, str]:
    payload = json.loads(INPUTS["regulation"].read_text(encoding="utf-8"))
    return payload["as_of"], json.dumps(payload, ensure_ascii=False).replace("'", "''")


def count_data_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as file:
        return max(sum(1 for _ in file) - 1, 0)


def build_sql(as_of: str, regulation_json: str, expected_counts: dict[str, int]) -> str:
    paths = {name: psql_copy_path(path) for name, path in INPUTS.items() if name != "regulation"}
    return f"""
\\set ON_ERROR_STOP on

begin;

create temp table load_complex (
  apt_seq text, name text, bjd_code text, lat text, lng text, built_year text,
  total_households text, building_count text, far text, bcr text, parking_per_hh text,
  redevelop_type text, redevelop_stage text, polygon_matched text, match_confidence text
);
create temp table load_complex_metrics (
  apt_seq text, sun_hours_avg text, sun_hours_best text, view_open_avg text,
  river_view_ratio text, road_centerline_m text, road_arterial_dist_m text,
  road_secondary_dist_m text, rail_centerline_m text, station_dist_m text,
  station_elev_diff text, station_walk_min_est text, station_ridership_daily text,
  station_congestion_peak text, traffic_weekday text, traffic_weekend text,
  elem_school_m text, elem_safe_route text, mid_school_m text, high_school_m text,
  daycare_500m text, tertiary_hosp_m text, general_hosp_m text, clinic_1km text,
  pediatric_1km text, mart_m text, dept_store_m text, supermarket_m text,
  cvs_500m text, restaurant_500m text, park_m text, park_area_m2 text,
  nightlife_300m text, dawn_delivery text
);
create temp table load_horizon_profile (
  apt_seq text, floor_band text, repr_floor text, obs_height text,
  sun_hours_winter text, sun_hours_spring text, view_block_pct text,
  open_angle_mean text, open_span_max text, river_view text, park_view text,
  mountain_view text
);
create temp table load_price_cell (
  apt_seq text, area_type text, floor_band text, n_trades_24m text,
  last_deal_ym text, last_price_manwon text, last_price_per_m2 text,
  mean_price_per_m2_24m text, area_last_floor_band text, price_source text
);
create temp table load_price_series (
  apt_seq text, area_type text, deal_ym text, n_trades text, median_price_per_m2 text
);
create temp table load_estimate (
  apt_seq text, area_type text, area_type_source text, floor_band text,
  jeonse_level text, jeonse_per_m2_adj text, jeonse_n_trades text,
  jeonse_months_since text, is_move_in_period text, jeonse_over_sale_ratio_gt1 text,
  est_price_per_m2 text, est_low text, est_high text, est_confidence text, est_note text
);
create temp table load_comparable (
  apt_seq text, area_type text, target_source text, rank text, comp_apt_seq text,
  comp_name text, comp_deal_ym text, comp_price_manwon text, comp_price_per_m2 text,
  adj_price_per_m2 text, adj_reason text, dist_m text
);

\\copy load_complex from '{paths["complex"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_complex_metrics from '{paths["complex_metrics"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_horizon_profile from '{paths["horizon_profile"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_price_cell from '{paths["price_cell"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_price_series from '{paths["price_series"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_estimate from '{paths["estimate"]}' with (format csv, delimiter E'\\t', header true, null '')
\\copy load_comparable from '{paths["comparable"]}' with (format csv, delimiter E'\\t', header true, null '')

do $$
begin
  if (select count(*) from load_complex) <> {expected_counts["complex"]} then
    raise exception 'complex row count mismatch: %', (select count(*) from load_complex);
  end if;
  if (select count(*) from load_price_cell) <> {expected_counts["price_cell"]} then
    raise exception 'price_cell row count mismatch: %', (select count(*) from load_price_cell);
  end if;
  if (select count(*) from load_estimate) <> {expected_counts["estimate"]} then
    raise exception 'estimate row count mismatch: %', (select count(*) from load_estimate);
  end if;
end $$;

insert into app.dataset_snapshot (as_of, source, note, is_active)
values ('{as_of}'::date, 'output', 'loaded by db/38.load_db.py', false);

create temp table load_snapshot_id as
select currval(pg_get_serial_sequence('app.dataset_snapshot', 'snapshot_id'))::bigint as snapshot_id;

insert into app.complex
select
  s.snapshot_id,
  apt_seq,
  name,
  nullif(regexp_replace(coalesce(bjd_code, ''), '\\.0$', ''), ''),
  nullif(lat, '')::double precision,
  nullif(lng, '')::double precision,
  nullif(built_year, '')::numeric::integer,
  nullif(total_households, '')::numeric::integer,
  nullif(building_count, '')::numeric::integer,
  nullif(far, '')::numeric,
  nullif(bcr, '')::numeric,
  nullif(parking_per_hh, '')::numeric,
  nullif(redevelop_type, ''),
  nullif(redevelop_stage, ''),
  nullif(polygon_matched, '')::boolean,
  coalesce(nullif(match_confidence, ''), 'LOW')
from load_complex cross join load_snapshot_id s;

insert into app.complex_metrics
select
  s.snapshot_id, apt_seq,
  nullif(sun_hours_avg, '')::numeric,
  nullif(sun_hours_best, '')::numeric,
  nullif(view_open_avg, '')::numeric,
  nullif(river_view_ratio, '')::numeric,
  nullif(road_centerline_m, '')::numeric,
  nullif(road_arterial_dist_m, '')::numeric,
  nullif(road_secondary_dist_m, '')::numeric,
  nullif(rail_centerline_m, '')::numeric,
  nullif(station_dist_m, '')::numeric,
  nullif(station_elev_diff, '')::numeric,
  nullif(station_walk_min_est, '')::numeric,
  nullif(station_ridership_daily, '')::numeric,
  nullif(station_congestion_peak, '')::numeric,
  nullif(traffic_weekday, '')::numeric,
  nullif(traffic_weekend, '')::numeric,
  nullif(elem_school_m, '')::numeric,
  nullif(elem_safe_route, '')::boolean,
  nullif(mid_school_m, '')::numeric,
  nullif(high_school_m, '')::numeric,
  nullif(daycare_500m, '')::numeric::integer,
  nullif(tertiary_hosp_m, '')::numeric,
  nullif(general_hosp_m, '')::numeric,
  nullif(clinic_1km, '')::numeric::integer,
  nullif(pediatric_1km, '')::numeric::integer,
  nullif(mart_m, '')::numeric,
  nullif(dept_store_m, '')::numeric,
  nullif(supermarket_m, '')::numeric,
  nullif(cvs_500m, '')::numeric::integer,
  nullif(restaurant_500m, '')::numeric::integer,
  nullif(park_m, '')::numeric,
  nullif(park_area_m2, '')::numeric,
  nullif(nightlife_300m, '')::numeric::integer,
  nullif(dawn_delivery, '')::boolean
from load_complex_metrics cross join load_snapshot_id s;

insert into app.horizon_profile
select
  s.snapshot_id, apt_seq, floor_band,
  nullif(repr_floor, '')::numeric,
  nullif(obs_height, '')::numeric,
  nullif(sun_hours_winter, '')::numeric,
  nullif(sun_hours_spring, '')::numeric,
  nullif(view_block_pct, '')::numeric,
  nullif(open_angle_mean, '')::numeric,
  nullif(open_span_max, '')::numeric,
  nullif(river_view, '')::boolean,
  nullif(park_view, '')::boolean,
  nullif(mountain_view, '')::boolean
from load_horizon_profile cross join load_snapshot_id s;

insert into app.price_cell
select
  s.snapshot_id, apt_seq, nullif(area_type, '')::numeric, floor_band,
  nullif(n_trades_24m, '')::numeric::integer,
  nullif(last_deal_ym, '')::numeric::integer,
  nullif(last_price_manwon, '')::numeric,
  nullif(last_price_per_m2, '')::numeric,
  nullif(mean_price_per_m2_24m, '')::numeric,
  nullif(area_last_floor_band, ''),
  price_source
from load_price_cell cross join load_snapshot_id s;

insert into app.price_series
select
  s.snapshot_id, apt_seq, nullif(area_type, '')::numeric,
  nullif(deal_ym, '')::numeric::integer,
  nullif(n_trades, '')::numeric::integer,
  nullif(median_price_per_m2, '')::numeric
from load_price_series cross join load_snapshot_id s;

insert into app.estimate
select
  s.snapshot_id, apt_seq, nullif(area_type, '')::numeric, nullif(area_type_source, ''),
  floor_band, nullif(jeonse_level, ''),
  nullif(jeonse_per_m2_adj, '')::numeric,
  nullif(jeonse_n_trades, '')::numeric::integer,
  nullif(jeonse_months_since, '')::numeric::integer,
  case
    when lower(nullif(is_move_in_period, '')) in ('true', 't', '1', '1.0', '1.000000') then true
    when lower(nullif(is_move_in_period, '')) in ('false', 'f', '0', '0.0', '0.000000') then false
    else null
  end,
  case
    when lower(nullif(jeonse_over_sale_ratio_gt1, '')) in ('true', 't', '1', '1.0', '1.000000') then true
    when lower(nullif(jeonse_over_sale_ratio_gt1, '')) in ('false', 'f', '0', '0.0', '0.000000') then false
    else null
  end,
  nullif(est_price_per_m2, '')::numeric,
  nullif(est_low, '')::numeric,
  nullif(est_high, '')::numeric,
  est_confidence,
  nullif(est_note, '')
from load_estimate cross join load_snapshot_id s;

insert into app.comparable
select
  s.snapshot_id, apt_seq, nullif(area_type, '')::numeric, target_source,
  nullif(rank, '')::numeric::integer,
  nullif(comp_apt_seq, ''), comp_name, comp_deal_ym,
  nullif(comp_price_manwon, '')::numeric,
  nullif(comp_price_per_m2, '')::numeric,
  nullif(adj_price_per_m2, '')::numeric,
  adj_reason,
  nullif(dist_m, '')::numeric
from load_comparable cross join load_snapshot_id s;

insert into app.regulation_summary
select
  s.snapshot_id,
  '{as_of}'::date,
  (('{regulation_json}'::jsonb)->>'seoul_apartment_permit_zone')::boolean,
  ('{regulation_json}'::jsonb)->>'source',
  ('{regulation_json}'::jsonb)->>'source_url',
  '{regulation_json}'::jsonb
from load_snapshot_id s;

do $$
declare
  sid bigint := (select snapshot_id from load_snapshot_id);
begin
  if (select count(*) from app.complex where snapshot_id = sid) <> {expected_counts["complex"]} then
    raise exception 'loaded complex count mismatch';
  end if;
  if exists (
    select 1
    from app.price_cell pc
    left join app.complex c on c.snapshot_id = pc.snapshot_id and c.apt_seq = pc.apt_seq
    where pc.snapshot_id = sid and c.apt_seq is null
  ) then
    raise exception 'price_cell has unknown apt_seq';
  end if;
end $$;

update app.dataset_snapshot set is_active = false where is_active;
update app.dataset_snapshot
set is_active = true
where snapshot_id = (select snapshot_id from load_snapshot_id);

commit;

select
  ds.snapshot_id,
  ds.as_of,
  ds.is_active,
  (select count(*) from app.complex where snapshot_id = ds.snapshot_id) as complex_rows,
  (select count(*) from app.price_cell where snapshot_id = ds.snapshot_id) as price_cell_rows,
  (select count(*) from app.estimate where snapshot_id = ds.snapshot_id) as estimate_rows,
  (select count(*) from app.comparable where snapshot_id = ds.snapshot_id) as comparable_rows
from app.dataset_snapshot ds
where ds.is_active;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=load_env("DATABASE_LOADER_URL"))
    args = parser.parse_args()

    if not shutil.which("psql"):
        raise SystemExit("psql 명령을 찾을 수 없습니다.")

    require_inputs()
    as_of, regulation_json = read_regulation()
    expected_counts = {
        name: count_data_rows(path)
        for name, path in INPUTS.items()
        if name != "regulation"
    }
    sql = build_sql(as_of, regulation_json, expected_counts)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as file:
        file.write(sql)
        sql_path = Path(file.name)

    try:
        subprocess.run(["psql", args.database_url, "-f", str(sql_path)], check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"DB 적재 실패: {mask_key(str(error))}") from error
    finally:
        sql_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
