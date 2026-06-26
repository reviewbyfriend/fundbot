from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from .db import summary


def create_report_xlsx(group_id: str, out_path: str) -> str:
    data = summary(group_id)
    if not data:
        raise ValueError("ยังไม่มีรอบเดือน")
    r, rows, expenses = data
    wb = Workbook()
    ws = wb.active
    ws.title = "รายงาน"
    ws["A1"] = "สำนักงานอัยการพิเศษฝ่ายคดีล้มละลาย ๑"
    ws["A2"] = f"ประจำเดือน {r['title']}"
    ws["A3"] = "รายรับ"
    ws["C3"] = "รายจ่าย"
    bold = Font(bold=True)
    for cell in ["A1", "A2", "A3", "C3"]:
        ws[cell].font = bold
    row = 4
    ws.cell(row, 1, "ยอดยกมา")
    ws.cell(row, 2, float(r['brought_forward'] or 0))
    row += 1
    total_income = float(r['brought_forward'] or 0)
    for m in rows:
        ws.cell(row, 1, m['display_name'])
        ws.cell(row, 2, float(m['paid_amount'] or 0))
        total_income += float(m['paid_amount'] or 0)
        row += 1
    income_total_row = row
    ws.cell(row, 1, "รวมรายรับ")
    ws.cell(row, 2, total_income)
    exp_row = 4
    total_exp = 0
    for e in expenses:
        ws.cell(exp_row, 3, e['item_date'] or "")
        ws.cell(exp_row, 4, e['title'])
        ws.cell(exp_row, 5, float(e['amount']))
        total_exp += float(e['amount'])
        exp_row += 1
    ws.cell(max(income_total_row, exp_row), 4, "รวมรายจ่าย")
    ws.cell(max(income_total_row, exp_row), 5, total_exp)
    ws.cell(max(income_total_row, exp_row)+2, 4, "เงินคงเหลือ")
    ws.cell(max(income_total_row, exp_row)+2, 5, total_income-total_exp)
    for col, width in [("A", 26),("B", 14),("C", 14),("D", 42),("E", 14)]:
        ws.column_dimensions[col].width = width
    thin = Side(style="thin")
    for row_cells in ws.iter_rows(min_row=3, max_row=max(income_total_row, exp_row)+2, min_col=1, max_col=5):
        for cell in row_cells:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
