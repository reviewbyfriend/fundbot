import base64
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import uuid
import asyncio
import json
import time
import secrets
import html
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, get_db, init_db
from . import services
from .line_client import flex, reply, push, text, download_message_content
from .promptpay import qr_png_base64
from .report import make_excel, make_word, make_pdf
from PIL import Image, ImageOps
from .models import Expense, Payment, BotState, AdminUser, AdminAuditLog
from .timezone import now_bangkok, format_th

app = FastAPI(title="FundBot v2 Stable")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
Path(settings.SLIP_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.SIGNATURE_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.EXPENSE_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/slips", StaticFiles(directory=settings.SLIP_STORAGE_DIR), name="slips")
app.mount("/signatures", StaticFiles(directory=settings.SIGNATURE_STORAGE_DIR), name="signatures")
app.mount("/expenses", StaticFiles(directory=settings.EXPENSE_STORAGE_DIR), name="expenses")

@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        if db.query(services.Member).count() == 0:
            services.seed_members(db)
        if not services.active_round(db):
            now = now_bangkok()
            services.open_round(db, f"{now.strftime('%Y-%m')}")
        ensure_owner_admin(db)
    finally:
        db.close()

@app.get("/")
def home():
    return {"ok": True, "name": settings.BOT_NAME, "dashboard": "/dashboard", "admin_login": "/admin/login", "webhook": "/webhook"}

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


def admin_notify_target(db: Session) -> str | None:
    # ส่งรายการรออนุมัติไปหาแอดมินส่วนตัวก่อน ถ้าไม่ได้ตั้งไว้จึง fallback ไปกลุ่มล่าสุด
    return settings.ADMIN_NOTIFY_TARGET_ID or get_state(db, "admin_notify_target_id") or get_state(db, "line_target_id")


def admin_code_hash(code: str) -> str:
    raw = (settings.ADMIN_TOKEN + "|" + (code or "").strip()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_admin_code(code: str, hashed: str) -> bool:
    return hmac.compare_digest(admin_code_hash(code), hashed or "")


def session_sign(payload: str) -> str:
    return hmac.new(settings.ADMIN_TOKEN.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_admin_session(admin: AdminUser) -> str:
    exp = int(time.time()) + 60 * 60 * 24 * 30
    payload = json.dumps({"id": admin.id, "name": admin.name, "role": admin.role, "exp": exp}, ensure_ascii=False, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return b64 + "." + session_sign(b64)


def read_admin_session(request: Request, db: Session):
    raw = request.cookies.get("fundbot_admin") if request else None
    if not raw or "." not in raw:
        return None
    b64, sig = raw.rsplit(".", 1)
    if not hmac.compare_digest(session_sign(b64), sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    admin = db.query(AdminUser).filter(AdminUser.id == int(payload.get("id", 0)), AdminUser.active == True).first()
    if not admin:
        return None
    return {"id": admin.id, "name": admin.name, "role": admin.role, "legacy": False}


def legacy_admin(token: str):
    if token and token == settings.ADMIN_TOKEN:
        return {"id": None, "name": "Owner Token", "role": "owner", "legacy": True}
    return None


def current_admin(request: Request, db: Session, token: str = ""):
    return legacy_admin(token) or read_admin_session(request, db)


def require_admin(request: Request, db: Session, token: str = "", roles=("owner", "approver", "viewer")):
    admin = current_admin(request, db, token)
    if not admin or admin.get("role") not in roles:
        raise HTTPException(403)
    return admin


def require_approver(request: Request, db: Session, token: str = ""):
    return require_admin(request, db, token, roles=("owner", "approver"))


def require_owner(request: Request, db: Session, token: str = ""):
    return require_admin(request, db, token, roles=("owner",))


def audit_admin(db: Session, admin: dict, action: str, payment_id: int | None = None, detail: str | None = None):
    db.add(AdminAuditLog(admin_id=admin.get("id"), admin_name=admin.get("name") or "Admin", action=action, payment_id=payment_id, detail=detail))
    db.commit()


def ensure_owner_admin(db: Session):
    # สร้าง owner เริ่มต้นจาก ADMIN_TOKEN เฉพาะกรณียังไม่มีแอดมินเลย
    if db.query(AdminUser).count() == 0:
        code = settings.ADMIN_TOKEN or "admin123"
        owner = AdminUser(name="เฟรน", role="owner", code_hash=admin_code_hash(code), active=True)
        db.add(owner)
        db.commit()


def admin_return_url(token: str = "") -> str:
    return f"/admin?token={token}#pending" if token else "/admin#pending"


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
    return now_bangkok().strftime("%Y-%m")

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


def evidence_file_path(payment: Payment) -> Path | None:
    raw = getattr(payment, "receipt_path", None) if getattr(payment, "payment_type", "") == "cash" else getattr(payment, "slip_path", None)
    if not raw:
        return None
    try:
        resolved = Path(raw).resolve()
        allowed = [Path(settings.SLIP_STORAGE_DIR).resolve(), Path(settings.SIGNATURE_STORAGE_DIR).resolve()]
        if not any(str(resolved).startswith(str(root)) for root in allowed):
            return None
        return resolved
    except Exception:
        return None


def evidence_file_exists(payment: Payment) -> bool:
    p = evidence_file_path(payment)
    return bool(p and p.exists() and p.is_file())


def notify_admin_pending_slip(db: Session, payment: Payment):
    """แจ้งแอดมินว่ามีสลิปรอตรวจ พร้อมปุ่มอนุมัติ/ไม่ผ่าน"""
    target = admin_notify_target(db)
    if not target:
        return
    admin_token = settings.ADMIN_TOKEN
    slip_url = f"{base_url()}/admin/evidence/{payment.id}?token={admin_token}" if evidence_file_exists(payment) else ""
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
    total = sum([Decimal(p.due_amount or 0) for p in pays], Decimal("0"))
    paid_total = sum([Decimal(p.due_amount or 0) for p in pays if p.status == "paid"], Decimal("0"))
    waiting_count = len([p for p in pays if p.status == "pending"])
    unpaid_count = len([p for p in pays if p.status not in ["paid", "pending"]])
    rows = []
    for p in pays:
        paid = p.status == "paid"
        pending = p.status == "pending"
        paid_cash = paid and getattr(p, "payment_type", "") == "cash"
        badge_bg = "#E8F7EE" if paid else ("#FFF4D8" if pending else "#FDE8E8")
        badge_color = "#148F4B" if paid else ("#B76B00" if pending else "#D93025")
        badge_text = ("จ่ายแล้ว เงินสด" if paid_cash else "จ่ายแล้ว โอน") if paid else ("รอตรวจ" if pending else "ยังไม่จ่าย")
        name_color = "#148F4B" if paid else "#C4212A"
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "paddingTop": "8px",
            "paddingBottom": "8px",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": "@", "size": "md", "weight": "bold", "color": name_color, "flex": 0},
                {"type": "text", "text": p.member.name, "size": "md", "weight": "bold", "wrap": True, "color": name_color, "flex": 4},
                {"type": "text", "text": money(p.due_amount), "size": "md", "align": "end", "weight": "bold", "color": "#101828", "flex": 3},
            ]
        })
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "paddingBottom": "8px",
            "contents": [
                {"type": "filler", "flex": 1},
                {"type": "box", "layout": "vertical", "cornerRadius": "999px", "backgroundColor": badge_bg, "paddingTop": "5px", "paddingBottom": "5px", "paddingStart": "12px", "paddingEnd": "12px", "contents": [
                    {"type": "text", "text": ("🟢 " if paid else ("🟡 " if pending else "🔴 ")) + badge_text, "size": "xs", "weight": "bold", "color": badge_color, "align": "center"}
                ], "flex": 0}
            ]
        })
        rows.append({"type": "separator", "color": "#EEF2F6"})

    contents = {
        "type": "bubble",
        "size": "giga",
        "styles": {"body": {"backgroundColor": "#F7FEFC"}, "footer": {"backgroundColor": "#FFFFFF"}},
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "0px",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "paddingAll": "22px",
                    "backgroundColor": "#82DED8",
                    "contents": [
                        {"type": "box", "layout": "horizontal", "contents": [
                            {"type": "box", "layout": "vertical", "contents": [
                                {"type": "text", "text": "FundBot มาแล้ว ✨", "size": "xl", "weight": "bold", "color": "#FFFFFF"},
                                {"type": "text", "text": f"เงินกองสำนักงาน • {r.title if r else '-'}", "size": "sm", "color": "#EFFFFE", "margin": "xs", "wrap": True},
                            ], "flex": 5},
                            {"type": "text", "text": "🐦", "size": "4xl", "align": "end", "flex": 1}
                        ]},
                        {"type": "text", "text": f"฿ {money(total)}", "size": "xxl", "weight": "bold", "color": "#FFFFFF", "margin": "lg"},
                        {"type": "text", "text": f"เก็บแล้ว {money(paid_total)} บาท • รอตรวจ {waiting_count} • ยังไม่จ่าย {unpaid_count}", "size": "sm", "color": "#EFFFFE", "margin": "xs", "wrap": True},
                    ]
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "paddingAll": "18px",
                    "backgroundColor": "#FFFFFF",
                    "contents": [
                        {"type": "text", "text": "👑 รายการสมาชิก", "size": "md", "weight": "bold", "color": "#101828"},
                        {"type": "separator", "margin": "md", "color": "#E4E7EC"},
                    ] + rows
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "16px",
            "contents": [
                {"type": "button", "style": "primary", "color": "#16A34A", "height": "sm", "action": {"type": "uri", "label": "ชำระเงิน", "uri": f"{base_url()}/pay"}},
                {"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "ดูว่าใครจ่ายแล้ว", "uri": f"{base_url()}/dashboard"}},
            ]
        }
    }
    return flex("เงินกองสำนักงาน", contents)

