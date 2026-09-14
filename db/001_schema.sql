create schema if not exists app;
create schema if not exists staging;

create table if not exists app.dataset_snapshot (
  snapshot_id bigserial primary key,
  as_of date not null,
  source text not null default 'output',
  note text,
  is_active boolean not null default false,
  created_at timestamptz not null default now()
);

create unique index if not exists dataset_snapshot_one_active
  on app.dataset_snapshot (is_active)
  where is_active;

create table if not exists app.complex (
  snapshot_id bigint not null references app.dataset_snapshot(snapshot_id) on delete cascade,
  apt_seq text not null,
  name text not null,
  bjd_code text,
  lat double precision not null,
  lng double precision not null,
  built_year integer,
  total_households integer,
  building_count integer,
  far numeric,
  bcr numeric,
  parking_per_hh numeric,
  redevelop_type text,
  redevelop_stage text,
  polygon_matched boolean not null,
  match_confidence text not null,
  primary key (snapshot_id, apt_seq),
  check (match_confidence in ('HIGH', 'MEDIUM', 'LOW', 'FAILED'))
);

create table if not exists app.complex_metrics (
  snapshot_id bigint not null,
  apt_seq text not null,
  sun_hours_avg numeric,
  sun_hours_best numeric,
  view_open_avg numeric,
  river_view_ratio numeric,
  road_centerline_m numeric,
  road_arterial_dist_m numeric,
  road_secondary_dist_m numeric,
  rail_centerline_m numeric,
  station_dist_m numeric,
  station_elev_diff numeric,
  station_walk_min_est numeric,
  station_ridership_daily numeric,
  station_congestion_peak numeric,
  traffic_weekday numeric,
  traffic_weekend numeric,
  elem_school_m numeric,
  elem_safe_route boolean,
  mid_school_m numeric,
  high_school_m numeric,
  daycare_500m integer,
  tertiary_hosp_m numeric,
  general_hosp_m numeric,
  clinic_1km integer,
  pediatric_1km integer,
  mart_m numeric,
  dept_store_m numeric,
  supermarket_m numeric,
  cvs_500m integer,
  restaurant_500m integer,
  park_m numeric,
  park_area_m2 numeric,
  nightlife_300m integer,
  dawn_delivery boolean,
  primary key (snapshot_id, apt_seq),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade
);

create table if not exists app.horizon_profile (
  snapshot_id bigint not null,
  apt_seq text not null,
  floor_band text not null,
  repr_floor numeric,
  obs_height numeric,
  sun_hours_winter numeric,
  sun_hours_spring numeric,
  view_block_pct numeric,
  open_angle_mean numeric,
  open_span_max numeric,
  river_view boolean,
  park_view boolean,
  mountain_view boolean,
  primary key (snapshot_id, apt_seq, floor_band),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade,
  check (floor_band in ('LOW', 'MID', 'HIGH', 'UNKNOWN'))
);

create table if not exists app.price_cell (
  snapshot_id bigint not null,
  apt_seq text not null,
  area_type numeric not null,
  floor_band text not null,
  n_trades_24m integer,
  last_deal_ym integer,
  last_price_manwon numeric,
  last_price_per_m2 numeric,
  mean_price_per_m2_24m numeric,
  area_last_floor_band text,
  price_source text not null,
  primary key (snapshot_id, apt_seq, area_type, floor_band),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade,
  check (floor_band in ('LOW', 'MID', 'HIGH', 'UNKNOWN')),
  check (price_source in ('CELL_LAST', 'AREA_LAST', 'COMPLEX_MEAN', 'MODEL', 'EXCLUDED'))
);

create table if not exists app.price_series (
  snapshot_id bigint not null,
  apt_seq text not null,
  area_type numeric not null,
  deal_ym integer not null,
  n_trades integer not null,
  median_price_per_m2 numeric not null,
  primary key (snapshot_id, apt_seq, area_type, deal_ym),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade
);

create table if not exists app.estimate (
  snapshot_id bigint not null,
  apt_seq text not null,
  area_type numeric not null,
  area_type_source text,
  floor_band text not null,
  jeonse_level text,
  jeonse_per_m2_adj numeric,
  jeonse_n_trades integer,
  jeonse_months_since integer,
  is_move_in_period boolean,
  jeonse_over_sale_ratio_gt1 boolean,
  est_price_per_m2 numeric not null,
  est_low numeric not null,
  est_high numeric not null,
  est_confidence text not null,
  est_note text,
  primary key (snapshot_id, apt_seq, area_type, floor_band),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade,
  check (floor_band in ('LOW', 'MID', 'HIGH', 'UNKNOWN')),
  check (est_confidence in ('HIGH', 'MEDIUM', 'LOW'))
);

create table if not exists app.comparable (
  snapshot_id bigint not null,
  apt_seq text not null,
  area_type numeric not null,
  target_source text not null,
  rank integer not null,
  comp_apt_seq text,
  comp_name text not null,
  comp_deal_ym text not null,
  comp_price_manwon numeric not null,
  comp_price_per_m2 numeric not null,
  adj_price_per_m2 numeric not null,
  adj_reason text not null,
  dist_m numeric,
  primary key (snapshot_id, apt_seq, area_type, target_source, rank),
  foreign key (snapshot_id, apt_seq) references app.complex(snapshot_id, apt_seq) on delete cascade
);

create table if not exists app.regulation_summary (
  snapshot_id bigint primary key references app.dataset_snapshot(snapshot_id) on delete cascade,
  as_of date not null,
  seoul_apartment_permit_zone boolean not null,
  source text,
  source_url text,
  payload jsonb not null
);

create table if not exists app.share (
  share_id text primary key,
  apt_seqs text[] not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  check (array_length(apt_seqs, 1) between 1 and 20)
);

create index if not exists complex_active_lookup
  on app.complex (apt_seq, snapshot_id);
create index if not exists complex_geo_idx
  on app.complex (snapshot_id, lat, lng);
create index if not exists price_cell_lookup
  on app.price_cell (snapshot_id, apt_seq, area_type, floor_band);
create index if not exists price_series_lookup
  on app.price_series (snapshot_id, apt_seq, area_type, deal_ym);
create index if not exists comparable_lookup
  on app.comparable (snapshot_id, apt_seq, area_type, target_source, rank);

grant usage on schema app to imnjang_readonly;
grant select on all tables in schema app to imnjang_readonly;
alter default privileges in schema app grant select on tables to imnjang_readonly;

grant usage on schema app to imnjang_loader;
grant select, insert, update, delete on all tables in schema app to imnjang_loader;
grant usage, select on all sequences in schema app to imnjang_loader;
alter default privileges in schema app grant select, insert, update, delete on tables to imnjang_loader;
alter default privileges in schema app grant usage, select on sequences to imnjang_loader;

grant usage, create on schema staging to imnjang_loader;
grant select, insert, update, delete, truncate on all tables in schema staging to imnjang_loader;
alter default privileges in schema staging grant select, insert, update, delete, truncate on tables to imnjang_loader;
