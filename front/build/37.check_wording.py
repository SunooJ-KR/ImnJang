# ============================================================================
# 37.check_wording.py
# ============================================================================
# Author:      yjkim
# Purpose:     화면 문구가 docs/product-identity.md §6 표기 규칙을 어기지 않는지
#              기계적으로 검사한다
# Description: 표기 규칙 위반은 기능 결함이 아니라 사실 왜곡이다. 사람 눈으로
#              잡으면 언젠가 새어 나가므로 스크립트로 강제한다.
#
#              두 가지를 본다:
#                1) 금지 표현이 index.html / app.js 에 있는가
#                2) app.js 가 참조하는 JSON 필드가 실제 스키마에 있는가
#                   (오타난 필드명은 브라우저에서만 드러난다)
#
#              스키마는 이 파일에 상수로 둔다. front/public/data 는 9,160개
#              파일이라 스캔하지 않는다.
# ============================================================================

import re
import sys
from pathlib import Path

work_dir = Path(__file__).resolve().parents[2]
front_dir = work_dir / "front"

# Next.js 소스 전체를 본다. node_modules / .next / out 은 산출물이라 제외한다.
SOURCE_DIRS = ["app", "components", "lib"]
TARGETS = sorted(
    path
    for directory in SOURCE_DIRS
    for pattern in ("*.ts", "*.tsx", "*.css")
    for path in (front_dir / directory).rglob(pattern)
)
if not TARGETS:
    sys.exit("검사 대상 소스를 찾지 못했다. front/{app,components,lib} 를 확인하라.")

# 금지 표현. (정규식, 설명) — docs/product-identity.md §6
FORBIDDEN = [
    (r"적정시세", "모델 출력을 '적정시세'라 부르면 내재가치 주장이 된다"),
    (r"저평가", "호가가 없어 저평가 판정을 할 수 없다"),
    (r"고평가", "호가가 없어 고평가 판정을 할 수 없다"),
    (r"서울\s*전역\s*완전\s*검색", "물리 지표 커버리지가 70.4%라 성립하지 않는다"),
    (r"일조는\s*가격에\s*반영되지\s*않습니다", "p=0.53은 '효과 없음'이 아니라 '증거 없음'이다"),
    (r"재건축으로\s*\d+\s*%\s*오릅", "150단지 표본의 계수를 인과로 말할 수 없다"),
    (r"초품아", "실제 보행경로가 아닌 직선 근사라 해당 명칭을 쓸 수 없다"),
    (r"임대\s*전용", "임대 관련 명칭만으로 전용 여부를 확정할 수 없다"),
    (r"재건축으로\s*\d+\s*%", "정비사업을 가격 상승률로 단정할 수 없다"),
    # '도보'는 '추정 도보'가 아닐 때만 위반이다
    (r"(?<!추정 )(?<!추정)도보\s*\d", "직선거리 추정치이므로 '추정 도보 N분'으로 써야 한다"),
]

