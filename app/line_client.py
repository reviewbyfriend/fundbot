import requests
from .config import settings

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
PUSH_URL = "https://api.line.me/v2/bot/message/push"
CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"

def headers():
    return {"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

def auth_headers():
    return {"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"}

def text(msg: str):
    return {"type": "text", "text": msg[:5000]}

def flex(alt: str, contents: dict):
    return {"type": "flex", "altText": alt, "contents": contents}

def reply(reply_token: str, messages: list[dict]):
    if not settings.LINE_CHANNEL_ACCESS_TOKEN or not reply_token:
        return
    try:
        requests.post(REPLY_URL, headers=headers(), json={"replyToken": reply_token, "messages": messages[:5]}, timeout=8)
    except Exception:
        pass


def push(to: str, messages: list[dict]):
    if not settings.LINE_CHANNEL_ACCESS_TOKEN or not to:
        return False
    try:
        r = requests.post(PUSH_URL, headers=headers(), json={"to": to, "messages": messages[:5]}, timeout=8)
        return r.status_code < 300
    except Exception:
        return False


def download_message_content(message_id: str) -> bytes | None:
    """Download image/file content from LINE Messaging API."""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN or not message_id:
        return None
    try:
        r = requests.get(CONTENT_URL.format(message_id=message_id), headers=auth_headers(), timeout=20)
        if r.status_code >= 300:
            return None
        return r.content
    except Exception:
        return None
