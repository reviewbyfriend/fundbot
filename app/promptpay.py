import base64
from io import BytesIO
from decimal import Decimal
import qrcode

# Minimal EMV PromptPay QR generator
def _crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for c in data.encode("ascii"):
        crc ^= c << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"

def _normalize_target(target: str) -> str:
    s = ''.join(ch for ch in target if ch.isdigit())
    if len(s) == 10:  # mobile
        return "0066" + s[1:]
    return s

def promptpay_payload(target: str, amount: Decimal | float | int | None = None) -> str:
    target = _normalize_target(target)
    if len(target) >= 13:
        aid = _tlv("00", "A000000677010111") + _tlv("02", target)
    else:
        aid = _tlv("00", "A000000677010111") + _tlv("01", target)
    payload = ""
    payload += _tlv("00", "01")
    payload += _tlv("01", "11")
    payload += _tlv("29", aid)
    payload += _tlv("53", "764")
    if amount is not None:
        payload += _tlv("54", f"{Decimal(amount):.2f}")
    payload += _tlv("58", "TH")
    payload += _tlv("59", "OFFICEFUND")
    payload += _tlv("60", "BANGKOK")
    payload_for_crc = payload + "6304"
    return payload_for_crc + _crc16_ccitt(payload_for_crc)

def qr_png_base64(target: str, amount) -> str:
    img = qrcode.make(promptpay_payload(target, amount))
    bio = BytesIO()
    img.save(bio, format="PNG")
    return base64.b64encode(bio.getvalue()).decode("ascii")
