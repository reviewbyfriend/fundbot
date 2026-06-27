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
            "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "8px",
            "contents": [
                {"type": "text", "text": p.member.name, "size": "sm", "weight": "bold", "flex": 4, "wrap": True, "color": "#101828"},
                {"type": "text", "text": money(p.due_amount), "size": "sm", "align": "end", "flex": 3, "color": "#101828"},
                {"type": "box", "layout": "vertical", "cornerRadius": "14px", "backgroundColor": "#E8F7EE" if paid else "#FDECEC", "paddingAll": "6px", "flex": 4,
                 "contents": [{"type": "text", "text": "✅ ชำระแล้ว" if paid else "⏰ ยังไม่ได้ชำระ", "size": "xs", "align": "center", "weight": "bold", "color": "#148F4B" if paid else "#D93025"}]},
            ]
        })
        rows.append({"type": "separator", "color": "#EEF2F6"})
    body = [
        {"type": "text", "text": "รายการสมาชิก", "weight": "bold", "size": "xl", "color": "#101828"},
        {"type": "text", "text": f"เงินกองสำนักงาน • {r.title if r else '-'}", "size": "sm", "color": "#667085", "margin": "sm"},
        {"type": "separator", "margin": "md", "color": "#E4E7EC"},
    ] + rows + [
        {"type": "button", "style": "primary", "color": "#16A34A", "margin": "lg", "height": "sm", "action": {"type": "uri", "label": "ชำระเงิน", "uri": f"{base_url()}/pay"}},
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
:root{--navy:#071b46;--blue:#2f67ff;--green:#16a34a;--red:#ef4444;--amber:#f59e0b;--bg:#f3f6fb;--muted:#667085;--line:#e6eaf2;--card:#fff}
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;background:linear-gradient(180deg,#eef5ff 0%,#f7f8fb 45%,#f5f7fb 100%);margin:0;color:#101828}.wrap{max-width:980px;margin:0 auto;padding:18px}.hero{background:linear-gradient(135deg,#071b46,#163e88 55%,#2f67ff);color:white;border-radius:28px;padding:22px;box-shadow:0 18px 50px rgba(7,27,70,.22);margin-bottom:14px}.title{font-size:28px;font-weight:900;color:#0b1f48;letter-spacing:-.3px}.hero .title{color:white}.sub{color:#667085}.hero .sub{color:#d9e6ff}.card{background:rgba(255,255,255,.92);backdrop-filter:blur(10px);border:1px solid #e6eaf2;border-radius:26px;box-shadow:0 14px 40px rgba(16,24,40,.08);padding:18px;margin:12px 0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.stat{border-radius:20px;padding:16px;background:#fff;border:1px solid #e6eaf2;box-shadow:0 10px 25px rgba(16,24,40,.04)}.num{font-size:24px;font-weight:900;letter-spacing:-.3px}.green{color:#159947}.red{color:#d93025}.muted{color:var(--muted)}.member-row{display:grid;grid-template-columns:42px 1fr 110px 146px;gap:10px;align-items:center;padding:13px 8px;border-bottom:1px solid #eef2f6}.avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#dce9ff,#f4f7ff);font-weight:900;color:#24437a}.pill{border-radius:999px;padding:9px 12px;text-align:center;font-weight:800;font-size:13px}.paid{background:#e9f8ef;color:#148f4b;border:1px solid #c5efd4}.unpaid{background:#fdecec;color:#d93025;border:1px solid #ffd0d0}.partial{background:#fff7e6;color:#b76b00;border:1px solid #ffe2a8}.btn{display:block;text-align:center;text-decoration:none;border:0;border-radius:18px;padding:15px 18px;margin:10px 0;background:linear-gradient(135deg,#16a34a,#0e8d40);color:white;font-weight:900;font-size:17px;box-shadow:0 10px 25px rgba(22,163,74,.24)}.btn:active{transform:scale(.99)}.btn2{display:block;text-align:center;text-decoration:none;border:1px solid #bdd7ff;border-radius:16px;padding:13px;margin:9px 0;background:#eef6ff;color:#0b53ce;font-weight:800}.btn-light{background:#fff;color:#0b53ce;border:1px solid #bdd7ff}.choice{display:grid;grid-template-columns:26px 1fr 110px;align-items:center;gap:12px;text-decoration:none;color:#101828;padding:15px 12px;border-bottom:1px solid #eef2f6;border-radius:14px;margin:4px 0}.choice:hover,.choice.active{background:#eaf3ff}.radio{width:22px;height:22px;border-radius:50%;border:2px solid #c4cedd;display:flex;align-items:center;justify-content:center;background:#fff}.choice.active .radio{border-color:#111827}.choice.active .radio:after{content:'';width:10px;height:10px;background:#111827;border-radius:50%}.qrbox{display:flex;align-items:center;justify-content:center;background:#fff;border:1px solid #e6eaf2;border-radius:22px;padding:18px;min-height:280px}.qr{max-width:240px;width:100%;display:block;margin:auto}.copybox{background:#f8fafc;border:1px solid #e6eaf2;border-radius:16px;padding:12px;margin:10px 0}.upload{border:2px dashed #b9c8dc;border-radius:20px;padding:24px;text-align:center;background:#f8fbff}.preview{max-width:100%;border-radius:16px;margin-top:12px;display:none}.success{font-size:72px;line-height:1}.top-actions{display:flex;gap:10px;flex-wrap:wrap}.top-actions a{flex:1}.note{font-size:13px;color:#667085;line-height:1.6}.bankhint{display:grid;grid-template-columns:1fr 1fr;gap:10px}.bankhint div{border-radius:16px;padding:12px;border:1px solid #e6eaf2;background:#fff}.admin-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:12px}input{width:100%;border:1px solid #d8e1ef;border-radius:14px;padding:12px;margin:6px 0;background:#fff;font:inherit}button{font:inherit;cursor:pointer}@media(max-width:720px){.wrap{padding:12px}.hero{border-radius:24px;padding:18px}.title{font-size:25px}.stats{grid-template-columns:1fr}.member-row{grid-template-columns:36px 1fr 96px;gap:8px}.member-row .status{grid-column:2/4}.card{border-radius:22px;padding:14px}.choice{grid-template-columns:26px 1fr 95px}.admin-grid{grid-template-columns:1fr}.bankhint{grid-template-columns:1fr}}
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
    <div class='hero'>
      <div class='title'>💰 เงินกองสำนักงาน</div>
      <div class='sub' id='round'>กำลังโหลด...</div>
      <div class='top-actions'><a class='btn btn-light' href='/pay'>ชำระเงิน</a></div>
    </div>
    <div class='stats'>
      <div class='stat'>ยอดรวมทั้งหมด<div class='num' id='due'>-</div><div class='muted'>บาท</div></div>
      <div class='stat'>ชำระแล้ว<div class='num green' id='paid'>-</div><div class='muted' id='paidCount'>-</div></div>
      <div class='stat'>ค้างชำระ<div class='num red' id='unpaid'>-</div><div class='muted' id='unpaidCount'>-</div></div>
    </div>
    <div class='card'>
      <div style='display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px'><b style='font-size:19px'>รายการสมาชิก</b><span class='muted'>อัปเดตทุก 3 วิ</span></div>
      <div id='rows'></div>
      <a class='btn' href='/pay'>💳 ชำระเงิน</a>
    </div>
    <script>
    function baht(n){return Number(n||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})}
    function initials(name){return (name||'?').replace('ท่าน','').trim().slice(0,1)||'?'}
    async function load(){
      let r=await fetch('/api/status').then(x=>x.json());
      round.textContent='เดือน '+r.round;
      due.textContent=baht(r.due); paid.textContent=baht(r.paid); unpaid.textContent=baht(r.unpaid);
      paidCount.textContent=(r.paid_count||0)+' คน'; unpaidCount.textContent=(r.unpaid_count||0)+' คน';
      rows.innerHTML=r.members.map(m=>`<div class='member-row'><div class='avatar'>${initials(m.name)}</div><b>${m.name}</b><b style='text-align:right'>${baht(m.amount)}</b><span class='status pill ${m.status=='paid'?'paid':(m.status=='partial'?'partial':'unpaid')}'>${m.status=='paid'?'✅ ชำระแล้ว':(m.status=='partial'?'🟡 ชำระบางส่วน':'⏰ ยังไม่ได้ชำระ')}</span></div>`).join('')
    }
    load();setInterval(load,3000)
    </script>
    """
    return page("Dashboard", body)

@app.get("/pay", response_class=HTMLResponse)
def pay(db: Session = Depends(get_db)):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    items = "".join([f"""
      <a class='choice' href='/pay/{p.member.id}'>
        <span class='radio'></span>
        <b>{p.member.name}<br><small class='muted'>{'ชำระแล้ว' if p.status=='paid' else 'เลือกชื่อนี้เพื่อชำระ'}</small></b>
        <b style='text-align:right'>{money(p.due_amount)} บาท</b>
      </a>
    """ for p in pays])
    return page("ชำระเงิน", f"""
      <div class='hero'><div class='title'>ชำระเงิน</div><div class='sub'>เลือกชื่อที่ต้องการชำระ • {r.title if r else '-'}</div></div>
      <div class='card'>{items}</div>
      <a class='btn2' href='/dashboard'>กลับ Dashboard</a>
      <script>
        document.querySelectorAll('.choice').forEach(el=>el.addEventListener('click',()=>{{document.querySelectorAll('.choice').forEach(x=>x.classList.remove('active'));el.classList.add('active')}}))
      </script>
    """)

@app.get("/pay/{member_id}", response_class=HTMLResponse)
def pay_member(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    pp = settings.PROMPTPAY_ID or 'ยังไม่ได้ตั้ง PROMPTPAY_ID'
    qr_html = f"<img class='qr' src='/qr/{m.id}'>" if settings.PROMPTPAY_ID else "<div class='muted' style='text-align:center'>เพิ่ม PROMPTPAY_ID ใน Railway ก่อน QR จึงจะขึ้น</div>"
    body = f"""
    <div class='hero'><div class='title'>ชำระเงิน</div><div class='sub'>{r.title}</div></div>
    <div class='card'>
      <h2 style='margin:0 0 8px'>{m.name}</h2>
      <div class='sub'>ยอดที่ต้องชำระ</div>
      <div class='num green' style='font-size:34px'>{money(p.due_amount)} บาท</div>
      <div class='copybox'>พร้อมเพย์<br><b id='pp'>{pp}</b></div>
      <div class='bankhint'>
        <div>📋 <b>Copy พร้อมเพย์</b><br><span class='note'>คัดลอกเลขแล้วเปิดแอปธนาคารเอง</span></div>
        <div>📱 <b>เปิดแอปธนาคาร</b><br><span class='note'>ปุ่มนี้เปิดได้เฉพาะบางเครื่อง/บางธนาคาร</span></div>
      </div>
      <button class='btn2' onclick='copyPP()'>Copy พร้อมเพย์</button>
      <a class='btn2' href='intent://scan/#Intent;scheme=promptpay;end'>ลองเปิดแอปธนาคาร</a>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>QR พร้อมเพย์</h3>
      <div class='qrbox'>{qr_html}</div>
      <p class='note'>แนะนำ: เปิดแอปธนาคาร แล้วสแกน QR หรือใช้ Copy พร้อมเพย์</p>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>อัปโหลดสลิป</h3>
      <form action='/upload/{m.id}' method='post' enctype='multipart/form-data'>
        <label class='upload'>
          <div style='font-size:34px'>📎</div>
          <b>แตะเพื่อเลือกรูปสลิป</b><br><span class='note'>รองรับ JPG, PNG</span>
          <input id='slipInput' style='display:none' type='file' name='slip' accept='image/*' required onchange='previewSlip(event)'>
          <img id='preview' class='preview'>
        </label>
        <button class='btn' type='submit'>อัปโหลดสลิป</button>
      </form>
    </div>
    <a class='btn2' href='/pay'>กลับ</a>
    <script>
      function copyPP(){{navigator.clipboard.writeText(document.getElementById('pp').innerText);alert('คัดลอกพร้อมเพย์แล้ว')}}
      function previewSlip(e){{let f=e.target.files[0]; if(!f)return; let img=document.getElementById('preview'); img.src=URL.createObjectURL(f); img.style.display='block'}}
    </script>
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
