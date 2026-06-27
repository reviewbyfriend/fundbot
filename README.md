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
