"""
브랜드명 → 구글 투명성센터 자동완성 '법인명(광고주)' 후보 추출 (subprocess 용).
UI '후보 찾기'가 호출해 법인명 칸을 자동 채운다.
출력: 마지막 줄에  ADVJSON:["주식회사 OOO", ...]
사용:  python jobs/google_advertisers.py "레이셀턴"
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import google_library_crawler as G  # noqa: E402

if __name__ == "__main__":
    brand = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        names = G.fetch_advertiser_names(brand) if brand else []
    except Exception as e:  # noqa: BLE001
        print(f"[err] {e}", file=sys.stderr)
        names = []
    print("ADVJSON:" + json.dumps(names, ensure_ascii=False))
