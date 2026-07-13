"""신규 광고 감지 알림 (Slack Incoming Webhook).

SLACK_WEBHOOK_URL (.env 또는 Streamlit secrets) 이 설정돼 있을 때만 전송.
미설정이면 조용히 False 반환 → 스케줄 잡을 막지 않음.
"""
import json
import urllib.request

import config


def slack(text: str) -> bool:
    """Slack 웹훅으로 text 전송. 성공 True / 미설정·실패 False."""
    url = config.secret("SLACK_WEBHOOK_URL", "")
    if not url:
        return False
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        return False
