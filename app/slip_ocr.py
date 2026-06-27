import re, hashlib, requests
from .config import settings

amount_patterns = [
    r"(?:จำนวนเงิน|amount|ยอดเงิน|จำนวน)\s*[:：]?\s*([0-9,]+\.\d{2})",
    r"([0-9,]+\.\d{2})\s*(?:บาท|THB|Baht)",
    r"(?:THB|บาท)\s*([0-9,]+\.\d{2})",
]

def parse_amount(text: str) -> float | None:
    t = text.replace(" ", " ")
    for pat in amount_patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
    nums = re.findall(r"[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2}", t)
    vals = []
    for n in nums:
        try: vals.append(float(n.replace(",", "")))
        except Exception: pass
    return max(vals) if vals else None

def simple_ref(image_bytes: bytes, text: str = "") -> str:
    return hashlib.sha256(image_bytes + text.encode("utf-8", "ignore")).hexdigest()[:24]

def ocr_space(image_bytes: bytes) -> tuple[str, float | None, str]:
    if not settings.ocr_space_api_key:
        ref = simple_ref(image_bytes)
        return "", None, ref
    files = {"filename": ("slip.jpg", image_bytes)}
    data = {"language": "tha", "isOverlayRequired": False, "OCREngine": 2}
    headers = {"apikey": settings.ocr_space_api_key}
    r = requests.post("https://api.ocr.space/parse/image", headers=headers, data=data, files=files, timeout=45)
    r.raise_for_status()
    js = r.json()
    parts = []
    for item in js.get("ParsedResults", []) or []:
        parts.append(item.get("ParsedText", ""))
    text = "\n".join(parts)
    return text, parse_amount(text), simple_ref(image_bytes, text)

def receiver_ok(text: str) -> bool:
    keys = settings.receiver_keywords
    if not keys:
        return True
    return any(k in text for k in keys)
