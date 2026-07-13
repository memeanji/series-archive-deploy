# Series Archive 배포 가이드 (내부 공유)

앱은 로그인(`.streamlit/secrets.toml`)으로 보호됩니다. 어떤 방식이든 secrets와 API 키를 안전히 주입하세요.

## 옵션 비교

| 방식 | 난이도 | 항상 켜짐 | 크롤링 | DB 영속 | 추천 상황 |
|------|--------|-----------|--------|---------|-----------|
| **A. 사내 LAN** | ★ | PC 켜둘 때만 | ✅ | ✅(로컬) | 같은 사무실/와이파이 |
| **B. 터널(cloudflared/ngrok)** | ★★ | PC 켜둘 때만 | ✅ | ✅(로컬) | 재택/외부에서 잠깐 공유 |
| **C. Docker on NAS/VM** | ★★★ | ✅ | ✅ | ✅(볼륨) | 상시 운영(권장) |
| **D. Streamlit Community Cloud** | ★★ | ✅ | △ 불안정 | ❌ 휘발 | 데모용(데이터 누적엔 부적합) |

> 이 앱은 **SQLite 누적 + Playwright 크롤링**이 핵심이라 장기 운영엔 **C(Docker 상시 호스트)** 가 가장 적합합니다.
> D(Community Cloud)는 파일시스템이 재배포 때 초기화돼 수집 데이터가 사라집니다.

## A. 사내 LAN (지금 바로)
```
streamlit run app.py --server.address 0.0.0.0 --server.port 8530
```
같은 네트워크에서 접속: `http://<이 PC의 LAN IP>:8530` (예: http://10.40.200.47:8530)
- Windows 방화벽에서 8530 인바운드 허용 필요할 수 있음.
- PC가 꺼지면 중단됨.

## B. 터널 (외부에서 접속)
```
# Cloudflare Tunnel (무료, 계정 불필요한 임시 URL)
cloudflared tunnel --url http://localhost:8530
# 또는 ngrok
ngrok http 8530
```
출력되는 https URL을 팀에 공유. 로그인으로 보호되지만, 외부 노출이므로 강한 비밀번호 사용.

## C. Docker (상시 운영 — NAS/클라우드 VM, 권장)
```
docker build -t series-archive .
docker run -d --name series-archive -p 8530:8530 \
  -v /path/on/host/data:/app/data \
  -v /path/on/host/secrets.toml:/app/.streamlit/secrets.toml:ro \
  -e YOUTUBE_API_KEY=... -e APIFY_TOKEN=... \
  series-archive
```
- `data` 볼륨으로 SQLite 영속.
- 시놀로지 NAS면 Container Manager에서 이 이미지를 올리면 됩니다.
- 매일 수집은 호스트 cron으로 `docker exec series-archive python jobs/collect_brands.py` 권장(스냅샷/추이 누적).

## D. Streamlit Community Cloud (데모용, 비권장)
1. GitHub 비공개 레포에 푸시(이 폴더). `.env`/`secrets.toml`/`data/*.db`는 커밋 금지.
2. share.streamlit.io 에서 레포 연결, main file = `app.py`.
3. App settings → Secrets 에 `[auth]`, `YOUTUBE_API_KEY`, `APIFY_TOKEN` 입력.
4. `packages.txt` 로 크롬 의존성 설치(아래). 단, SQLite는 휘발 → 데이터 누적 불가.

## Streamlit Cloud secrets 예시 (실제 토큰 대신 placeholder)
App settings → **Secrets** 에 아래를 붙여넣기 (TOML):
```toml
[auth]
company = "Series Builder"
[auth.users]
series = "CHANGE_ME_PASSWORD"

# 또는 평면 변수(둘 중 하나)
LOGIN_USERNAME = "series"
LOGIN_PASSWORD = "CHANGE_ME_PASSWORD"
# LOGIN_PASSWORD_HASH = "<sha256>"

YOUTUBE_API_KEY = "YOUR_YOUTUBE_API_KEY"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"   # 영상 자막 없을 때 대본 추출(폴백)
APIFY_TOKEN = "YOUR_APIFY_TOKEN"
META_ACCESS_TOKEN = "YOUR_META_TOKEN"
USE_APIFY = "true"
GOOGLE_TRANSPARENCY_PROVIDER = "scraper"
SOCIAL_VIDEO_PROVIDER = "manual"
```
코드는 **1순위 st.secrets → 2순위 .env(os.getenv)** 로 읽습니다(`config.secret()`). 토큰 값은 로그에 출력하지 않습니다.

> ⚠️ Community Cloud는 파일시스템이 재배포 때 초기화돼 **SQLite(데이터)·static 썸네일이 사라집니다.** 데이터 누적이 필요하면 **Docker 상시 호스트(C안)** 를 쓰세요.

## API 키 발급
- **YouTube**: Google Cloud Console → YouTube Data API v3 사용 설정 → API 키. (search.list는 quota 100/회 — 브랜드 추가 시에만 사용)
- **Apify**: apify.com → Settings → Integrations → API token. (무료 플랜 크레딧 내 소량 테스트 권장)
- **Meta**: developers.facebook.com 앱 토큰(Ad Library API는 앱 승인 필요 — 현재 직접 크롤링 사용 중).

## 수집 실행 방법 (렌더 시 자동 실행 안 됨)
- UI: 사이드바 **➕ 브랜드 추가 → 후보 찾기 → 저장 → 📥 지금 수집**
- CLI: `python jobs/crawl_brand.py "브랜드명"` (메타+구글+YouTube), `python jobs/collect_youtube.py`, `python jobs/test_apify.py tiktok 2 3`

## DB 초기화
- 앱 시작 시 `init_db()` 가 테이블 자동 생성 + 컬럼 안전 마이그레이션. DB가 없으면 빈 화면 + 안내.
- 완전 초기화: `data/series_archive.db` 삭제 후 재실행(수집 데이터 사라짐).

## 문제 해결
- **썸네일 안 보임**: 구글 썸네일은 `static/thumbnails/*.png` + 렌더 시 data URI 변환. 파일이 없으면(재배포로 초기화) 해당 브랜드 재수집 필요. `enableStaticServing=true` 확인.
- **API 키 없음**: 앱은 정상 동작(YouTube 등록/Apify 수집 버튼만 비활성 + 안내문). 키 넣으면 활성.
- **로그인 안 됨**: secrets의 `[auth.users]` 또는 `LOGIN_USERNAME/LOGIN_PASSWORD` 확인. 비번 바꾸면 다음 실행 시 동기화.
- **소셜 영상이 안 보임(승인 0)**: 기본은 approved만 표시. 브랜드 공식 handle/domain 미설정 시 대부분 needs_review → "검토 필요 포함" 토글 또는 상세에서 "이 브랜드 맞음"으로 승인.

## 보안 체크
- `.gitignore` 에 `.env`, `.streamlit/secrets.toml`, `data/*.db` 포함(커밋 금지).
- 로그인 계정은 secrets.toml 단일 소스(추가/변경/삭제 자동 동기화).
