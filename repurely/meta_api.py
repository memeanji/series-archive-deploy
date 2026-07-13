"""Meta Marketing API — repurely 광고 계정의 소재를 직접 조회(이름·상태·썸네일·영상·랜딩).

소재명(예: f_v_b_o_l_0609_6)이 Meta 대시보드 시트의 UTM/조인소스와 동일 → 정확 매칭.
토큰/계정: 이 프로젝트 secrets(META_ACCESS_TOKEN·META_AD_ACCOUNT_ID) 우선,
없으면 meta-ads-automation/.env 폴백(로컬 편의). 읽기 전용(GET).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

GRAPH = "https://graph.facebook.com/v21.0"
_ROOT = os.path.dirname(os.path.dirname(__file__))


def _creds() -> tuple[str, str]:
    # 1) 메타 자동화 프로젝트 .env(로컬, 활성·신선한 토큰 우선)
    tok = acct = ""
    try:
        p = os.path.join(os.path.expanduser("~"), "meta-ads-automation", ".env")
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == "META_ACCESS_TOKEN":
                    tok = v
                elif k.strip() == "META_AD_ACCOUNT_ID":
                    acct = v
    except Exception:  # noqa: BLE001
        pass
    # 2) 이 프로젝트 secrets(클라우드 배포 시)
    tok = tok or (config.secret("META_ACCESS_TOKEN") or "")
    acct = acct or (config.secret("META_AD_ACCOUNT_ID") or "")
    return tok, acct


def enabled() -> bool:
    t, a = _creds()
    return bool(t and a)


def _landing(cr: dict) -> str:
    oss = cr.get("object_story_spec", {}) or {}
    ld = oss.get("link_data", {}) or {}
    if ld.get("link"):
        return ld["link"]
    try:
        return oss["video_data"]["call_to_action"]["value"]["link"]
    except Exception:  # noqa: BLE001
        return ""


_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "repurely_meta_ads.json")


def _save_cache(ads: list[dict]) -> None:
    import json
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(ads, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _load_cache() -> list[dict]:
    import json
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def load_ads() -> list[dict]:
    """계정의 모든 광고 → [{ad_name,status,thumbnail_url,video_id,landing,campaign,adset}].
    성공 시 디스크 캐시에 저장. 인증 없음/호출한도(rate limit)/실패 시 마지막 정상 캐시로 폴백."""
    import requests
    tok, acct = _creds()
    if not (tok and acct):
        return _load_cache()
    acct_id = acct if acct.startswith("act_") else f"act_{acct}"
    # 매칭/카드용 경량 필드(깊은 object_story_spec 제외) → limit 높여 요청 수↓·속도↑.
    # landing은 상세에서 video_info로 on-demand 조회.
    fields = "name,effective_status,creative{thumbnail_url,image_url,video_id},campaign{name}"
    out: list[dict] = []
    url = f"{GRAPH}/{acct_id}/ads"
    params = {"fields": fields, "limit": 200, "access_token": tok}
    ok = False
    try:
        for _ in range(20):
            r = requests.get(url, params=params, timeout=30)
            if r.status_code != 200:
                break   # 호출한도/오류 → 루프 종료(아래에서 캐시 폴백)
            ok = True
            j = r.json()
            for a in j.get("data", []):
                cr = a.get("creative", {}) or {}
                out.append({
                    "ad_name": (a.get("name") or "").strip(),
                    "status": a.get("effective_status", ""),
                    "thumbnail_url": cr.get("thumbnail_url") or cr.get("image_url") or "",
                    "video_id": cr.get("video_id") or "",
                    "landing": _landing(cr),
                    "campaign": (a.get("campaign") or {}).get("name", ""),
                    "adset": (a.get("adset") or {}).get("name", ""),
                })
            nxt = (j.get("paging", {}) or {}).get("next")
            if not nxt:
                break
            url, params = nxt, None
    except Exception:  # noqa: BLE001
        pass
    if ok and out:        # 정상 응답 → 캐시 갱신 후 반환
        prev = {a.get("ad_name"): a.get("thumb_local")
                for a in _load_cache() if a.get("thumb_local")}
        for a in out:     # 영구화된 로컬 썸네일 경로는 API 재호출에도 보존
            if prev.get(a.get("ad_name")):
                a["thumb_local"] = prev[a["ad_name"]]
        _save_cache(out)
        return out
    return _load_cache()  # 한도/실패 → 마지막 정상 소재로 폴백


def _safe_name(name: str) -> str:
    s = "".join(c for c in (name or "") if c.isalnum() or c in "_-")
    if s:
        return s
    import hashlib
    return hashlib.md5((name or "").encode()).hexdigest()[:14]


def persist_thumbs(ads: list[dict], active_only: bool = True) -> int:
    """fbcdn 썸네일(서명·만료되는 임시 URL)을 static/thumbnails/rep_*.png 로 영구 저장.
    클라우드는 Meta API가 안 돌아 fbcdn URL이 깨지므로, 로컬 파일이라야 미리보기가 뜬다.
    잡에서만 호출(화면 렌더에서 금지). 각 ad에 thumb_local 경로를 채우고 캐시(json) 갱신.
    이미 받은 파일은 스킵. 새로 저장한 건수 반환."""
    import requests
    saved = 0
    for a in ads:
        if active_only and not (a.get("status") or "").upper().startswith("ACTIVE"):
            continue
        url = (a.get("thumbnail_url") or "").strip()
        name = (a.get("ad_name") or "").strip()
        vid = (a.get("video_id") or "").strip()
        if vid:   # 영상 → 1080급 고해상도 썸네일 우선(작은 t15 프레임 대신 선명하게)
            try:
                hi = (video_info(vid) or {}).get("thumbnail") or ""
                if hi.startswith("http"):
                    url = hi
            except Exception:  # noqa: BLE001
                pass
        if not url.startswith("http") or not name:
            continue
        rel = f"static/thumbnails/rep_{_safe_name(name)}.png"
        fp = os.path.join(_ROOT, rel)
        a["thumb_local"] = "app/" + rel
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            continue
        try:
            r = requests.get(url, timeout=25)
            if r.status_code == 200 and r.content:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "wb") as f:
                    f.write(r.content)
                saved += 1
        except Exception:  # noqa: BLE001
            pass
    _save_cache(ads)
    return saved


def video_permalink(video_id: str) -> str:
    """영상의 Facebook 절대 permalink(재생 임베드/링크용). 없으면 ''."""
    import requests
    tok, _ = _creds()
    if not (tok and video_id):
        return ""
    try:
        r = requests.get(f"{GRAPH}/{video_id}",
                         params={"fields": "permalink_url", "access_token": tok}, timeout=20)
        if r.status_code == 200:
            pl = r.json().get("permalink_url", "") or ""
            if pl.startswith("/"):
                return "https://www.facebook.com" + pl
            return pl
    except Exception:  # noqa: BLE001
        pass
    return ""


def video_info(video_id: str) -> dict:
    """영상 원본 mp4(source) + permalink + 고해상도 썸네일(1080급).
    {source, permalink, thumbnail}. source는 자사 계정 영상이라 직접 재생 가능."""
    import requests
    tok, _ = _creds()
    if not (tok and video_id):
        return {"source": "", "permalink": "", "thumbnail": ""}
    try:
        r = requests.get(f"{GRAPH}/{video_id}", timeout=20,
                         params={"fields": "source,permalink_url,thumbnails{uri,width,is_preferred}",
                                 "access_token": tok})
        if r.status_code == 200:
            j = r.json()
            pl = j.get("permalink_url", "") or ""
            if pl.startswith("/"):
                pl = "https://www.facebook.com" + pl
            ths = (j.get("thumbnails", {}) or {}).get("data", [])
            pref = [t for t in ths if t.get("is_preferred")]
            pool = pref or sorted(ths, key=lambda t: -(t.get("width") or 0))
            return {"source": j.get("source", "") or "", "permalink": pl,
                    "thumbnail": pool[0].get("uri", "") if pool else ""}
    except Exception:  # noqa: BLE001
        pass
    return {"source": "", "permalink": "", "thumbnail": ""}


def index_by_name(ads: list[dict]) -> dict:
    """소재명(소문자) → ad. 매칭용. f_v_b_o_l_0609_6 == 시트 UTM/조인소스."""
    return {a["ad_name"].strip().lower(): a for a in ads if a.get("ad_name")}
