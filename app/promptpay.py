import qrcode
from io import BytesIO

# EMV QR payload helpers
def _crc16(payload: str) -> str:
    poly = 0x1021
    crc = 0xFFFF
    for b in payload.encode("ascii"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"

def _format_target(target: str) -> tuple[str, str]:
    digits = ''.join(ch for ch in target if ch.isdigit())
    if len(digits) == 10:
        return "01", "0066" + digits[1:]
    if len(digits) == 13:
        return "02", digits
    return "03", digits

def promptpay_payload(target: str, amount: float | None = None) -> str:
    target_type, target_value = _format_target(target)
    mai = _tlv("00", "A000000677010111") + _tlv(target_type, target_value)
    parts = [
        _tlv("00", "01"),
        _tlv("01", "12"),
        _tlv("29", mai),
        _tlv("58", "TH"),
        _tlv("53", "764"),
    ]
    if amount is not None and amount > 0:
        parts.append(_tlv("54", f"{amount:.2f}"))
    parts.append(_tlv("63", ""))
    raw = "".join(parts)
    return raw + _crc16(raw)

def make_qr_png(target: str, amount: float) -> bytes:
    img = qrcode.make(promptpay_payload(target, amount))
    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()
