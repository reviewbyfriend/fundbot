import requests
from .config import settings

LINE_API = "https://api.line.me/v2/bot/message"
DATA_API = "https://api-data.line.me/v2/bot/message"

def _headers():
    return {"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

def reply(reply_token: str, messages: list[dict]):
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        return
    requests.post(f"{LINE_API}/reply", headers=_headers(), json={"replyToken": reply_token, "messages": messages[:5]}, timeout=8)

def push(to: str, messages: list[dict]):
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        return
    requests.post(f"{LINE_API}/push", headers=_headers(), json={"to": to, "messages": messages[:5]}, timeout=8)

def text(msg: str) -> dict:
    return {"type": "text", "text": msg[:4900]}

def quick_reply_text(msg: str, items: list[tuple[str, str]]) -> dict:
    return {"type": "text", "text": msg[:4900], "quickReply": {"items": [
        {"type": "action", "action": {"type": "message", "label": label[:20], "text": txt[:300]}} for label, txt in items[:13]
    ]}}

def image_url(original_url: str, preview_url: str | None = None) -> dict:
    return {"type": "image", "originalContentUrl": original_url, "previewImageUrl": preview_url or original_url}

def get_message_content(message_id: str) -> bytes | None:
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        return None
    r = requests.get(f"{DATA_API}/{message_id}/content", headers={"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=15)
    if r.ok:
        return r.content
    return None
