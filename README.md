# FundBot Group UI

บอท LINE กลุ่มสำหรับเก็บเงินกองกลางรายเดือน แบบใช้งานง่าย มีหน้า Admin UI และคำสั่งใน LINE

## ใช้กับ Railway

Variables ที่ต้องมีใน Railway → web → Variables

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
LINE_CHANNEL_SECRET=จาก LINE Developers
LINE_CHANNEL_ACCESS_TOKEN=จาก LINE Developers
PROMPTPAY_ID=เบอร์/เลขพร้อมเพย์ที่รับเงิน
ADMIN_TOKEN=ตั้งรหัสเอง เช่น friend1234
PUBLIC_BASE_URL=https://web-production-1b96.up.railway.app
```

Webhook URL ใน LINE Developers:

```text
https://web-production-1b96.up.railway.app/webhook
```

Admin UI:

```text
https://web-production-1b96.up.railway.app/admin?token=รหัสที่ตั้งใน ADMIN_TOKEN
```

## คำสั่งใน LINE กลุ่ม

```text
เมนู
เพิ่มสมาชิก รักษิน 500
เปิดรอบ กรกฎาคม 2569 ยกมา 17813.50
ลงทะเบียน รักษิน
สถานะ
ชำระเงิน
จ่ายแล้ว 500
รายจ่าย ค่าน้ำ 350
สรุป
ใครยังไม่จ่าย
ทวงเงิน
รายงาน
```

## วิธีใช้งานจริงเร็วสุด

1. อัปไฟล์ทั้งหมดขึ้น GitHub ทับ repo เดิม
2. Railway จะ deploy อัตโนมัติ
3. ตั้ง Variables ให้ครบ
4. ใส่ Webhook URL ใน LINE Developers
5. ลากบอทเข้า LINE กลุ่ม
6. พิมพ์ `เมนู`

## หมายเหตุ

เวอร์ชันนี้เน้นใช้ได้จริงด่วน:
- มี QR PromptPay ให้สแกน
- รับรูปสลิปได้ แต่ยังให้พิมพ์ `จ่ายแล้ว 500` เพื่อยืนยันยอด
- OCR สลิปอัตโนมัติ 100% จะเพิ่มในเวอร์ชันถัดไป
