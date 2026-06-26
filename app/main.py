import os
import re
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, PushMessageRequest,
    TextMessage, FlexMessage, FlexContainer, ImageMessage
)
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, JoinEvent
from . import db
from .promptpay import qr_png_bytes
from .excel_import import import_members_from_excel
from .report import create_report_xlsx

load_dotenv()
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
PROMPTPAY_ID = os.getenv("PROMPTPAY_ID", "")
ADMIN_SETUP_CODE = os.getenv("ADMIN_SETUP_CODE", "friend1234")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)
app = FastAPI(title="Fund Group LINE Bot")
db.init_db()


def get_source_ids(event):
    src = event.source
    group_id = getattr(src, "group_id", None) or getattr(src, "room_id", None) or "PRIVATE"
    user_id = getattr(src, "user_id", None) or "UNKNOWN"
    return group_id, user_id


def reply(reply_token: str, messages):
    if not isinstance(messages, list):
        messages = [messages]
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages))


def push(to: str, messages):
    if not isinstance(messages, list):
        messages = [messages]
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(PushMessageRequest(to=to, messages=messages))


def money(x):
    return f"{float(x):,.2f}".rstrip("0").rstrip(".")


def summary_text(group_id: str) -> str:
    data = db.summary(group_id)
    if not data:
        return "ยังไม่มีรอบเดือนที่เปิดอยู่\nพิมพ์: เปิดรอบ กรกฎาคม 2569"
    r, rows, expenses = data
    paid = [x for x in rows if x['pay_status'] == 'paid']
    partial = [x for x in rows if x['pay_status'] == 'partial']
    unpaid = [x for x in rows if x['pay_status'] == 'unpaid']
    due_total = sum(float(x['due_amount'] or 0) for x in rows)
    paid_total = sum(float(x['paid_amount'] or 0) for x in rows)
    exp_total = sum(float(x['amount'] or 0) for x in expenses)
    lines = [
        f"📊 สรุปกองกลาง {r['title']}",
        f"ยอดต้องเก็บ: {money(due_total)} บาท",
        f"รับแล้ว: {money(paid_total)} บาท",
        f"ค้างรับ: {money(due_total-paid_total)} บาท",
        f"รายจ่าย: {money(exp_total)} บาท",
        "",
        f"✅ จ่ายแล้ว {len(paid)} คน",
        f"🟡 บางส่วน {len(partial)} คน",
        f"🔴 ยังไม่จ่าย {len(unpaid)} คน",
    ]
    if unpaid or partial:
        lines += ["", "รายชื่อค้าง:"]
        for x in partial + unpaid:
            remain = float(x['due_amount'] or 0) - float(x['paid_amount'] or 0)
            lines.append(f"- {x['display_name']} ค้าง {money(remain)}")
    return "\n".join(lines)[:4900]


def payment_flex(group_id: str, user_id: str):
    data = db.summary(group_id)
    if not data:
        return TextMessage(text="ยังไม่มีรอบเดือนที่เปิดอยู่")
    r, rows, _ = data
    member = None
    for x in rows:
        if x['line_user_id'] == user_id:
            member = x
            break
    if not member:
        return TextMessage(text="ระบบยังไม่รู้ว่าคุณคือใคร\nให้พิมพ์: ลงทะเบียน ชื่อของคุณ\nเช่น ลงทะเบียน รักษิน")
    amount = max(float(member['due_amount'] or 0) - float(member['paid_amount'] or 0), 0)
    if amount <= 0:
        return TextMessage(text=f"✅ {member['display_name']} จ่ายครบแล้ว")
    qr_url = f"{APP_BASE_URL}/qr?amount={amount}"
    bubble = {
      "type":"bubble",
      "hero":{"type":"image","url":qr_url,"size":"full","aspectRatio":"1:1","aspectMode":"cover"},
      "body":{"type":"box","layout":"vertical","contents":[
        {"type":"text","text":f"เงินกอง {r['title']}","weight":"bold","size":"lg"},
        {"type":"text","text":member['display_name'],"margin":"md"},
        {"type":"text","text":f"ยอดชำระ {money(amount)} บาท","size":"xl","weight":"bold","margin":"md"},
        {"type":"text","text":"โอนแล้วส่งสลิปในกลุ่ม ระบบจะบันทึกสถานะให้","wrap":True,"margin":"md"}
      ]},
      "footer":{"type":"box","layout":"vertical","contents":[
        {"type":"button","style":"primary","action":{"type":"uri","label":"เปิด QR ชำระเงิน","uri":qr_url}},
        {"type":"button","action":{"type":"message","label":"แจ้งว่าโอนแล้ว","text":f"จ่ายแล้ว {money(amount)}"}}
      ]}
    }
    return FlexMessage(alt_text="ชำระเงินกอง", contents=FlexContainer.from_dict(bubble))


