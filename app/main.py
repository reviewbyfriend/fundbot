from fastapi import FastAPI, Request, Header, HTTPException, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import re
from .database import init_db, get_db, SessionLocal
from .config import settings
from .line_client import verify_signature, reply, text_message, image_message, download_content
from .promptpay import make_qr_png
from .slip_ocr import ocr_space, receiver_ok
from .report import create_report_excel
from .services import *

app = FastAPI(title="FundBot v1.0")
QR_CACHE: dict[str, bytes] = {}
REPORT_CACHE: dict[str, bytes] = {}

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def root():
    return {"ok": True, "name": "FundBot v1.0", "webhook": "/webhook"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/qr/{key}.png")
def qr_png(key: str):
    data = QR_CACHE.get(key)
    if not data:
        raise HTTPException(404, "QR not found")
    return Response(content=data, media_type="image/png")

@app.get("/report/{key}.xlsx")
def report_xlsx(key: str):
    data = REPORT_CACHE.get(key)
    if not data:
        raise HTTPException(404, "Report not found")
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=fund-report-{key}.xlsx"})

def src_ids(event: dict):
    source = event.get("source", {})
    group_id = source.get("groupId") or source.get("roomId") or source.get("userId") or "private"
    user_id = source.get("userId", "")
    return group_id, user_id

def help_text():
    return """🤖 FundBot กองกลางสำนักงาน

คำสั่งหลัก
• เพิ่มสมาชิก รักษิน 500
• รายชื่อ
• เปิดรอบ กรกฎาคม 2569 ยกมา 17813.50
• ลงทะเบียน รักษิน
• ยอดของฉัน
• ชำระเงิน
• จ่ายแล้ว 500
• รับเงิน รักษิน 500
• รายจ่าย ค่าน้ำ 1816
• สรุป
• ทวงเงิน
• รายงาน

ส่งสลิปเป็นรูปได้ ถ้าตั้ง OCR_SPACE_API_KEY แล้วระบบจะพยายามอ่านยอดให้อัตโนมัติ"""

def parse_amount(text: str) -> float | None:
    m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?|[0-9]+(?:\.\d+)?)", text)
    if not m: return None
    return float(m.group(1).replace(",", ""))

def handle_text(db: Session, event: dict, text: str):
    group_line_id, user_id = src_ids(event)
    group = get_group(db, group_line_id)
    t = text.strip()

    if t in ["ช่วย", "ช่วยเหลือ", "help", "Help"]:
        return help_text()

    if t.startswith("เพิ่มสมาชิก"):
        m = re.match(r"เพิ่มสมาชิก\s+(.+?)\s+([0-9,]+(?:\.\d+)?)$", t)
        if not m: return "รูปแบบ: เพิ่มสมาชิก รักษิน 500"
        name, amount = m.group(1).strip(), float(m.group(2).replace(",", ""))
        add_member(db, group, name, amount)
        return f"✅ เพิ่ม/แก้สมาชิกแล้ว\n{name}: {money(amount)} บาท"

    if t == "รายชื่อ":
        members = db.query(Member).filter_by(group_id=group.id, active=1).order_by(Member.name).all()
        if not members: return "ยังไม่มีสมาชิก\nพิมพ์: เพิ่มสมาชิก รักษิน 500"
        return "👥 รายชื่อสมาชิก\n" + "\n".join([f"• {m.name} {money(m.monthly_amount)}" + (" ✅ลงทะเบียน" if m.line_user_id else "") for m in members])

    if t.startswith("เปิดรอบ"):
        rest = t.replace("เปิดรอบ", "", 1).strip()
        bf = 0.0
        title = rest
        m = re.search(r"ยกมา\s+([0-9,]+(?:\.\d+)?)", rest)
        if m:
            bf = float(m.group(1).replace(",", ""))
            title = rest[:m.start()].strip()
        if not title: return "รูปแบบ: เปิดรอบ กรกฎาคม 2569 ยกมา 17813.50"
        rnd = open_round(db, group, title, bf)
        members = db.query(Member).filter_by(group_id=group.id, active=1).all()
        total = sum(x.monthly_amount for x in members)
        return f"✅ เปิดรอบ {rnd.title}\nยอดยกมา {money(bf)} บาท\nสมาชิก {len(members)} คน\nยอดที่ต้องเก็บ {money(total)} บาท"

    if t.startswith("ลงทะเบียน"):
        name = t.replace("ลงทะเบียน", "", 1).strip()
        if not name: return "รูปแบบ: ลงทะเบียน รักษิน"
        m = register_user(db, group, user_id, name)
        if not m: return f"ไม่พบชื่อ {name}\nให้แอดมินเพิ่มก่อน: เพิ่มสมาชิก {name} 500"
        return f"✅ ลงทะเบียนสำเร็จ\nLINE นี้ผูกกับ: {m.name}\nยอดประจำเดือน: {money(m.monthly_amount)} บาท"

    if t in ["ยอดของฉัน", "ชำระเงิน"]:
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่\nพิมพ์: เปิดรอบ กรกฎาคม 2569"
        member = member_by_user(db, group, user_id)
        if not member: return "ยังไม่รู้ว่าคุณคือใคร\nพิมพ์: ลงทะเบียน ชื่อของคุณ"
        p = db.query(Payment).filter_by(round_id=rnd.id, member_id=member.id).first()
        if p: return f"✅ {member.name} จ่ายแล้ว\nรอบ {rnd.title}\nจำนวน {money(p.amount)} บาท"
        if t == "ยอดของฉัน": return f"📌 {member.name}\nรอบ {rnd.title}\nยอดต้องชำระ {money(member.monthly_amount)} บาท"
        if not settings.promptpay_id: return "ยังไม่ได้ตั้ง PROMPTPAY_ID ใน Railway"
        key = f"{rnd.id}-{member.id}"
        QR_CACHE[key] = make_qr_png(settings.promptpay_id, member.monthly_amount)
        url = f"{settings.app_base_url.rstrip('/')}/qr/{key}.png"
        reply(event["replyToken"], [text_message(f"💳 {member.name}\nยอดชำระ {money(member.monthly_amount)} บาท\nสแกน QR แล้วส่งสลิปกลับมาในกลุ่มได้เลย"), image_message(url)])
        return None

    if t.startswith("จ่ายแล้ว"):
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่"
        member = member_by_user(db, group, user_id)
        if not member: return "ยังไม่ได้ลงทะเบียน\nพิมพ์: ลงทะเบียน ชื่อของคุณ"
        amt = parse_amount(t)
        if amt is None: return "รูปแบบ: จ่ายแล้ว 500"
        record_payment(db, rnd, member, amt, source="manual")
        return f"✅ บันทึกแล้ว\n{member.name} ชำระ {money(amt)} บาท\nรอบ {rnd.title}"

    if t.startswith("รับเงิน"):
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่"
        m = re.match(r"รับเงิน\s+(.+?)\s+([0-9,]+(?:\.\d+)?)$", t)
        if not m: return "รูปแบบ: รับเงิน รักษิน 500"
        member = find_member(db, group, m.group(1).strip())
        if not member: return "ไม่พบสมาชิก"
        amt = float(m.group(2).replace(",", ""))
        record_payment(db, rnd, member, amt, source="admin")
        return f"✅ รับเงินแล้ว\n{member.name}: {money(amt)} บาท"

    if t.startswith("รายจ่าย"):
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่"
        m = re.match(r"รายจ่าย\s+(.+?)\s+([0-9,]+(?:\.\d+)?)$", t)
        if not m: return "รูปแบบ: รายจ่าย ค่าน้ำ 1816"
        title, amt = m.group(1).strip(), float(m.group(2).replace(",", ""))
        add_expense(db, rnd, title, amt)
        return f"✅ เพิ่มรายจ่ายแล้ว\n{title}: {money(amt)} บาท"

    if t in ["สรุป", "ใครยังไม่จ่าย", "ทวงเงิน"]:
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่"
        s = round_summary(db, rnd)
        lines = [f"📊 สรุป {rnd.title}", f"ยอดยกมา {money(rnd.brought_forward)}", f"รับแล้ว {money(s['paid'])}", f"รายจ่าย {money(s['expense'])}", f"คงเหลือ {money(s['balance'])}", ""]
        if s["unpaid"]:
            lines.append(f"ยังไม่จ่าย {len(s['unpaid'])} คน")
            lines.extend([f"• {m.name} {money(m.monthly_amount)}" for m in s["unpaid"]])
        else:
            lines.append("✅ จ่ายครบแล้ว")
        return "\n".join(lines)

    if t == "รายงาน":
        rnd = current_round(db, group)
        if not rnd: return "ยังไม่มีรอบเปิดอยู่"
        key = f"{group.id}-{rnd.id}"
        REPORT_CACHE[key] = create_report_excel(db, rnd)
        url = f"{settings.app_base_url.rstrip('/')}/report/{key}.xlsx"
        return f"📄 รายงาน Excel\n{rnd.title}\nดาวน์โหลด:\n{url}"

    return None

