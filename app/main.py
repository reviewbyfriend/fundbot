import base64
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import uuid
import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, get_db, init_db
from . import services
from .line_client import flex, reply, push, text
from .promptpay import qr_png_base64
from .report import make_excel, make_word, make_pdf
from PIL import Image, ImageOps
from .models import Expense, Payment, BotState

app = FastAPI(title="FundBot v2 Stable")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
Path(settings.SLIP_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.SIGNATURE_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/slips", StaticFiles(directory=settings.SLIP_STORAGE_DIR), name="slips")
app.mount("/signatures", StaticFiles(directory=settings.SIGNATURE_STORAGE_DIR), name="signatures")

@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        if db.query(services.Member).count() == 0:
            services.seed_members(db)
        if not services.active_round(db):
            now = datetime.now()
            services.open_round(db, f"{now.strftime('%Y-%m')}")
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

def amount_key(x):
    try:
        return str(int(Decimal(x or 0)))
    except Exception:
        return ""

def bank_qr_url(amount):
    key = amount_key(amount)
    static_file = Path(__file__).parent / "static" / "payment_qr" / f"{key}.jpg"
    if static_file.exists():
        return f"/static/payment_qr/{key}.jpg"
    return None

def set_state(db: Session, key: str, value: str | None):
    st = db.query(BotState).filter(BotState.key == key).first()
    if not st:
        st = BotState(key=key, value=value)
        db.add(st)
    else:
        st.value = value
    db.commit()


def get_state(db: Session, key: str) -> str | None:
    st = db.query(BotState).filter(BotState.key == key).first()
    return st.value if st else None


def save_line_target(db: Session, event: dict):
    source = event.get("source", {}) or {}
    target = source.get("groupId") or source.get("roomId") or source.get("userId")
    if target:
        set_state(db, "line_target_id", target)


def update_line_summary(db: Session):
    """LINE messages cannot be edited after sending, so push a fresh updated card."""
    target = get_state(db, "line_target_id")
    if target:
        push(target, [collection_flex(db)])




def slip_public_url(slip_path: str | None) -> str:
    if not slip_path:
        return ""
    try:
        rel = Path(slip_path).resolve().relative_to(Path(settings.SLIP_STORAGE_DIR).resolve())
        return f"{base_url()}/slips/{str(rel).replace(os.sep, '/')}"
    except Exception:
        return ""


def signature_public_url(receipt_path: str | None) -> str:
    if not receipt_path:
        return ""
    try:
        rel = Path(receipt_path).resolve().relative_to(Path(settings.SIGNATURE_STORAGE_DIR).resolve())
        return f"{base_url()}/signatures/{str(rel).replace(os.sep, '/')}"
    except Exception:
        return ""

def current_month_key() -> str:
    return datetime.now().strftime("%Y-%m")

def save_upload_image(upload: UploadFile, folder: Path, prefix: str) -> Path:
    content_type = (upload.content_type or "").lower()
    if content_type not in ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/octet-stream"]:
        raise HTTPException(status_code=400, detail="รองรับเฉพาะไฟล์รูปภาพ")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = (Path(upload.filename or "").suffix or ".jpg").lower()
    if suffix not in [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"]:
        suffix = ".jpg"
    tmp = folder / f"tmp_{uuid.uuid4().hex}{suffix}"
    with tmp.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    if tmp.stat().st_size > 8 * 1024 * 1024:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="ไฟล์ใหญ่เกิน 8MB")
    out = folder / f"{prefix}_{uuid.uuid4().hex}.jpg"
    try:
        img = Image.open(tmp)
        img = ImageOps.exif_transpose(img)
        img.thumbnail((1600, 1600))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=82, optimize=True)
        tmp.unlink(missing_ok=True)
        return out
    except Exception:
        tmp.rename(out)
        return out

def evidence_url(payment: Payment) -> str:
    if getattr(payment, "payment_type", "") == "cash":
        return signature_public_url(getattr(payment, "receipt_path", None))
    return slip_public_url(payment.slip_path)