def parse_amount(text):
    m = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", text)
    return float(m.group(1).replace(',', '')) if m else None


def handle_text(event: MessageEvent, text: str):
    group_id, user_id = get_source_ids(event)
    db.ensure_group(group_id, promptpay_id=PROMPTPAY_ID)
    t = text.strip()

    if t.startswith("ตั้งแอดมิน"):
        code = t.replace("ตั้งแอดมิน", "").strip()
        if code == ADMIN_SETUP_CODE:
            db.set_admin(group_id, user_id)
            return TextMessage(text="✅ ตั้งคุณเป็นแอดมินของบอทในกลุ่มนี้แล้ว")
        return TextMessage(text="รหัสไม่ถูกต้อง")

    if t.startswith("ลงทะเบียน"):
        name = t.replace("ลงทะเบียน", "").strip()
        if not name:
            return TextMessage(text="พิมพ์แบบนี้นะ: ลงทะเบียน รักษิน")
        ok = db.link_member(group_id, user_id, name)
        return TextMessage(text="✅ ลงทะเบียนสำเร็จ ต่อไปส่งสลิปแล้วระบบจะรู้ว่าเป็นของคุณ" if ok else "ไม่พบชื่อนี้ในรายชื่อสมาชิก ให้แอดมินเพิ่มชื่อก่อน")

    if t in ["ชำระเงิน", "จ่ายเงิน", "qr", "QR", "ยอดของฉัน"]:
        return payment_flex(group_id, user_id)

    if t.startswith("เปิดรอบ"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น\nถ้ายังไม่ตั้งแอดมิน พิมพ์: ตั้งแอดมิน <รหัส>")
        title = t.replace("เปิดรอบ", "").strip() or "เดือนนี้"
        db.open_round(group_id, title)
        members = db.list_members(group_id)
        return TextMessage(text=f"✅ เปิดรอบ {title} แล้ว\nสมาชิก {len(members)} คน\nพิมพ์: สรุปยอด เพื่อดูสถานะ")

    if t.startswith("นำเข้าไฟล์ตัวอย่าง") or t.startswith("import sample"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น")
        n = import_members_from_excel("data/sample_fund.xlsx", group_id, sheet_name="มิ.ย.68")
        return TextMessage(text=f"✅ นำเข้ารายชื่อจาก Excel แล้ว {n} รายการ")

    if t.startswith("เพิ่มสมาชิก"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น")
        # เพิ่มสมาชิก รักษิน 500
        m = re.match(r"เพิ่มสมาชิก\s+(.+?)\s+([0-9,]+(?:\.\d+)?)$", t)
        if not m:
            return TextMessage(text="ตัวอย่าง: เพิ่มสมาชิก รักษิน 500")
        db.upsert_member(group_id, m.group(1), float(m.group(2).replace(',', '')))
        return TextMessage(text=f"✅ เพิ่ม/แก้ไขสมาชิก {m.group(1)} ยอด {m.group(2)} บาท")

    if t.startswith("รายจ่าย") or t.startswith("เพิ่มรายจ่าย"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น")
        body = t.replace("เพิ่มรายจ่าย", "").replace("รายจ่าย", "").strip()
        amt = parse_amount(body)
        if amt is None:
            return TextMessage(text="ตัวอย่าง: รายจ่าย ค่าน้ำ 1816")
        title = re.sub(r"[0-9][0-9,]*(?:\.\d+)?", "", body).strip() or "รายจ่าย"
        db.add_expense(group_id, title, amt)
        return TextMessage(text=f"✅ บันทึกรายจ่าย {title} {money(amt)} บาท")

    if t in ["สรุปยอด", "ใครยังไม่จ่าย", "สรุปกอง"]:
        return TextMessage(text=summary_text(group_id))

    if t.startswith("ทวงเงิน"):
        data = db.summary(group_id)
        if not data:
            return TextMessage(text="ยังไม่มีรอบเดือน")
        r, rows, _ = data
        lines = [f"📢 แจ้งเตือนเงินกอง {r['title']}", "กรุณาชำระตามยอดค้างของแต่ละท่าน", "พิมพ์ ‘ชำระเงิน’ เพื่อเปิด QR ของตนเอง", ""]
        for x in rows:
            remain = float(x['due_amount'] or 0) - float(x['paid_amount'] or 0)
            if remain > 0:
                lines.append(f"- {x['display_name']} {money(remain)} บาท")
        return TextMessage(text="\n".join(lines)[:4900])

    if t.startswith("จ่ายแล้ว") or t.startswith("โอนแล้ว"):
        amt = parse_amount(t)
        if amt is None:
            return TextMessage(text="พิมพ์ยอดด้วยนะ เช่น จ่ายแล้ว 500")
        try:
            m = db.record_payment_by_user(group_id, user_id, amt, note="บันทึกจากข้อความ")
            return TextMessage(text=f"✅ รับชำระแล้ว\n{m['display_name']} {money(amt)} บาท")
        except Exception as e:
            return TextMessage(text=f"ยังบันทึกไม่ได้: {e}\nถ้ายังไม่ลงทะเบียน พิมพ์: ลงทะเบียน ชื่อของคุณ")

    # Admin manual: รับเงิน รักษิน 500
    if t.startswith("รับเงิน"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น")
        m = re.match(r"รับเงิน\s+(.+?)\s+([0-9,]+(?:\.\d+)?)$", t)
        if not m:
            return TextMessage(text="ตัวอย่าง: รับเงิน รักษิน 500")
        try:
            member = db.record_payment(group_id, m.group(1), float(m.group(2).replace(',', '')), note="แอดมินบันทึกเอง")
            return TextMessage(text=f"✅ บันทึกแล้ว {member['display_name']} จ่าย {m.group(2)} บาท")
        except Exception as e:
            return TextMessage(text=f"บันทึกไม่ได้: {e}")

    if t.startswith("สร้างรายงาน"):
        if not db.is_admin(group_id, user_id):
            return TextMessage(text="คำสั่งนี้สำหรับแอดมินเท่านั้น")
        out = f"reports/report_{group_id}.xlsx".replace('/', '_')
        create_report_xlsx(group_id, out)
        return TextMessage(text=f"✅ สร้างรายงานแล้ว\nดาวน์โหลด: {APP_BASE_URL}/report/{Path(out).name}")

    if t in ["help", "วิธีใช้", "คำสั่ง"]:
        return TextMessage(text=(
            "คำสั่งหลัก\n"
            "ตั้งแอดมิน <รหัส>\n"
            "นำเข้าไฟล์ตัวอย่าง\n"
            "เปิดรอบ กรกฎาคม 2569\n"
            "ลงทะเบียน ชื่อของคุณ\n"
            "ชำระเงิน\n"
            "จ่ายแล้ว 500\n"
            "รับเงิน รักษิน 500 (แอดมิน)\n"
            "รายจ่าย ค่าน้ำ 1816\n"
            "สรุปยอด\nทวงเงิน\nสร้างรายงาน"
        ))
    return None


@app.get("/")
def home():
    return {"status":"ok", "name":"Fund Group LINE Bot"}


@app.get("/qr")
def qr(amount: float | None = None):
    if not PROMPTPAY_ID:
        raise HTTPException(400, "PROMPTPAY_ID is not set")
    return Response(content=qr_png_bytes(PROMPTPAY_ID, amount), media_type="image/png")


@app.get("/report/{filename}")
def report_file(filename: str):
    path = Path("reports") / filename
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, filename=filename)


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, JoinEvent):
            group_id, _ = get_source_ids(event)
            db.ensure_group(group_id, promptpay_id=PROMPTPAY_ID)
            reply(event.reply_token, TextMessage(text="สวัสดีค่ะ กองกลางBot พร้อมใช้งานแล้ว\nเริ่มจากพิมพ์: ตั้งแอดมิน <รหัส>\nแล้วพิมพ์: วิธีใช้"))
        elif isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            msg = handle_text(event, event.message.text)
            if msg:
                reply(event.reply_token, msg)
        elif isinstance(event, MessageEvent) and isinstance(event.message, ImageMessageContent):
            group_id, user_id = get_source_ids(event)
            # เวอร์ชันวันนี้: รับรูปสลิปแล้วให้ผู้ใช้พิมพ์ยืนยันยอด เพราะ OCR/เช็กสลิปจริงต้องต่อ API เพิ่ม
            reply(event.reply_token, TextMessage(text="📎 ได้รับรูปสลิปแล้ว\nเวอร์ชันเดโมให้พิมพ์ยืนยันยอด: จ่ายแล้ว 500\nหรือแอดมินพิมพ์: รับเงิน รักษิน 500"))
    return "OK"
