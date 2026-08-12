-- ============================================================
-- Creative Radar — Supabase / PostgreSQL schema
-- Supabase SQL Editor 에 통째로 붙여넣고 실행하세요.
-- (재실행해도 안전하도록 IF NOT EXISTS 사용)
-- ============================================================

create extension if not exists "pgcrypto";  -- gen_random_uuid()

-- ── 1. ads : 광고 레퍼런스 기본 정보 ──────────────────────
create table if not exists ads (
  id uuid primary key default gen_random_uuid(),
  platform text not null,                 -- meta | tiktok | google
  platform_ad_id text,                    -- 플랫폼 고유 광고 ID(있으면 dedup 키)
  dedup_key text,                          -- platform_ad_id 없을 때 fallback 해시
  advertiser_name text,
  advertiser_id text,
  ad_text text,
  headline text,
  description text,
  cta text,
  landing_url text,
  original_ad_url text,
  media_type text,                         -- video | image | unknown
  video_url text,
  thumbnail_url text,
  status text default 'unknown',           -- live | ended | unknown
  first_seen date,
  last_seen date,
  started_at date,
  ended_at date,
  estimated_running_days int,
  views bigint,
  likes bigint,
  comments bigint,
  shares bigint,
  reference_score int default 0,
  hook_tags text[],
  format_tags text[],
  raw_data jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- dedup: platform_ad_id 가 있으면 (platform, platform_ad_id) 유일,
--        없으면 (platform, dedup_key) 유일. 부분 unique 인덱스 2개로 구현.
create unique index if not exists ux_ads_platform_adid
  on ads (platform, platform_ad_id)
  where platform_ad_id is not null;

create unique index if not exists ux_ads_platform_dedup
  on ads (platform, dedup_key)
  where platform_ad_id is null and dedup_key is not null;

create index if not exists ix_ads_score      on ads (reference_score desc);
create index if not exists ix_ads_last_seen  on ads (last_seen desc);
create index if not exists ix_ads_status     on ads (status);
create index if not exists ix_ads_platform   on ads (platform);
create index if not exists ix_ads_hook_tags  on ads using gin (hook_tags);
create index if not exists ix_ads_format_tags on ads using gin (format_tags);

-- ── 2. ad_snapshots : 매일 수집된 상태/지표 변화 ──────────
create table if not exists ad_snapshots (
  id uuid primary key default gen_random_uuid(),
  ad_id uuid references ads(id) on delete cascade,
  snapshot_date date not null,
  status text,
  views bigint,
  likes bigint,
  comments bigint,
  shares bigint,
  raw_data jsonb,
  created_at timestamptz default now()
);
-- 같은 광고를 하루 1행만(여러 번 수집해도 덮어쓰기 위해 upsert 키)
create unique index if not exists ux_snap_ad_date on ad_snapshots (ad_id, snapshot_date);
create index if not exists ix_snap_date on ad_snapshots (snapshot_date desc);

-- ── 3. bookmarks : 사용자 저장 광고 ──────────────────────
create table if not exists bookmarks (
  id uuid primary key default gen_random_uuid(),
  ad_id uuid references ads(id) on delete cascade,
  user_id uuid,
  memo text,
  user_tags text[],
  priority text default 'normal',          -- low | normal | high
  project_name text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists ix_bookmarks_user on bookmarks (user_id);
create index if not exists ix_bookmarks_project on bookmarks (project_name);

-- ── 4. keyword_trends : 네이버 데이터랩 등 외부 추이 ──────
create table if not exists keyword_trends (
  id uuid primary key default gen_random_uuid(),
  keyword text not null,
  source text not null,                    -- naver_datalab | kakao | ...
  trend_date date not null,
  value numeric,
  raw_data jsonb,
  created_at timestamptz default now()
);
create unique index if not exists ux_trend_key on keyword_trends (keyword, source, trend_date);

-- ── 5. collection_logs : 수집 실행 로그 ──────────────────
create table if not exists collection_logs (
  id uuid primary key default gen_random_uuid(),
  platform text,
  collector_name text,
  status text,                             -- success | error | partial
  collected_count int default 0,
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz default now()
);
create index if not exists ix_logs_created on collection_logs (created_at desc);

-- updated_at 자동 갱신 트리거(ads, bookmarks)
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_ads_updated on ads;
create trigger trg_ads_updated before update on ads
  for each row execute function set_updated_at();

drop trigger if exists trg_bookmarks_updated on bookmarks;
create trigger trg_bookmarks_updated before update on bookmarks
  for each row execute function set_updated_at();
