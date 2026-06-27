import base64, hashlib, hmac, os, re, shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from sqlalchemy.orm import Session

from .config import settings
from .database import init_db, get_db, SessionLocal
from .models import Member, Payment, Expense
from . import services
from .line_client import reply, text, quick_reply_text, image_url, flex
from .promptpay import qr_png_base64
from .report import make_excel

app = FastAPI(title="FundBot Office Collection")

@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Member).count() == 0:
            services.seed_members(db)
        if not services.active_round(db):
            now = datetime.now()
            services.open_round(db, f"{now.strftime('%B')} {now.year + 543}")
    finally:
        db.close()

@app.get("/")
def home():
    return {"ok": True, "name": settings.BOT_NAME, "dashboard": "/dashboard", "webhook": "/webhook"}

@app.get("/health")
def health(): return {"ok": True}

def verify_signature(body: bytes, sig: str | None):
    if not settings.LINE_CHANNEL_SECRET:
        return True
    if not sig: return False
    mac = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), sig)

def parse_amount(s: str) -> Decimal | None:
    m = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", s or "")
    if not m: return None
    try: return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation: return None

def base_url() -> str:
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip('/')
    return "https://web-production-1b96.up.railway.app"

def money(x): return services.money(x)

def line_collection_card(db: Session) -> dict:
    r = services.active_round(db)
    payments = services.get_payments(db, r) if r else []
    rows = []
    for p in payments:
        paid = p.status == "paid"
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "8px",
            "contents": [
                {"type": "text", "text": p.member.name, "size": "sm", "weight": "bold", "flex": 4, "wrap": True},
                {"type": "text", "text": money(p.due_amount), "size": "sm", "align": "end", "flex": 3},
                {"type": "text", "text": "✅ ชำระแล้ว" if paid else "🔴 ยังไม่ได้ชำระ", "size": "xs", "align": "center", "color": "#148F4B" if paid else "#D93025", "flex": 4},
            ]
        })
    body = [
        {"type": "text", "text": "รายการสมาชิก", "weight": "bold", "size": "lg", "color": "#101828"},
        {"type": "text", "text": f"เงินกองสำนักงาน • {r.title if r else '-'}", "size": "xs", "color": "#667085", "margin": "sm"},
        {"type": "separator", "margin": "md"},
    ] + rows + [
        {"type": "separator", "margin": "md"},
        {"type": "button", "style": "primary", "color": "#12A150", "margin": "md", "height": "sm", "action": {"type": "uri", "label": "ชำระเงิน", "uri": f"{base_url()}/pay"}},
        {"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "เปิด Dashboard", "uri": f"{base_url()}/dashboard"}}
    ]
    return flex("เงินกองสำนักงาน", {"type": "bubble", "size": "giga", "body": {"type": "box", "layout": "vertical", "contents": body}})

def menu_msg():
    return quick_reply_text(
        "💰 FundBot เงินกองสำนักงาน\nใช้หลักๆ แค่กดปุ่มใน Dashboard ได้เลย",
        [("ส่งหน้าเก็บเงิน", "ส่งหน้าเก็บเงิน"), ("Dashboard", "dashboard"), ("ชำระเงิน", "ชำระเงิน"), ("สถานะของฉัน", "สถานะ")]
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
            reply(token, [text(f"📎 รับรูปสลิปแล้ว\nเพื่อให้ระบบผูกกับชื่อได้ถูกต้อง กรุณาอัปโหลดผ่านหน้าเว็บนี้ค่ะ\n{base_url()}/pay")])
    return {"ok": True}

def handle_text(reply_token: str, user_id: str, raw: str):
    db = SessionLocal()
    try:
        s = raw.strip(); low = s.lower()
        if low in ["เมนู", "menu", "help", "วิธีใช้"]:
            reply(reply_token, [menu_msg()]); return
        if low in ["ส่งหน้าเก็บเงิน", "เก็บเงิน", "รายการ", "dashboard", "แดชบอร์ด"]:
            reply(reply_token, [line_collection_card(db)]); return
        if low in ["ชำระเงิน", "จ่ายเงิน", "โอนเงิน"]:
            reply(reply_token, [text(f"กดเลือกชื่อและอัปโหลดสลิปได้ที่\n{base_url()}/pay")]); return
        if s.startswith("ลงทะเบียน"):
            name = s.replace("ลงทะเบียน", "", 1).strip()
            ok, msg = services.register_line(db, user_id, name)
            reply(reply_token, [text(msg)]); return
        if low in ["สถานะ", "ยอดของฉัน"]:
            reply(reply_token, [text(services.my_status_text(db, user_id))]); return
        if s.startswith("เปิดรอบ"):
            rest = s.replace("เปิดรอบ", "", 1).strip()
            title = rest or datetime.now().strftime('%B %Y')
            services.open_round(db, title)
            reply(reply_token, [text(f"✅ เปิดรอบ {title} แล้ว"), line_collection_card(db)]); return
        if s.startswith("เพิ่มสมาชิก"):
            parts = s.replace("เพิ่มสมาชิก", "", 1).strip().split()
            amount = parse_amount(parts[-1]) if parts else None
            name = " ".join(parts[:-1]).strip()
            if not name or amount is None:
                reply(reply_token, [text("รูปแบบ: เพิ่มสมาชิก ท่านรักษิน 500")]); return
            services.add_member(db, name, amount)
            reply(reply_token, [text(f"✅ เพิ่ม/แก้สมาชิกแล้ว {name} {money(amount)} บาท")]); return
        if low in ["สรุป", "ใครยังไม่จ่าย"]:
            reply(reply_token, [text(services.summary_text(db))]); return
        reply(reply_token, [menu_msg()])
    finally:
        db.close()

@app.get("/qr/{member_id}")
def qr(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db)
    m = services.member_by_id(db, member_id)
    if not r or not m: return Response("not found", status_code=404)
    p = services.ensure_payment(db, r, m)
    amount = Decimal(p.due_amount or 0)
    if not settings.PROMPTPAY_ID:
        return Response("PROMPTPAY_ID not set", status_code=400)
    png_b64 = qr_png_base64(settings.PROMPTPAY_ID, amount)
    return Response(base64.b64decode(png_b64), media_type="image/png")

# ---------- API for realtime-ish dashboard ----------
@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    return {
        "round": r.title if r else "-",
        "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in pays]
    }

def thai_page(title: str, body: str, extra_js: str = ""):
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{title}</title><style>
:root{{--navy:#071b4d;--blue:#315cf6;--green:#12a150;--red:#d93025;--bg:#f5f8ff;--card:#fff}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;background:linear-gradient(135deg,#eef5ff,#ffffff);color:#101828}}
.wrap{{max-width:520px;margin:0 auto;padding:18px}}.top{{background:linear-gradient(135deg,#071b4d,#315cf6);color:white;padding:22px;border-radius:0 0 28px 28px;margin:0 -18px 18px}}
h1{{margin:0;font-size:24px}}.sub{{opacity:.82;margin-top:6px}}.card{{background:white;border:1px solid #e6eaf2;border-radius:18px;box-shadow:0 10px 28px #18458f14;margin:12px 0;overflow:hidden}}
.row{{display:grid;grid-template-columns:1.45fr .9fr 1.1fr;gap:8px;align-items:center;padding:14px;border-bottom:1px solid #edf0f5}}
.row:last-child{{border-bottom:0}}.name{{font-weight:700}}.amt{{text-align:right;font-weight:700}}.pill{{font-size:13px;text-align:center;border-radius:12px;padding:8px 6px;font-weight:700}}
.paid{{background:#e8f7ef;color:#118045;border:1px solid #bfe8d0}}.unpaid{{background:#ffecec;color:#c62828;border:1px solid #ffd0d0}}
.btn{{display:block;width:100%;border:0;border-radius:16px;padding:15px 18px;margin:12px 0;background:linear-gradient(135deg,#0fb46b,#058947);color:white;text-align:center;text-decoration:none;font-weight:800;font-size:17px}}
.btn2{{background:#eef4ff;color:#174ea6;border:1px solid #c9dbff}}.selectrow{{display:flex;justify-content:space-between;align-items:center;padding:16px;border-bottom:1px solid #edf0f5;text-decoration:none;color:#101828}}
input,select{{width:100%;padding:13px;border:1px solid #d8deea;border-radius:14px;margin:8px 0;font-size:16px}}button{{cursor:pointer}}.qr{{display:block;max-width:260px;margin:16px auto;border:1px solid #e5e7eb;border-radius:16px;padding:12px;background:white}}
.note{{background:#eef9f1;border:1px solid #ccebd6;color:#176b39;padding:13px;border-radius:14px;margin:12px 0}}.small{{font-size:13px;color:#667085}}
</style></head><body><div class='wrap'>{body}</div>{extra_js}</body></html>""")

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    body = """<div class='top'><h1>💰 เงินกองสำนักงาน</h1><div class='sub' id='round'>กำลังโหลด...</div></div>
<div class='card' id='list'></div><a class='btn' href='/pay'>ชำระเงิน</a><div class='small'>หน้านี้อัปเดตอัตโนมัติทุก 3 วินาที โดยไม่ต้องส่งข้อความซ้ำในกลุ่ม LINE</div>"""
    js = """<script>
function baht(n){return Number(n||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})}
async function load(){const r=await fetch('/api/status'); const d=await r.json(); document.getElementById('round').textContent='เดือน '+d.round; document.getElementById('list').innerHTML=d.members.map(m=>`<div class='row'><div class='name'>${m.name}</div><div class='amt'>${baht(m.amount)}</div><div class='pill ${m.status==='paid'?'paid':'unpaid'}'>${m.status==='paid'?'✓ ชำระแล้ว':'○ ยังไม่ได้ชำระ'}</div></div>`).join('')}
load(); setInterval(load,3000);
</script>"""
    return thai_page("FundBot Dashboard", body, js)

@app.get("/pay", response_class=HTMLResponse)
def pay(db: Session = Depends(get_db)):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    body = "<div class='top'><h1>ชำระเงิน</h1><div class='sub'>เลือกชื่อที่ต้องการชำระ</div></div><div class='card'>"
    for p in pays:
        body += f"<a class='selectrow' href='/pay/{p.member.id}'><b>{p.member.name}</b><span>{money(p.due_amount)} บาท</span></a>"
    body += "</div><a class='btn btn2' href='/dashboard'>กลับ Dashboard</a>"
    return thai_page("ชำระเงิน", body)

@app.get("/pay/{member_id}", response_class=HTMLResponse)
def pay_member(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    qr_url = f"/qr/{m.id}"
    body = f"""<div class='top'><h1>ชำระเงิน</h1><div class='sub'>{m.name}</div></div>
<div class='card' style='padding:18px'><h2>{m.name}</h2><div class='small'>ยอดที่ต้องชำระเดือน {r.title}</div><h1>{money(p.due_amount)} บาท</h1>
<div class='note'>พร้อมเพย์: <b>{settings.PROMPTPAY_ID or 'ยังไม่ได้ตั้งค่า PROMPTPAY_ID'}</b></div>
<img class='qr' src='{qr_url}'><button class='btn btn2' onclick="navigator.clipboard.writeText('{settings.PROMPTPAY_ID}')">Copy พร้อมเพย์</button>
<form method='post' action='/upload/{m.id}' enctype='multipart/form-data'><label><b>หลังโอนแล้ว อัปโหลดสลิป</b></label><input type='file' name='slip' accept='image/*' required><button class='btn'>อัปโหลดสลิป</button></form></div>"""
    return thai_page("ชำระเงิน", body)

def safe_name(s: str) -> str:
    return re.sub(r"[^0-9A-Za-zก-๙_.-]+", "_", s)[:80]

def ocr_amount_from_image(path: str) -> Decimal | None:
    # Optional OCR.Space support. If no key, skip OCR and mark paid by selected member amount.
    if not settings.OCR_SPACE_API_KEY:
        return None
    try:
        with open(path, 'rb') as f:
            res = requests.post('https://api.ocr.space/parse/image', files={'file': f}, data={'apikey': settings.OCR_SPACE_API_KEY, 'language': 'eng'}, timeout=20)
        txt = res.json().get('ParsedResults', [{}])[0].get('ParsedText', '')
        nums = [Decimal(x.replace(',', '')) for x in re.findall(r"\d[\d,]*\.\d{2}", txt)]
        return max(nums) if nums else None
    except Exception:
        return None

@app.post("/upload/{member_id}", response_class=HTMLResponse)
async def upload_slip(member_id: int, slip: UploadFile = File(...), db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    month_dir = Path(settings.SLIP_STORAGE_DIR) / safe_name(r.title)
    month_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(slip.filename or '')[1].lower() or '.jpg'
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name(m.name)}{ext}"
    out = month_dir / filename
    with out.open('wb') as f:
        shutil.copyfileobj(slip.file, f)
    detected = ocr_amount_from_image(str(out))
    due = Decimal(p.due_amount or 0)
    # ถ้ามี OCR แล้วไม่ตรง ให้ยังไม่ mark paid; ถ้าไม่มี OCR ให้ mark paid ตามชื่อที่เลือกเพื่อใช้งานด่วน
    if detected is not None and abs(detected - due) > Decimal('0.01'):
        body = f"<div class='top'><h1>ตรวจสอบสลิป</h1></div><div class='card' style='padding:18px'><h2>⚠️ ยอดไม่ตรง</h2><p>ระบบอ่านได้ {money(detected)} บาท แต่ยอดของ {m.name} คือ {money(due)} บาท</p><p class='small'>ไฟล์ถูกเก็บไว้ที่โฟลเดอร์สลิปเดือน {r.title}</p><a class='btn btn2' href='/dashboard'>กลับ Dashboard</a></div>"
        return thai_page("ยอดไม่ตรง", body)
    services.mark_member_paid(db, m.id, due, note=f"slip:{out}")
    body = f"<div class='top'><h1>ตรวจสอบสำเร็จ</h1></div><div class='card' style='padding:22px;text-align:center'><h1>✅</h1><h2>ชำระเงินเรียบร้อยแล้ว</h2><p>{m.name}<br>{money(due)} บาท</p><p class='small'>เก็บสลิปไว้ที่โฟลเดอร์ {r.title}</p><a class='btn' href='/dashboard'>กลับหน้ารายการ</a></div>"
    return thai_page("ชำระแล้ว", body)

@app.get("/report.xlsx")
def report_xlsx(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: return Response("no active round", status_code=404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    expenses = db.query(Expense).filter(Expense.round_id == r.id).all()
    data = make_excel(r, payments, expenses)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=fundbot_report.xlsx"})

# ---------- Admin ----------
def auth(token: str):
    if token != settings.ADMIN_TOKEN: raise HTTPException(403, "bad token")

@app.get("/admin", response_class=HTMLResponse)
def admin(token: str, db: Session = Depends(get_db)):
    auth(token)
    r = services.active_round(db)
    body = f"<div class='top'><h1>หลังบ้าน FundBot</h1><div class='sub'>รอบปัจจุบัน: {r.title if r else '-'}</div></div><a class='btn' href='/dashboard'>ดู Dashboard</a><a class='btn btn2' href='/report.xlsx'>ดาวน์โหลด Excel</a>"
    body += f"<div class='card' style='padding:18px'><h3>เปิดรอบใหม่</h3><form method='post' action='/admin/open?token={token}'><input name='title' placeholder='กรกฎาคม 2569'><button class='btn'>เปิดรอบ</button></form></div>"
    body += f"<div class='card' style='padding:18px'><h3>เพิ่ม/แก้สมาชิก</h3><form method='post' action='/admin/member?token={token}'><input name='name' placeholder='ชื่อ'><input name='amount' placeholder='ยอดต่อเดือน'><button class='btn'>บันทึก</button></form></div>"
    body += "<div class='card' id='list'></div>"
    js = """<script>function baht(n){return Number(n||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})}async function load(){const r=await fetch('/api/status');const d=await r.json();document.getElementById('list').innerHTML=d.members.map(m=>`<div class='row'><div class='name'>${m.name}</div><div class='amt'>${baht(m.amount)}</div><div class='pill ${m.status==='paid'?'paid':'unpaid'}'>${m.status==='paid'?'ชำระแล้ว':'ยังไม่ได้ชำระ'}</div></div>`).join('')}load();setInterval(load,3000)</script>"""
    return thai_page("Admin", body, js)

@app.post("/admin/open")
def admin_open(token: str, title: str = Form(...), db: Session = Depends(get_db)):
    auth(token); services.open_round(db, title.strip()); return RedirectResponse(f"/admin?token={token}", status_code=303)

@app.post("/admin/member")
def admin_member(token: str, name: str = Form(...), amount: str = Form(...), db: Session = Depends(get_db)):
    auth(token); services.add_member(db, name.strip(), parse_amount(amount) or Decimal('0')); return RedirectResponse(f"/admin?token={token}", status_code=303)
