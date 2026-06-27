import base64, hashlib, hmac, re
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import desc
from .config import settings
from .database import init_db, get_db, SessionLocal
from .models import Member, Round, Payment, Expense
from . import services
from .line_client import reply, text, quick_reply_text, image_url
from .promptpay import qr_png_base64
from .report import make_excel

app = FastAPI(title="FundBot Group UI")

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def home():
    return {"ok": True, "name": settings.BOT_NAME, "webhook": "/webhook", "admin": "/admin?token=..."}

@app.get("/health")
def health(): return {"ok": True}

def verify_signature(body: bytes, sig: str | None):
    if not settings.LINE_CHANNEL_SECRET:
        return True
    if not sig: return False
    mac = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), sig)

def parse_amount(s: str) -> Decimal | None:
    m = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", s)
    if not m: return None
    try: return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation: return None

def menu_msg():
    return quick_reply_text(
        "💰 เมนูกองกลาง\nเลือกคำสั่งด้านล่าง หรือพิมพ์คำสั่งเองได้เลย",
        [("สถานะของฉัน", "สถานะ"), ("ชำระเงิน", "ชำระเงิน"), ("สรุป", "สรุป"), ("ใครยังไม่จ่าย", "ใครยังไม่จ่าย"), ("วิธีใช้", "วิธีใช้")]
    )

@app.post("/webhook")
async def webhook(req: Request):
    body = await req.body()
    if not verify_signature(body, req.headers.get("x-line-signature")):
        raise HTTPException(status_code=400, detail="invalid signature")
    data = await req.json()
    for ev in data.get("events", []):
        if ev.get("type") != "message":
            continue
        token = ev.get("replyToken")
        src = ev.get("source", {})
        user_id = src.get("userId", "")
        msg = ev.get("message", {})
        if msg.get("type") == "text":
            handle_text(token, user_id, msg.get("text", ""))
        elif msg.get("type") == "image":
            # MVP: รับรูปสลิปแล้วให้ผู้ใช้พิมพ์ยอดยืนยัน
            reply(token, [text("📎 รับรูปสลิปแล้ว\nตอนนี้ให้พิมพ์ยืนยันยอดก่อน เช่น\nจ่ายแล้ว 500\nเดี๋ยวเวอร์ชันถัดไปจะ OCR ให้อัตโนมัติ")])
    return {"ok": True}

def handle_text(reply_token: str, user_id: str, raw: str):
    db = SessionLocal()
    try:
        s = raw.strip()
        low = s.lower()
        if low in ["เมนู", "menu", "help", "วิธีใช้"]:
            reply(reply_token, [menu_msg(), text("คำสั่งแอดมิน:\nเพิ่มสมาชิก ชื่อ 500\nเปิดรอบ กรกฎาคม 2569 ยกมา 17813.50\nรายจ่าย ค่าน้ำ 350\nรายงาน\n\nคำสั่งสมาชิก:\nลงทะเบียน ชื่อ\nสถานะ\nชำระเงิน\nจ่ายแล้ว 500")]); return
        if s.startswith("เพิ่มสมาชิก"):
            parts = s.replace("เพิ่มสมาชิก", "", 1).strip().split()
            if len(parts) < 2:
                reply(reply_token, [text("รูปแบบ: เพิ่มสมาชิก ชื่อ 500")]); return
            amount = parse_amount(parts[-1])
            name = " ".join(parts[:-1]).strip()
            if not name or amount is None:
                reply(reply_token, [text("รูปแบบ: เพิ่มสมาชิก ชื่อ 500")]); return
            services.add_member(db, name, amount)
            reply(reply_token, [text(f"✅ เพิ่ม/แก้สมาชิกแล้ว\n{name}: {services.money(amount)} บาท")]); return
        if s.startswith("เปิดรอบ"):
            rest = s.replace("เปิดรอบ", "", 1).strip()
            carry = Decimal("0")
            if "ยกมา" in rest:
                title, carry_txt = rest.split("ยกมา", 1)
                carry = parse_amount(carry_txt) or Decimal("0")
            else:
                title = rest
            if not title.strip():
                reply(reply_token, [text("รูปแบบ: เปิดรอบ กรกฎาคม 2569 ยกมา 17813.50")]); return
            r = services.open_round(db, title.strip(), carry)
            reply(reply_token, [text(f"✅ เปิดรอบ {r.title} แล้ว\nยอดยกมา {services.money(r.carry_over)} บาท")]); return
        if s.startswith("ลงทะเบียน"):
            name = s.replace("ลงทะเบียน", "", 1).strip()
            ok, msg = services.register_line(db, user_id, name)
            reply(reply_token, [text(msg)]); return
        if low in ["สถานะ", "ยอดของฉัน", "ดูยอด", "my"]:
            reply(reply_token, [text(services.my_status_text(db, user_id))]); return
        if low in ["ชำระเงิน", "จ่ายเงิน", "โอนเงิน", "qr"]:
            r = services.active_round(db)
            m = db.query(Member).filter(Member.line_user_id == user_id).first()
            if not r or not m:
                reply(reply_token, [text("ยังไม่มีรอบ หรือยังไม่ได้ลงทะเบียน\nพิมพ์: ลงทะเบียน ชื่อของคุณ")]); return
            p = services.ensure_payment(db, r, m)
            remain = Decimal(p.due_amount or 0) - Decimal(p.paid_amount or 0)
            if remain <= 0:
                reply(reply_token, [text("✅ คุณจ่ายครบแล้ว")]); return
            qr_url = f"https://{get_public_host()}/qr/{m.id}?amount={remain}"
            reply(reply_token, [text(f"💳 {m.name}\nยอดที่ต้องชำระ: {services.money(remain)} บาท\nสแกน QR แล้วส่งสลิปในกลุ่มได้เลย"), image_url(qr_url)]); return
        if s.startswith("จ่ายแล้ว") or s.startswith("โอนแล้ว"):
            amount = parse_amount(s)
            ok, msg = services.pay_for_user(db, user_id, amount)
            reply(reply_token, [text(msg)]); return
        if s.startswith("รายจ่าย"):
            rest = s.replace("รายจ่าย", "", 1).strip()
            amount = parse_amount(rest)
            title = re.sub(r"[0-9][0-9,]*(?:\.\d+)?", "", rest).strip() or "รายจ่าย"
            if amount is None:
                reply(reply_token, [text("รูปแบบ: รายจ่าย ค่าน้ำ 350")]); return
            ok, msg = services.add_expense(db, title, amount)
            reply(reply_token, [text(msg)]); return
        if low in ["สรุป", "ใครยังไม่จ่าย", "ทวงเงิน"]:
            reply(reply_token, [text(services.summary_text(db))]); return
        if low in ["รายงาน", "excel"]:
            reply(reply_token, [text(f"📄 ดาวน์โหลดรายงาน Excel ได้ที่\nhttps://{get_public_host()}/report.xlsx")]); return
        reply(reply_token, [menu_msg()])
    finally:
        db.close()

