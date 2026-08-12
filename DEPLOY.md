# Series Archive — Streamlit Cloud 배포

데이터는 **Supabase**(Postgres + Storage)가 원본입니다. 이 레포에는 코드만 있고
DB 파일·썸네일은 들어 있지 않습니다.

## 배포 설정

| 항목 | 값 |
|---|---|
| Repository | `memeanji/series-archive-deploy` |
| Branch | `main` |
| **Main file path** | `app.py` |
| Python version | 3.11 이상 |

## Secrets (Settings → Secrets)

```toml
SUPABASE_URL = "https://<프로젝트>.supabase.co"
SUPABASE_SERVICE_KEY = "<service key>"
SUPABASE_READ_ALL = "true"          # 번들 DB 없이 Supabase만으로 조회

[auth.users]
repurely = "<비밀번호>"
```

· `SUPABASE_READ_ALL=true` 이면 앱이 부팅 시 Supabase에서 목록 데이터를 받아
  로컬 미러(.cache)를 만들고, **기존 조회 SQL을 그대로** 실행합니다(결과 동일).
· 조회수 추이는 상세 진입 시에만 조회하고, 썸네일은 Storage 공개 URL로 직접 로드합니다.
· 첫 부팅은 미러 생성으로 약 50초, 이후 화면 전환은 1초 내외입니다(미러 TTL 300초).

## 선택 설정

```toml
SUPABASE_MIRROR_TTL = "300"    # 미러 갱신 주기(초)
SUPABASE_PAGE_SIZE = "5000"    # 한 번에 받아오는 행 수
GEMINI_API_KEY = "..."         # 영상 스크립트 생성 기능을 쓸 때만
YOUTUBE_API_KEY = "..."        # 조회수 수집을 클라우드에서 돌릴 때만(보통 불필요)
```