def menu_text():
    return text("FundBot ใช้งานหลัก:\n• ส่งหน้าเก็บเงิน\n• ชำระเงิน\n• สรุป\n\nรายจ่าย/รายงานใน LINE:\n• รายจ่าย ค่าอาหาร 250\n• รายจ่าย ค่าถ่ายเอกสาร 120 หมวด เอกสาร\n• ส่งรูปใบเสร็จหลังจากบันทึกรายจ่าย\n• รายงาน / พิมพ์รายงาน")


def report_links_text():
    return text(
        "📄 ออกรายงานเงินกอง\n"
        f"Excel: {base_url()}/report.xlsx\n"
        f"Word: {base_url()}/report.docx\n"
        f"PDF: {base_url()}/report.pdf\n\n"
        "ถ้าจะปริ้น แนะนำเปิด Excel/Word แล้วสั่งพิมพ์จากเครื่องค่ะ"
    )


def parse_expense_line(raw: str):
    # รูปแบบ: รายจ่าย ค่าอาหาร 250 หรือ รายจ่าย ค่าอาหาร 250 หมวด อาหาร
    body = raw.replace("เพิ่มรายจ่าย", "", 1).replace("รายจ่าย", "", 1).strip()
    amount = parse_amount(body)
    if amount is None:
        return None, None, None, None
    m = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", body)
    title = body[:m.start()].strip(" -:：") if m else body
    tail = body[m.end():].strip() if m else ""
    category = None
    cat_match = re.search(r"หมวด\s+(.+)$", tail)
    if cat_match:
        category = cat_match.group(1).strip()
    if not title:
        title = "รายจ่ายไม่ระบุรายการ"
    return title, amount, category, tail


def expense_receipt_dir():
    ym = now_bangkok().strftime("%Y-%m")
    d = Path(settings.EXPENSE_STORAGE_DIR) / ym
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_expense_receipt_bytes(content: bytes) -> str:
    out = expense_receipt_dir() / f"{uuid.uuid4().hex}.jpg"
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(content)
    try:
        img = Image.open(tmp)
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1600, 1600))
        img.save(out, "JPEG", quality=82, optimize=True)
        tmp.unlink(missing_ok=True)
    except Exception:
        tmp.rename(out)
    return str(out)


def expense_receipt_exists(e: Expense) -> bool:
    path = getattr(e, "receipt_path", None)
    return bool(path and Path(path).exists())


