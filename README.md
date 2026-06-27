# FundBot Office Collection

เวอร์ชันนี้เน้นใช้งานตามโจทย์ล่าสุด:

- ส่งหน้าเก็บเงินใน LINE กลุ่มแบบครั้งเดียว
- รายการสมาชิก 7 คน + ยอดรายคน
- สถานะ `ชำระแล้ว / ยังไม่ได้ชำระ`
- ปุ่มชำระเงินเปิดหน้าเว็บให้เลือกชื่อ
- แสดง QR PromptPay / copy พร้อมเพย์
- อัปโหลดสลิปผ่านเว็บ
- Dashboard อัปเดตอัตโนมัติทุก 3 วินาที ไม่ต้องสแปม LINE กลุ่ม
- เก็บไฟล์สลิปแยกตามโฟลเดอร์เดือน เช่น `/data/slips/มิถุนายน_2569/`

## คำสั่งใน LINE

```text
เมนู
ส่งหน้าเก็บเงิน
ชำระเงิน
สถานะ
สรุป
เปิดรอบ กรกฎาคม 2569
เพิ่มสมาชิก ท่านรักษิน 500
```

## URL สำคัญ

```text
/dashboard
/pay
/admin?token=ADMIN_TOKEN
/webhook
```

## Variables ใน Railway

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
PROMPTPAY_ID=เบอร์พร้อมเพย์
ADMIN_TOKEN=ตั้งรหัสหลังบ้าน
PUBLIC_BASE_URL=https://web-production-1b96.up.railway.app
SLIP_STORAGE_DIR=/data/slips
OCR_SPACE_API_KEY=เว้นว่างได้
```

หมายเหตุ: ถ้าไม่ใส่ `OCR_SPACE_API_KEY` ระบบจะเก็บสลิปและเปลี่ยนสถานะตามชื่อที่เลือกทันที เพื่อให้ใช้งานด่วนได้ก่อน หากใส่ OCR key จะตรวจยอดจากรูปเบื้องต้นด้วย

## Railway Start Command

ถ้า Railway มี Custom Start Command ให้ใช้:

```text
sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
```

หรือปล่อยว่างให้ Dockerfile ทำงานแทน