# app.js 가 참조해도 되는 필드. front/build/36.build_payload.py 산출 스키마와 일치해야 한다
SCHEMA = {
    "root": {"id", "name", "gu", "umd_name", "lat", "lng", "built_year", "households",
             "match_confidence", "env", "floors", "price", "series", "comparables",
             "regulation", "redevelop", "unknowns"},
    "env": {"sun_hours_avg", "sun_hours_best", "view_open_avg", "river_view_ratio",
            "road_centerline_m", "road_arterial_dist_m", "road_secondary_dist_m",
            "rail_centerline_m", "station_dist_m", "station_walk_min_est",
            "station_ridership_daily", "station_congestion_peak", "elem_school_m",
            "elem_safe_route", "mid_school_m", "high_school_m", "tertiary_hosp_m",
            "general_hosp_m", "clinic_1km", "pediatric_1km", "mart_m", "dept_store_m",
            "supermarket_m", "cvs_500m", "restaurant_500m", "park_m", "park_area_m2",
            "nightlife_300m"},
    "price": {"area_type", "floor_band", "source", "n_trades_24m", "last_deal_ym",
              "last_price_manwon", "mean_price_per_m2_24m", "est_price_per_m2",
              "est_low", "est_high", "est_confidence", "reason"},
    "comparables": {"area_type", "target_source", "rank", "comp_id", "name", "deal_ym",
                    "price_manwon", "price_per_m2", "adj_price_per_m2", "adj_reason",
                    "dist_m"},
    "regulation": {"land_permit_zone", "as_of", "note"},
    "regulation_summary": {"as_of", "seoul_apartment_permit_zone"},
    "redevelop": {"type", "stage"},
    "series": {"area_type", "points"},
    # index.json 은 용량 때문에 키를 1글자로 줄였다 (front/build/36.build_payload.py)
    # match_confidence 는 HIGH 가 아닐 때만 실린다 (36.build_payload.py)
    "index": {"id", "n", "g", "u", "lat", "lng", "y", "h", "match_confidence"},
}
VALID_SOURCES = {"CELL_LAST", "COMPLEX_MEAN", "MODEL", "EXCLUDED"}


print("===== 1. 금지 표현 검사 =====")
violations = []
for path in TARGETS:
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("//", "*", "/*", "#")):
            continue          # 주석에서 규칙 자체를 설명할 수 있어야 한다
        for pattern, reason in FORBIDDEN:
            if re.search(pattern, line):
                violations.append((path.name, line_no, pattern, reason, line.strip()[:70]))

for name, line_no, pattern, reason, snippet in violations:
    print(f"  [FAIL] {name}:{line_no} /{pattern}/ — {reason}")
    print(f"         {snippet}")
if not violations:
    print(f"  [PASS] 금지 표현 없음 ({len(FORBIDDEN)}종 검사)")


print("\n===== 2. 필드명 대조 =====")
# 타입 선언(lib/types.ts)은 스키마 자체를 적는 곳이라 필드 접근 검사에서 제외한다
app_text = "\n".join(
    path.read_text(encoding="utf-8") for path in TARGETS if path.name != "types.ts"
)
known = set().union(*SCHEMA.values())
unknown_fields = []

# d.env.xxx / data.env.xxx 처럼 env 하위 접근을 먼저 본다
for match in re.finditer(r"\.env\.([a-z_0-9]+)", app_text):
    if match.group(1) not in SCHEMA["env"]:
        unknown_fields.append(("env", match.group(1)))

# entry.xxx / c.xxx / data.xxx 형태의 1단계 접근
for match in re.finditer(r"\b(?:data|entry|item|c|s|d|regulation)\??\.([a-z_][a-z_0-9]*)\b", app_text):
    field = match.group(1)
    if field in {"env", "length", "map", "filter", "forEach", "slice", "some", "json",
                 "ok", "id", "name", "type", "value", "target", "textContent", "hidden",
                 "className", "points"}:
        continue
    if field not in known:
        unknown_fields.append(("root/price/comp", field))

for scope, field in sorted(set(unknown_fields)):
    print(f"  [FAIL] {scope} 에 없는 필드 참조: {field}")
if not unknown_fields:
    print("  [PASS] 참조 필드가 전부 스키마에 존재")


print("\n===== 3. source 분기 완전성 =====")
handled = set(re.findall(r"source === [\"']([A-Z_]+)[\"']", app_text))
missing = VALID_SOURCES - handled
if missing:
    print(f"  [FAIL] 처리하지 않은 source: {sorted(missing)}")
else:
    print(f"  [PASS] source 4종 전부 분기 ({sorted(handled)})")


all_passed = not violations and not unknown_fields and not missing
print(f"\n===== 표기 규칙 검사 {'통과' if all_passed else '미통과'} =====")
sys.exit(0 if all_passed else 1)
