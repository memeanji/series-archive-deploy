"""
구글 광고 투명성 센터 웹 크롤러 (Playwright) — 소재형 광고만.
구글은 소재를 cross-origin iframe 으로 렌더해 이미지/영상 URL 직접 추출이 안 된다.
→ 렌더된 소재 카드를 **스크린샷**해 썸네일(base64)로 만들고,
  검색/텍스트 광고·미디어 없는 카드는 제외한다.

저장 대상: ad_format ∈ {video, image, display} 이고 미디어(스크린샷) 확보된 광고만.
단독 테스트:  python collectors/google_library_crawler.py 더스크랙
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services.urls import normalize_google_transparency_url  # noqa: E402

PLATFORM = "google"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_STATIC = Path(__file__).resolve().parent.parent / "static" / "thumbnails"

_CLASSIFY_JS = """
el => {
  let c = el;
  for (let i=0;i<5 && c.parentElement;i++){c=c.parentElement; if(c.querySelector('iframe,video,img:not([src^="data"])')) break;}
  const t = (c.innerText||'');
  return {
    hasIframe: !!c.querySelector('iframe'),
    hasVideo: !!c.querySelector('video'),
    hasImg: !!c.querySelector('img:not([src^="data"])'),
    fmt: (t.match(/텍스트|이미지|동영상|반응형|Text|Image|Video/)||[''])[0],
    text: t.replace(/\\s+/g,' ').trim().slice(0,220)
  };
}
"""


def _classify(info: dict) -> str:
    fmt = info.get("fmt", "")
    if info.get("hasVideo") or "동영상" in fmt or "Video" in fmt:
        return "video"
    if "반응형" in fmt:
        return "display"
    if info.get("hasIframe") or info.get("hasImg") or "이미지" in fmt or "Image" in fmt:
        return "image"
    if "텍스트" in fmt or "Text" in fmt:
        return "search_text"
    return "unknown"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def search_brand(brand: str, region: str = "KR", scrolls: int = 5, limit: int = 30,
                 headless: bool = True, shot: bool = False,
                 advertiser_id: str = "") -> tuple[list[dict], dict]:
    """advertiser_id를 주면 검색/자동완성 대신 광고주 페이지로 직접 진입해 전체 광고를 수집."""
    from playwright.sync_api import sync_playwright

    log = {"found": 0, "kept": 0, "excluded_search_text": 0,
           "excluded_no_media": 0, "dedup_removed": 0}
    ads: list[dict] = []
    seen_keys: set = set()

    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless)
        # device_scale_factor=2 → 소재 스크린샷을 2배 해상도로(이미지 광고 화질 개선)
        ctx = br.new_context(locale="ko-KR", user_agent=UA,
                             viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        page = ctx.new_page()
        try:
            if advertiser_id:
                # advertiser ID로 광고주 페이지 직접 진입(검색/자동완성 우회)
                page.goto(f"https://adstransparency.google.com/advertiser/{advertiser_id}?region={region}",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                try:
                    page.wait_for_selector('a[href*="/creative/"]', timeout=20000)
                except Exception:  # noqa: BLE001
                    pass
            else:
                page.goto(f"https://adstransparency.google.com/?region={region}",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                for sel in ('input[aria-label]', 'input[type="text"]', 'input'):
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click(); el.fill(brand); page.wait_for_timeout(2500)
                        opt = page.query_selector('[role="option"]')
                        opt.click() if opt else page.keyboard.press("Enter")
                        page.wait_for_timeout(5000)
                        break
            for _ in range(scrolls):
                page.mouse.wheel(0, 6000)
                page.wait_for_timeout(2000)

            anchors = page.query_selector_all('a[href*="/creative/"]')
            for a in anchors:
                # 절대 URL 로 해석(상대경로 저장 방지)
                try:
                    href = a.evaluate("e => e.href") or a.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    href = a.get_attribute("href") or ""
                m = re.search(r"/creative/([A-Za-z0-9_-]+)", href)
                if not m:
                    continue
                cid = m.group(1)
                # 중복 제거: 같은 creative_id 는 1회만(그리드에 앵커 중복)
                if cid in seen_keys:
                    log["dedup_removed"] += 1
                    continue
                seen_keys.add(cid)
                log["found"] += 1
                try:
                    info = a.evaluate(_CLASSIFY_JS)
                except Exception:  # noqa: BLE001
                    info = {}
                fmt = _classify(info)
                if fmt == "search_text":
                    log["excluded_search_text"] += 1
                    continue
                media_present = info.get("hasIframe") or info.get("hasVideo") or info.get("hasImg")
                if fmt == "unknown" or not media_present:
                    log["excluded_no_media"] += 1
                    continue
                if len(ads) >= limit:
                    continue
                # 단일 소재 카드만 스크린샷 → static 파일로 저장(base64 금지)
                thumb = ""
                try:
                    handle = a.evaluate_handle(
                        "el=>{let c=el;for(let i=0;i<5&&c.parentElement;i++){c=c.parentElement;"
                        "if(c.querySelector('iframe,video,img'))break;}return c;}").as_element()
                    if handle:
                        box = handle.bounding_box()
                        if box and 60 <= box["height"] <= 520 and box["width"] <= 520:
                            handle.scroll_into_view_if_needed(timeout=4000)
                            page.wait_for_timeout(300)
                            _STATIC.mkdir(parents=True, exist_ok=True)
                            safe = "".join(ch for ch in cid if ch.isalnum() or ch in "_-")
                            handle.screenshot(timeout=6000, path=str(_STATIC / f"{safe}.png"))
                            thumb = f"app/static/thumbnails/{safe}.png"
                except Exception:  # noqa: BLE001
                    pass
                if not thumb:
                    log["excluded_no_media"] += 1
                    continue
                turl = normalize_google_transparency_url(href)
                ads.append({
                    "platform": PLATFORM, "platform_ad_id": cid,
                    "brand_name": brand, "advertiser_name": brand,
                    "ad_title": "", "ad_copy": "",   # 구글 그리드엔 실제 카피 없음(스크린샷이 레퍼런스)
                    "ad_format": fmt, "media_type": "video" if fmt == "video" else "image",
                    "thumbnail_url": thumb, "video_url": "",
                    "preview_url": thumb, "media_url": "",
                    "landing_url": "", "transparency_url": turl or "",
                    "original_ad_url": turl or "", "status": "live",
                    "raw_data": {"creative_id": cid, "fmt": fmt}, "_href": href,
                    "_iframe": bool(info.get("hasIframe")),
                })
                log["kept"] += 1

            # 구글 영상광고는 safeframe iframe(=유튜브)로 렌더돼 분류가 image로 잡힘.
            # iframe 있는 소재의 상세를 열어 중첩 frame의 youtube embed ID를 추출 → 찾으면 video로 재분류.
            dp = ctx.new_page()
            for adrow in ads:
                if not adrow.get("_iframe") or not adrow.get("_href"):
                    adrow["raw_data"]["collection_status"] = "thumbnail_only"
                    continue
                res = _extract_google_video(dp, adrow["_href"], adrow["platform_ad_id"])
                adrow["raw_data"].update({
                    "variant_count": res["variant_count"],
                    "selected_variant_index": res["variant_index"],
                    "is_youtube_embedded": bool(res["youtube_id"]),
                    "collection_status": "playable_video" if res["youtube_id"] else "thumbnail_only",
                })
                adrow["google_ad_url"] = adrow.get("transparency_url", "")
                # 이미지 대안: 가장 화질 좋은 변형 스크린샷으로 썸네일 교체(있으면)
                if not res["youtube_id"] and res.get("image_thumb"):
                    adrow["thumbnail_url"] = res["image_thumb"]
                    adrow["preview_url"] = res["image_thumb"]
                    adrow["local_thumbnail_path"] = res["image_thumb"]
                    adrow["scrape_status"] = "shot"
                if res["youtube_id"]:
                    vid = res["youtube_id"]
                    adrow["video_url"] = f"https://www.youtube.com/watch?v={vid}"
                    adrow["ad_format"] = "video"
                    adrow["media_type"] = "video"
                    adrow["raw_data"]["youtube_id"] = vid
                    # 썸네일을 유튜브 고화질로 교체(스크린샷 깨짐 방지)
                    adrow["thumbnail_url"] = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    adrow["preview_url"] = adrow["thumbnail_url"]
                    adrow["local_thumbnail_path"] = ""
                    log["youtube_linked"] = log.get("youtube_linked", 0) + 1
                    # 구글 영상광고=유튜브 영상 → 유튜브 공개 지표(조회수/좋아요/댓글) 수집
                    try:
                        import services.youtube as _YT
                        if _YT.is_enabled():
                            v = _YT.fetch_video(vid)
                            if v:
                                adrow["yt_views"] = v.get("views", 0)
                                adrow["yt_likes"] = v.get("likes", 0)
                                adrow["yt_comments"] = v.get("comments", 0)
                    except Exception:  # noqa: BLE001
                        pass
            dp.close()
            for adrow in ads:
                adrow.pop("_href", None)
                adrow.pop("_iframe", None)

            if shot:
                Path("data").mkdir(exist_ok=True)
                page.screenshot(path=f"data/_google_{brand}.png")
        finally:
            br.close()
    return ads, log


def _scan_youtube(page) -> str:
    """현재 대안에서 youtube 영상 ID 탐지: 중첩 frame(embed) → 앵커 → 페이지 HTML 순."""
    # 1) 중첩 frame의 embed
    for f in page.frames:
        m = re.search(r"youtube\.com/embed/([A-Za-z0-9_-]{11})", f.url or "")
        if m:
            return m.group(1)
    # 2) 앵커/HTML의 watch·embed·youtu.be (Visit advertiser 등으로 붙는 경우)
    try:
        hrefs = page.eval_on_selector_all(
            'a[href*="youtu"]', 'els=>els.map(e=>e.href)')
    except Exception:  # noqa: BLE001
        hrefs = []
    for h in hrefs:
        m = re.search(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})", h or "")
        if m:
            return m.group(1)
    try:
        html = page.content()
        m = re.search(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})", html)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _creative_box(page):
    """상세의 소재 렌더 영역 핸들(스크린샷 대상). creative-bounding-box → 가장 큰 iframe 순."""
    el = page.query_selector(".creative-bounding-box")
    if el:
        try:
            bb = el.bounding_box()
            if bb and bb["width"] >= 120:
                return el, bb["width"] * bb["height"]
        except Exception:  # noqa: BLE001
            pass
    # 폴백: 가장 큰 iframe
    best, area = None, 0
    for f in page.query_selector_all("iframe"):
        try:
            bb = f.bounding_box()
            if bb and bb["width"] >= 120 and bb["width"] * bb["height"] > area:
                best, area = f, bb["width"] * bb["height"]
        except Exception:  # noqa: BLE001
            continue
    return best, area


def _extract_google_video(page, url: str, cid: str) -> dict:
    """소재 상세의 대안 carousel을 끝까지 순회.
    - 유튜브 영상 발견 → youtube_id 반환(video)
    - 영상 없으면 → 가장 화질 좋은(가장 큰) 대안을 고해상 스크린샷해 image_thumb 반환
    반환 {youtube_id, variant_index, variant_count, image_thumb}."""
    out = {"youtube_id": "", "variant_index": 0, "variant_count": 1, "image_thumb": ""}
    safe = "".join(ch for ch in cid if ch.isalnum() or ch in "_-")
    # 네트워크 요청에서 youtube embed 영상 ID 포착(frame엔 안 떠도 player 요청은 감) — 순서 유지
    net_ids: list = []

    def _on_req(r):
        mm = re.search(r"youtube\.com/embed/([A-Za-z0-9_-]{11})", r.url or "")
        if mm and mm.group(1) not in net_ids:
            net_ids.append(mm.group(1))
    try:
        page.on("request", _on_req)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4500)
        body = page.inner_text("body") or ""
        m = re.search(r"\b(\d+)\s*/\s*(\d+)\b", body)
        if m:
            out["variant_count"] = min(int(m.group(2)), 12)
        n = out["variant_count"] or 1
        best_area = 0
        for i in range(n):
            vid = _scan_youtube(page) or (net_ids[0] if net_ids else "")
            if vid:
                out["youtube_id"] = vid
                out["variant_index"] = i + 1
                try:
                    page.remove_listener("request", _on_req)
                except Exception:  # noqa: BLE001
                    pass
                return out
            # 이미지 대안: 소재 영역을 고해상 스크린샷, 가장 큰 변형을 채택
            try:
                el, area = _creative_box(page)
                if el and area > best_area:
                    el.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(250)
                    _STATIC.mkdir(parents=True, exist_ok=True)
                    el.screenshot(timeout=6000, path=str(_STATIC / f"{safe}.png"))
                    out["image_thumb"] = f"app/static/thumbnails/{safe}.png"
                    out["variant_index"] = i + 1
                    best_area = area
            except Exception:  # noqa: BLE001
                pass
            if i < n - 1:
                btn = page.query_selector("button.variation-right-arrow, .variation-right-arrow")
                if not btn:
                    break
                try:
                    btn.click(timeout=3000)
                except Exception:  # noqa: BLE001
                    break
                page.wait_for_timeout(2500)
    except Exception:  # noqa: BLE001
        pass
    # 순회 후에도 frame엔 없지만 네트워크로 잡힌 embed가 있으면 그걸 채택
    if not out["youtube_id"] and net_ids:
        out["youtube_id"] = net_ids[0]
        out["variant_index"] = out["variant_index"] or 1
    try:
        page.remove_listener("request", _on_req)
    except Exception:  # noqa: BLE001
        pass
    return out


def fetch_advertiser_names(brand: str, region: str = "KR", limit: int = 6) -> list[str]:
    """투명성센터 검색창에 브랜드명을 입력해 자동완성에 뜨는 '광고주(법인)명' 후보를 반환.
    사용자가 법인명을 몰라도 브랜드명만으로 '주식회사 OOO' 후보를 제안하기 위함."""
    from playwright.sync_api import sync_playwright

    names: list[str] = []
    seen: set = set()
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(locale="ko-KR", user_agent=UA,
                             viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        try:
            page.goto(f"https://adstransparency.google.com/?region={region}",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            for sel in ('input[aria-label]', 'input[type="text"]', 'input'):
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    el.fill(brand)
                    page.wait_for_timeout(3500)
                    break
            for o in page.query_selector_all('[role="option"]'):
                try:
                    t = (o.inner_text() or "").strip().split("\n")[0].strip()
                except Exception:  # noqa: BLE001
                    continue
                # 자동완성 안내 문구/검색 옵션은 제외
                if not t or "검색" in t or t.lower() == brand.lower():
                    continue
                if t not in seen:
                    seen.add(t)
                    names.append(t)
                if len(names) >= limit:
                    break
        finally:
            br.close()
    return names


def collect() -> list[dict]:
    wl = json.loads((config.DATA_DIR / "watchlist.json").read_text(encoding="utf-8"))
    out = []
    for b in wl.get("brands", []):
        try:
            ads, log = search_brand(b)
            print(f"  [google-lib] '{b}' 발견 {log['found']} / 저장 {log['kept']} "
                  f"(검색광고 제외 {log['excluded_search_text']}, 미디어없음 {log['excluded_no_media']}, "
                  f"중복 {log['dedup_removed']})")
            out.extend(ads)
        except Exception as e:  # noqa: BLE001
            print(f"  [google-lib] '{b}' 실패: {e}")
    return out


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    ads, log = search_brand(term, headless=True, shot=True, limit=8)
    print(f"\n로그: {log}")
    for a in ads[:8]:
        print(f"  - {a['ad_format']:6} thumb={'O' if a['thumbnail_url'] else 'X'} "
              f"({len(a['thumbnail_url'])//1000}KB) | {a['ad_copy'][:40]}")