def expense_receipt_url(e: Expense, token: str = "") -> str:
    q = f"?token={token}" if token else ""
    return f"/admin/expense/receipt/{e.id}{q}"


def add_expense(db: Session, title: str, amount: Decimal, category: str | None = None, note: str | None = None, created_by: str | None = None):
    r = services.active_round(db)
    if not r:
        r = services.open_round(db, now_bangkok().strftime("%Y-%m"))
    e = Expense(
        round_id=r.id,
        title=title,
        amount=amount,
        category=category,
        note=note,
        created_by=created_by,
        expense_date=now_bangkok().replace(tzinfo=None),
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def handle_line_image(reply_token: str, message_id: str):
    db = SessionLocal()
    try:
        pending_id = get_state(db, "line_pending_expense_id")
        if pending_id:
            e = db.query(Expense).filter(Expense.id == int(pending_id)).first()
            if e:
                content = download_message_content(message_id)
                if not content:
                    reply(reply_token, [text("รับรูปแล้ว แต่ดาวน์โหลดจาก LINE ไม่สำเร็จ ลองส่งรูปอีกครั้งนะคะ")])
                    return
                e.receipt_path = save_expense_receipt_bytes(content)
                e.note = ((e.note or "") + "\nแนบใบเสร็จจาก LINE").strip()
                db.commit()
                set_state(db, "line_pending_expense_id", "")
                reply(reply_token, [text(f"✅ แนบใบเสร็จแล้ว\n{e.title} {money(e.amount)} บาท\nถ้าจะพิมพ์รายงาน พิมพ์: รายงาน"), report_links_text()])
                return
        reply(reply_token, [text("รับรูปแล้วค่ะ ถ้าเป็นใบเสร็จ ให้พิมพ์รายจ่ายก่อน เช่น\nรายจ่าย ค่าอาหาร 250\nแล้วส่งรูปใบเสร็จตามมา")])
    finally:
        db.close()

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
            handle_line_image(token, msg.get("id", ""))
    return {"ok": True}

def handle_text(reply_token: str, raw: str):
    db = SessionLocal()
    try:
        s = raw.strip()
        low = s.lower()
        if low.startswith("ตั้งแอดมิน") or low.startswith("admin set"):
            # ใช้ในแชทส่วนตัวกับบอท: ตั้งแอดมิน <ADMIN_TOKEN>
            parts = s.split(maxsplit=1)
            given = parts[1].strip() if len(parts) > 1 else ""
            if given != settings.ADMIN_TOKEN:
                reply(reply_token, [text("รหัสแอดมินไม่ถูกต้องค่ะ\nใช้รูปแบบ: ตั้งแอดมิน ADMIN_TOKEN")])
                return
            # save current LINE source as admin target
            # target is saved in webhook before handle_text; for private chat line_target_id = userId
            set_state(db, "admin_notify_target_id", get_state(db, "line_target_id"))
            reply(reply_token, [text(f"✅ ตั้งแอดมินสำหรับรับอนุมัติส่วนตัวแล้ว\nหลังบ้าน: {base_url()}/admin/login")])
            return
        if low.startswith("หลังบ้าน") or low.startswith("admin"):
            parts = s.split(maxsplit=1)
            given = parts[1].strip() if len(parts) > 1 else ""
            if given == settings.ADMIN_TOKEN:
                reply(reply_token, [text(f"🔐 หน้าแอดมิน\n{base_url()}/admin?token={settings.ADMIN_TOKEN}")])
            else:
                reply(reply_token, [text("ใช้รูปแบบ: หลังบ้าน ADMIN_TOKEN")])
            return
        if low in ["เมนู", "menu", "help"]:
            reply(reply_token, [menu_text()])
            return
        if low.startswith("รายจ่าย") or low.startswith("เพิ่มรายจ่าย"):
            title, amount, category, note = parse_expense_line(s)
            if amount is None:
                reply(reply_token, [text("รูปแบบรายจ่าย:\nรายจ่าย ค่าอาหาร 250\nหรือ รายจ่าย ค่าถ่ายเอกสาร 120 หมวด เอกสาร")])
                return
            e = add_expense(db, title, amount, category=category, note="บันทึกจาก LINE", created_by="LINE")
            set_state(db, "line_pending_expense_id", str(e.id))
            reply(reply_token, [text(f"✅ บันทึกรายจ่ายแล้ว\n{e.title} {money(e.amount)} บาท\nส่งรูปใบเสร็จต่อจากข้อความนี้ได้เลย ระบบจะแนบเข้าเดือน {services.active_round(db).title}")])
            return
        if low in ["รายงาน", "พิมพ์รายงาน", "ออกรายงาน", "report"]:
            reply(reply_token, [report_links_text()])
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
*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;background:linear-gradient(180deg,#eaf3ff 0%,#f6f8fc 45%,#f7f9fd 100%);margin:0;color:#101828}.wrap{max-width:980px;margin:0 auto;padding:18px}.hero{background:linear-gradient(135deg,#071b46,#17479c 58%,#2f67ff);color:white;border-radius:30px;padding:22px;box-shadow:0 18px 50px rgba(7,27,70,.22);margin-bottom:14px;position:relative;overflow:hidden}.hero:after{content:'';position:absolute;right:-35px;top:-35px;width:140px;height:140px;border-radius:50%;background:rgba(255,255,255,.12)}.title{font-size:28px;font-weight:900;color:#0b1f48;letter-spacing:-.3px}.hero .title{color:white}.sub{color:#667085}.hero .sub{color:#d9e6ff}.card{background:rgba(255,255,255,.94);backdrop-filter:blur(10px);border:1px solid #e6eaf2;border-radius:26px;box-shadow:0 14px 40px rgba(16,24,40,.08);padding:18px;margin:12px 0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.stat{border-radius:22px;padding:16px;background:#fff;border:1px solid #e6eaf2;box-shadow:0 10px 25px rgba(16,24,40,.04)}.num{font-size:24px;font-weight:900;letter-spacing:-.3px}.green{color:#159947}.red{color:#d93025}.muted{color:var(--muted)}.member-row{display:grid;grid-template-columns:44px 1fr 112px 152px;gap:10px;align-items:center;padding:13px 8px;border-bottom:1px solid #eef2f6}.member-row:last-child{border-bottom:0}.avatar{width:38px;height:38px;border-radius:16px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#dce9ff,#f4f7ff);font-weight:900;color:#24437a}.pill{border-radius:999px;padding:9px 12px;text-align:center;font-weight:800;font-size:13px}.paid{background:#e9f8ef;color:#148f4b;border:1px solid #c5efd4}.unpaid{background:#fdecec;color:#d93025;border:1px solid #ffd0d0}.partial{background:#fff7e6;color:#b76b00;border:1px solid #ffe2a8}.btn{display:block;text-align:center;text-decoration:none;border:0;border-radius:18px;padding:15px 18px;margin:10px 0;background:linear-gradient(135deg,#16a34a,#0e8d40);color:white;font-weight:900;font-size:17px;box-shadow:0 10px 25px rgba(22,163,74,.24)}.btn:active{transform:scale(.99)}.btn2{display:block;text-align:center;text-decoration:none;border:1px solid #bdd7ff;border-radius:16px;padding:13px;margin:9px 0;background:#eef6ff;color:#0b53ce;font-weight:800}.btn-light{background:#fff;color:#0b53ce;border:1px solid #bdd7ff}.leave-card{border-radius:28px;overflow:hidden;background:#fff;box-shadow:0 14px 42px rgba(16,24,40,.1);border:1px solid #e6eaf2}.leave-head{padding:20px 18px;background:linear-gradient(135deg,#ff7a1a,#ff9f43);color:#fff}.leave-head.blue{background:linear-gradient(135deg,#1f6feb,#2f67ff)}.leave-head h1{font-size:26px;margin:0 0 4px}.leave-body{padding:16px;background:#fff7ec}.leave-body.blue{background:#eef5ff}.list-title{font-size:22px;font-weight:900;color:#101828;margin-bottom:12px}.choice{display:grid;grid-template-columns:28px 1fr 118px;align-items:center;gap:12px;text-decoration:none;color:#101828;padding:16px 12px;border:1px solid transparent;border-radius:18px;margin:8px 0;background:white;box-shadow:0 5px 14px rgba(16,24,40,.04)}.choice:active,.choice.active{background:#eaf3ff;border-color:#93c5fd;box-shadow:0 8px 22px rgba(47,103,255,.16)}.radio{width:23px;height:23px;border-radius:50%;border:2px solid #c4cedd;display:flex;align-items:center;justify-content:center;background:#fff}.choice.active .radio,.choice:active .radio{border-color:#111827}.choice.active .radio:after,.choice:active .radio:after{content:'';width:10px;height:10px;background:#111827;border-radius:50%}.qrbox{display:flex;align-items:center;justify-content:center;background:#fff;border:1px solid #e6eaf2;border-radius:22px;padding:12px;min-height:300px;overflow:hidden}.qr{max-width:310px;width:100%;display:block;margin:auto;border-radius:12px}.qr.scb{max-width:360px;border-radius:10px}.copybox{background:#f8fafc;border:1px solid #e6eaf2;border-radius:16px;padding:12px;margin:10px 0}.upload{border:2px dashed #b9c8dc;border-radius:22px;padding:22px;text-align:center;background:#f8fbff;display:block;min-height:170px;cursor:pointer}.upload:hover{background:#eef6ff}.preview{max-width:100%;border-radius:16px;margin-top:12px;display:none}.success{font-size:72px;line-height:1}.top-actions{display:flex;gap:10px;flex-wrap:wrap}.top-actions a{flex:1}.note{font-size:13px;color:#667085;line-height:1.6}.bankhint{display:grid;grid-template-columns:1fr;gap:10px}.bankhint button,.bankhint a,.bankhint div{border-radius:18px;padding:15px;border:1px solid #e6eaf2;background:#fff;text-decoration:none;color:#101828;text-align:left;box-shadow:0 6px 16px rgba(16,24,40,.04)}.bankhint .bankbtn{color:#fff;border:0;font-weight:900;box-shadow:0 12px 28px rgba(16,24,40,.16)}.bankhint .bankbtn .note{color:rgba(255,255,255,.86)}.bankhint .ktb{background:linear-gradient(135deg,#0b7cff,#0051c8)}.bankhint .scb{background:linear-gradient(135deg,#7b2cff,#4b159f)}.bankhint .kplus{background:linear-gradient(135deg,#18b83e,#079226)}.qr-save{display:block;width:100%;margin:12px 0 0;border:0;border-radius:18px;padding:15px 18px;background:linear-gradient(135deg,#0b7cff,#17479c);color:#fff;font-weight:900;text-align:center;box-shadow:0 12px 28px rgba(23,71,156,.18)}.qr-save .note{color:rgba(255,255,255,.86)}.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#101828;color:#fff;border-radius:999px;padding:12px 18px;font-weight:800;box-shadow:0 15px 45px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:.25s;z-index:9999}.toast.show{opacity:1}.admin-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:12px}input{width:100%;border:1px solid #d8e1ef;border-radius:14px;padding:12px;margin:6px 0;background:#fff;font:inherit}button{font:inherit;cursor:pointer}@media(max-width:720px){.wrap{padding:12px}.hero{border-radius:24px;padding:18px}.title{font-size:25px}.stats{grid-template-columns:1fr}.member-row{grid-template-columns:38px 1fr 96px;gap:8px}.member-row .status{grid-column:2/4}.card{border-radius:22px;padding:14px}.choice{grid-template-columns:28px 1fr 100px}.admin-grid{grid-template-columns:1fr}.bankhint{grid-template-columns:1fr}.qr.scb{max-width:100%}}
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
        "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "payment_type": getattr(p, "payment_type", "") or "", "paid_at": format_th(p.paid_at, default='')} for p in pays]
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
                    "members": [{"id": p.member.id, "name": p.member.name, "amount": float(p.due_amount or 0), "status": p.status, "payment_type": getattr(p, "payment_type", "") or "", "paid_at": format_th(p.paid_at, default='')} for p in pays],
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
        <button class='bankbtn ktb' type='button' onclick='openSchemes(["ktbnext://","krungthai-next://","next://"])'>🔵 <b>Open Krungthai NEXT</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
        <button class='bankbtn scb' type='button' onclick='openSchemes(["scbeasy://","scbeasyapp://"])'>🟣 <b>Open SCB EASY</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
        <button class='bankbtn kplus' type='button' onclick='openKPlus()'>🟢 <b>Open K PLUS</b><br><span class='note'>ถ้าเปิดไม่ได้ ระบบจะนิ่งไว้</span></button>
      </div>
      <div id='toast' class='toast'>คัดลอกพร้อมเพย์แล้ว</div>
    </div>
    <div class='card'>
      <h3 style='margin-top:0'>QR พร้อมเพย์</h3>
      <div class='qrbox'>{qr_html}</div>
      <button class='qr-save' type='button' onclick='saveQR()'>💾 Save QR Code<br><span class='note'>บันทึก/แชร์รูป QR ลงเครื่อง</span></button>
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
      function openSchemes(schemes){{
        try{{
          let i=0;
          function go(){{ if(i>=schemes.length) return; location.href=schemes[i++]; if(i<schemes.length) setTimeout(go,650); }}
          go();
        }}catch(e){{}}
      }}
      function openKPlus(){{
        openSchemes(['kplus://','KPLUS://','kplusapp://','kasikornbank://','com.kasikorn.retail.mbanking.wap://','kbank://']);
        setTimeout(()=>showToast('ถ้า K PLUS ไม่เปิด ให้คัดลอกพร้อมเพย์หรือเซฟ QR ไปสแกนในแอป'),2200);
      }}
      async function saveQR(){{
        let img=document.querySelector('.qr');
        if(!img){{ showToast('ยังไม่มีรูป QR'); return; }}
        const url=img.currentSrc || img.src;
        try{{
          const res=await fetch(url,{{cache:'no-store'}});
          const blob=await res.blob();
          const file=new File([blob],'fundbot-qr.jpg',{{type:blob.type||'image/jpeg'}});
          if(navigator.canShare && navigator.canShare({{files:[file]}})){{
            await navigator.share({{files:[file],title:'FundBot QR Code'}});
            return;
          }}
          const a=document.createElement('a');
          a.href=URL.createObjectURL(blob);
          a.download='fundbot-qr.jpg';
          document.body.appendChild(a);
          a.click();
          setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove();}},1500);
          showToast('กำลังบันทึก QR');
        }}catch(e){{
          const a=document.createElement('a');
          a.href=url;
          a.download='fundbot-qr.jpg';
          document.body.appendChild(a);
          a.click();
          setTimeout(()=>a.remove(),500);
        }}
      }}
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
    expenses = db.query(Expense).filter(Expense.round_id == r.id).order_by(Expense.expense_date, Expense.id).all()
    return Response(make_word(r, payments, expenses), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition":"attachment; filename=fundbot_report.docx"})

@app.get("/report.pdf")
def report_pdf(db: Session = Depends(get_db)):
    r = services.active_round(db)
    if not r: raise HTTPException(404)
    payments = db.query(Payment).filter(Payment.round_id == r.id).all()
    expenses = db.query(Expense).filter(Expense.round_id == r.id).order_by(Expense.expense_date, Expense.id).all()
    return Response(make_pdf(r, payments, expenses), media_type="application/pdf", headers={"Content-Disposition":"attachment; filename=fundbot_report.pdf"})


@app.get("/admin/expenses", response_class=HTMLResponse)
def admin_expenses(request: Request, token: str = "", db: Session = Depends(get_db)):
    admin_ctx = require_admin(request, db, token)
    r = services.active_round(db)
    expenses = db.query(Expense).filter(Expense.round_id == r.id).order_by(Expense.expense_date.desc(), Expense.id.desc()).all() if r else []
    query = f"?token={token}" if token else ""
    token_hidden = f"<input type='hidden' name='token' value='{html.escape(token)}'>" if token else ""
    total_exp = sum([Decimal(e.amount or 0) for e in expenses], Decimal("0"))
    rows = "".join([f"""
    <div class='member-row'>
      <div class='avatar'>🧾</div>
      <b>{html.escape(e.title)}</b>
      <b style='text-align:right'>{money(e.amount)}</b>
      <span class='status pill {'paid' if expense_receipt_exists(e) else 'partial'}'>{'มีใบเสร็จ' if expense_receipt_exists(e) else 'ยังไม่มีรูป'}</span>
    </div>
    <div class='copybox'>วันที่ {format_th(getattr(e, 'expense_date', None), '%d/%m/%Y', default='-')} · หมวด {html.escape(getattr(e, 'category', '') or '-')} · ผู้บันทึก {html.escape(getattr(e, 'created_by', '') or '-')}
      {'<br><a class="btn2" target="_blank" href="'+expense_receipt_url(e, token)+'">เปิดใบเสร็จ</a>' if expense_receipt_exists(e) else ''}
    </div>
    """ for e in expenses]) or "<p class='muted'>ยังไม่มีรายจ่าย</p>"
    body = f"""
    <div class='hero'><div class='title'>🧾 รายจ่ายเงินกอง</div><div class='sub'>กรอกเหมือนลง Excel แต่ทำผ่านเว็บ/LINE ได้ · รอบ {r.title if r else '-'}</div></div>
    <div class='top-actions'><a class='btn2' href='/admin{query}'>กลับหน้าอนุมัติ</a><a class='btn2' href='/report.xlsx'>ดาวน์โหลด Excel</a><a class='btn2' href='/report.docx'>Word</a></div>
    <div class='stats'><div class='stat'><b>รวมรายจ่าย</b><div class='num red'>{money(total_exp)}</div><div class='muted'>บาท</div></div><div class='stat'><b>จำนวนรายการ</b><div class='num'>{len(expenses)}</div><div class='muted'>รายการ</div></div></div>
    <div class='card'>
      <h3>เพิ่มรายจ่าย</h3>
      <form method='post' action='/admin/expenses' enctype='multipart/form-data'>
        {token_hidden}
        <input name='title' placeholder='รายการ เช่น ค่าอาหารประชุม' required>
        <input name='amount' placeholder='ยอดเงิน เช่น 250' required>
        <input name='category' placeholder='หมวด เช่น อาหาร/เอกสาร/ของใช้'>
        <input name='note' placeholder='หมายเหตุ'>
        <label class='upload'>แนบรูปใบเสร็จ/สลิป<input type='file' name='receipt' accept='image/*' style='display:none'></label>
        <button class='btn'>บันทึกรายจ่าย</button>
      </form>
      <p class='note'>ทำใน LINE ได้ด้วย: พิมพ์ “รายจ่าย ค่าอาหาร 250” แล้วส่งรูปใบเสร็จตามมา</p>
    </div>
    <div class='card'><h3>รายการรายจ่าย</h3>{rows}</div>
    """
    return page("Expenses", body)


@app.post("/admin/expenses")
async def admin_expenses_post(request: Request, token: str = Form(""), title: str = Form(...), amount: str = Form(...), category: str = Form(""), note: str = Form(""), receipt: UploadFile | None = File(None), db: Session = Depends(get_db)):
    admin_ctx = require_admin(request, db, token, roles=("owner", "approver"))
    amt = parse_amount(amount)
    if amt is None:
        raise HTTPException(400, detail="ยอดเงินไม่ถูกต้อง")
    e = add_expense(db, title.strip(), amt, category=category.strip() or None, note=note.strip() or None, created_by=admin_ctx.get("name"))
    if receipt and receipt.filename:
        data = await receipt.read()
        e.receipt_path = save_expense_receipt_bytes(data)
        db.commit()
    audit_admin(db, admin_ctx, "add_expense", detail=f"{title} {money(amt)}")
    return RedirectResponse(f"/admin/expenses{'?token='+token if token else ''}", status_code=303)


@app.get("/admin/expense/receipt/{expense_id}")
def admin_expense_receipt(expense_id: int, request: Request, token: str = "", db: Session = Depends(get_db)):
    require_admin(request, db, token)
    e = db.query(Expense).filter(Expense.id == expense_id).first()
    if not e or not getattr(e, "receipt_path", None):
        raise HTTPException(404)
    path = Path(e.receipt_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, detail="ไม่พบไฟล์ใบเสร็จ อาจเกิดจากยังไม่ได้ผูก Railway Volume")
    suffix = path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/admin/evidence/{payment_id}")
def admin_evidence(payment_id: int, request: Request, token: str = "", db: Session = Depends(get_db)):
    require_admin(request, db, token)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    path = evidence_file_path(p)
    if not path or not path.exists() or not path.is_file():
        raise HTTPException(404, detail="ไม่พบไฟล์หลักฐาน อาจเกิดจากการ redeploy/restart โดยยังไม่ได้ผูก Railway Volume")
    suffix = path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login():
    return page("Admin Login", """
    <div class='hero'><div class='title'>🔐 หลังบ้าน FundBot</div><div class='sub'>แอดมินหลายคนเข้าได้ด้วย Admin Code ของตัวเอง</div></div>
    <div class='card'>
      <h2 style='margin-top:0'>เข้าสู่ระบบแอดมิน</h2>
      <p class='note'>ใส่ Admin Code ส่วนตัวของแต่ละคน หรือใส่ ADMIN_TOKEN หลักครั้งแรกเพื่อเข้าแบบ Owner</p>
      <form method='post' action='/admin/login'>
        <input name='code' type='password' placeholder='Admin Code' autocomplete='current-password' required>
        <button class='btn' type='submit'>เข้าอนุมัติรายการ</button>
      </form>
      <a class='btn2' href='/dashboard'>กลับ Dashboard</a>
    </div>
    """)


@app.post("/admin/login")
def admin_login_post(code: str = Form(...), db: Session = Depends(get_db)):
    ensure_owner_admin(db)
    code = (code or "").strip()
    admin = None
    # legacy token ยังคงเข้าได้เสมอ และถือเป็น Owner
    if code == settings.ADMIN_TOKEN:
        admin = db.query(AdminUser).filter(AdminUser.role == "owner", AdminUser.active == True).order_by(AdminUser.id).first()
        if not admin:
            admin = AdminUser(name="เฟรน", role="owner", code_hash=admin_code_hash(code), active=True)
            db.add(admin); db.commit(); db.refresh(admin)
    else:
        for a in db.query(AdminUser).filter(AdminUser.active == True).all():
            if verify_admin_code(code, a.code_hash):
                admin = a
                break
    if not admin:
        return page("Login ไม่สำเร็จ", "<div class='card'><h2>รหัสไม่ถูกต้อง</h2><p class='note'>กรุณาตรวจ Admin Code อีกครั้ง</p><a class='btn2' href='/admin/login'>กลับไปล็อกอิน</a></div>")
    admin.last_login_at = datetime.utcnow()
    db.commit()
    resp = RedirectResponse("/admin#pending", status_code=303)
    resp.set_cookie("fundbot_admin", make_admin_session(admin), httponly=True, samesite="lax", secure=True, max_age=60*60*24*30)
    return resp


@app.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie("fundbot_admin")
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, token: str = "", db: Session = Depends(get_db)):
    admin_ctx = require_admin(request, db, token)
    r = services.active_round(db)
    pays = services.get_payments(db, r) if r else []
    pending = [p for p in pays if p.status == "pending"]
    token_hidden = f"<input type='hidden' name='token' value='{token}'>" if token else ""
    query = f"?token={token}" if token else ""
    rows = "".join([f"<div class='member-row'><div class='avatar'>{p.member.name.replace('ท่าน','').strip()[:1] or '?'}</div><b>{p.member.name}</b><b style='text-align:right'>{money(p.due_amount)}</b><span class='status pill {'paid' if p.status=='paid' else ('partial' if p.status=='pending' else 'unpaid')}'>{'✅ ชำระแล้ว' if p.status=='paid' else ('🟡 รอตรวจ' if p.status=='pending' else 'ยังไม่ชำระ')}</span></div>" for p in pays])
    pending_cards = ""
    can_approve = admin_ctx["role"] in ["owner", "approver"]
    for p in pending:
        slip_url = f"/admin/evidence/{p.id}{query}&v={int(datetime.now().timestamp())}" if evidence_file_exists(p) else ""
        if slip_url:
            img = f"""<a href='{slip_url}' target='_blank' rel='noopener'>
              <img src='{slip_url}' alt='หลักฐานการชำระเงิน'
                   style='width:100%;max-height:620px;object-fit:contain;background:#f8fafc;border-radius:18px;border:1px solid #e6eaf2;margin:10px 0'
                   onerror="this.outerHTML='<div class=\'copybox\'><b>รูปหลักฐานเปิดไม่ขึ้น</b><br><span class=\'note\'>ให้กดอัปโหลดใหม่ หรือเช็ก Railway Volume</span></div>'">
            </a>"""
        else:
            img = "<div class='copybox'><b>ไม่พบไฟล์หลักฐาน</b><br><span class='note'>รายการนี้อาจถูกสร้างก่อน deploy รอบล่าสุด หรือยังไม่ได้ตั้ง Railway Volume ให้เก็บไฟล์ถาวร ให้ผู้ใช้ส่งหลักฐานใหม่อีกครั้ง</span></div>"
        actions = f"""
          <div class='top-actions'>
            <a class='btn' href='/admin/approve/{p.id}{query}'>✅ อนุมัติ</a>
            <form action='/admin/reject/{p.id}' method='post' style='flex:1'>
              {token_hidden}
              <input name='reason' required placeholder='เหตุผลที่ไม่ผ่าน'>
              <button class='btn2' type='submit'>❌ Reject</button>
            </form>
          </div>
        """ if can_approve else "<div class='copybox'><b>Viewer</b><br><span class='note'>บัญชีนี้ดูได้อย่างเดียว ยังอนุมัติไม่ได้</span></div>"
        pending_cards += f"""
        <div class='card' id='pending'>
          <h3 style='margin:0'>📥 {p.member.name}</h3>
          <div class='num green'>{money(p.due_amount)} บาท</div><p class='note'>ประเภท: {'เงินสด' if getattr(p, 'payment_type', '') == 'cash' else 'โอน'} • วันที่ส่ง: {format_th(p.paid_at)}</p>
          {img}
          {actions}
        </div>
        """
    if not pending_cards:
        pending_cards = "<div class='card' id='pending'><h3>📥 สลิปรอตรวจ</h3><p class='muted'>ยังไม่มีสลิปรอตรวจ</p></div>"
    owner_tools = ""
    if admin_ctx["role"] == "owner":
        owner_tools = f"""
        <div class='admin-grid'>
          <div class='card'><h3>เปิดรอบใหม่</h3><form action='/admin/open' method='post'>{token_hidden}<input name='title' placeholder='กรกฎาคม 2569'><button class='btn'>เปิดรอบ</button></form></div>
          <div class='card'><h3>เพิ่ม/แก้สมาชิก</h3><form action='/admin/member' method='post'>{token_hidden}<input name='name' placeholder='ชื่อ'><input name='amount' placeholder='ยอด'><button class='btn'>บันทึก</button></form></div>
        </div>
        """
    logs = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(12).all()
    log_html = "".join([f"<div class='member-row'><div class='avatar'>📝</div><b>{l.admin_name}</b><span>{l.action}</span><span class='note'>{format_th(l.created_at, '%d/%m %H:%M')}</span></div>" for l in logs]) or "<p class='muted'>ยังไม่มีประวัติ</p>"
    body = f"""
    <div class='hero'><div class='title'>หลังบ้าน FundBot</div><div class='sub'>รอบ: {r.title if r else '-'} · เข้าระบบโดย {admin_ctx['name']} ({admin_ctx['role']})</div></div>
    <div class='top-actions'><a class='btn2' href='/admin/admins{query}'>👥 จัดการแอดมิน</a><a class='btn2' href='/admin/expenses{query}'>🧾 รายจ่าย/ใบเสร็จ</a><a class='btn2' href='/dashboard'>เปิด Dashboard</a><a class='btn2' href='/admin/logout'>ออกจากระบบ</a></div>
    <div class='stats'>
      <div class='stat'><b>สลิปรอตรวจ</b><div class='num' style='color:#b76b00'>{len(pending)}</div><div class='muted'>รายการ</div></div>
      <div class='stat'><b>รายงาน</b><a class='btn2' href='/report.xlsx'>Excel</a><a class='btn2' href='/report.docx'>Word</a><a class='btn2' href='/report.pdf'>PDF</a></div>
      <div class='stat'><b>สิทธิ์</b><div class='num'>{admin_ctx['role']}</div><div class='muted'>{admin_ctx['name']}</div></div>
    </div>
    {pending_cards}
    <div class='leave-card'>
      <div class='leave-head blue'><h1>รายการสมาชิก</h1><div>แก้ไข/ตรวจสถานะ</div></div>
      <div class='leave-body blue'>{rows}</div>
    </div>
    {owner_tools}
    <div class='card'><h3>ประวัติแอดมินล่าสุด</h3>{log_html}</div>
    """
    return page("Admin", body)


