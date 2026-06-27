# FundBot v1.0 Production

LINE Group Bot สำหรับเก็บเงินกองกลางรายเดือน ยอดสมาชิกไม่เท่ากัน มี PromptPay QR, OCR สลิปเบื้องต้น, สรุป/ทวง/รายงาน Excel และ deploy บน Railway ได้ทันที

## Railway Variables ที่ต้องมี

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
PROMPTPAY_ID=เบอร์พร้อมเพย์หรือเลขบัตรประชาชน
APP_BASE_URL=https://xxx.up.railway.app
OFFICE_NAME=สำนักงานอัยการพิเศษฝ่ายคดีล้มละลาย ๑
FUND_NAME=เงินกองกลางสำนักงาน
OCR_SPACE_API_KEY=เว้นว่างก่อนได้
SLIP_RECEIVER_KEYWORDS=ชื่อบัญชีรับเงิน เช่น พรสรวง,กองกลาง
ADMIN_USER_IDS=เว้นว่างช่วงทดสอบได้
```

## Webhook URL

ใส่ใน LINE Developers:

```text
https://xxx.up.railway.app/webhook
```

เปิด Use webhook = ON

## คำสั่งใน LINE กลุ่ม

```text
ช่วยเหลือ
เพิ่มสมาชิก รักษิน 500
รายชื่อ
เปิดรอบ กรกฎาคม 2569 ยกมา 17813.50
ลงทะเบียน รักษิน
ยอดของฉัน
ชำระเงิน
จ่ายแล้ว 500
รับเงิน รักษิน 500
รายจ่าย ค่าน้ำ 1816
สรุป
ทวงเงิน
รายงาน
```

## หมายเหตุ OCR

- ถ้าใส่ `OCR_SPACE_API_KEY` ระบบจะพยายามอ่านยอดจากรูปสลิป
- ถ้าอ่านได้และผู้ส่งลงทะเบียนชื่อไว้แล้ว ระบบจะบันทึกจ่ายอัตโนมัติ
- ถ้า OCR อ่านไม่ได้ ให้พิมพ์ `จ่ายแล้ว 500` เป็น fallback
- ระบบกันเลขอ้างอิงซ้ำแบบเบื้องต้นจากข้อความ OCR/รหัสรูป
