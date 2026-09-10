# ============================================================================
# 10.render_mockups.py
# ============================================================================
# Author:      yjkim
# Purpose:     HTML 목업을 썸네일 PNG로 렌더링한다
# Description: codex가 작성한 광고용 HTML 목업 3장을 1200x800 PNG로 굽는다.
#              폰트 로딩과 SVG 렌더 완료를 기다린 뒤 캡처한다.
# ============================================================================

from pathlib import Path
from playwright.sync_api import sync_playwright

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
mockup_dir = Path(__file__).parent
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)

WIDTH, HEIGHT = 1200, 800

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
    for html_path in sorted(mockup_dir.glob("*.html")):
        page.goto(html_path.as_uri())
        page.wait_for_timeout(1200)
        out_path = output_dir / f"10.{html_path.stem}.png"
        page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        overflow = page.evaluate(
            "() => ({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})")
        flag = "" if overflow["w"] <= WIDTH and overflow["h"] <= HEIGHT else \
               f"  [경고] 오버플로 {overflow['w']}x{overflow['h']}"
        print(f"  {out_path.name}  ({out_path.stat().st_size // 1024}KB){flag}")
    browser.close()

print("\n===== 렌더링 완료 =====")
