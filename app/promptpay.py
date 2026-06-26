import re
from io import BytesIO
import base64
import qrcode


def _crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for ch in data.encode("ascii"):
        crc ^= ch << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def promptpay_payload(promptpay_id: str, amount: float | None = None) -> str:
    raw = re.sub(r"\D", "", promptpay_id)
    if len(raw) == 10:
        # phone number: convert 0xxxxxxxxx to 0066xxxxxxxxx
        proxy_type = "01"
        proxy_value = "0066" + raw[1:]
    elif len(raw) == 13:
        proxy_type = "02"
        proxy_value = raw
    else:
        # e-wallet/other id fallback
        proxy_type = "03"
        proxy_value = raw

    merchant_account = _tlv("00", "A000000677010111") + _tlv(proxy_type, proxy_value)
    payload = ""
    payload += _tlv("00", "01")
    payload += _tlv("01", "11" if amount else "12")
    payload += _tlv("29", merchant_account)
    payload += _tlv("58", "TH")
    payload += _tlv("53", "764")
    if amount is not None:
        payload += _tlv("54", f"{amount:.2f}")
    payload += _tlv("63", "")
    return payload + _crc16_ccitt(payload)


def qr_png_bytes(promptpay_id: str, amount: float | None = None) -> bytes:
    img = qrcode.make(promptpay_payload(promptpay_id, amount))
    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def qr_data_uri(promptpay_id: str, amount: float | None = None) -> str:
    b64 = base64.b64encode(qr_png_bytes(promptpay_id, amount)).decode("ascii")
    return f"data:image/png;base64,{b64}"