@app.get("/admin/admins", response_class=HTMLResponse)
def admin_users(request: Request, token: str = "", db: Session = Depends(get_db)):
    admin_ctx = require_admin(request, db, token)
    query = f"?token={token}" if token else ""
    token_hidden = f"<input type='hidden' name='token' value='{token}'>" if token else ""
    admins = db.query(AdminUser).order_by(AdminUser.id).all()
    rows = "".join([f"""
    <div class='member-row'>
      <div class='avatar'>{a.name[:1]}</div><b>{a.name}</b><span class='pill {'paid' if a.active else 'unpaid'}'>{a.role} · {'เปิดใช้' if a.active else 'ปิด'}</span>
      <span class='note'>{format_th(a.last_login_at, '%d/%m %H:%M')}</span>
    </div>""" for a in admins])
    add_form = ""
    if admin_ctx["role"] == "owner":
        add_form = f"""
        <div class='card'>
          <h3>เพิ่ม/เปลี่ยน Admin Code</h3>
          <form method='post' action='/admin/admins'>
            {token_hidden}
            <input name='name' placeholder='ชื่อ เช่น จริยา' required>
            <select name='role' style='width:100%;border:1px solid #d8e1ef;border-radius:14px;padding:12px;margin:6px 0;background:#fff;font:inherit'>
              <option value='approver'>Approver: อนุมัติ/ปฏิเสธได้</option>
              <option value='viewer'>Viewer: ดูได้อย่างเดียว</option>
              <option value='owner'>Owner: จัดการแอดมินได้</option>
            </select>
            <input name='code' placeholder='ตั้ง Admin Code ส่วนตัว' required>
            <button class='btn'>บันทึกแอดมิน</button>
          </form>
          <p class='note'>ส่ง Admin Code ให้เฉพาะคนที่ไว้ใจได้ แต่ละคนใช้คนละรหัส เวลาอนุมัติระบบจะบันทึกชื่อไว้</p>
        </div>
        """
    else:
        add_form = "<div class='copybox'>เฉพาะ Owner เท่านั้นที่เพิ่ม/แก้แอดมินได้</div>"
    return page("Admins", f"""
    <div class='hero'><div class='title'>👥 แอดมินหลายคน</div><div class='sub'>ไม่ผูก LINE ID · ใช้ Admin Code แยกคน</div></div>
    <div class='top-actions'><a class='btn2' href='/admin{query}'>กลับหน้าอนุมัติ</a><a class='btn2' href='/admin/logout'>ออกจากระบบ</a></div>
    {add_form}
    <div class='card'><h3>รายชื่อแอดมิน</h3>{rows}</div>
    """)


