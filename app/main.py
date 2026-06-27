import base64
import hashlib
import hmac
import os
import re
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, get_db, init_db
from . import services
from .line_client import flex, reply, text
from .promptpay import qr_png_base64
from .report import make_excel
from .models import Expense, Payment

app = FastAPI(title="FundBot MVP Clean")

@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        if db.query(services.Member).count() == 0:
            services.seed_members(db)
        if not services.active_round(db):
            now = datetime.now()
            services.open_round(db, f"มิถุนายน {now.year + 543}")
    finally:
        db.close()

@app.get("/")
def home():
    return {"ok": True, "name": settings.BOT_NAME, "dashboard": "/dashboard", "webhook": "/webhook"}

@app.get("/health")
def health():
    return {"ok": True}

def base_url():
    return (settings.PUBLIC_BASE_URL or "https://web-production-1b96.up.railway.app").rstrip("/")

def verify_signature(body: bytes, sig: str | None):
    if not settings.LINE_CHANNEL_SECRET:
        return True
    if not sig:
        return False
    mac = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), sig)

def parse_amount(s: str):
    m = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None

def money(x):
    return services.money(x)

def collection_flex(db: Session):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    rows = []
    for p in pays:
        paid = p.status == "paid"
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "7px",
            "contents": [
                {"type": "text", "text": p.member.name, "size": "sm", "weight": "bold", "flex": 4, "wrap": True, "color": "#101828"},
                {"type": "text", "text": money(p.due_amount), "size": "sm", "align": "end", "flex": 3, "color": "#101828"},
                {"type": "text", "text": "✅ ชำระแล้ว" if paid else "⏰ ยังไม่ได้ชำระ", "size": "xs", "align": "center", "color": "#118A4B" if paid else "#D93025", "flex": 4},
            ]
        })
        rows.append({"type": "separator", "color": "#EEF2F6"})
    body = [
        {"type": "text", "text": "รายการสมาชิก", "weight": "bold", "size": "lg", "color": "#101828"},
        {"type": "text", "text": f"เงินกองสำนักงาน • {r.title if r else '-'}", "size": "xs", "color": "#667085", "margin": "sm"},
        {"type": "separator", "margin": "md", "color": "#E4E7EC"},
    ] + rows + [
        {"type": "button", "style": "primary", "color": "#12A150", "margin": "md", "height": "sm", "action": {"type": "uri", "label": "ชำระเงิน", "uri": f"{base_url()}/pay"}},
        {"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "เปิด Dashboard", "uri": f"{base_url()}/dashboard"}},
    ]
    return flex("เงินกองสำนักงาน", {"type": "bubble", "size": "giga", "body": {"type": "box", "layout": "vertical", "contents": body}})

def menu_text():
    return text("FundBot ใช้งานหลัก:\n• ส่งหน้าเก็บเงิน\n• ชำระเงิน\n• สรุป")

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
        msg = ev.get("message", {})
        if msg.get("type") == "text":
            handle_text(token, msg.get("text", ""))
        elif msg.get("type") == "image":
            reply(token, [text(f"รับรูปแล้วค่ะ เพื่อเลือกชื่อให้ถูกต้อง กรุณาอัปโหลดสลิปที่หน้าเว็บ\n{base_url()}/pay")])
    return {"ok": True}

def handle_text(reply_token: str, raw: str):
    db = SessionLocal()
    try:
        s = raw.strip()
        low = s.lower()
        if low in ["เมนู", "menu", "help"]:
            reply(reply_token, [menu_text()])
            return
        if low in ["ส่งหน้าเก็บเงิน", "เก็บเงิน", "รายการ", "dashboard", "แดชบอร์ด"]:
            reply(reply_token, [collection_flex(db)])
            return
        if low in ["ชำระเงิน", "จ่ายเงิน", "โอนเงิน"]:
            reply(reply_token, [text(f"เลือกชื่อและอัปโหลดสลิปได้ที่\n{base_url()}/pay")])
            return
        if low in ["สรุป", "ใครยังไม่จ่าย"]:
            reply(reply_token, [text(services.summary_text(db))])
            return
        if s.startswith("เปิดรอบ"):
            title = s.replace("เปิดรอบ", "", 1).strip()
            services.open_round(db, title or f"เดือน {datetime.now().month}/{datetime.now().year + 543}")
            reply(reply_token, [text("✅ เปิดรอบแล้ว"), collection_flex(db)])
            return
        if s.startswith("เพิ่มสมาชิก"):
            rest = s.replace("เพิ่มสมาชิก", "", 1).strip().split()
            amount = parse_amount(rest[-1]) if rest else None
            name = " ".join(rest[:-1])
            if not name or amount is None:
                reply(reply_token, [text("รูปแบบ: เพิ่มสมาชิก ท่านรักษิน 500")])
                return
            services.add_member(db, name, amount)
            reply(reply_token, [text(f"✅ เพิ่ม/แก้สมาชิกแล้ว {name} {money(amount)} บาท")])
            return
        reply(reply_token, [menu_text()])
    finally:
        db.close()

@app.get("/qr/{member_id}")
def qr(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db)
    m = services.member_by_id(db, member_id)
    if not r or not m:
        raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    if not settings.PROMPTPAY_ID:
        return Response("PROMPTPAY_ID not set", status_code=400)
    png_b64 = qr_png_base64(settings.PROMPTPAY_ID, Decimal(p.due_amount or 0))
    return Response(base64.b64decode(png_b64), media_type="image/png")

CSS = """
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;background:#f5f7fb;margin:0;color:#101828}.wrap{max-width:980px;margin:0 auto;padding:20px}.card{background:white;border:1px solid #e6eaf2;border-radius:22px;box-shadow:0 14px 40px rgba(16,24,40,.08);padding:18px;margin:12px 0}.title{font-size:26px;font-weight:800;color:#0b1f48}.sub{color:#667085}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.stat{border-radius:16px;padding:14px;background:#f8fafc;border:1px solid #e6eaf2}.num{font-size:22px;font-weight:800}.green{color:#159947}.red{color:#d93025}.row{display:grid;grid-template-columns:1fr 130px 150px;gap:10px;align-items:center;padding:13px;border-bottom:1px solid #eef2f6}.pill{border-radius:999px;padding:8px 12px;text-align:center;font-weight:700;font-size:13px}.paid{background:#e9f8ef;color:#148f4b}.unpaid{background:#fdecec;color:#d93025}.btn{display:block;text-align:center;text-decoration:none;border:0;border-radius:16px;padding:14px;margin:8px 0;background:linear-gradient(135deg,#12a150,#0a7e3b);color:white;font-weight:800}.btn2{display:block;text-align:center;text-decoration:none;border:1px solid #bdd7ff;border-radius:14px;padding:12px;margin:8px 0;background:#eef6ff;color:#0b53ce;font-weight:700}input,button{font:inherit}.choice{display:block;text-decoration:none;color:#101828;padding:14px;border-bottom:1px solid #eef2f6}.qr{max-width:240px;width:100%;display:block;margin:auto}.upload{border:2px dashed #cbd5e1;border-radius:18px;padding:24px;text-align:center}@media(max-width:640px){.row{grid-template-columns:1fr 95px}.row .status{grid-column:1/3}.stats{grid-template-columns:1fr}.wrap{padding:12px}.title{font-size:22px}}
</style>
"""

def page(title: str, body: str):
    return HTMLResponse(f"<!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'><meta charset='utf-8'><title>{title}</title>{CSS}</head><body><div class='wrap'>{body}</div></body></html>")

@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    t = services.status_totals(db)
    return {
        "round": r.title if r else "-",
        "due": float(t["due"]), "paid": float(t["paid"]), "unpaid": float(t["unpaid"]),
        "paid_count": t["paid_count"], "unpaid_count": t["unpaid_count"], "count": t["count"],
        "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "paid_at": p.paid_at.strftime('%d/%m/%Y %H:%M') if p.paid_at else ""} for p in pays]
    }

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    body = """
    <div class='title'>💰 เงินกองสำนักงาน</div><div class='sub' id='round'>กำลังโหลด...</div>
    <div class='stats'><div class='stat'>ยอดรวม<div class='num' id='due'>-</div></div><div class='stat'>ชำระแล้ว<div class='num green' id='paid'>-</div></div><div class='stat'>ค้างชำระ<div class='num red' id='unpaid'>-</div></div></div>
    <div class='card'><b>รายการสมาชิก</b><div id='rows'></div><a class='btn' href='/pay'>ชำระเงิน</a></div>
    <script>
    function baht(n){return Number(n||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})}
    async function load(){let r=await fetch('/api/status').then(x=>x.json());round.textContent='เดือน '+r.round+' · อัปเดตอัตโนมัติ';due.textContent=baht(r.due);paid.textContent=baht(r.paid);unpaid.textContent=baht(r.unpaid);rows.innerHTML=r.members.map(m=>`<div class='row'><b>${m.name}</b><b>${baht(m.amount)}</b><span class='status pill ${m.status=='paid'?'paid':'unpaid'}'>${m.status=='paid'?'✅ ชำระแล้ว':'⏰ ยังไม่ได้ชำระ'}</span></div>`).join('')}
    load();setInterval(load,3000)
    </script>
    """
    return page("Dashboard", body)

@app.get("/pay", response_class=HTMLResponse)
def pay(db: Session = Depends(get_db)):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    items = "".join([f"<a class='choice' href='/pay/{p.member.id}'><b>{p.member.name}</b><span style='float:right'>{money(p.due_amount)} บาท</span></a>" for p in pays])
    return page("ชำระเงิน", f"<div class='title'>ชำระเงิน</div><div class='sub'>เลือกชื่อที่ต้องการชำระ</div><div class='card'>{items}</div><a class='btn2' href='/dashboard'>กลับ Dashboard</a>")

@app.get("/pay/{member_id}", response_class=HTMLResponse)
def pay_member(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    body = f"""
    <div class='title'>ชำระเงิน</div><div class='card'><h2>{m.name}</h2><div class='sub'>ยอดที่ต้องชำระเดือน {r.title}</div><div class='num green'>{money(p.due_amount)} บาท</div><p>พร้อมเพย์: <b id='pp'>{settings.PROMPTPAY_ID or 'ยังไม่ได้ตั้ง PROMPTPAY_ID'}</b></p><img class='qr' src='/qr/{m.id}'><button class='btn2' onclick='navigator.clipboard.writeText(document.getElementById("pp").innerText);alert("คัดลอกแล้ว")'>Copy พร้อมเพย์</button></div>
    <div class='card'><h3>อัปโหลดสลิป</h3><form action='/upload/{m.id}' method='post' enctype='multipart/form-data'><div class='upload'><input type='file' name='slip' accept='image/*' required></div><button class='btn' type='submit'>อัปโหลดสลิป</button></form></div><a class='btn2' href='/pay'>กลับ</a>
    """
    return page("ชำระเงิน", body)

def safe_name(s: str):
    return re.sub(r"[^0-9A-Za-zก-๙_.-]+", "_", s)[:80]

@app.post("/upload/{member_id}", response_class=HTMLResponse)
async def upload_slip(member_id: int, slip: UploadFile = File(...), db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    month_dir = Path(settings.SLIP_STORAGE_DIR) / safe_name(r.title)
    month_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(slip.filename or '')[1].lower() or '.jpg'
    out = month_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name(m.name)}{ext}"
    with out.open('wb') as f:
        shutil.copyfileobj(slip.file, f)
    services.mark_member_paid(db, m.id, Decimal(p.due_amount or 0), note=f"slip:{out}", slip_path=str(out))
    return page("ชำระแล้ว", f"<div class='card' style='text-align:center'><div style='font-size:70px'>✅</div><h2>ชำระเงินเรียบร้อยแล้ว</h2><p>{m.name}</p><div class='num green'>{money(p.due_amount)} บาท</div><p class='sub'>เก็บสลิปไว้ที่โฟลเดอร์ {r.title}</p><a class='btn' href='/dashboard'>กลับหน้ารายการ</a></div>")

@app.get("/report.xlsx")
def report_xlsx(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: raise HTTPException(404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    expenses = db.query(Expense).filter(Expense.round_id == r.id).all()
    return Response(make_excel(r, payments, expenses), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition":"attachment; filename=fundbot_report.xlsx"})

@app.get("/admin", response_class=HTMLResponse)
def admin(token: str = "", db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(403)
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    rows = "".join([f"<div class='row'><b>{p.member.name}</b><span>{money(p.due_amount)}</span><span class='pill {'paid' if p.status=='paid' else 'unpaid'}'>{'ชำระแล้ว' if p.status=='paid' else 'ยังไม่ชำระ'}</span></div>" for p in pays])
    body = f"<div class='title'>หลังบ้าน FundBot</div><div class='sub'>รอบ: {r.title if r else '-'}</div><div class='card'><a class='btn2' href='/report.xlsx'>ดาวน์โหลด Excel</a>{rows}</div><div class='card'><h3>เปิดรอบใหม่</h3><form action='/admin/open' method='post'><input type='hidden' name='token' value='{token}'><input name='title' placeholder='กรกฎาคม 2569'><button class='btn'>เปิดรอบ</button></form></div><div class='card'><h3>เพิ่ม/แก้สมาชิก</h3><form action='/admin/member' method='post'><input type='hidden' name='token' value='{token}'><input name='name' placeholder='ชื่อ'><input name='amount' placeholder='ยอด'><button class='btn'>บันทึก</button></form></div>"
    return page("Admin", body)

@app.post("/admin/open")
def admin_open(token: str = Form(...), title: str = Form(...), db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN: raise HTTPException(403)
    services.open_round(db, title)
    return RedirectResponse(f"/admin?token={token}", status_code=303)

@app.post("/admin/member")
def admin_member(token: str = Form(...), name: str = Form(...), amount: str = Form(...), db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN: raise HTTPException(403)
    services.add_member(db, name, parse_amount(amount) or Decimal("0"))
    return RedirectResponse(f"/admin?token={token}", status_code=303)
