import requests
from .config import settings

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
PUSH_URL = "https://api.line.me/v2/bot/message/push"

def headers():
    return {"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

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