def get_public_host():
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.replace("https://", "").replace("http://", "")
    return "web-production-1b96.up.railway.app"

@app.get("/qr/{member_id}")
def qr(member_id: int, amount: str = "0"):
    if not settings.PROMPTPAY_ID:
        return Response("PROMPTPAY_ID not set", status_code=400)
    png_b64 = qr_png_base64(settings.PROMPTPAY_ID, Decimal(amount))
    return Response(base64.b64decode(png_b64), media_type="image/png")

@app.get("/report.xlsx")
def report_xlsx(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: return Response("no active round", status_code=404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    expenses = db.query(Expense).filter(Expense.round_id == r.id).all()
    data = make_excel(r, payments, expenses)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=fundbot_report.xlsx"})

# ---------- Simple Admin UI ----------
def page(body: str):
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>FundBot</title>
<style>body{{font-family:Arial,'Tahoma',sans-serif;margin:24px;background:#f7f7fb;color:#222}}.card{{background:white;padding:18px;border-radius:14px;margin:12px 0;box-shadow:0 2px 8px #0001}}input,button{{padding:10px;border-radius:8px;border:1px solid #ccc;margin:4px}}button{{background:#06c755;color:white;border:0;font-weight:bold}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{border:1px solid #ddd;padding:8px}}th{{background:#eee}}a{{color:#06c755}}</style></head><body>{body}</body></html>""")

def auth(token: str):
    if token != settings.ADMIN_TOKEN: raise HTTPException(403, "bad token")

@app.get("/admin", response_class=HTMLResponse)
def admin(token: str, db: Session = Depends(get_db)):
    auth(token)
    r = services.active_round(db)
    members = db.query(Member).order_by(Member.name).all()
    body = f"<h1>💰 FundBot Admin</h1><div class='card'><b>รอบปัจจุบัน:</b> {r.title if r else '-'}<br><a href='/report.xlsx'>ดาวน์โหลด Excel</a></div>"
    body += f"""<div class='card'><h3>เปิดรอบใหม่</h3><form method='post' action='/admin/open?token={token}'><input name='title' placeholder='กรกฎาคม 2569'><input name='carry_over' placeholder='ยอดยกมา' value='0'><button>เปิดรอบ</button></form></div>"""
    body += f"""<div class='card'><h3>เพิ่มสมาชิก</h3><form method='post' action='/admin/member?token={token}'><input name='name' placeholder='ชื่อ'><input name='amount' placeholder='ยอดต่อเดือน'><button>บันทึก</button></form></div>"""
    body += f"""<div class='card'><h3>เพิ่มรายจ่าย</h3><form method='post' action='/admin/expense?token={token}'><input name='title' placeholder='ค่าน้ำ'><input name='amount' placeholder='จำนวน'><button>บันทึก</button></form></div>"""
    body += "<div class='card'><h3>สมาชิก</h3><table><tr><th>ชื่อ</th><th>ยอด</th><th>LINE</th></tr>"
    for m in members:
        body += f"<tr><td>{m.name}</td><td>{services.money(m.default_amount)}</td><td>{'ผูกแล้ว' if m.line_user_id else '-'}</td></tr>"
    body += "</table></div>"
    body += "<div class='card'><pre>"+services.summary_text(db)+"</pre></div>"
    return page(body)

@app.post("/admin/open")
def admin_open(token: str, title: str = Form(...), carry_over: str = Form("0"), db: Session = Depends(get_db)):
    auth(token); services.open_round(db, title, parse_amount(carry_over) or Decimal("0")); return HTMLResponse(f"<script>location.href='/admin?token={token}'</script>")

@app.post("/admin/member")
def admin_member(token: str, name: str = Form(...), amount: str = Form(...), db: Session = Depends(get_db)):
    auth(token); services.add_member(db, name.strip(), parse_amount(amount) or Decimal("0")); return HTMLResponse(f"<script>location.href='/admin?token={token}'</script>")

@app.post("/admin/expense")
def admin_expense(token: str, title: str = Form(...), amount: str = Form(...), db: Session = Depends(get_db)):
    auth(token); services.add_expense(db, title.strip(), parse_amount(amount) or Decimal("0")); return HTMLResponse(f"<script>location.href='/admin?token={token}'</script>")
