from io import BytesIO
from datetime import datetime
from decimal import Decimal
from openpyxl import Workbook
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from .services import money

def _status_th(p):
    if p.status == "paid":
        if getattr(p, "payment_type", "") == "cash":
            return "ชำระแล้ว (เงินสด)"
        return "ชำระแล้ว (โอน)"
    if p.status == "pending":
        return "รอตรวจสอบ"
    return "ยังไม่ได้ชำระ"

def make_excel(round_, payments, expenses=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Monthly Summary"
    ws.append(["FundBot รายงานเงินกอง", round_.title])
    ws.append(["สร้างรายงาน", datetime.now().strftime("%Y-%m-%d %H:%M")])
    ws.append([])
    ws.append(["ชื่อ", "ยอดที่ต้องชำระ", "สถานะ", "ประเภท", "วันที่อนุมัติ", "หมายเหตุ"])
    total_due = Decimal("0")
    total_paid = Decimal("0")
    for p in payments:
        total_due += Decimal(p.due_amount or 0)
        if p.status == "paid":
            total_paid += Decimal(p.due_amount or 0)
        ws.append([
            p.member.name,
            float(p.due_amount or 0),
            _status_th(p),
            getattr(p, "payment_type", "") or "",
            p.paid_at.strftime("%Y-%m-%d %H:%M") if p.paid_at else "",
            getattr(p, "rejection_reason", "") or "",
        ])
    ws.append([])
    ws.append(["รวมต้องเก็บ", float(total_due)])
    ws.append(["เก็บแล้ว", float(total_paid)])
    ws.append(["คงค้าง", float(max(total_due - total_paid, Decimal("0")))])
    for col in ["A","B","C","D","E","F"]:
        ws.column_dimensions[col].width = 22
    out = BytesIO()
    wb.save(out)
    return out.getvalue()

def make_word(round_, payments):
    doc = Document()
    doc.add_heading("รายงานเงินกองสำนักงาน", 0)
    doc.add_paragraph(f"รอบเดือน: {round_.title}")
    doc.add_paragraph(f"สร้างรายงาน: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    table = doc.add_table(rows=1, cols=5)
    hdr = table.rows[0].cells
    hdr[0].text = "ชื่อ"
    hdr[1].text = "ยอด"
    hdr[2].text = "สถานะ"
    hdr[3].text = "ประเภท"
    hdr[4].text = "วันที่อนุมัติ"
    for p in payments:
        row = table.add_row().cells
        row[0].text = p.member.name
        row[1].text = money(p.due_amount)
        row[2].text = _status_th(p)
        row[3].text = getattr(p, "payment_type", "") or "-"
        row[4].text = p.paid_at.strftime("%d/%m/%Y %H:%M") if p.paid_at else "-"
    out = BytesIO()
    doc.save(out)
    return out.getvalue()

def make_pdf(round_, payments):
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "FundBot Monthly Summary")
    y -= 24
    c.setFont("Helvetica", 11)
    c.drawString(40, y, f"Round: {round_.title}")
    y -= 18
    c.drawString(40, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Member")
    c.drawString(210, y, "Amount")
    c.drawString(300, y, "Status")
    c.drawString(420, y, "Type")
    y -= 16
    c.setFont("Helvetica", 10)
    for p in payments:
        if y < 60:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica", 10)
        c.drawString(40, y, str(p.member.name)[:26])
        c.drawString(210, y, money(p.due_amount))
        c.drawString(300, y, _status_th(p))
        c.drawString(420, y, getattr(p, "payment_type", "") or "-")
        y -= 15
    c.save()
    return out.getvalue()