def notify_admin_pending_slip(db: Session, payment: Payment):
    """แจ้งแอดมินว่ามีสลิปรอตรวจ พร้อมปุ่มอนุมัติ/ไม่ผ่าน"""
    target = settings.ADMIN_NOTIFY_TARGET_ID or get_state(db, "line_target_id")
    if not target:
        return
    slip_url = evidence_url(payment)
    admin_token = settings.ADMIN_TOKEN
    approve_url = f"{base_url()}/admin/approve/{payment.id}?token={admin_token}"
    reject_url = f"{base_url()}/admin/reject/{payment.id}?token={admin_token}"
    view_url = f"{base_url()}/admin?token={admin_token}#pending"
    contents = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "📥 มีสลิปรอตรวจ", "weight": "bold", "size": "xl", "color": "#101828"},
                {"type": "separator", "color": "#E4E7EC"},
                {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "text", "text": f"ชื่อ: {payment.member.name}", "size": "md", "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"ยอด: {money(payment.due_amount)} บาท", "size": "md", "color": "#16A34A", "weight": "bold"},
                    {"type": "text", "text": f"ประเภท: {'เงินสด' if getattr(payment, 'payment_type', '') == 'cash' else 'โอน'}", "size": "sm", "color": "#667085"},
                    {"type": "text", "text": f"เดือน: {payment.round.title}", "size": "sm", "color": "#667085", "wrap": True},
                ]},
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "button", "style": "primary", "color": "#16A34A", "action": {"type": "uri", "label": "✅ อนุมัติ", "uri": approve_url}},
                {"type": "button", "style": "secondary", "action": {"type": "uri", "label": "🖼 ดูสลิป/หลังบ้าน", "uri": view_url}},
                {"type": "button", "style": "link", "color": "#DC2626", "action": {"type": "uri", "label": "❌ ไม่ผ่าน", "uri": reject_url}},
            ]
        }
    }
    if slip_url:
        contents["hero"] = {"type": "image", "url": slip_url, "size": "full", "aspectRatio": "16:10", "aspectMode": "cover"}
    push(target, [flex("มีสลิปรอตรวจ", contents)])

def ocr_space_text(image_path: Path) -> str:
    if not settings.OCR_SPACE_API_KEY:
        return ""
    try:
        import requests
        with image_path.open("rb") as f:
            resp = requests.post(
                "https://api.ocr.space/parse/image",
                files={"filename": f},
                data={"apikey": settings.OCR_SPACE_API_KEY, "language": "eng", "OCREngine": "2", "isOverlayRequired": "false"},
                timeout=25,
            )
        js = resp.json()
        parts = []
        for item in js.get("ParsedResults", []) or []:
            parts.append(item.get("ParsedText") or "")
        return "\n".join(parts)
    except Exception:
        return ""


def amounts_from_text(txt: str) -> list[Decimal]:
    vals = []
    # Find money-looking numbers. Handles 500.00, 1,000.00, 2500.00.
    for raw in re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d{3,5}(?:\.\d{2})?)(?!\d)", txt or ""):
        try:
            vals.append(Decimal(raw.replace(",", "")))
        except Exception:
            pass
    return vals


def verify_slip_amount(image_path: Path, expected: Decimal) -> tuple[str, str, Decimal | None]:
    """Return status: paid / pending / rejected.
    We only mark paid if OCR confidently finds the exact expected amount.
    If OCR is not configured or cannot read, keep pending for admin review.
    """
    expected = Decimal(expected or 0).quantize(Decimal("0.01"))
    if not settings.OCR_SPACE_API_KEY:
        return "pending", "ยังไม่ได้ตั้ง OCR_SPACE_API_KEY จึงเก็บสลิปไว้รอตรวจสอบ ไม่ปรับเป็นชำระแล้วอัตโนมัติ", None
    txt = ocr_space_text(image_path)
    vals = [v.quantize(Decimal("0.01")) for v in amounts_from_text(txt)]
    if expected in vals:
        return "paid", f"OCR พบยอด {money(expected)} บาท ตรงกับยอดที่ต้องชำระ", expected
    if vals:
        return "rejected", f"ยอดในสลิปไม่ตรง ระบบอ่านได้: {', '.join(money(v) for v in vals[:6])} บาท / ต้องชำระ {money(expected)} บาท", vals[0]
    return "pending", "OCR อ่านยอดเงินไม่ได้ จึงเก็บสลิปไว้รอตรวจสอบ ไม่ปรับเป็นชำระแล้วอัตโนมัติ", None

