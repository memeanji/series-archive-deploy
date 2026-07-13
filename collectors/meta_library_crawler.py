"""
메타 Ad Library 웹페이지 직접 크롤러 (Playwright).
API 가 아니라 사람이 보는 공개 라이브러리 페이지를 렌더링해 광고를 추출한다.
  - Ad Library API(ads_archive) 는 앱 승인(에러10)·지역제한이 있어 한국 상업광고를 못 줌.
  - 웹페이지는 그 제한 없이 보이므로 브랜드 검색 결과를 그대로 긁는다.

단독 테스트:  python collectors/meta_library_crawler.py 더스크랙
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (stdout utf-8 재설정 포함)

PLATFORM = "meta"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_STATIC = Path(__file__).resolve().parent.parent / "static" / "thumbnails"


def _save_thumb(url: str, ad_id: str) -> str:
    """fbcdn 서명 썸네일은 만료되므로 크롤 시점에 static 파일로 내려받아 영구 보존."""
    if not url or not url.startswith("http"):
        return url
    try:
        import requests
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and r.content:
            _STATIC.mkdir(parents=True, exist_ok=True)
            safe = "".join(ch for ch in str(ad_id) if ch.isalnum() or ch in "_-")
            (_STATIC / f"m_{safe}.jpg").write_bytes(r.content)
            return f"app/static/thumbnails/m_{safe}.jpg"
    except Exception:  # noqa: BLE001
        pass
    return url  # 실패 시 원본 URL 폴백

_NAV = {
    "Meta 광고 라이브러리", "광고 라이브러리", "광고 라이브러리 보고서", "광고 라이브러리 API",
    "브랜디드 콘텐츠", "대한민국", "모든 광고", "키워드 또는 광고주로 검색", "로그인",
    "시스템 상태", "이메일 업데이트 구독", "FAQ", "광고 및 데이터 사용 정보",
    "개인 정보 보호", "이용 약관", "쿠키", "필터", "정렬", "정렬 기준",
    "광고", "플랫폼", "드롭다운 열기", "활성", "비활성", "광고 상세 정보 보기",
}

# 현재 DOM에 로드된 고유 광고 ID 수 — 스크롤 종료 판정용(가벼움)
_JS_COUNT = r"""
() => { const s = new Set();
  for (const el of document.querySelectorAll('div')) {
    const m = (el.innerText || '').match(/(?:Library ID|라이브러리 ID)[:\s]*([0-9]{6,})/);
    if (m) s.add(m[1]);
  } return s.size; }
