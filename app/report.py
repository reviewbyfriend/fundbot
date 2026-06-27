from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from .services import round_summary, money

def create_report_excel(db, rnd) -> bytes:
    s = round_summary(db, rnd)
    wb = Workbook(); ws = wb.active; ws.title = "รายงานกองกลาง"
    thin = Side(style="thin"); border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.merge_cells("A1:F1"); ws["A1"] = f"รายงานกองกลาง {rnd.title}"; ws["A1"].font = Font(bold=True, size=16); ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([]); ws.append(["รายรับ", "ยอด", "สถานะ", "รายจ่าย", "ยอด", "วันที่"])
    for c in ws[3]: c.font = Font(bold=True); c.border = border; c.alignment = Alignment(horizontal="center")
    members = s["members"]; expenses = s["expenses"]; max_len = max(len(members), len(expenses), 1)
    for i in range(max_len):
        m = members[i] if i < len(members) else None
        e = expenses[i] if i < len(expenses) else None
        pay = s["payments"].get(m.id) if m else None
        row = [m.name if m else "", m.monthly_amount if m else "", "จ่ายแล้ว" if pay else ("ค้าง" if m else ""), e.title if e else "", e.amount if e else "", e.created_at.strftime("%d/%m/%Y") if e else ""]
        ws.append(row)
    r = ws.max_row + 2
    ws[f"D{r}"] = "ยอดยกมา"; ws[f"E{r}"] = rnd.brought_forward
    ws[f"D{r+1}"] = "รายรับ"; ws[f"E{r+1}"] = s["paid"]
    ws[f"D{r+2}"] = "รายจ่าย"; ws[f"E{r+2}"] = s["expense"]
    ws[f"D{r+3}"] = "เงินคงเหลือ"; ws[f"E{r+3}"] = s["balance"]; ws[f"D{r+3}"].font = Font(bold=True); ws[f"E{r+3}"].font = Font(bold=True)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, min_col=1, max_col=6):
        for cell in row:
            cell.border = border
            if isinstance(cell.value, (int, float)): cell.number_format = '#,##0.00'
    for col, width in zip("ABCDEF", [28, 14, 14, 35, 14, 15]): ws.column_dimensions[col].width = width
    bio = BytesIO(); wb.save(bio); return bio.getvalue()