@app.post("/admin/admins")
def admin_users_post(request: Request, token: str = Form(""), name: str = Form(...), role: str = Form("approver"), code: str = Form(...), db: Session = Depends(get_db)):
    admin_ctx = require_owner(request, db, token)
    role = role if role in ["owner", "approver", "viewer"] else "approver"
    name = " ".join(name.strip().split())
    code = code.strip()
    if len(code) < 4:
        raise HTTPException(400, detail="Admin Code ต้องมีอย่างน้อย 4 ตัว")
    a = db.query(AdminUser).filter(AdminUser.name == name).first()
    if not a:
        a = AdminUser(name=name, role=role, code_hash=admin_code_hash(code), active=True)
        db.add(a)
        action = "create_admin"
    else:
        a.role = role
        a.code_hash = admin_code_hash(code)
        a.active = True
        action = "update_admin"
    db.commit()
    audit_admin(db, admin_ctx, action, detail=f"{name}:{role}")
    return RedirectResponse(f"/admin/admins{'?token='+token if token else ''}", status_code=303)


@app.get("/admin/approve/{payment_id}")
def admin_approve(payment_id: int, request: Request, token: str = "", db: Session = Depends(get_db)):
    admin_ctx = require_approver(request, db, token)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    ptype = getattr(p, "payment_type", "") or ("cash" if getattr(p, "receipt_path", None) else "transfer")
    services.mark_member_paid(db, p.member_id, Decimal(p.due_amount or 0), note=(p.note or "") + f"\nadmin_approved_by:{admin_ctx['name']}", slip_path=p.slip_path)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    p.payment_type = ptype
    p.rejection_reason = None
    db.commit()
    audit_admin(db, admin_ctx, "approve", p.id, f"{p.member.name} {money(p.due_amount)} {ptype}")
    update_line_summary(db)
    target = admin_notify_target(db)
    if target:
        push(target, [text(f"✅ อนุมัติแล้ว: {p.member.name} {money(p.due_amount)} บาท\nโดย: {admin_ctx['name']}")])
    return RedirectResponse(admin_return_url(token), status_code=303)


