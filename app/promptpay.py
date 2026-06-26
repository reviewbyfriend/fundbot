import re
from io import BytesIO
import base64
import qrcode


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for b in data.encode("ascii"):
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _normalize_promptpay_id(promptpay_id: str) -> tuple[str, str]:
    raw = re.sub(r"\D", "", promptpay_id or "")
    if len(raw) == 10:  # mobile
        return "01", "0066" + raw[1:]
    if len(raw) == 13:  # citizen id
        return "02", raw
    if len(raw) == 15:  # e-wallet
        return "03", raw
    raise ValueError("PROMPTPAY_ID ต้องเป็นเบอร์ 10 หลัก / เลขบัตร 13 หลัก / e-wallet 15 หลัก")


def build_promptpay_payload(promptpay_id: str, amount: float | None = None) -> str:
    proxy_type, proxy_value = _normalize_promptpay_id(promptpay_id)
    merchant_account = _tlv("00", "A000000677010111") + _tlv(proxy_type, proxy_value)
    payload = ""
    payload += _tlv("00", "01")
    payload += _tlv("01", "12")
    payload += _tlv("29", merchant_account)
    payload += _tlv("53", "764")
    if amount is not None and float(amount) > 0:
        payload += _tlv("54", f"{float(amount):.2f}")
    payload += _tlv("58", "TH")
    payload += _tlv("63", "")
    return payload + _crc16_ccitt(payload)


def qr_png_base64(payload: str) -> str:
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
