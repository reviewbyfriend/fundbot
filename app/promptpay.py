import base64
from decimal import Decimal
from io import BytesIO
import qrcode

# Minimal QR generator placeholder: creates QR with PromptPay ID + amount text.
# ใช้งานด่วนก่อน: แอปธนาคารบางตัวอาจไม่อ่านเป็น PromptPay มาตรฐาน 100%
# แต่ผู้ใช้ยัง copy พร้อมเพย์ได้จากหน้าเว็บ
def qr_png_base64(promptpay_id: str, amount: Decimal | None = None) -> str:
    text = f"PROMPTPAY:{promptpay_id}"
    if amount is not None:
        text += f":{Decimal(amount):.2f}"
    img = qrcode.make(text)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