def collection_flex(db: Session):
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    rows = []
    for p in pays:
        paid = p.status == "paid"
        pending = p.status == "pending"
        paid_cash = paid and getattr(p, "payment_type", "") == "cash"
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "8px",
            "contents": [
                {"type": "text", "text": p.member.name, "size": "sm", "weight": "bold", "flex": 4, "wrap": True, "color": "#101828"},
                {"type": "text", "text": money(p.due_amount), "size": "sm", "align": "end", "flex": 3, "color": "#101828"},
                {"type": "box", "layout": "vertical", "cornerRadius": "14px", "backgroundColor": "#E8F7EE" if paid else ("#FFF7E6" if pending else "#FDECEC"), "paddingAll": "6px", "flex": 4,
                 "contents": [{"type": "text", "text": ("✅ เงินสด" if paid_cash else "✅ โอน") if paid else ("🟡 รอตรวจ" if pending else "🔴 ยังไม่จ่าย"), "size": "xs", "align": "center", "weight": "bold", "color": "#148F4B" if paid else ("#B76B00" if pending else "#D93025")}]},
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
        # Remember the latest group/room/user target so upload can send a fresh updated card.
        dbtmp = SessionLocal()
        try:
            save_line_target(dbtmp, ev)
        finally:
            dbtmp.close()
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
:root{--navy:#071b46;--blue:#2f67ff;--green:#16a34a;--red:#ef4444;--orange:#ff7a1a;--purple:#7c3aed;--pink:#ec4899;--bg:#eef4ff;--muted:#667085;--line:#e6eaf2;--card:#fff}
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;background:linear-gradient(180deg,#eaf3ff 0%,#f6f8fc 45%,#f7f9fd 100%);margin:0;color:#101828}.wrap{max-width:980px;margin:0 auto;padding:18px}.hero{background:linear-gradient(135deg,#071b46,#17479c 58%,#2f67ff);color:white;border-radius:30px;padding:22px;box-shadow:0 18px 50px rgba(7,27,70,.22);margin-bottom:14px;position:relative;overflow:hidden}.hero:after{content:'';position:absolute;right:-35px;top:-35px;width:140px;height:140px;border-radius:50%;background:rgba(255,255,255,.12)}.title{font-size:28px;font-weight:900;color:#0b1f48;letter-spacing:-.3px}.hero .title{color:white}.sub{color:#667085}.hero .sub{color:#d9e6ff}.card{background:rgba(255,255,255,.94);backdrop-filter:blur(10px);border:1px solid #e6eaf2;border-radius:26px;box-shadow:0 14px 40px rgba(16,24,40,.08);padding:18px;margin:12px 0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.stat{border-radius:22px;padding:16px;background:#fff;border:1px solid #e6eaf2;box-shadow:0 10px 25px rgba(16,24,40,.04)}.num{font-size:24px;font-weight:900;letter-spacing:-.3px}.green{color:#159947}.red{color:#d93025}.muted{color:var(--muted)}.member-row{display:grid;grid-template-columns:44px 1fr 112px 152px;gap:10px;align-items:center;padding:13px 8px;border-bottom:1px solid #eef2f6}.member-row:last-child{border-bottom:0}.avatar{width:38px;height:38px;border-radius:16px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#dce9ff,#f4f7ff);font-weight:900;color:#24437a}.pill{border-radius:999px;padding:9px 12px;text-align:center;font-weight:800;font-size:13px}.paid{background:#e9f8ef;color:#148f4b;border:1px solid #c5efd4}.unpaid{background:#fdecec;color:#d93025;border:1px solid #ffd0d0}.partial{background:#fff7e6;color:#b76b00;border:1px solid #ffe2a8}.btn{display:block;text-align:center;text-decoration:none;border:0;border-radius:18px;padding:15px 18px;margin:10px 0;background:linear-gradient(135deg,#16a34a,#0e8d40);color:white;font-weight:900;font-size:17px;box-shadow:0 10px 25px rgba(22,163,74,.24)}.btn:active{transform:scale(.99)}.btn2{display:block;text-align:center;text-decoration:none;border:1px solid #bdd7ff;border-radius:16px;padding:13px;margin:9px 0;background:#eef6ff;color:#0b53ce;font-weight:800}.btn-light{background:#fff;color:#0b53ce;border:1px solid #bdd7ff}.leave-card{border-radius:28px;overflow:hidden;background:#fff;box-shadow:0 14px 42px rgba(16,24,40,.1);border:1px solid #e6eaf2}.leave-head{padding:20px 18px;background:linear-gradient(135deg,#ff7a1a,#ff9f43);color:#fff}.leave-head.blue{background:linear-gradient(135deg,#1f6feb,#2f67ff)}.leave-head h1{font-size:26px;margin:0 0 4px}.leave-body{padding:16px;background:#fff7ec}.leave-body.blue{background:#eef5ff}.list-title{font-size:22px;font-weight:900;color:#101828;margin-bottom:12px}.choice{display:grid;grid-template-columns:28px 1fr 118px;align-items:center;gap:12px;text-decoration:none;color:#101828;padding:16px 12px;border:1px solid transparent;border-radius:18px;margin:8px 0;background:white;box-shadow:0 5px 14px rgba(16,24,40,.04)}.choice:active,.choice.active{background:#eaf3ff;border-color:#93c5fd;box-shadow:0 8px 22px rgba(47,103,255,.16)}.radio{width:23px;height:23px;border-radius:50%;border:2px solid #c4cedd;display:flex;align-items:center;justify-content:center;background:#fff}.choice.active .radio,.choice:active .radio{border-color:#111827}.choice.active .radio:after,.choice:active .radio:after{content:'';width:10px;height:10px;background:#111827;border-radius:50%}.qrbox{display:flex;align-items:center;justify-content:center;background:#fff;border:1px solid #e6eaf2;border-radius:22px;padding:12px;min-height:300px;overflow:hidden}.qr{max-width:310px;width:100%;display:block;margin:auto;border-radius:12px}.qr.scb{max-width:360px;border-radius:10px}.copybox{background:#f8fafc;border:1px solid #e6eaf2;border-radius:16px;padding:12px;margin:10px 0}.upload{border:2px dashed #b9c8dc;border-radius:22px;padding:22px;text-align:center;background:#f8fbff;display:block;min-height:170px;cursor:pointer}.upload:hover{background:#eef6ff}.preview{max-width:100%;border-radius:16px;margin-top:12px;display:none}.success{font-size:72px;line-height:1}.top-actions{display:flex;gap:10px;flex-wrap:wrap}.top-actions a{flex:1}.note{font-size:13px;color:#667085;line-height:1.6}.bankhint{display:grid;grid-template-columns:1fr 1fr;gap:10px}.bankhint button,.bankhint a,.bankhint div{border-radius:18px;padding:13px;border:1px solid #e6eaf2;background:#fff;text-decoration:none;color:#101828;text-align:left}.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#101828;color:#fff;border-radius:999px;padding:12px 18px;font-weight:800;box-shadow:0 15px 45px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:.25s;z-index:9999}.toast.show{opacity:1}.admin-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:12px}input{width:100%;border:1px solid #d8e1ef;border-radius:14px;padding:12px;margin:6px 0;background:#fff;font:inherit}button{font:inherit;cursor:pointer}@media(max-width:720px){.wrap{padding:12px}.hero{border-radius:24px;padding:18px}.title{font-size:25px}.stats{grid-template-columns:1fr}.member-row{grid-template-columns:38px 1fr 96px;gap:8px}.member-row .status{grid-column:2/4}.card{border-radius:22px;padding:14px}.choice{grid-template-columns:28px 1fr 100px}.admin-grid{grid-template-columns:1fr}.bankhint{grid-template-columns:1fr}.qr.scb{max-width:100%}}
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
        "paid_count": t["paid_count"], "waiting_count": len([p for p in pays if p.status == "pending"]), "unpaid_count": len([p for p in pays if p.status != "paid" and p.status != "pending"]), "count": t["count"],
        "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "payment_type": getattr(p, "payment_type", "") or "", "paid_at": p.paid_at.strftime('%d/%m/%Y %H:%M') if p.paid_at else ""} for p in pays]
    }

@app.websocket("/ws")
async def websocket_status(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            db = SessionLocal()
            try:
                r = services.active_round(db)
                pays = services.get_payments(db, r) if r else []
                t = services.status_totals(db)
                await ws.send_json({
                    "round": r.title if r else "-",
                    "due": float(t["due"]),
                    "paid": float(t["paid"]),
                    "unpaid": float(t["unpaid"]),
                    "paid_count": t["paid_count"],
                    "waiting_count": len([p for p in pays if p.status == "pending"]),
                    "unpaid_count": len([p for p in pays if p.status != "paid" and p.status != "pending"]),
                    "count": t["count"],
                    "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "payment_type": getattr(p, "payment_type", "") or "", "paid_at": p.paid_at.strftime('%d/%m/%Y %H:%M') if p.paid_at else ""} for p in pays],
                })
            finally:
                db.close()
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return

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
      <div class='stat'>รอตรวจ<div class='num' style='color:#b76b00' id='waitingCount'>-</div><div class='muted'>รายการ</div></div>
      <div class='stat'>ค้างชำระ<div class='num red' id='unpaid'>-</div><div class='muted' id='unpaidCount'>-</div></div>
    </div>
    <div class='leave-card'>
      <div class='leave-head blue'><h1>รายการสมาชิก</h1><div id='round2'>เงินกองสำนักงาน</div></div>
      <div class='leave-body blue'>
        <div style='display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px'><b>สถานะการชำระ</b><span class='muted'>อัปเดตทุก 3 วิ</span></div>
        <div id='rows'></div>
        <a class='btn' href='/pay'>💳 ชำระเงิน</a>
      </div>
    </div>
    <script>
    function baht(n){return Number(n||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})}
    function initials(name){return (name||'?').replace('ท่าน','').trim().slice(0,1)||'?'}
    async function load(){
      let r=await fetch('/api/status').then(x=>x.json());
      round.textContent='เดือน '+r.round; round2.textContent='เงินกองสำนักงาน • '+r.round;
      due.textContent=baht(r.due); paid.textContent=baht(r.paid); unpaid.textContent=baht(r.unpaid);
      paidCount.textContent=(r.paid_count||0)+' คน'; waitingCount.textContent=(r.waiting_count||0); unpaidCount.textContent=(r.unpaid_count||0)+' คน';
      rows.innerHTML=r.members.map(m=>`<div class='member-row'><div class='avatar'>${initials(m.name)}</div><b>${m.name}</b><b style='text-align:right'>${baht(m.amount)}</b><span class='status pill ${m.status=='paid'?'paid':((m.status=='pending'||m.status=='partial')?'partial':'unpaid')}'>${m.status=='paid'?(m.payment_type=='cash'?'🟢 Paid (Cash)':'🟢 Paid (Transfer)'):(m.status=='pending'?'🟡 Waiting Approval':'🔴 Not Paid')}</span></div>`).join('')
    }
    load();
    try{
      const proto=location.protocol==='https:'?'wss':'ws';
      const ws=new WebSocket(proto+'://'+location.host+'/ws');
      ws.onmessage=(ev)=>render(JSON.parse(ev.data));
      function render(r){
        round.textContent='เดือน '+r.round; round2.textContent='เงินกองสำนักงาน • '+r.round;
        due.textContent=baht(r.due); paid.textContent=baht(r.paid); unpaid.textContent=baht(r.unpaid);
        paidCount.textContent=(r.paid_count||0)+' คน'; waitingCount.textContent=(r.waiting_count||0); unpaidCount.textContent=(r.unpaid_count||0)+' คน';
        rows.innerHTML=r.members.map(m=>`<div class='member-row'><div class='avatar'>${initials(m.name)}</div><b>${m.name}</b><b style='text-align:right'>${baht(m.amount)}</b><span class='status pill ${m.status=='paid'?'paid':((m.status=='pending'||m.status=='partial')?'partial':'unpaid')}'>${m.status=='paid'?(m.payment_type=='cash'?'🟢 Paid (Cash)':'🟢 Paid (Transfer)'):(m.status=='pending'?'🟡 Waiting Approval':'🔴 Not Paid')}</span></div>`).join('')
      }
      ws.onerror=()=>setInterval(load,3000);
    }catch(e){setInterval(load,3000)}
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
        <b>{p.member.name}<br><small class='muted'>{'ชำระแล้ว' if p.status=='paid' else 'แตะเพื่อเลือกชื่อนี้'}</small></b>
        <b style='text-align:right'>{money(p.due_amount)} บาท</b>
      </a>
    """ for p in pays])
    return page("ชำระเงิน", f"""
      <div class='leave-card'>
        <div class='leave-head'><h1>ชำระเงิน</h1><div>เลือกชื่อที่ต้องการชำระ • {r.title if r else '-'}</div></div>
        <div class='leave-body'>
          <div class='list-title'>เลือกผู้ชำระเงิน</div>
          {items}
          <a class='btn2' href='/dashboard'>กลับ Dashboard</a>
        </div>
      </div>
      <script>
        document.querySelectorAll('.choice').forEach(el=>el.addEventListener('click',()=>{{document.querySelectorAll('.choice').forEach(x=>x.classList.remove('active'));el.classList.add('active')}}))
      </script>
    """)

@app.get("/pay/{member_id}", response_class=HTMLResponse)
def pay_member(member_id: int, db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    p = services.ensure_payment(db, r, m)
    pp = settings.PROMPTPAY_ID or '0858131344'
    static_qr = bank_qr_url(p.due_amount)
    if static_qr:
        qr_html = f"<img class='qr scb' src='{static_qr}' alt='QR พร้อมเพย์ {money(p.due_amount)} บาท'>"
        qr_note = "QR นี้เป็นรูปจากธนาคารและกำหนดยอดตามชื่อที่เลือกแล้ว"
    elif settings.PROMPTPAY_ID:
        qr_html = f"<img class='qr' src='/qr/{m.id}' alt='QR พร้อมเพย์'>"
        qr_note = "QR นี้สร้างจาก PROMPTPAY_ID ในระบบ"
    else:
        qr_html = "<div class='muted' style='text-align:center'>ยังไม่ได้ตั้ง PROMPTPAY_ID และยังไม่มีรูป QR ยอดนี้</div>"
        qr_note = "เพิ่ม PROMPTPAY_ID หรือรูป QR ตามยอดก่อนใช้งาน"
    body = f"""
    <div class='hero'><div class='title'>ชำระเงิน</div><div class='sub'>{r.title}</div></div>
    <div class='card'>
      <h2 style='margin:0 0 8px'>{m.name}</h2>
      <div class='sub'>ยอดที่ต้องชำระ</div>
      <div class='num green' style='font-size:34px'>{money(p.due_amount)} บาท</div>
      <div class='copybox'>พร้อมเพย์<br><b id='pp'>{pp}</b></div>
      <div class='bankhint'>
        <button type='button' onclick='copyPP()'>📋 <b>Copy PromptPay</b><br><span class='note'>คัดลอกเลขพร้อมเพย์</span></button>
        <button type='button' onclick='saveQR()'>💾 <b>Save QR Code</b><br><span class='note'>เปิดรูป QR เพื่อบันทึก</span></button>
        <button type='button' onclick='openScheme("krungthai-next://")'>🔵 <b>Open Krungthai NEXT</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
        <button type='button' onclick='openScheme("scbeasy://")'>🟣 <b>Open SCB EASY</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
        <button type='button' onclick='openScheme("kplus://")'>🟢 <b>Open K PLUS</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
      </div>
      <div id='toast' class='toast'>คัดลอกพร้อมเพย์แล้ว</div>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>QR พร้อมเพย์</h3>
      <div class='qrbox'>{qr_html}</div>
      <p class='note'>{qr_note}</p>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>อัปโหลดสลิป</h3>
      <form action='/upload/{m.id}' method='post' enctype='multipart/form-data'>
        <label class='upload'>
          <div style='font-size:44px'>📎</div>
          <b>แตะเพื่อเลือกรูปสลิป</b><br><span class='note'>รองรับ JPG, PNG</span>
          <input id='slipInput' style='display:none' type='file' name='slip' accept='image/*' required onchange='previewSlip(event)'>
          <img id='preview' class='preview'>
        </label>
        <button class='btn' type='submit'>อัปโหลดสลิป</button>
      </form>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>ชำระเงินสด</h3>
      <div class='copybox'>
        <b>ใบรับเงินสด</b><br>
        ชื่อ: {m.name}<br>
        ยอด: {money(p.due_amount)} บาท<br>
        วันที่/เวลา: <span id='nowText'></span>
      </div>
      <form action='/cash/{m.id}' method='post' onsubmit='return submitCash()'>
        <input type='hidden' name='signature_data' id='signatureData'>
        <canvas id='sig' width='900' height='360' style='width:100%;height:180px;background:#fff;border:1px solid #d8e1ef;border-radius:18px;touch-action:none'></canvas>
        <div class='top-actions'>
          <button class='btn2' type='button' onclick='clearSig()'>ล้างลายเซ็น</button>
          <button class='btn' type='submit'>Cash Payment</button>
        </div>
      </form>
    </div>
    <a class='btn2' href='/pay'>กลับ</a>
    <script>
      function showToast(msg){{let t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1600)}}
      function copyPP(){{navigator.clipboard.writeText(document.getElementById('pp').innerText).then(()=>showToast('คัดลอกพร้อมเพย์แล้ว'))}}
      function openScheme(scheme){{ try{{ location.href=scheme; }}catch(e){{}} }}
      function saveQR(){{ let img=document.querySelector('.qr'); if(img) window.open(img.src,'_blank'); else showToast('ยังไม่มีรูป QR') }}
      document.getElementById('nowText').textContent=new Date().toLocaleString('th-TH');
      const canvas=document.getElementById('sig'), ctx=canvas.getContext('2d'); let drawing=false, signed=false;
      ctx.lineWidth=5; ctx.lineCap='round'; ctx.strokeStyle='#101828';
      function pos(e){{ const r=canvas.getBoundingClientRect(); const t=e.touches?e.touches[0]:e; return {{x:(t.clientX-r.left)*canvas.width/r.width,y:(t.clientY-r.top)*canvas.height/r.height}}; }}
      function start(e){{ drawing=true; signed=true; const p=pos(e); ctx.beginPath(); ctx.moveTo(p.x,p.y); e.preventDefault(); }}
      function move(e){{ if(!drawing)return; const p=pos(e); ctx.lineTo(p.x,p.y); ctx.stroke(); e.preventDefault(); }}
      function end(){{ drawing=false; }}
      canvas.addEventListener('mousedown',start); canvas.addEventListener('mousemove',move); window.addEventListener('mouseup',end);
      canvas.addEventListener('touchstart',start,{{passive:false}}); canvas.addEventListener('touchmove',move,{{passive:false}}); canvas.addEventListener('touchend',end);
      function clearSig(){{ ctx.clearRect(0,0,canvas.width,canvas.height); signed=false; }}
      function submitCash(){{ if(!signed){{showToast('กรุณาเซ็นชื่อก่อน'); return false;}} document.getElementById('signatureData').value=canvas.toDataURL('image/png'); return true; }}
      function previewSlip(e){{let f=e.target.files[0]; if(!f)return; if(f.size>8*1024*1024){{showToast('ไฟล์ใหญ่เกิน 8MB'); e.target.value=''; return;}} let img=document.getElementById('preview'); img.src=URL.createObjectURL(f); img.style.display='block'}}
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
    month_dir = Path(settings.SLIP_STORAGE_DIR) / current_month_key()
    out = save_upload_image(slip, month_dir, f"slip_{safe_name(m.name)}")

    # v2: NEVER auto approve. All evidence waits for admin approval.
    p.slip_path = str(out)
    p.receipt_path = None
    p.payment_type = "transfer"
    p.rejection_reason = None
    p.note = f"transfer_slip:{out}\nรอแอดมินตรวจสอบ"
    p.status = "pending"
    p.paid_amount = Decimal("0")
    p.paid_at = None
    db.commit()
    db.refresh(p)
    notify_admin_pending_slip(db, p)
    update_line_summary(db)
    return page("รอตรวจสอบสลิป", f"<div class='card' style='text-align:center'><div style='font-size:70px'>🟡</div><h2>ส่งสลิปแล้ว รอตรวจสอบ</h2><p>{m.name}</p><div class='num' style='color:#b76b00'>{money(p.due_amount)} บาท</div><p class='sub'>ระบบบันทึกรูปสลิปไว้แล้ว และแจ้งแอดมินเพื่อตรวจสอบ</p><p class='note'>สถานะจะเปลี่ยนเป็นชำระแล้ว หลังแอดมินกดอนุมัติ</p><a class='btn' href='/dashboard'>กลับหน้ารายการ</a></div>")


@app.post("/cash/{member_id}", response_class=HTMLResponse)
async def cash_payment(member_id: int, signature_data: str = Form(...), db: Session = Depends(get_db)):
    r = services.active_round(db); m = services.member_by_id(db, member_id)
    if not r or not m: raise HTTPException(404)
    if not signature_data.startswith("data:image/png;base64,"):
        raise HTTPException(400, detail="signature required")
    p = services.ensure_payment(db, r, m)
    month_dir = Path(settings.SIGNATURE_STORAGE_DIR) / current_month_key()
    month_dir.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(signature_data.split(",", 1)[1])
    if len(raw) > 3 * 1024 * 1024:
        raise HTTPException(400, detail="signature too large")
    out = month_dir / f"cash_{safe_name(m.name)}_{uuid.uuid4().hex}.png"
    out.write_bytes(raw)
    p.receipt_path = str(out)
    p.slip_path = None
    p.payment_type = "cash"
    p.rejection_reason = None
    p.status = "pending"
    p.paid_amount = Decimal("0")
    p.paid_at = None
    p.note = f"cash_receipt:{out}\nรอแอดมินตรวจสอบ"
    db.commit()
    db.refresh(p)
    notify_admin_pending_slip(db, p)
    update_line_summary(db)
    return page("รอตรวจสอบเงินสด", f"<div class='card' style='text-align:center'><div style='font-size:70px'>🟡</div><h2>ส่งใบรับเงินสดแล้ว รอตรวจสอบ</h2><p>{m.name}</p><div class='num' style='color:#b76b00'>{money(p.due_amount)} บาท</div><p class='sub'>แอดมินต้องกดอนุมัติก่อน สถานะจึงจะเป็น Paid (Cash)</p><a class='btn' href='/dashboard'>กลับหน้ารายการ</a></div>")

@app.get("/report.xlsx")
def report_xlsx(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: raise HTTPException(404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    expenses = db.query(Expense).filter(Expense.round_id == r.id).all()
    return Response(make_excel(r, payments, expenses), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition":"attachment; filename=fundbot_report.xlsx"})

@app.get("/report.docx")
def report_docx(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: raise HTTPException(404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    return Response(make_word(r, payments), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition":"attachment; filename=fundbot_report.docx"})

@app.get("/report.pdf")
def report_pdf(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: raise HTTPException(404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    return Response(make_pdf(r, payments), media_type="application/pdf", headers={"Content-Disposition":"attachment; filename=fundbot_report.pdf"})

@app.get("/admin", response_class=HTMLResponse)
def admin(token: str = "", db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(403)
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    pending = [p for p in pays if p.status == "pending"]
    rows = "".join([f"<div class='member-row'><div class='avatar'>{p.member.name.replace('ท่าน','').strip()[:1] or '?'}</div><b>{p.member.name}</b><b style='text-align:right'>{money(p.due_amount)}</b><span class='status pill {'paid' if p.status=='paid' else ('partial' if p.status=='pending' else 'unpaid')}'>{'✅ ชำระแล้ว' if p.status=='paid' else ('🟡 รอตรวจ' if p.status=='pending' else 'ยังไม่ชำระ')}</span></div>" for p in pays])
    pending_cards = ""
    for p in pending:
        slip_url = evidence_url(p)
        img = f"<img src='{slip_url}' style='width:100%;border-radius:18px;border:1px solid #e6eaf2;margin:10px 0'>" if slip_url else "<div class='note'>ไม่มีหลักฐาน</div>"
        pending_cards += f"""
        <div class='card' id='pending'>
          <h3 style='margin:0'>📥 {p.member.name}</h3>
          <div class='num green'>{money(p.due_amount)} บาท</div><p class='note'>ประเภท: {'เงินสด' if getattr(p, 'payment_type', '') == 'cash' else 'โอน'} • วันที่ส่ง: {p.paid_at.strftime('%d/%m/%Y %H:%M') if p.paid_at else '-'}</p>
          {img}
          <div class='top-actions'>
            <a class='btn' href='/admin/approve/{p.id}?token={token}'>✅ อนุมัติ</a>
            <form action='/admin/reject/{p.id}' method='post' style='flex:1'>
              <input type='hidden' name='token' value='{token}'>
              <input name='reason' required placeholder='เหตุผลที่ไม่ผ่าน'>
              <button class='btn2' type='submit'>❌ Reject</button>
            </form>
          </div>
        </div>
        """
    if not pending_cards:
        pending_cards = "<div class='card' id='pending'><h3>📥 สลิปรอตรวจ</h3><p class='muted'>ยังไม่มีสลิปรอตรวจ</p></div>"
    body = f"""
    <div class='hero'><div class='title'>หลังบ้าน FundBot</div><div class='sub'>รอบ: {r.title if r else '-'}</div></div>
    <div class='stats'>
      <div class='stat'><b>สลิปรอตรวจ</b><div class='num' style='color:#b76b00'>{len(pending)}</div><div class='muted'>รายการ</div></div>
      <div class='stat'><b>รายงาน</b><a class='btn2' href='/report.xlsx'>Excel</a><a class='btn2' href='/report.docx'>Word</a><a class='btn2' href='/report.pdf'>PDF</a></div>
      <div class='stat'><b>Dashboard</b><a class='btn2' href='/dashboard'>เปิดหน้า Dashboard</a></div>
    </div>
    {pending_cards}
    <div class='leave-card'>
      <div class='leave-head blue'><h1>รายการสมาชิก</h1><div>แก้ไข/ตรวจสถานะ</div></div>
      <div class='leave-body blue'>{rows}</div>
    </div>
    <div class='admin-grid'>
      <div class='card'><h3>เปิดรอบใหม่</h3><form action='/admin/open' method='post'><input type='hidden' name='token' value='{token}'><input name='title' placeholder='กรกฎาคม 2569'><button class='btn'>เปิดรอบ</button></form></div>
      <div class='card'><h3>เพิ่ม/แก้สมาชิก</h3><form action='/admin/member' method='post'><input type='hidden' name='token' value='{token}'><input name='name' placeholder='ชื่อ'><input name='amount' placeholder='ยอด'><button class='btn'>บันทึก</button></form></div>
    </div>
    """
    return page("Admin", body)

@app.get("/admin/approve/{payment_id}")
def admin_approve(payment_id: int, token: str = "", db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(403)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    ptype = getattr(p, "payment_type", "") or ("cash" if getattr(p, "receipt_path", None) else "transfer")
    services.mark_member_paid(db, p.member_id, Decimal(p.due_amount or 0), note=(p.note or "") + "\nadmin_approved", slip_path=p.slip_path)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    p.payment_type = ptype
    p.rejection_reason = None
    db.commit()
    update_line_summary(db)
    target = settings.ADMIN_NOTIFY_TARGET_ID or get_state(db, "line_target_id")
    if target:
        push(target, [text(f"✅ อนุมัติแล้ว: {p.member.name} {money(p.due_amount)} บาท")])
    return RedirectResponse(f"/admin?token={token}#pending", status_code=303)

@app.get("/admin/reject/{payment_id}", response_class=HTMLResponse)
def admin_reject_form(payment_id: int, token: str = "", db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(403)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    return page("Reject", f"<div class='card'><h2>Reject: {p.member.name}</h2><form method='post' action='/admin/reject/{p.id}'><input type='hidden' name='token' value='{token}'><input name='reason' required placeholder='เหตุผลที่ไม่ผ่าน'><button class='btn'>บันทึก Reject</button></form></div>")

@app.post("/admin/reject/{payment_id}")
def admin_reject(payment_id: int, token: str = Form(...), reason: str = Form(...), db: Session = Depends(get_db)):
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(403)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    p.status = "unpaid"
    p.paid_amount = Decimal("0")
    p.paid_at = None
    p.rejection_reason = reason.strip()
    p.note = (p.note or "") + f"\nadmin_rejected:{reason.strip()}"
    db.commit()
    update_line_summary(db)
    target = settings.ADMIN_NOTIFY_TARGET_ID or get_state(db, "line_target_id")
    if target:
        push(target, [text(f"❌ ไม่ผ่าน: {p.member.name}\nเหตุผล: {reason.strip()}\nกรุณาอัปโหลดใหม่")])
    return RedirectResponse(f"/admin?token={token}#pending", status_code=303)

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
