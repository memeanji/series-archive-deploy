"""라이브러리 ID 또는 라이브러리 링크로 광고주 page_id 를 찾는다.
   결과를 'PAGEID:<id>' 로 출력(앱이 파싱). 사용: python jobs/find_page_id.py <id 또는 URL>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import meta_library_crawler  # noqa: E402


def main(arg: str) -> None:
    r = meta_library_crawler.resolve_page_id(arg)
    print(f"LIBID:{r.get('library_id','')}")
    print(f"PAGEID:{r.get('page_id','')}")
    print(f"PAGENAME:{r.get('page_name','')}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python jobs/find_page_id.py <library_id|url>")
        sys.exit(1)
    main(sys.argv[1])
