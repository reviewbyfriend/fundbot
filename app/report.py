from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from .models import Payment, Expense

def make_excel(round_, payments, expenses) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "รายงานกองกลาง"
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.merge_cells("A1:F1")
    ws["A1"] = f"รายงานเงินกองกลาง {round_.title}"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.append([])
    ws.append(["รายรับ", "ยอดต้องจ่าย", "จ่ายแล้ว", "สถานะ", "รายจ่าย", "จำนวน"])
    for c in ws[3]:
        c.font = Font(bold=True); c.border = border; c.alignment = Alignment(horizontal="center")
    max_len = max(len(payments), len(expenses))
    for i in range(max_len):
        p = payments[i] if i < len(payments) else None
        e = expenses[i] if i < len(expenses) else None
        ws.append([
            p.member.name if p else "",
            float(p.due_amount) if p else "",
            float(p.paid_amount) if p else "",
            "จ่ายแล้ว" if p and p.status == "paid" else ("ยังไม่ครบ" if p else ""),
            e.title if e else "",
            float(e.amount) if e else "",
        ])
    for row in ws.iter_rows(min_row=4, max_row=3+max_len, min_col=1, max_col=6):
        for cell in row: cell.border = border
    start = 5 + max_len
    total_due = sum(Decimal(p.due_amount or 0) for p in payments)
    total_paid = sum(Decimal(p.paid_amount or 0) for p in payments)
    total_exp = sum(Decimal(e.amount or 0) for e in expenses)
    balance = Decimal(round_.carry_over or 0) + total_paid - total_exp
    ws[f"E{start}"] = "ยอดยกมา"; ws[f"F{start}"] = float(round_.carry_over or 0)
    ws[f"E{start+1}"] = "รวมต้องเก็บ"; ws[f"F{start+1}"] = float(total_due)
    ws[f"E{start+2}"] = "รวมรับจริง"; ws[f"F{start+2}"] = float(total_paid)
    ws[f"E{start+3}"] = "รวมรายจ่าย"; ws[f"F{start+3}"] = float(total_exp)
    ws[f"E{start+4}"] = "เงินคงเหลือ"; ws[f"F{start+4}"] = float(balance)
    for r in range(start, start+5):
        ws[f"E{r}"].font = Font(bold=True); ws[f"F{r}"].font = Font(bold=True)
    for col in "ABCDEF": ws.column_dimensions[col].width = 22
    bio = BytesIO(); wb.save(bio); return bio.getvalue()
