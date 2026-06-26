import base64, hashlib, hmac, requests
from .config import settings

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message"


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not settings.line_channel_secret:
        return True  # local dev only
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")


def _headers():
    return {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}


def reply(reply_token: str, messages: list[dict] | dict):
    if isinstance(messages, dict):
        messages = [messages]
    if not settings.line_channel_access_token:
        print("LINE_REPLY", messages)
        return
    r = requests.post(LINE_REPLY_URL, headers=_headers(), json={"replyToken": reply_token, "messages": messages}, timeout=15)
    if r.status_code >= 300:
        print("LINE reply error", r.status_code, r.text)


def push(to: str, messages: list[dict] | dict):
    if isinstance(messages, dict):
        messages = [messages]
    if not settings.line_channel_access_token:
        print("LINE_PUSH", to, messages)
        return
    r = requests.post(LINE_PUSH_URL, headers=_headers(), json={"to": to, "messages": messages}, timeout=15)
    if r.status_code >= 300:
        print("LINE push error", r.status_code, r.text)


def text_message(text: str) -> dict:
    return {"type": "text", "text": text[:4900]}


def payment_flex(title: str, amount: float, url: str) -> dict:
    return {
        "type": "flex",
        "altText": f"{title} {amount:,.2f} บาท",
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": title, "weight": "bold", "size": "lg", "wrap": True},
                {"type": "text", "text": f"ยอดชำระ {amount:,.2f} บาท", "size": "xl", "weight": "bold", "margin": "md"},
                {"type": "text", "text": "กดปุ่มเพื่อเปิด QR พร้อมเพย์ แล้วโอนในแอปธนาคาร จากนั้นส่งสลิปกลับเข้ากลุ่ม", "wrap": True, "margin": "md"}
            ]},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "action": {"type": "uri", "label": "ชำระเงิน", "uri": url}},
                {"type": "button", "action": {"type": "message", "label": "ฉันจ่ายแล้ว", "text": f"จ่ายแล้ว {amount:.2f}"}}
            ]}
        }
    }


def get_message_content(message_id: str) -> bytes | None:
    """Download image/file bytes from LINE message content API."""
    if not settings.line_channel_access_token:
        return None
    url = f"{LINE_CONTENT_URL}/{message_id}/content"
    r = requests.get(url, headers={"Authorization": f"Bearer {settings.line_channel_access_token}"}, timeout=30)
    if r.status_code >= 300:
        print("LINE content error", r.status_code, r.text[:500])
        return None
    return r.content
