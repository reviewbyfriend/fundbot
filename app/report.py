from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from .services import money
from .timezone import format_th, format_now_th


def _status_th(p):
    if p.status == "paid":
        if getattr(p, "payment_type", "") == "cash":
            return "ชำระแล้ว (เงินสด)"
        return "ชำระแล้ว (โอน)"
    if p.status == "pending":
        return "รอตรวจสอบ"
    return "ยังไม่ได้ชำระ"


def _expense_date(e):
    return format_th(getattr(e, "expense_date", None) or getattr(e, "created_at", None), "%d/%m/%Y", default="")


def make_excel(round_, payments, expenses=None):
    expenses = expenses or []
    wb = Workbook()
    ws = wb.active
    ws.title = "สรุปรายเดือน"
    blue = "1F6FEB"
    light = "EAF3FF"
    green = "159947"
    red = "D93025"
    border = Border(bottom=Side(style="thin", color="E5E7EB"))

    ws["A1"] = "รายงานเงินกองสำนักงาน"
    ws["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=blue)
    ws.merge_cells("A1:F1")
    ws["A2"] = f"รอบเดือน: {round_.title}"
    ws["A3"] = f"สร้างรายงาน: {format_now_th('%d/%m/%Y %H:%M')}"

    total_due = sum([Decimal(p.due_amount or 0) for p in payments], Decimal("0"))
    total_paid = sum([Decimal(p.due_amount or 0) for p in payments if p.status == "paid"], Decimal("0"))
    total_expense = sum([Decimal(e.amount or 0) for e in expenses], Decimal("0"))
    balance = total_paid + Decimal(getattr(round_, "carry_over", 0) or 0) - total_expense

    summary = [
        ("ยอดตั้งต้น/ยกมา", Decimal(getattr(round_, "carry_over", 0) or 0)),
        ("รวมต้องเก็บ", total_due),
        ("เก็บแล้ว", total_paid),
        ("รายจ่าย", total_expense),
        ("คงเหลือ", balance),
    ]
    row = 5
    for label, val in summary:
        ws.cell(row, 1, label).font = Font(bold=True)
        ws.cell(row, 2, float(val)).number_format = '#,##0.00'
        row += 1

    row += 1
    ws.cell(row, 1, "รายรับสมาชิก").font = Font(size=14, bold=True, color="1F2937")
    row += 1
    headers = ["ชื่อ", "ยอดที่ต้องชำระ", "สถานะ", "ประเภท", "วันที่อนุมัติ", "หมายเหตุ"]
    for col, h in enumerate(headers, 1):
        c = ws.cell(row, col, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=blue)
        c.alignment = Alignment(horizontal="center")
    row += 1
    for p in payments:
        ws.append([
            p.member.name,
            float(p.due_amount or 0),
            _status_th(p),
            getattr(p, "payment_type", "") or "",
            format_th(p.paid_at, "%d/%m/%Y %H:%M", default=""),
            getattr(p, "rejection_reason", "") or "",
        ])
        for col in range(1, 7): ws.cell(row, col).border = border
        ws.cell(row, 2).number_format = '#,##0.00'
        row += 1

    row += 2
    ws.cell(row, 1, "รายจ่าย").font = Font(size=14, bold=True, color="1F2937")
    row += 1
    exp_headers = ["วันที่", "รายการ", "หมวด", "ยอด", "ผู้บันทึก", "หมายเหตุ"]
    for col, h in enumerate(exp_headers, 1):
        c = ws.cell(row, col, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=red)
        c.alignment = Alignment(horizontal="center")
    row += 1
    if expenses:
        for e in expenses:
            ws.append([_expense_date(e), e.title, getattr(e, "category", "") or "", float(e.amount or 0), getattr(e, "created_by", "") or "", getattr(e, "note", "") or ""])
            for col in range(1, 7): ws.cell(row, col).border = border
            ws.cell(row, 4).number_format = '#,##0.00'
            row += 1
    else:
        ws.cell(row, 1, "ยังไม่มีรายจ่าย")

    for i, width in enumerate([20, 24, 18, 14, 18, 32], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for r in ws.iter_rows():
        for c in r:
            c.alignment = Alignment(vertical="center", wrap_text=True)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def make_word(round_, payments, expenses=None):
    expenses = expenses or []
    doc = Document()
    doc.add_heading("รายงานเงินกองสำนักงาน", 0)
    doc.add_paragraph(f"รอบเดือน: {round_.title}")
    doc.add_paragraph(f"สร้างรายงาน: {format_now_th('%d/%m/%Y %H:%M')}")
    total_paid = sum([Decimal(p.due_amount or 0) for p in payments if p.status == "paid"], Decimal("0"))
    total_expense = sum([Decimal(e.amount or 0) for e in expenses], Decimal("0"))
    doc.add_paragraph(f"รวมรับจริง: {money(total_paid)} บาท")
    doc.add_paragraph(f"รวมรายจ่าย: {money(total_expense)} บาท")
    doc.add_paragraph(f"คงเหลือ: {money(total_paid - total_expense)} บาท")

    doc.add_heading("รายรับสมาชิก", level=1)
    table = doc.add_table(rows=1, cols=5)
    hdr = table.rows[0].cells
    for i, h in enumerate(["ชื่อ", "ยอด", "สถานะ", "ประเภท", "วันที่อนุมัติ"]): hdr[i].text = h
    for p in payments:
        row = table.add_row().cells
        row[0].text = p.member.name
        row[1].text = money(p.due_amount)
        row[2].text = _status_th(p)
        row[3].text = getattr(p, "payment_type", "") or "-"
        row[4].text = format_th(p.paid_at)

    doc.add_heading("รายจ่าย", level=1)
    et = doc.add_table(rows=1, cols=4)
    eh = et.rows[0].cells
    for i, h in enumerate(["วันที่", "รายการ", "ยอด", "หมายเหตุ"]): eh[i].text = h
    for e in expenses:
        row = et.add_row().cells
        row[0].text = _expense_date(e)
        row[1].text = e.title
        row[2].text = money(e.amount)
        row[3].text = getattr(e, "note", "") or ""
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def make_pdf(round_, payments, expenses=None):
    expenses = expenses or []
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
    c.drawString(40, y, f"Generated: {format_now_th('%Y-%m-%d %H:%M')}")
    y -= 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Member")
    c.drawString(210, y, "Amount")
    c.drawString(300, y, "Status")
    c.drawString(420, y, "Type")
    y -= 16
    c.setFont("Helvetica", 10)
    for p in payments:
        if y < 80:
            c.showPage(); y = h - 50; c.setFont("Helvetica", 10)
        c.drawString(40, y, str(p.member.name)[:26])
        c.drawString(210, y, money(p.due_amount))
        c.drawString(300, y, _status_th(p)[:18])
        c.drawString(420, y, getattr(p, "payment_type", "") or "-")
        y -= 15
    y -= 12
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Expenses")
    y -= 18
    c.setFont("Helvetica", 10)
    for e in expenses:
        if y < 80:
            c.showPage(); y = h - 50; c.setFont("Helvetica", 10)
        c.drawString(40, y, _expense_date(e))
        c.drawString(110, y, str(e.title)[:36])
        c.drawString(360, y, money(e.amount))
        y -= 15
    c.save()
    return out.getvalue()
