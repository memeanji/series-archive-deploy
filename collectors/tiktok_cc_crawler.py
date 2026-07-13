"""
TikTok Creative Center(Top Ads) 웹 크롤러 (Playwright).
공개 인기광고 페이지를 렌더링해 광고 소재(커버/영상/지표)를 추출한다.
단독 테스트:  python collectors/tiktok_cc_crawler.py 더스크랙
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

PLATFORM = "tiktok"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_JS_EXTRACT = r"""
() => {
  const vids = Array.from(document.querySelectorAll('video'))
    .map(v => v.src || (v.querySelector('source')||{}).src).filter(Boolean);
  const imgs = Array.from(document.querySelectorAll('img'))
    .map(i => i.src).filter(s => s && !s.startsWith('data:') && /tiktokcdn|byteimg|p16|tiktok/.test(s));
  // 카드 후보: 광고 상세로 가는 링크나 'item' 류 컨테이너
  const cards = [];
  const anchors = Array.from(document.querySelectorAll('a[href*="ads_detail"], a[href*="/inspiration/"], [data-testid], .index-mobile_cardWrapper__, [class*="cardWrapper"], [class*="CardWrapper"]'));
  for (const c of anchors) {
    const img = c.querySelector ? c.querySelector('img') : null;
    const v = c.querySelector ? c.querySelector('video') : null;
    const txt = (c.innerText||'').trim().slice(0,120);
    if (img || v) cards.push({thumb: img?img.src:'', video: v?(v.src||''):'', text: txt,
                               href: c.href||''});
  }
  return {videos: vids.slice(0,40), images: imgs.slice(0,40), cards: cards.slice(0,60)};
}
"""


def search(keyword: str, region: str = "KR", scrolls: int = 6,
           headless: bool = True, shot: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    url = (f"https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en"
           f"?region={region}")
    data = {}
    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless)
        ctx = br.new_context(locale="ko-KR", user_agent=UA,
                             viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            # 검색창 시도
            for sel in ('input[type="text"]', 'input[placeholder]', 'input'):
                el = page.query_selector(sel)
                if el and el.is_visible():
                    try:
                        el.click(); el.fill(keyword); page.keyboard.press("Enter")
                        page.wait_for_timeout(5000)
                    except Exception:  # noqa: BLE001
                        pass
                    break
            for _ in range(scrolls):
                page.mouse.wheel(0, 6000)
                page.wait_for_timeout(2200)
            data = page.evaluate(_JS_EXTRACT)
            if shot:
                Path("data").mkdir(exist_ok=True)
                page.screenshot(path=f"data/_tiktok_{keyword}.png")
        finally:
            br.close()
    return data


def search_brand(brand: str, **kw) -> list[dict]:
    data = search(brand, **kw)
    ads = []
    for i, c in enumerate(data.get("cards", [])):
        if not (c.get("thumb") or c.get("video")):
            continue
        ads.append({
            "platform": PLATFORM,
            "platform_ad_id": f"ttcc_{brand}_{i}",
            "advertiser_name": brand,
            "ad_text": c.get("text", ""),
            "media_type": "video" if c.get("video") else "image",
            "video_url": c.get("video", ""),
            "thumbnail_url": c.get("thumb", ""),
            "original_ad_url": c.get("href", ""),
            "status": "live",
            "raw_data": c,
        })
    return ads


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    d = search(term, headless=True, shot=True)
    print(f"videos={len(d.get('videos',[]))} images={len(d.get('images',[]))} "
          f"cards={len(d.get('cards',[]))}")
    for c in d.get("cards", [])[:5]:
        print("  card thumb:", (c.get('thumb') or '-')[:60], "| vid:", 'O' if c.get('video') else 'X')
    print("  sample img:", (d.get('images') or ['-'])[0][:80])
