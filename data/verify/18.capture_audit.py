# ============================================================================
# 18.capture_audit.py
# ============================================================================
# Author:      yjkim
# Purpose:     검수 표본의 배정 결과를 위성사진 위에 겹쳐 PNG로 굽는다
# Description: 16이 뽑은 층화 표본을 사람이 카카오맵에서 하나씩 열어 보는 대신,
#              배정된 동 폴리곤을 위성사진에 겹친 이미지를 미리 만들어 둔다.
#
#              위성 타일은 Esri World Imagery를 쓴다. 카카오맵 JS SDK는 도메인
#              등록이 필요해 localhost에서 ERR_BLOCKED_BY_ORB로 막히고, 진단
#              과정에서 에러 메시지에 JS 키가 실려 나온다(HANDOFF 7절 지뢰).
#              Esri 타일은 키가 없어 두 문제가 모두 사라진다.
#
#              읽는 법:
#                빨강   이 단지에 배정된 동
#                회색   주변 NEAR_DEG 안의 다른 아파트 동 (배정 안 됨)
#                하늘색 지오코딩 앵커 (단지 대표 좌표)
#              빨강이 한 덩어리로 모여 있고 앵커가 그 안에 있으면 정상이다.
#              빨강이 회색 무리 사이에 흩어져 있으면 남의 동을 물어온 것이다.
#
#              한계: 위성사진 촬영 시점을 알 수 없다. 신축 단지는 사진에 건물이
#              없을 수 있으므로 "건물이 안 보임"을 오배정으로 판정하면 안 된다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

import pandas as pd
from shapely import wkt
from playwright.sync_api import sync_playwright

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

SAMPLE_PATH = output_dir / "16.1.audit_sample.txt"
COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
BUILDING_RAW_PATH = output_dir / "12.1.osm_buildings.txt"
BUILDING_ASSIGNED_PATH = output_dir / "15.2.building_assigned.txt"
CAPTURE_DIR = output_dir / "18.captures"

WIDTH, HEIGHT = 1200, 900
NEAR_DEG = 0.006          # 주변 동을 함께 그릴 범위. 위경도 약 500~660m
FIT_PAD = 0.6             # 배정 동 bbox를 이만큼 넓혀 주변 맥락을 남긴다
TILE_WAIT_MS = 5000       # 타일 로딩 대기
PORT = 8734
N_DEFAULT = 5

n_wanted = int(sys.argv[1]) if len(sys.argv) > 1 else N_DEFAULT


# ============================================================================
# 1. 표본 선정
# ============================================================================
# 전량 캡처 전에 소수로 확인할 때는 층과 의심 신호가 골고루 섞이게 뽑는다.

print("===== 1. 표본 선정 =====")
sample = pd.read_csv(SAMPLE_PATH, sep="\t", encoding="utf-8-sig", dtype={"aptSeq": str})
sample["flagged"] = sample["prescreen"].fillna("") != ""

if n_wanted >= len(sample):
    picked = sample
else:
    # 층 x 의심신호 조합마다 하나씩 채운다
    picked = (sample.sort_values(["confidence", "flagged", "n_deals"],
                                 ascending=[True, False, False])
              .groupby(["confidence", "flagged"], group_keys=False)
              .head(1).head(n_wanted))
    if len(picked) < n_wanted:
        rest = sample[~sample["aptSeq"].isin(picked["aptSeq"])]
        picked = pd.concat([picked, rest.head(n_wanted - len(picked))])

print(f"  전체 표본 {len(sample)}건 중 {len(picked)}건 캡처")
print(picked[["confidence", "apt_name", "gu", "n_dong", "reg_dong", "prescreen"]]
      .to_string(index=False))


# ============================================================================
# 2. 지오메트리 준비
# ============================================================================

print("\n===== 2. 지오메트리 로드 =====")
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
assigned = pd.read_csv(BUILDING_ASSIGNED_PATH, sep="\t", dtype={"aptSeq": str})
raw = pd.read_csv(BUILDING_RAW_PATH, sep="\t")
raw = raw[raw["building"] == "apartments"].copy()
raw["shape"] = raw["geometry"].apply(wkt.loads)
raw["cx"] = raw["shape"].apply(lambda g: g.centroid.x)
raw["cy"] = raw["shape"].apply(lambda g: g.centroid.y)
print(f"  아파트 동 {len(raw)}개 / 배정 기록 {int(assigned['aptSeq'].notna().sum())}건")


def ring_of(geom):
    """leaflet은 [lat, lng] 순서를 받는다. shapely는 (x=lng, y=lat)로 저장한다"""
    return [[point[1], point[0]] for point in geom.exterior.coords]


