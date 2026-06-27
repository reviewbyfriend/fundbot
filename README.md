# FundBot MVP Clean

เวอร์ชันแก้พัง/รีเซ็ตให้เรียบง่ายสำหรับใช้งานด่วน:

- LINE Group Bot ส่งหน้าเก็บเงินแบบ Flex Message
- Dashboard เว็บ `/dashboard` อัปเดตทุก 3 วินาที
- หน้า `/pay` เลือกชื่อ → QR PromptPay → อัปโหลดสลิป
- เก็บสลิปแยกโฟลเดอร์เดือนใน `/data/slips/<เดือน>/`
- หลังบ้าน `/admin?token=ADMIN_TOKEN`
- Migration ป้องกันฐานข้อมูลเก่าคอลัมน์ไม่ตรง

## LINE commands

- `เมนู`
- `ส่งหน้าเก็บเงิน`
- `ชำระเงิน`
- `สรุป`
- `เปิดรอบ กรกฎาคม 2569`

## Railway Variables

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
PROMPTPAY_ID=เบอร์พร้อมเพย์
PUBLIC_BASE_URL=https://web-production-1b96.up.railway.app
ADMIN_TOKEN=ตั้งรหัสหลังบ้าน
SLIP_STORAGE_DIR=/data/slips
OCR_SPACE_API_KEY=เว้นว่างได้
```

## Railway

ให้ลบ Custom Start Command ให้โล่ง หรือใช้:

```text
sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
```
