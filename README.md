# FundBot v2 Stable จาก main เดิม

เวอร์ชันนี้ต่อยอดจาก `main` เดิมที่ Railway ใช้ได้ ไม่รื้อโครงใหม่ จึง deploy ได้เหมือนเดิม

## ฟีเจอร์ v2 ที่เพิ่ม
- Dashboard card UI แบบ blue/glass และสถานะ:
  - 🔴 Not Paid
  - 🟡 Waiting Approval
  - 🟢 Paid (Transfer)
  - 🟢 Paid (Cash)
- Realtime Dashboard ผ่าน WebSocket `/ws` พร้อม fallback polling
- หน้าชำระเงินเลือกสมาชิกแบบ card/radio
- QR ตามยอดจาก `app/static/payment_qr/{300,500,800,1000,2500}.jpg`
- ปุ่ม Copy PromptPay / Save QR / Open Krungthai NEXT / Open SCB EASY / Open K PLUS
- Upload Slip พร้อม validate ไฟล์, จำกัด 8MB, compress รูป, filename เป็น UUID
- Slip ทุกใบเป็น Waiting Approval เท่านั้น ไม่อนุมัติอัตโนมัติ
- Cash Payment พร้อม Signature Pad และบันทึกที่ `/data/signatures/YYYY-MM/`
- Admin `/admin?token=ADMIN_TOKEN`
  - ดูหลักฐาน
  - Approve
  - Reject พร้อมเหตุผลบังคับกรอก
- Reports:
  - `/report.xlsx`
  - `/report.docx`
  - `/report.pdf`


## หน้าแอดมิน / การอนุมัติส่วนตัว

เข้าแอดมินง่าย ๆ ที่:

```text
/admin/login
```

หรือเข้าตรง:

```text
/admin?token=ADMIN_TOKEN
```

ถ้าต้องการให้บอทส่งสลิป/ใบเงินสดที่รออนุมัติไปหาแอดมินแบบส่วนตัว ให้ทักบอทในแชทส่วนตัวแล้วพิมพ์:

```text
ตั้งแอดมิน ADMIN_TOKEN
```

หลังจากนั้นเมื่อมีคนอัปโหลดสลิปหรือเซ็นรับเงินสด ระบบจะส่งการ์ดอนุมัติไปที่แชทส่วนตัวของแอดมินก่อน ถ้าไม่ได้ตั้งไว้จะ fallback ไปที่กลุ่มล่าสุดที่คุยกับบอท

## Railway Variables

```env
DATABASE_URL=
ADMIN_TOKEN=
PUBLIC_BASE_URL=
PROMPTPAY_ID=
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
ADMIN_NOTIFY_TARGET_ID=
SLIP_STORAGE_DIR=/data/slips
SIGNATURE_STORAGE_DIR=/data/signatures
```

## Railway
ใช้ Dockerfile เดิมได้เลย ไม่ต้องมี Procfile

ควรผูก Railway Volume ที่ `/data` เพื่อให้สลิปและลายเซ็นไม่หายหลัง redeploy

## หน้าเว็บ
- `/dashboard`
- `/pay`
- `/admin?token=ADMIN_TOKEN`
- `/health`

## หมายเหตุ
เวอร์ชันนี้ยังคงแนวทางเดิมของ main คือมี migration compatibility ใน `app/database.py` เพื่อไม่ทำข้อมูลเดิมหาย


## v2 Fix: Admin Evidence + K PLUS

- เพิ่ม route `/admin/evidence/{payment_id}` สำหรับเปิดสลิป/ลายเซ็นผ่านหลังบ้านโดยตรง
- หน้า Admin จะแจ้งชัดเจนถ้าไฟล์หลักฐานหายหลัง redeploy/restart
- เพิ่ม K PLUS fallback หลาย scheme และไม่ใช้ browser alert
- แนะนำ Railway: ผูก Volume ที่ `/data` เพื่อให้สลิปและลายเซ็นไม่หายหลัง redeploy

## Multi-Admin v2.1

เพิ่มระบบแอดมินหลายคนแบบไม่ผูก LINE ID

### วิธีใช้งานครั้งแรก
1. เข้า `/admin/login`
2. ใส่ `ADMIN_TOKEN` จาก Railway Variables เพื่อเข้าเป็น Owner ครั้งแรก
3. ไปที่ `/admin/admins`
4. เพิ่มแอดมินได้หลายคน โดยกำหนด Role:
   - Owner: เพิ่ม/แก้แอดมิน เปิดรอบ แก้สมาชิก อนุมัติ/ปฏิเสธ
   - Approver: อนุมัติ/ปฏิเสธ และดูหลักฐาน
   - Viewer: ดูรายการได้อย่างเดียว

ระบบจะบันทึกประวัติว่าใครเป็นคนอนุมัติหรือปฏิเสธใน Admin Audit Log

## v2 Expense + LINE Reports

เพิ่มระบบรายจ่ายเงินกอง:

- กรอกรายจ่ายผ่านหลังบ้าน `/admin/expenses`
- แนบรูปใบเสร็จ/สลิปค่าใช้จ่าย
- สั่งงานผ่าน LINE ได้:
  - `รายจ่าย ค่าอาหาร 250`
  - จากนั้นส่งรูปใบเสร็จตามมา ระบบจะแนบเข้ารายการล่าสุด
  - `รายงาน` หรือ `พิมพ์รายงาน` เพื่อรับลิงก์ Excel / Word / PDF
- รายงาน Excel/Word/PDF รวมทั้งรายรับสมาชิก รายจ่าย และยอดคงเหลือ

> แนะนำ Railway Volume สำหรับ `/data` เพื่อไม่ให้รูปสลิป/ใบเสร็จหายหลัง redeploy/restart