def build_page(row):
    anchor = master[master["aptSeq"] == row["aptSeq"]].iloc[0]
    ids = assigned[assigned["aptSeq"] == row["aptSeq"]]["osm_id"].tolist()
    mine = [ring_of(g) for g in raw[raw["osm_id"].isin(ids)]["shape"]]
    near = raw[(raw["cx"].sub(anchor["lon"]).abs() < NEAR_DEG)
               & (raw["cy"].sub(anchor["lat"]).abs() < NEAR_DEG)
               & (~raw["osm_id"].isin(ids))]
    others = [ring_of(g) for g in near["shape"]]

    caption = (f"{row['apt_name']} · {row['gu']} · {row['confidence']} · "
               f"배정 {row['n_dong']:.0f}동 / 등록 {row['reg_dong']:.0f}동")
    if str(row.get("prescreen") or "").strip():
        caption += f" · 신호: {row['prescreen']}"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
 html,body{{margin:0;background:#222}} #map{{width:{WIDTH}px;height:{HEIGHT - 40}px}}
 #cap{{height:40px;line-height:40px;padding:0 14px;background:#111;color:#eee;
      font:15px/40px system-ui,sans-serif}}
 #cap b{{color:#FF6B6B}}
</style></head><body>
<div id="cap">{caption} &nbsp;|&nbsp; <b>빨강</b>=배정된 동, 회색=주변 미배정, 하늘색=앵커</div>
<div id="map"></div><script>
const map = L.map('map', {{zoomControl:false, attributionControl:false}})
  .setView([{anchor['lat']}, {anchor['lon']}], 17);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom:19}}).addTo(map);
{json.dumps(others)}.forEach(r =>
  L.polygon(r, {{color:'#9aa', weight:1, fillOpacity:0.15}}).addTo(map));
const mine = {json.dumps(mine)};
const grp = L.featureGroup(mine.map(r =>
  L.polygon(r, {{color:'#FF2D2D', weight:3, fillColor:'#FF2D2D', fillOpacity:0.35}}).addTo(map)));
L.circleMarker([{anchor['lat']}, {anchor['lon']}],
  {{radius:7, color:'#00E5FF', weight:3, fillOpacity:0.9}}).addTo(map);
if (mine.length) map.fitBounds(grp.getBounds().pad({FIT_PAD}));
</script></body></html>"""


# ============================================================================
# 3. 캡처
# ============================================================================
# 로컬 http 서버를 띄운다. file:// 로 열면 타일 요청이 CORS에 막힌다.

print("\n===== 3. 캡처 =====")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CAPTURE_DIR), **kwargs)

    def log_message(self, *args):
        pass


server = socketserver.TCPServer(("127.0.0.1", PORT), QuietHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()

saved = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
    for i, (_, row) in enumerate(picked.iterrows(), 1):
        page_path = CAPTURE_DIR / f"_{row['aptSeq']}.html"
        page_path.write_text(build_page(row), encoding="utf-8")
        page.goto(f"http://127.0.0.1:{PORT}/{page_path.name}")
        page.wait_for_timeout(TILE_WAIT_MS)
        safe_name = str(row["apt_name"]).replace("/", "_")[:30]
        png = CAPTURE_DIR / f"{row['confidence']}_{row['aptSeq']}_{safe_name}.png"
        page.screenshot(path=str(png))
        page_path.unlink()
        saved.append(png.name)
        print(f"  캡처 중 [{i}/{len(picked)}]: {png.name}")
    browser.close()
server.shutdown()


# ============================================================================
# 4. 저장
# ============================================================================

index_path = CAPTURE_DIR / "index.txt"
index = picked[["confidence", "aptSeq", "apt_name", "gu", "n_dong", "reg_dong",
                "dong_diff", "prescreen", "kakao_map_url"]].copy()
index["capture"] = saved
index.to_csv(index_path, sep="\t", index=False, lineterminator="\n", encoding="utf-8-sig")

print(f"\n캡처 {len(saved)}장: {CAPTURE_DIR}")
print(f"목록: {index_path}")
print("""
읽는 법:
  빨강이 한 덩어리로 모여 있고 하늘색 앵커가 그 안에 있으면 정상
  빨강이 회색 무리 사이에 흩어져 있으면 남의 동을 물어온 것
  주의: 위성사진 촬영 시점이 불명이라 신축 단지는 건물이 안 보일 수 있다.
        "건물이 안 보임"을 오배정으로 판정하지 말 것""")
