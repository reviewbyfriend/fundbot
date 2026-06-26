from pathlib import Path
import pandas as pd
from sqlalchemy.orm import Session
from .models import Group
from .services import add_member


def import_members_from_excel(db: Session, group: Group, file_path: str | Path) -> int:
    # อ่านแบบยืดหยุ่น: หาคอลัมน์ที่มีชื่อ + จำนวนเงิน จากชีตแรก
    df = pd.read_excel(file_path, header=None)
    count = 0
    for _, row in df.iterrows():
        values = [x for x in row.tolist() if pd.notna(x)]
        if len(values) < 2:
            continue
        name = str(values[0]).strip()
        amount = None
        for v in values[1:]:
            try:
                amount = float(str(v).replace(',', '').strip())
                break
            except Exception:
                continue
        if amount is None or amount <= 0:
            continue
        if any(skip in name for skip in ["รายรับ", "รวม", "ยอดยกมา", "สำนักงาน"]):
            continue
        add_member(db, group, name, amount)
        count += 1
    return count