"""

# 카드 추출 JS — 'Library ID/라이브러리 ID' 텍스트를 가진 최소 블록을 카드로 본다.
_JS_EXTRACT = r"""
() => {
  const out = [], seen = new Set();
  for (const el of Array.from(document.querySelectorAll('div'))) {
    const t = el.innerText || '';
    const m = t.match(/(?:Library ID|라이브러리 ID)[:\s]*([0-9]{6,})/);
    if (!m) continue;
    let card = el;
    for (let i = 0; i < 6 && card.parentElement; i++) {
      if ((card.innerText || '').length > 80) break;
      card = card.parentElement;
    }
    const id = m[1];
    if (seen.has(id)) continue;
    seen.add(id);
    card.setAttribute('data-sa-card', id);   // Python에서 screenshot 폴백용 위치표시
    const vids = Array.from(card.querySelectorAll('video'))
        .map(v => v.src || (v.querySelector('source')||{}).src).filter(Boolean);
    const posters = Array.from(card.querySelectorAll('video')).map(v => v.poster).filter(Boolean);
    // 이미지 후보를 '화면 표시 크기(rendered)'+'카드 내 세로 위치'와 함께 수집.
    // 프로필/로고는 (1) 작거나 (2) 카드 상단 헤더(페이지명 옆)에 있음 → 둘 다로 걸러냄.
    const cardTop = card.getBoundingClientRect().top;
    const imgInfo = Array.from(card.querySelectorAll('img'))
        .filter(i => i.src && !i.src.startsWith('data:'))
        .map(i => { const rc = i.getBoundingClientRect();
                    return { src: i.src,
                             w: rc.width || i.clientWidth || 0,
                             h: rc.height || i.clientHeight || 0,
                             top: rc.top - cardTop }; });
    // 상단 헤더 밴드(~70px)에 있고 작은 이미지는 프로필/로고로 보고 제외(큰 소재는 유지)
    let pool = imgInfo.filter(x => x.top >= 70 || Math.min(x.w, x.h) >= 170);
    if (!pool.length) pool = imgInfo;
    pool.sort((a, b) => (b.w * b.h) - (a.w * a.h));
    const bigImgs = pool.filter(x => Math.min(x.w, x.h) >= 100);
    const creativeImg = (bigImgs[0] || pool[0] || {}).src || '';
    // background-image url 후보
    let bg = '';
    for (const e of card.querySelectorAll('*')) {
      const b = getComputedStyle(e).backgroundImage || '';
      const mm = b.match(/url\(["']?(https?:[^"')]+)["']?\)/);
      if (mm) { bg = mm[1]; break; }
    }
    const links = Array.from(card.querySelectorAll('a')).map(a => a.href).filter(Boolean);
    // 광고주 page_id 후보 — 카드 링크의 view_all_page_id= / page_id= 에서 추출
    let page_id = '';
    for (const h of links) {
      const pm = (h || '').match(/(?:view_all_page_id|page_id)=([0-9]+)/);
      if (pm) { page_id = pm[1]; break; }
    }
    // 영상 광고: poster만 사용(없으면 비워 둬서 Python 스크린샷 폴백 → 프로필 img 회피)
    // 이미지 광고: 가장 큰 소재 이미지 우선
    let thumb = '', src = 'none';
    if (vids.length) {
      if (posters[0]) { thumb = posters[0]; src = 'poster'; }
      // poster 없으면 thumb 비움 → Python에서 카드 screenshot 폴백(프로필 img 안 씀)
    } else {
      if (creativeImg) { thumb = creativeImg; src = 'img'; }
      else if (bg) { thumb = bg; src = 'bg'; }
    }
    // 게재 플랫폼(Facebook/Instagram/Messenger/Audience Network) 추출 — aria-label/아이콘 src 기반
    const platSet = ['Facebook', 'Instagram', 'Messenger', 'Audience Network', 'Threads'];
    const plats = [];
    for (const e of card.querySelectorAll('[aria-label]')) {
      const al = e.getAttribute('aria-label') || '';
      for (const pf of platSet) if (al.includes(pf) && !plats.includes(pf)) plats.push(pf);
    }
    for (const i of card.querySelectorAll('img')) {
      const s = (i.src || '').toLowerCase();
      if (s.includes('facebook') && !plats.includes('Facebook')) plats.push('Facebook');
      if (s.includes('instagram') && !plats.includes('Instagram')) plats.push('Instagram');
    }
    out.push({ library_id: id, has_video: vids.length > 0,
               video_url: vids[0] || '', thumbnail_url: thumb, thumb_src: src,
               platforms: plats, links: links.slice(0, 8), page_id: page_id,
               text: (card.innerText || '').trim().slice(0, 1200) });
  }
  return out;
}
"""


def resolve_page_id(ad_id_or_url: str, country: str = "KR", headless: bool = True) -> dict:
    """라이브러리 ID(또는 링크)로 광고주 page_id·page_name 해석.
       반환 {library_id, page_id, page_name}. 실패 시 page_id=''."""
    import re as _re
    from collections import Counter as _Counter
    from playwright.sync_api import sync_playwright
    m = _re.search(r"id=(\d{6,})", ad_id_or_url) or _re.search(r"(\d{6,})", ad_id_or_url)
    if not m:
        return {"library_id": "", "page_id": "", "page_name": ""}
    lib = m.group(1)
    url = (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
           f"&country={country}&id={lib}&media_type=all")
    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless)
        pg = br.new_context(locale="ko-KR", user_agent=UA,
                            viewport={"width": 1440, "height": 1000}).new_page()
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                pg.wait_for_selector("text=/라이브러리 ID|Library ID/", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            pg.wait_for_timeout(2500)
            html = pg.content()
        except Exception:  # noqa: BLE001
            return {"library_id": lib, "page_id": "", "page_name": ""}
        finally:
            br.close()
    hits = _re.findall(r'(?:view_all_page_id["\\=:%\s]*?|"page_id"\s*:\s*")(\d{6,})', html)
    pid = _Counter(hits).most_common(1)[0][0] if hits else ""
    nm = _re.findall(r'"page_name"\s*:\s*"([^"]{1,80})"', html)
    pname = _Counter(nm).most_common(1)[0][0] if nm else ""
    return {"library_id": lib, "page_id": pid, "page_name": pname}


def _pick_landing(links: list[str]) -> str:
    """l.facebook.com 리다이렉트(u=)를 풀어 실제 랜딩 URL을 고른다."""
    for href in links:
        if "l.facebook.com" in href and "u=" in href:
            u = parse_qs(urlparse(href).query).get("u", [""])[0]
            if u:
                return unquote(u)
    for href in links:
        host = urlparse(href).netloc
        if host and "facebook.com" not in host and "fbcdn" not in host:
            return href
    return ""


def _parse_card(r: dict, brand: str) -> dict:
    text = r.get("text", "")
    # 카드가 다음 광고까지 먹었으면 두 번째 '라이브러리 ID' 앞에서 자른다
    ids = [m.start() for m in re.finditer(r"라이브러리 ID", text)]
    if len(ids) > 1:
        text = text[:ids[1]]
    dm = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.", text)
    started = (f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
               if dm else None)
    body = text.split("광고 상세 정보 보기", 1)[1] if "광고 상세 정보 보기" in text else text
    lines = []
    for ln in body.split("\n"):
        ln = ln.replace("​", "").strip()
        if not ln or ln in _NAV or ln.startswith("결과 ") or ln.startswith("이 결과") \
                or "라이브러리 ID" in ln or "게재 시작" in ln or re.match(r"^\d+:\d+\s*/", ln):
            continue
        lines.append(ln)
    page_name = lines[0] if lines else brand
    page_name = re.sub(r"\s*페이지는.*함께합니다$", "", page_name)
    cta_set = ["지금 구매하기", "구매하기", "지금 주문하기", "주문하기", "자세히 알아보기",
               "더 알아보기", "자세히 보기", "지금 신청하기", "신청하기", "문의하기",
               "지금 예약하기", "예약하기", "다운로드", "가입하기", "무료 체험하기",
               "지금 이용해 보기", "쇼핑하기", "지금 쇼핑", "할인받기", "더보기"]
    full_text = r.get("text", "")
    cta = next((c for c in cta_set if c in full_text), "")
    # A/B 테스트: "이 크리에이티브 및 문구를 사용하는 광고 N개" / "광고 N개에서 …" → N 추출
    vm = (re.search(r"광고\s*(\d+)\s*개[^\n]{0,40}(?:크리에이티브|문구)", full_text)
          or re.search(r"(?:크리에이티브|문구)[^\n]{0,40}광고\s*(\d+)\s*개", full_text))
    variant_count = int(vm.group(1)) if vm else 1
    return {
        "started": started,
        "page_name": page_name,
        "ad_text": "\n".join(lines),
        "landing": _pick_landing(r.get("links", [])),
        "cta": cta,
        "variant_count": variant_count,
    }


def search_brand(brand: str, country: str = "KR", scrolls: int = 6,
                 headless: bool = True, shot: bool = False, retries: int = 1,
                 page_id: str = "", ad_id: str = "",
                 max_scroll: int = 80, no_new_ads_limit: int = 5,
                 media_type: str = "all", publisher_platform: str = "") -> list[dict]:
    """Playwright 렌더 → 다단계 썸네일 추출 → 실패 시 카드 screenshot 폴백.
    page_id 주면 광고주 전체 크롤, ad_id 주면 단건. media_type=all/video/image,
    publisher_platform=facebook/instagram 로 경로 다중화(수집률↑)."""
    from playwright.sync_api import sync_playwright

    _pf = f"&publisher_platforms[0]={publisher_platform}" if publisher_platform else ""
    if ad_id:
        url = (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
               f"&country={country}&id={ad_id}&media_type={media_type}{_pf}")
    elif page_id:
        url = (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
               f"&country={country}&view_all_page_id={page_id}&search_type=page"
               f"&media_type={media_type}{_pf}")
    else:
        url = (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
               f"&country={country}&q={quote(brand)}&search_type=keyword_unordered"
               f"&media_type={media_type}{_pf}")
    rows: list[dict] = []
    ads: list[dict] = []
    stats = {"img": 0, "poster": 0, "bg": 0, "screenshot": 0, "failed": 0}

    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless)
        ctx = br.new_context(locale="ko-KR", user_agent=UA,
                             viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        try:
            ok = False
            for attempt in range(retries + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_selector("text=/라이브러리 ID|Library ID/", timeout=15000)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:  # noqa: BLE001
                        pass
                    ok = True
                    break
                except Exception:  # noqa: BLE001
                    page.wait_for_timeout(3000)
            if not ok:
                return []
            # 새 광고 ID가 더 안 늘 때까지 스크롤(무한루프 방지 max_scroll). ad_id 단건은 스크롤 불필요.
            done_scrolls = 1
            if not ad_id:
                last_cnt, stale = 0, 0
                for i in range(max_scroll):
                    page.mouse.wheel(0, 6000)
                    page.wait_for_timeout(2800)              # 로딩 여유(보수적)
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)  # 스피너/요청 종료 대기
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        cnt = page.evaluate(_JS_COUNT)
                    except Exception:  # noqa: BLE001
                        cnt = last_cnt
                    if cnt <= last_cnt:
                        stale += 1
                    else:
                        stale, last_cnt = 0, cnt
                    if stale >= no_new_ads_limit:
                        # 종료 직전 한 번 더 끝까지 + 재스캔(누락 방지)
                        page.mouse.wheel(0, 400000)
                        page.wait_for_timeout(2500)
                        try:
                            cnt2 = page.evaluate(_JS_COUNT)
                        except Exception:  # noqa: BLE001
                            cnt2 = last_cnt
                        if cnt2 > last_cnt:
                            last_cnt, stale = cnt2, 0
                            continue
                        break
                done_scrolls = i + 1
                print(f"  [meta-scroll] '{brand or page_id or ad_id}' "
                      f"스크롤 {done_scrolls}회 · 광고ID {last_cnt}개 로드")
            # lazy-load 소재 이미지가 모두 뜨도록: 천천히 위로 되감으며 viewport 통과시키고 대기
            for _ in range(min(done_scrolls, 12)):
                page.mouse.wheel(0, -4000)
                page.wait_for_timeout(700)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            page.mouse.wheel(0, 200000)  # 다시 끝까지(레이아웃 확정)
            page.wait_for_timeout(1500)
            rows = page.evaluate(_JS_EXTRACT)

            # 광고주 page_id 후보 + 라이브러리 표기 결과수
            cand_pid = page_id or ""
            reported = 0
            try:
                import re as _re
                from collections import Counter as _Counter
                html = page.content()
                if not cand_pid:
                    hits = _re.findall(r'(?:view_all_page_id["\\=:%\s]*?|"page_id"\s*:\s*")(\d{6,})', html)
                    if hits:
                        cand_pid = _Counter(hits).most_common(1)[0][0]
                txt = page.inner_text("body")
                rm = (_re.search(r"약\s*([\d,]+)\s*개", txt)
                      or _re.search(r"~?\s*([\d,]+)\s*results", txt, _re.I)
                      or _re.search(r"([\d,]+)\s*개의?\s*(?:광고|결과)", txt))
                if rm:
                    reported = int(rm.group(1).replace(",", ""))
            except Exception:  # noqa: BLE001
                pass

            for r in rows:
                cid = r["library_id"]
                parsed = _parse_card(r, brand)
                status, err, thumb = "failed", "", ""
                # 1) img/poster/bg 후보 다운로드
                if r.get("thumbnail_url"):
                    saved = _save_thumb(r["thumbnail_url"], cid)
                    if saved.startswith("app/static"):
                        thumb, status = saved, r.get("thumb_src", "img")
                    else:
                        err = "이미지 다운로드 실패"
                # 2) 폴백: 카드 element screenshot
                if not thumb:
                    try:
                        el = page.query_selector(f'[data-sa-card="{cid}"]')
                        if el:
                            box = el.bounding_box()
                            if box and box["height"] <= 700:
                                el.scroll_into_view_if_needed(timeout=3000)
                                page.wait_for_timeout(200)
                                _STATIC.mkdir(parents=True, exist_ok=True)
                                el.screenshot(timeout=6000, path=str(_STATIC / f"m_{cid}.jpg"))
                                thumb, status, err = f"app/static/thumbnails/m_{cid}.jpg", "screenshot", ""
                    except Exception as e:  # noqa: BLE001
                        err = f"screenshot 실패: {str(e)[:80]}"
                stats[status] = stats.get(status, 0) + 1
                ads.append({
                    "platform": PLATFORM, "platform_ad_id": cid, "advertiser_name": brand,
                    "headline": parsed["page_name"], "ad_text": parsed["ad_text"], "transcript": "",
                    "media_type": "video" if r["has_video"] else "image",
                    "video_url": r["video_url"], "thumbnail_url": thumb,
                    "local_thumbnail_path": thumb if thumb.startswith("app/static") else "",
                    "landing_url": parsed["landing"],
                    "original_ad_url": f"https://www.facebook.com/ads/library/?id={cid}",
                    "status": "live", "first_seen": parsed["started"], "started_at": parsed["started"],
                    "cta": parsed.get("cta", ""),
                    "ad_variant_count": parsed.get("variant_count", 1),
                    "platforms": ", ".join(r.get("platforms") or []),
                    "scrape_status": status, "error_message": err,
                    "page_id": page_id or r.get("page_id") or cand_pid or "",  # 수집/카드/HTML스캔 후보
                    "reported_count": reported,   # 이 경로에서 라이브러리가 표기한 결과 수
                    "views": 0, "likes": 0, "comments": 0, "shares": 0, "raw_data": r,
                })
        finally:
            br.close()

    _drop_profile_thumbs(ads, stats)
    okc = len(ads) - stats["failed"]
    print(f"  [meta-thumb] '{brand}' 성공 {okc}/{len(ads)} "
          f"(img {stats['img']}, poster {stats['poster']}, bg {stats['bg']}, "
          f"shot {stats['screenshot']}, 실패 {stats['failed']}, 프로필제거 {stats.get('profile',0)})")
    return ads


def _drop_profile_thumbs(ads: list[dict], stats: dict) -> None:
    """브랜드의 여러 광고에서 '같은 이미지'가 반복되면 페이지 프로필/로고로 보고 썸네일 제거.
    (소재는 광고마다 다르지만 프로필 아바타는 모든 광고에 동일하게 박힘)"""
    import hashlib
    img_ads = [a for a in ads if a.get("media_type") == "image"
               and (a.get("thumbnail_url") or "").startswith("app/static")]
    if len(img_ads) < 4:
        return
    by_hash: dict = {}
    for a in img_ads:
        fp = _STATIC.parent.parent / (a["thumbnail_url"][4:] if a["thumbnail_url"].startswith("app/")
                                      else a["thumbnail_url"])
        try:
            h = hashlib.sha1(fp.read_bytes()).hexdigest()
        except Exception:  # noqa: BLE001
            continue
        by_hash.setdefault(h, []).append(a)
    thresh = max(4, int(len(img_ads) * 0.35))   # 35%+ 또는 4건+ 반복 → 프로필/로고
    for h, group in by_hash.items():
        if len(group) >= thresh:
            for a in group:
                a["thumbnail_url"] = ""
                a["local_thumbnail_path"] = ""
                a["scrape_status"] = "failed"
                a["error_message"] = "페이지 프로필/로고 추정(여러 광고 중복) — 소재 썸네일 아님"
                stats["profile"] = stats.get("profile", 0) + 1


_UNAVAILABLE_PHRASES = (
    "광고가 광고 라이브러리에 없습니다",
    "아직 노출이 발생하지 않았",
    "검색 팁을 확인",
    "isn't in the Ad Library",
    "hasn't received any impressions",
)


def verify_available(ad_ids: list[str], headless: bool = True) -> dict:
    """각 광고 permalink(?id=)를 열어 '광고 라이브러리에 없습니다' 류 문구를 감지.
    반환 {ad_id: 'unavailable' | 'ok'}. 확인 실패는 'ok'(과도한 제외 방지)."""
    from playwright.sync_api import sync_playwright
    out: dict = {}
    with sync_playwright() as p:
        br = p.chromium.launch(headless=headless)
        ctx = br.new_context(locale="ko-KR", user_agent=UA,
                             viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for aid in ad_ids:
            status = "ok"
            try:
                page.goto(f"https://www.facebook.com/ads/library/?id={aid}",
                          wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3500)
                txt = page.inner_text("body") or ""
                if any(ph in txt for ph in _UNAVAILABLE_PHRASES):
                    # 라이브러리 ID가 본문에 있으면 정상(실제 광고 표시), 없고 문구만 있으면 unavailable
                    if not re.search(r"라이브러리 ID|Library ID", txt):
                        status = "unavailable"
            except Exception:  # noqa: BLE001
                status = "ok"
            out[aid] = status
        br.close()
    return out


def collect() -> list[dict]:
    """watchlist.json 의 모든 브랜드를 검색해 합친다."""
    wl = json.loads((config.DATA_DIR / "watchlist.json").read_text(encoding="utf-8"))
    out = []
    for b in wl.get("brands", []):
        try:
            ads = search_brand(b)
            print(f"  [meta-lib] '{b}' {len(ads)}건")
            out.extend(ads)
        except Exception as e:  # noqa: BLE001
            print(f"  [meta-lib] '{b}' 실패: {e}")
    return out


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "더스크랙"
    res = search_brand(term, headless=True, shot=True)
    print(f"\n'{term}' 추출 {len(res)}건")
    for a in res[:6]:
        print(f"  - id={a['platform_ad_id']} {a['first_seen']} video={'O' if a['video_url'] else 'X'} "
              f"| {a['headline'][:20]} | {a['ad_text'][:40].replace(chr(10), ' ')}")
