# Series Archive — 광고 레퍼런스 수집기

경쟁사·레퍼런스 광고(남의 광고)를 여러 플랫폼에서 수집해 **Reference Score(0~100)** 로 점수화하고,
Supabase(또는 로컬 JSON)에 적재하는 백엔드 수집기.

> 우리 광고 성과 대시보드(GFA/Meta/TikTok × 카페24)와 별개 프로젝트.
> 이쪽은 "잘 돌아가는 **남의** 광고"를 모아 레퍼런스 풀을 만드는 용도.

## 빠른 시작 (키 없이 바로)

```bash
pip install -r requirements.txt
cp .env.example .env          # 아무것도 안 채워도 됨
python jobs/collect_all.py    # mock 데이터로 수집 → data/local_db.json
```

`.env` 가 비어 있으면 **mock 모드 + 로컬 DB**(`data/local_db.json`)로 동작해 외부 의존성 없이 즉시 실행된다.

## 실행 잡

| 명령 | 설명 |
|------|------|
| `python jobs/collect_all.py` | 전체 플랫폼 수집 → 적재 → 점수 재계산 (스케줄러가 매일 실행) |
| `python jobs/collect_meta.py` | Meta 단독 수집 |
| `python jobs/collect_tiktok.py` | TikTok 단독 수집 |
| `python jobs/update_scores.py` | 수집 없이 기존 ads 의 Reference Score 만 재계산 |

## 동작 흐름

```
collectors/*  →  jobs/_runner.run_collectors  →  store.upsert_ad / insert_snapshot
                                              →  services.scoring (전역 통계 기준 재계산)
                                              →  store.log_collection
```

- **collectors/** — 플랫폼별 수집기. 각자 `collect()` 로 표준화된 ad dict 리스트 반환.
  - `meta_collector` (Meta Ad Library API), `tiktok_collector` (TikTok), `google_collector`(2차 예정 placeholder), `naver_trend_collector`
- **services/supabase_client** — 저장 레이어. `.env` 에 Supabase 설정 있으면 PostgREST, 없으면 `LocalStore`(JSON).
  dedup: `platform_ad_id` 있으면 `(platform, platform_ad_id)`, 없으면 `(platform, dedup_key)`.
- **services/scoring** — 성과 데이터가 없으므로 공개 지표 + 집행 패턴으로 0~100 산출.
- **services/tagging**, **services/media_utils** — 후킹/포맷 태깅, 썸네일·영상 처리.

## Reference Score 가점 (services/scoring.py)

| 신호 | 점수 |
|------|------|
| 최근 90일 내 발견 | +10 |
| 영상 소재 | +10 |
| 현재 라이브 중 | +20 |
| 30일 이상 유지 | +20 |
| 60일 이상 유지 | +30 (30일과 누적 → 60일+면 +50) |
| 조회수 상위 | +20 |
| 좋아요/댓글 반응 높음 | +10 |
| 동일 광고주 변형 다수 | +15 |
| 동일 후킹 패턴 반복 | +15 |
| (여러 플랫폼 유사 패턴) | +20 — 2차 교차분석 |

최종 0~100 clamp. `build_context()` 가 전역 통계(상위 컷·광고주별 변형 수·후킹 빈도)를 먼저 계산한 뒤 건별 점수화.

## 실 API / Supabase 전환

`.env` 에서:

```bash
USE_REAL_META=true            # requests 필요
USE_REAL_TIKTOK=true
SUPABASE_URL=...              # 채우면 Supabase 모드 (supabase 패키지 필요)
SUPABASE_SERVICE_ROLE_KEY=... # service_role 키 — 서버 전용, 절대 프론트 노출 금지
```

`sql/schema.sql` 을 Supabase SQL 에디터에서 실행해 테이블(`ads`, `ad_snapshots`, `collection_logs`) 생성.

## 스케줄링

`jobs/collect_all.py` 를 GitHub Actions / cron / NAS 작업 스케줄러로 매일 1회 실행.

## 상태

- [x] 스캐폴딩 (config / collectors / services / jobs / sql / mock 데이터)
- [x] mock 파이프라인 end-to-end 검증 (7건 적재·점수화 확인)
- [x] 수집 결과 뷰어 (`streamlit run app.py`) — 점수순 카드·필터·검색
- [ ] 실 API 수집기(Meta Ad Library / TikTok) 토큰 연동·검증
- [ ] Supabase 스키마 배포 및 실 적재
- [ ] Google Transparency Center / Naver DataLab 수집 (2~3차)