@app.get("/admin/reject/{payment_id}", response_class=HTMLResponse)
def admin_reject_form(payment_id: int, request: Request, token: str = "", db: Session = Depends(get_db)):
    require_approver(request, db, token)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    token_hidden = f"<input type='hidden' name='token' value='{token}'>" if token else ""
    return page("Reject", f"<div class='card'><h2>Reject: {p.member.name}</h2><form method='post' action='/admin/reject/{p.id}'>{token_hidden}<input name='reason' required placeholder='เหตุผลที่ไม่ผ่าน'><button class='btn'>บันทึก Reject</button></form></div>")


@app.post("/admin/reject/{payment_id}")
def admin_reject(payment_id: int, request: Request, token: str = Form(""), reason: str = Form(...), db: Session = Depends(get_db)):
    admin_ctx = require_approver(request, db, token)
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404)
    p.status = "unpaid"
    p.paid_amount = Decimal("0")
    p.paid_at = None
    p.rejection_reason = reason.strip()
    p.note = (p.note or "") + f"\nadmin_rejected_by:{admin_ctx['name']}:{reason.strip()}"
    db.commit()
    audit_admin(db, admin_ctx, "reject", p.id, f"{p.member.name}: {reason.strip()}")
    update_line_summary(db)
    target = admin_notify_target(db)
    if target:
        push(target, [text(f"❌ ไม่ผ่าน: {p.member.name}\nเหตุผล: {reason.strip()}\nโดย: {admin_ctx['name']}\nกรุณาอัปโหลดใหม่")])
    return RedirectResponse(admin_return_url(token), status_code=303)


@app.post("/admin/open")
def admin_open(request: Request, token: str = Form(""), title: str = Form(...), db: Session = Depends(get_db)):
    admin_ctx = require_owner(request, db, token)
    services.open_round(db, title)
    audit_admin(db, admin_ctx, "open_round", detail=title)
    return RedirectResponse(f"/admin{'?token='+token if token else ''}", status_code=303)


@app.post("/admin/member")
def admin_member(request: Request, token: str = Form(""), name: str = Form(...), amount: str = Form(...), db: Session = Depends(get_db)):
    admin_ctx = require_owner(request, db, token)
    services.add_member(db, name, parse_amount(amount) or Decimal("0"))
    audit_admin(db, admin_ctx, "upsert_member", detail=f"{name} {amount}")
    return RedirectResponse(f"/admin{'?token='+token if token else ''}", status_code=303)
