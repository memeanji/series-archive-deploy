"""
YouTube '광고' 매칭 (전체 수집과 분리된 별도 기능).
광고 데이터(광고주/법인명·문구·썸네일·랜딩·게재일)로 검색어를 만들어 YouTube 후보를 찾고,
유사도(matching_score)로 youtube_ad_matched / youtube_ad_candidate / youtube_social_or_ppl 분류.

사용:
  python jobs/match_youtube_ads.py "레이셀턴"      # 한 브랜드
  python jobs/match_youtube_ads.py                  # 구글 광고가 있는 모든 브랜드
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import services.yt_ad_match as YM  # noqa: E402
import services.youtube as YT  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
THUMB_LIMIT = 6        # 광고 썸네일 해시는 상위 N개만(속도)
MAX_VIDEOS = 25        # 브랜드당 후보 영상 상한


def _brand_context(brand: str) -> dict:
    """브랜드의 광고들(구글+메타)에서 매칭 컨텍스트 구성."""
    conn = database.get_conn()
    br = conn.execute("SELECT google_advertiser_name FROM brands WHERE display_name=?",
                      (brand,)).fetchone()
    advertiser = (br[0] if br and br[0] else brand)
    ads = [dict(r) for r in conn.execute(
        "SELECT platform, ad_copy, landing_url, thumbnail_url, started_at "
        "FROM ad_library_ads WHERE brand_name=?", (brand,)).fetchall()]
    conn.close()

    copies = list({(a["ad_copy"] or "").strip() for a in ads if (a.get("ad_copy") or "").strip()})
    landings = list({(a["landing_url"] or "").strip() for a in ads
                     if (a.get("landing_url") or "").strip()})
    # 구글 게재일(마지막) 우선
    dates = [a.get("started_at") for a in ads if a.get("started_at")]
    last_shown = max(dates) if dates else ""
    # 광고 썸네일 해시(로컬 파일 우선)
    thumbs = [a.get("thumbnail_url") for a in ads if a.get("thumbnail_url")][:THUMB_LIMIT]
    thumb_hashes = [h for h in (YM.ahash(t, root=ROOT) for t in thumbs) if h is not None]

    return {"advertiser": advertiser, "display_brand": brand, "copies": copies[:12],
            "landing_urls": landings[:8], "thumb_hashes": thumb_hashes, "last_shown": last_shown}


def match_brand(brand: str) -> dict:
    if not YT.is_enabled():
        print("  YOUTUBE_API_KEY 없음 — 건너뜀")
        return {"matched": 0, "candidate": 0, "ppl": 0}
    ctx = _brand_context(brand)
    queries = YM.build_queries(ctx["advertiser"], brand, ctx["copies"], ctx["landing_urls"])
    print(f"  검색어: {queries}")

    ids: list = []
    for q in queries:
        ids += YT.search_video_ids(q, max_results=8)
    ids = list(dict.fromkeys(ids))[:MAX_VIDEOS]
    if not ids:
        print("  후보 영상 없음")
        return {"matched": 0, "candidate": 0, "ppl": 0}

    vids = YT.fetch_videos_detailed(ids)
    rows, tally = [], {"youtube_ad_matched": 0, "youtube_ad_candidate": 0, "not_matched": 0}
    for v in vids:
        v["thumb_hash"] = YM.ahash(v.get("thumbnail_url"))
        sc = YM.score(ctx, v)
        tally[sc["match_status"]] = tally.get(sc["match_status"], 0) + 1
        rows.append({**v, "brand_name": brand, "query": queries[0],
                     "advertiser_legal_name": ctx.get("advertiser", ""),
                     "source_account_name": v.get("channel_title", ""),
                     "matching_score": sc["matching_score"],
                     "matching_confidence": sc["matching_confidence"],
                     "match_status": sc["match_status"], "matched_by": sc["matched_by"],
                     "classification": sc["match_status"], "signals": sc["signals"]})
    database.ingest_youtube_candidates(rows)
    print(f"  → 광고확정 {tally['youtube_ad_matched']} · 후보 {tally['youtube_ad_candidate']} "
          f"· 미매칭 {tally['not_matched']}")
    return {"matched": tally["youtube_ad_matched"], "candidate": tally["youtube_ad_candidate"],
            "ppl": tally["not_matched"]}


def main() -> None:
    database.init_db()
    if len(sys.argv) > 1:
        brands = [sys.argv[1]]
    else:
        # 광고가 있는 모든 브랜드(구글+메타) — 브랜드 단위로 YouTube 광고 매칭
        conn = database.get_conn()
        brands = [r[0] for r in conn.execute(
            "SELECT DISTINCT brand_name FROM ad_library_ads WHERE brand_name<>''").fetchall()]
        conn.close()
    print(f"=== YouTube 광고 매칭: {len(brands)}개 브랜드 ===")
    tot = {"matched": 0, "candidate": 0, "ppl": 0}
    for b in brands:
        print(f"\n--- {b} ---")
        try:
            r = match_brand(b)
            for k in tot:
                tot[k] += r[k]
        except Exception as e:  # noqa: BLE001
            print(f"  실패: {e}")
    print(f"\n=== 완료: 광고확정 {tot['matched']} · 후보 {tot['candidate']} "
          f"· 소셜/PPL {tot['ppl']} ===")


if __name__ == "__main__":
    main()
