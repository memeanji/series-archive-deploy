# -*- coding: utf-8 -*-
"""R2 썸네일 저장소 소량 테스트 — 실제 마이그레이션 전 검증용(안전).

수행:
  1) is_enabled() 확인
  2) DB가 참조하는 실제 썸네일 N개(기본 5) 선택
  3) R2 업로드(멱등)
  4) 공개 URL HTTP GET → 200 + image/* 확인 (Q5: 공개 URL 서빙 검증)
  5) 재업로드 → 전부 skipped 확인 (Q7: 중복 방지/재실행)
  6) --cleanup 주면 테스트 키 삭제 (Q10: 삭제 경로 검증)

전체 5.7GB 업로드/‌git 변경 없음. 테스트 파일 N개만 다룸.
사용: python jobs/r2_test_upload.py [--n 5] [--cleanup]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import database  # noqa: E402
import services.thumbnail_store as ts  # noqa: E402

import requests  # noqa: E402


def _sample_local_thumbs(n: int) -> list[Path]:
    """DB 참조 썸네일 중 실제 로컬 파일 존재하는 것 n개."""
    con = sqlite3.connect(str(database.DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT local_thumbnail_path FROM ad_library_ads "
        "WHERE local_thumbnail_path != '' LIMIT 500"
    ).fetchall()
    con.close()
    out: list[Path] = []
    for r in rows:
        v = (r["local_thumbnail_path"] or "").strip()
        if not v or v.startswith(("http", "data:")):
            continue
        rel = v[4:] if v.startswith("app/") else v
        p = database.ROOT / rel
        if p.exists():
            out.append(p)
        if len(out) >= n:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--cleanup", action="store_true", help="테스트 후 R2에서 삭제")
    args = ap.parse_args()

    print("=" * 60)
    if not ts.is_enabled():
        print("❌ R2 비활성(USE_R2!=true 또는 설정 누락). .env 확인 후 재실행.")
        print("   필요: USE_R2, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET")
        raise SystemExit(1)
    base = config.secret("R2_PUBLIC_BASE_URL")
    print(f"✅ R2 활성 · bucket={config.secret('R2_BUCKET')} · public_base={base or '(미설정!)'}")

    files = _sample_local_thumbs(args.n)
    print(f"\n[1] 테스트 대상 {len(files)}개:")
    for p in files:
        print(f"    {p.name} ({p.stat().st_size//1024}KB) → key {ts.object_key(p.name)}")
    if not files:
        raise SystemExit("로컬 썸네일 표본을 못 찾음")

    print("\n[2] 업로드(1차)")
    r1 = ts.upload_many(files, overwrite=False)
    print(f"    {r1}")

    print("\n[3] 공개 URL HTTP 검증")
    ok = 0
    for p in files:
        url = ts.public_url(p.name)
        if not url:
            print("    ⚠️ R2_PUBLIC_BASE_URL 미설정 → URL 검증 건너뜀"); break
        try:
            resp = requests.get(url, timeout=15)
            ct = resp.headers.get("content-type", "")
            good = resp.status_code == 200 and ct.startswith("image/")
            print(f"    [{resp.status_code} {ct}] {url}  {'✅' if good else '❌'}")
            ok += 1 if good else 0
        except Exception as e:  # noqa: BLE001
            print(f"    ❌ {url} → {e}")

    print("\n[4] 재업로드(멱등성) — 전부 skipped 여야 함")
    r2 = ts.upload_many(files, overwrite=False)
    print(f"    {r2}  {'✅ 멱등 OK' if r2['skipped'] == len(files) and r2['uploaded'] == 0 else '❌ 멱등 실패'}")

    if args.cleanup:
        print("\n[5] 정리(삭제)")
        d = ts.delete_files([p.name for p in files])
        print(f"    삭제 요청 {d}건")

    print("\n" + "=" * 60)
    print(f"결과: 업로드 {r1['uploaded']}·skip {r1['skipped']}·실패 {r1['failed']} / URL검증 {ok}/{len(files)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
