# -*- coding: utf-8 -*-
"""Cloudflare R2(S3 호환) 썸네일 저장소 — 독립 모듈.

설계 원칙:
  · USE_R2 != true 이면 **완전 비활성**(is_enabled()=False). 운영/앱/잡 어디도 영향 없음.
  · 앱 렌더링은 public_url()만 사용 — **순수 문자열 계산, boto3 불필요**(Cloud 앱 가볍게 유지).
  · 업로드/삭제(잡 전용)만 boto3 를 **lazy import**. boto3 미설치여도 앱은 정상.
  · 객체 키 = 'thumbnails/{원본파일명}'. DB 참조 썸네일은 전부 ASCII 안전(검증 완료).
  · 멱등: exists() head 체크로 이미 있으면 skip → 재실행 안전(중복 업로드 방지).

환경변수(.env 또는 Streamlit secrets, config.secret 로 로드):
  USE_R2                 = true|false   (기본 false = 비활성)
  R2_ACCOUNT_ID          = <cloudflare account id>
  R2_ACCESS_KEY_ID       = <R2 API 토큰 access key>
  R2_SECRET_ACCESS_KEY   = <R2 API 토큰 secret>
  R2_BUCKET              = series-archive-thumbnails
  R2_PUBLIC_BASE_URL     = https://pub-xxxx.r2.dev  또는  https://cdn.example.com  (공개 버킷/커스텀도메인)
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import config

KEY_PREFIX = "thumbnails/"


# ── 설정/활성 ────────────────────────────────────────────────
def is_enabled() -> bool:
    """USE_R2=true 이고 필수 설정이 모두 있을 때만 True."""
    if (config.secret("USE_R2") or "").strip().lower() != "true":
        return False
    return all(config.secret(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                                          "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))


def _bucket() -> str:
    return config.secret("R2_BUCKET") or ""


# ── 키/URL (순수 문자열 — boto3 불필요, 앱에서 사용) ──────────
def object_key(filename_or_path) -> str:
    """로컬 경로/파일명 → R2 객체 키. 'static/thumbnails/m_1.jpg' → 'thumbnails/m_1.jpg'."""
    return KEY_PREFIX + Path(str(filename_or_path)).name


def public_url(filename_or_path) -> str:
    """R2 공개 URL. R2_PUBLIC_BASE_URL 미설정이면 '' (→ 호출측이 로컬 폴백)."""
    base = (config.secret("R2_PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/{object_key(filename_or_path)}"


# ── S3 클라이언트 (lazy, 잡 전용) ────────────────────────────
_client = None


def _s3():
    global _client
    if _client is None:
        import boto3  # lazy: 앱 렌더 경로에서는 절대 import 안 됨
        from botocore.config import Config as BotoConfig
        acct = config.secret("R2_ACCOUNT_ID")
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
            aws_access_key_id=config.secret("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=config.secret("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _client


def _content_type(p: Path) -> str:
    return "image/png" if p.suffix.lower() == ".png" else "image/jpeg"


# ── 업로드/존재/삭제 (잡 전용) ───────────────────────────────
def exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=_bucket(), Key=key)
        return True
    except Exception:  # noqa: BLE001  (404 포함 — 없음으로 취급)
        return False


def upload_file(local_path, overwrite: bool = False) -> dict:
    """단일 파일 업로드(멱등). 반환: {ok, skipped, key, error}.
    overwrite=False면 이미 있는 키는 head 체크 후 skip(중복 업로드 방지)."""
    p = Path(local_path)
    if not p.exists():
        return {"ok": False, "skipped": False, "key": "", "error": "local missing"}
    key = object_key(p.name)
    try:
        if not overwrite and exists(key):
            return {"ok": True, "skipped": True, "key": key, "error": ""}
        _s3().put_object(
            Bucket=_bucket(), Key=key, Body=p.read_bytes(),
            ContentType=_content_type(p),
            CacheControl="public, max-age=31536000, immutable",  # 불변 컨텐츠 → CDN 장기 캐시
        )
        return {"ok": True, "skipped": False, "key": key, "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skipped": False, "key": key, "error": str(e)[:200]}


def upload_many(local_paths: Iterable, overwrite: bool = False, workers: int = 8) -> dict:
    """여러 파일 병렬 업로드. 반환: {uploaded, skipped, failed, errors[]}. 재실행 안전."""
    from concurrent.futures import ThreadPoolExecutor
    paths = [Path(x) for x in local_paths]
    res = {"uploaded": 0, "skipped": 0, "failed": 0, "errors": []}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(lambda pp: upload_file(pp, overwrite=overwrite), paths):
            if r["ok"] and r["skipped"]:
                res["skipped"] += 1
            elif r["ok"]:
                res["uploaded"] += 1
            else:
                res["failed"] += 1
                if len(res["errors"]) < 50:
                    res["errors"].append(f'{r["key"]}: {r["error"]}')
    return res


def delete_keys(keys: Iterable[str]) -> int:
    """키 목록 삭제(최대 1000/배치). 반환: 삭제 요청 수. 없는 키는 무시."""
    keys = [k for k in keys if k]
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        try:
            _s3().delete_objects(
                Bucket=_bucket(),
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            deleted += len(batch)
        except Exception:  # noqa: BLE001
            pass
    return deleted


def delete_files(local_paths_or_names: Iterable) -> int:
    """로컬 경로/파일명 목록 → 객체 키로 변환해 삭제(retention 연동용)."""
    return delete_keys(object_key(x) for x in local_paths_or_names)