def handle_image(db: Session, event: dict):
    group_line_id, user_id = src_ids(event)
    group = get_group(db, group_line_id)
    rnd = current_round(db, group)
    if not rnd: return "ได้รับรูปแล้ว แต่ยังไม่มีรอบเปิดอยู่"
    member = member_by_user(db, group, user_id)
    if not member: return "ได้รับสลิปแล้ว แต่ยังไม่รู้ว่าคุณคือใคร\nพิมพ์ก่อน: ลงทะเบียน ชื่อของคุณ"
    img = download_content(event["message"]["id"])
    text, amt, ref = ocr_space(img)
    if not amt:
        return "รับสลิปแล้ว แต่ OCR ยังอ่านยอดไม่ได้\nกรุณาพิมพ์ยืนยัน: จ่ายแล้ว 500"
    if text and not receiver_ok(text):
        return f"⚠️ OCR อ่านยอดได้ {money(amt)} บาท แต่ยังไม่พบชื่อบัญชีรับเงินที่ตั้งไว้\nกรุณาให้แอดมินตรวจสอบ หรือพิมพ์: จ่ายแล้ว {money(amt)}"
    existing = db.query(Payment).filter_by(round_id=rnd.id, slip_ref=ref).first()
    if existing:
        return "⚠️ สลิปนี้เคยถูกใช้บันทึกแล้ว"
    record_payment(db, rnd, member, amt, source="ocr", slip_ref=ref, note=text[:1000] if text else None)
    return f"✅ OCR รับชำระแล้ว\n{member.name}\nจำนวน {money(amt)} บาท\nรอบ {rnd.title}"

@app.post("/webhook")
async def webhook(request: Request, x_line_signature: str | None = Header(default=None)):
    body = await request.body()
    if not verify_signature(body, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    payload = await request.json()
    db = SessionLocal()
    try:
        for event in payload.get("events", []):
            if event.get("type") != "message":
                continue
            msg = event.get("message", {})
            out = None
            if msg.get("type") == "text":
                out = handle_text(db, event, msg.get("text", ""))
            elif msg.get("type") == "image":
                out = handle_image(db, event)
            if out:
                reply(event["replyToken"], text_message(out))
    finally:
        db.close()
    return JSONResponse({"ok": True})
