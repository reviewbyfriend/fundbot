from io import BytesIO
from openpyxl import Workbook
from .services import money

def make_excel(round_, payments, expenses):
    wb = Workbook()
    ws = wb.active
    ws.title = "รายงานเงินกอง"
    ws.append(["เงินกองสำนักงาน", round_.title])
    ws.append([])
    ws.append(["ชื่อ", "ยอดที่ต้องชำระ", "สถานะ", "วันที่ชำระ"])
    for p in payments:
        ws.append([p.member.name, float(p.due_amount or 0), "ชำระแล้ว" if p.status == "paid" else "ยังไม่ได้ชำระ", p.paid_at.strftime("%Y-%m-%d %H:%M") if p.paid_at else ""])
    ws.append([])
    ws.append(["รายจ่าย"])
    ws.append(["รายการ", "ยอด"])
    for e in expenses:
        ws.append([e.title, float(e.amount or 0)])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
