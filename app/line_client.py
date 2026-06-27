import base64, hashlib, hmac, requests
from .config import settings

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message"
LINE_PROFILE_URL = "https://api.line.me/v2/bot/profile"

def verify_signature(body: bytes, signature: str | None) -> bool:
    if not settings.line_channel_secret:
        return True
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")

def headers(binary: bool = False):
    h = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    if not binary:
        h["Content-Type"] = "application/json"
    return h

def reply(reply_token: str, messages: dict | list[dict]):
    if isinstance(messages, dict):
        messages = [messages]
    if not settings.line_channel_access_token:
        print("LINE_REPLY", messages)
        return
    r = requests.post(LINE_REPLY_URL, headers=headers(), json={"replyToken": reply_token, "messages": messages}, timeout=15)
    if r.status_code >= 300:
        print("LINE reply error", r.status_code, r.text)

def push(to: str, messages: dict | list[dict]):
    if isinstance(messages, dict):
        messages = [messages]
    if not settings.line_channel_access_token:
        print("LINE_PUSH", to, messages)
        return
    r = requests.post(LINE_PUSH_URL, headers=headers(), json={"to": to, "messages": messages}, timeout=15)
    if r.status_code >= 300:
        print("LINE push error", r.status_code, r.text)

def text_message(text: str) -> dict:
    return {"type": "text", "text": text[:4900]}

def image_message(url: str) -> dict:
    return {"type": "image", "originalContentUrl": url, "previewImageUrl": url}

def download_content(message_id: str) -> bytes:
    url = f"{LINE_CONTENT_URL}/{message_id}/content"
    r = requests.get(url, headers=headers(binary=True), timeout=30)
    r.raise_for_status()
    return r.content

def get_profile(user_id: str) -> dict:
    if not settings.line_channel_access_token:
        return {}
    r = requests.get(f"{LINE_PROFILE_URL}/{user_id}", headers=headers(binary=True), timeout=15)
    return r.json() if r.ok else {}
