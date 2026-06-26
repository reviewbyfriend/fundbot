from pathlib import Path
import re
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from .config import settings
from .database import Base, engine, db_session
from .models import Group, Member
from . import line_client
from .services import (
    get_or_create_group, add_member, bind_member, open_round, mark_paid, add_expense,
    summary_text, remind_text, my_due, is_admin, money, current_round, find_member, process_slip_payment
)
from .promptpay import build_promptpay_payload, qr_png_base64
from .reports import create_excel_report
from .slip_ocr import ocr_space, receiver_ok
from .importers import import_members_from_excel

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Office Fund LINE Bot", version="1.0.0")

HELP = """คำสั่งหลัก
- ลงทะเบียน ชื่อของฉัน
- ชำระเงิน
- จ่ายแล้ว 500

คำสั่งแอดมิน
- เพิ่มสมาชิก ชื่อ 500
- เปิดรอบ กรกฎาคม 2569 ยกมา 1000
- รับเงิน ชื่อ 500
- รายจ่าย ค่าน้ำ 1816
- สรุป
- ทวงเงิน
- รายงาน
"""

@app.get("/health")
def health():
    return {"ok": True, "service": "office-fund-line-bot"}

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><body style='font-family:sans-serif;padding:32px'>
    <h2>Office Fund LINE Bot is running ✅</h2>
    <p>Webhook: <code>/webhook</code></p>
    <p>Health: <code>/health</code></p>
    </body></html>
    """

@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(db_session)):
    body = await request.body()
    signature = request.headers.get("x-line-signature")
    if not line_client.verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    payload = await request.json()
    for event in payload.get("events", []):
        handle_event(db, event)
    return {"ok": True}


def handle_event(db: Session, event: dict):
    source = event.get("source", {})
    reply_token = event.get("replyToken")
    user_id = source.get("userId")
    group_id = source.get("groupId") or source.get("roomId") or user_id or "private"
    group = get_or_create_group(db, group_id)

    if event.get("type") == "join":
        line_client.reply(reply_token, line_client.text_message("สวัสดีจ้า ฉันคือบอทเก็บเงินกองกลาง\nพิมพ์ 'ช่วยเหลือ' เพื่อดูคำสั่ง"))
        return

    if event.get("type") != "message":
        return
    msg = event.get("message", {})
    msg_type = msg.get("type")

    if msg_type == "image":
        message_id = msg.get("id")
        image_bytes = line_client.get_message_content(message_id) if message_id else None
        if not image_bytes:
            line_client.reply(reply_token, line_client.text_message(
                "📎 รับรูปแล้ว แต่ดาวน์โหลดรูปจาก LINE ไม่สำเร็จ\nให้พิมพ์ยืนยันยอด เช่น 'จ่ายแล้ว 500'"
            ))
            return
        result = ocr_space(image_bytes)
        if not result.ok:
            line_client.reply(reply_token, line_client.text_message(
                f"📎 รับสลิปแล้ว แต่ OCR อ่านไม่สำเร็จ\nสาเหตุ: {result.error}\nให้พิมพ์ยืนยันยอด เช่น 'จ่ายแล้ว 500'"
            ))
            return
        ok, response = process_slip_payment(
            db, group, user_id, message_id or "", result.amount, result.reference_no, result.raw_text, receiver_ok(result.raw_text)
        )
        line_client.reply(reply_token, line_client.text_message(response))
        return

    if msg_type != "text":
        return
    text = (msg.get("text") or "").strip()
    response = handle_text(db, group, user_id, text)
    if isinstance(response, list):
        line_client.reply(reply_token, response)
    else:
        line_client.reply(reply_token, line_client.text_message(response))


def handle_text(db: Session, group: Group, user_id: str | None, text: str):
    admin = is_admin(user_id, settings.admin_ids) or not settings.admin_ids  # ช่วงทดลอง ถ้ายังไม่ตั้ง ADMIN ให้ทุกคนทดสอบได้
    t = text.strip()

    if t in ["ช่วยเหลือ", "help", "Help", "เมนู"]:
        return HELP

    m = re.match(r"^ลงทะเบียน\s+(.+)$", t)
    if m:
        member = bind_member(db, group, user_id or "", m.group(1).strip())
        if member:
            return f"✅ ลงทะเบียนแล้ว\n{member.display_name}\nยอดประจำเดือน {money(member.monthly_amount):,.2f} บาท"
        return "ไม่พบชื่อนี้ในรายชื่อสมาชิก ให้แอดมินเพิ่มก่อน เช่น\nเพิ่มสมาชิก รักษิน 500"

    m = re.match(r"^เพิ่มสมาชิก\s+(.+?)\s+(\d+(?:\.\d+)?)$", t)
    if m:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        member = add_member(db, group, m.group(1).strip(), float(m.group(2)))
        return f"✅ เพิ่ม/แก้ไขสมาชิกแล้ว\n{member.display_name}: {money(member.monthly_amount):,.2f} บาท"

    m = re.match(r"^เปิดรอบ\s+(.+?)(?:\s+ยกมา\s+(\d+(?:\.\d+)?))?$", t)
    if m:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        month_label = m.group(1).strip()
        opening = float(m.group(2) or 0)
        r = open_round(db, group, month_label, opening)
        return f"✅ เปิดรอบ {r.month_label} แล้ว\nยอดยกมา {money(r.opening_balance):,.2f} บาท\nพิมพ์ 'สรุป' เพื่อดูยอดทั้งหมด"

    if t in ["สรุป", "สรุปยอด", "ใครยังไม่จ่าย"]:
        return summary_text(db, group)

    if t in ["ทวงเงิน", "เตือนจ่ายเงิน"]:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        return remind_text(db, group)

    if t in ["ชำระเงิน", "จ่ายเงิน", "ขอ qr", "QR", "qr"]:
        due = my_due(db, group, user_id)
        if not due:
            return "ยังไม่พบยอดของคุณ\nให้พิมพ์: ลงทะเบียน ชื่อของคุณ"
        remain = max(0, money(due.amount_due) - money(due.amount_paid))
        if remain <= 0:
            return f"✅ คุณชำระครบแล้ว\n{due.member.display_name}"
        url = f"{settings.app_base_url.rstrip('/')}/pay/{due.id}"
        return [line_client.payment_flex(f"{settings.fund_name}", remain, url)]

    m = re.match(r"^จ่ายแล้ว\s+(\d+(?:\.\d+)?)$", t)
    if m:
        ok, msg = mark_paid(db, group, user_id, float(m.group(1)))
        return msg

    m = re.match(r"^รับเงิน\s+(.+?)\s+(\d+(?:\.\d+)?)$", t)
    if m:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        ok, msg = mark_paid(db, group, user_id, float(m.group(2)), member_keyword=m.group(1).strip())
        return msg

    m = re.match(r"^รายจ่าย\s+(.+?)\s+(\d+(?:\.\d+)?)$", t)
    if m:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        ok, msg = add_expense(db, group, m.group(1).strip(), float(m.group(2)))
        return msg

    if t in ["รายงาน", "สร้างรายงาน"]:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        path = create_excel_report(db, group)
        url = f"{settings.app_base_url.rstrip()}/download/{path.name}"
        return f"✅ สร้างรายงานแล้ว\nดาวน์โหลด: {url}"

    m = re.match(r"^นำเข้าไฟล์ตัวอย่าง$", t)
    if m:
        if not admin: return "คำสั่งนี้ใช้ได้เฉพาะแอดมิน"
        count = import_members_from_excel(db, group, "data/sample_fund_2569.xlsx")
        return f"✅ นำเข้ารายชื่อจากไฟล์ตัวอย่างแล้ว {count} รายการ"

    return "ยังไม่เข้าใจคำสั่งนี้จ้า\nพิมพ์ 'ช่วยเหลือ' เพื่อดูคำสั่ง"

@app.get("/pay/{due_id}", response_class=HTMLResponse)
def pay_page(due_id: int, db: Session = Depends(db_session)):
    from .models import PaymentDue
    due = db.get(PaymentDue, due_id)
    if not due:
        raise HTTPException(404, "Payment not found")
    remain = max(0, money(due.amount_due) - money(due.amount_paid))
    payload = build_promptpay_payload(settings.promptpay_id, remain)
    img64 = qr_png_base64(payload)
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>body{{font-family:sans-serif;text-align:center;padding:20px}} img{{width:280px;max-width:90%}} .amt{{font-size:28px;font-weight:bold}}</style></head>
    <body>
      <h2>{settings.fund_name}</h2>
      <p>{due.member.display_name}</p>
      <div class='amt'>{remain:,.2f} บาท</div>
      <p>สแกน QR ด้วยแอปธนาคาร แล้วส่งสลิปกลับใน LINE กลุ่ม</p>
      <img src='data:image/png;base64,{img64}' />
      <p style='font-size:12px;color:#666'>PromptPay: {settings.promptpay_id}</p>
    </body></html>
    """

@app.get("/download/{filename}")
def download(filename: str):
    safe = Path(filename).name
    path = Path("reports") / safe
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=safe)
