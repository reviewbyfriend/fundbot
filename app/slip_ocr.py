import io
import re
import requests
from dataclasses import dataclass
from PIL import Image, ImageOps, ImageFilter
from .config import settings

@dataclass
class SlipOCRResult:
    ok: bool
    amount: float | None = None
    reference_no: str | None = None
    raw_text: str = ""
    error: str | None = None


def _prepare_image(image_bytes: bytes) -> bytes:
    """Resize/normalize before sending to OCR API to improve Thai bank slip reading."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = ImageOps.exif_transpose(img)
    max_side = 1800
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    # mild sharpen/contrast without destroying small text
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def ocr_space(image_bytes: bytes) -> SlipOCRResult:
    """OCR via OCR.Space. Set OCR_SPACE_API_KEY in Railway. A blank key uses demo key and may be rate-limited."""
    api_key = settings.ocr_space_api_key or "helloworld"
    files = {"file": ("slip.jpg", _prepare_image(image_bytes), "image/jpeg")}
    data = {
        "apikey": api_key,
        "language": "tha",
        "OCREngine": "2",
        "scale": "true",
        "isTable": "false",
        "detectOrientation": "true",
    }
    try:
        r = requests.post("https://api.ocr.space/parse/image", data=data, files=files, timeout=45)
        if r.status_code >= 300:
            return SlipOCRResult(ok=False, error=f"OCR HTTP {r.status_code}")
        js = r.json()
        if js.get("IsErroredOnProcessing"):
            return SlipOCRResult(ok=False, error=str(js.get("ErrorMessage") or js.get("ErrorDetails") or "OCR error"))
        parsed = js.get("ParsedResults") or []
        text = "\n".join((x.get("ParsedText") or "") for x in parsed)
        amount = extract_amount(text)
        ref = extract_reference(text)
        return SlipOCRResult(ok=True, amount=amount, reference_no=ref, raw_text=text)
    except Exception as e:
        return SlipOCRResult(ok=False, error=str(e))


def extract_amount(text: str) -> float | None:
    """Extract likely transfer amount from Thai bank slip OCR text."""
    clean = text.replace("\u00a0", " ")
    candidates: list[float] = []
    # Strong context around amount labels
    patterns = [
        r"(?:จำนวนเงิน|ยอดเงิน|Amount|จำนวน|ยอด)\s*[:\-]?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})|[0-9]+(?:\.\d{2})?)",
        r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})|[0-9]+(?:\.\d{2})?)\s*(?:บาท|THB|Baht)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, clean, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(",", ""))
                if 1 <= val <= 1_000_000:
                    candidates.append(val)
            except Exception:
                pass
    if candidates:
        # On slips, transfer amount often appears as the last context amount; use max to avoid fees/time fragments.
        return sorted(candidates)[-1]
    return None


def extract_reference(text: str) -> str | None:
    clean = re.sub(r"\s+", " ", text)
    patterns = [
        r"(?:เลขที่รายการ|เลขอ้างอิง|Ref(?:erence)?\.?|Transaction ID|รหัสอ้างอิง)\s*[:\-]?\s*([A-Za-z0-9\-]{8,40})",
        r"\b([0-9]{12,30})\b",
    ]
    for pat in patterns:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            return m.group(1).strip("-: ")[:120]
    return None


def receiver_ok(raw_text: str) -> bool:
    """Optional receiver-name check. Put comma-separated keywords in SLIP_RECEIVER_KEYWORDS."""
    kws = [x.strip() for x in (settings.slip_receiver_keywords or "").split(",") if x.strip()]
    if not kws:
        return True
    return any(k in raw_text for k in kws)
