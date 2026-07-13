"""11~20번 브랜드 구글 투명성센터 크롤(신규+갱신) → 매칭·재등급 → demo.db → git push.
   표기 변형 폴백(주식회사↔(주)↔핵심명) 포함. Windows 작업 스케줄러 일회성 실행용.
   사용:  python jobs/crawl_google_11_20.py
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import config  # noqa: E402
from collectors import google_library_crawler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log(m: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {m}", flush=True)


def _variants(term: str) -> list[str]:
    v = [term]
    if term.startswith("주식회사 "):
        core = term[len("주식회사 "):]
        v += [f"(주){core}", core]
    elif term.endswith(" 주식회사"):
        core = term[:-len(" 주식회사")]
        v += [f"(주){core}", core]
    elif term.startswith("(주)"):
        core = term[len("(주)"):]
        v += [f"주식회사 {core}", core]
    return list(dict.fromkeys(v))


def main() -> None:
    database.init_db()
    g = database.brand_index_groups()
    rows = sorted(g.items(), key=lambda x: x[1]["index"])[10:20]
    _log(f"=== 구글 11~20번 크롤 시작: {[k for k,_ in rows]} ===")
    total = 0
    for k, v in rows:
        idx = v["index"]
        brand = database.get_brand(k) or {}
        bid = brand.get("id") or 0
        legal = (brand.get("google_advertiser_name") or "").strip()
        base = legal or k
        t0 = _now()
        used, gads, glog = "", [], {"found": 0}
        try:
            for term in _variants(base):
                gads, glog = google_library_crawler.search_brand(term, limit=300, scrolls=8)
                if gads:
                    used = term
                    break
            gads = [{**a, "brand_name": k} for a in gads]
            saved = database.ingest_ad_library(gads) if gads else 0
            database.log_brand_collection(bid, "google", used or base, "success",
                                          glog.get("found", 0), saved,
                                          glog.get("found", 0) - saved, started=t0)
            total += saved
            via = f" via '{used}'" if used and used != base else ""
            _log(f"[{idx}] {k} (검색='{base}'{via}): 발견 {glog.get('found',0)} 저장 {saved}")
        except Exception as e:  # noqa: BLE001
            database.log_brand_collection(bid, "google", base, "error", error=str(e)[:200], started=t0)
            _log(f"[{idx}] {k}: 실패 {str(e)[:120]}")

    database.compute_matches()
    database.regrade()
    database.migrate_brands()
    _log(f"크롤 완료 · 신규/갱신 저장 {total}건")

    try:
        database.regenerate_demo_db()
        _log("demo.db 갱신 완료")
    except Exception as e:  # noqa: BLE001
        _log(f"demo.db 갱신 실패: {e}")

    if config.ENABLE_AUTO_GIT_PUSH:
        msg = f"auto: 구글 11~20번 크롤 {datetime.now():%Y-%m-%d}"
        for cmd in (["git", "add", "sample_data/demo.db"],
                    ["git", "add", "static/thumbnails"],
                    ["git", "commit", "-m", msg],
                    ["git", "push", "origin", "main"]):
            try:
                r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
                if r.returncode != 0 and cmd[1] not in ("commit",):
                    _log(f"git {cmd[1]}: {r.stderr[:120]}")
            except Exception as e:  # noqa: BLE001
                _log(f"git {cmd[1]} 오류: {e}")
        _log("=== 완료(배포 푸시) ===")
    else:
        _log("=== 완료(로컬 갱신만 · 자동 git push 비활성) ===")


if __name__ == "__main__":
    main()
