# Fund Group LINE Bot — บอทเก็บเงินกองใน LINE กลุ่ม

ตัวนี้เป็นโค้ดพร้อมรันสำหรับทำบอทคล้ายขุนทอง แต่ปรับให้เหมาะกับ “กองกลางสำนักงาน” ที่แต่ละคนยอดไม่เท่ากัน และมีรายจ่ายรายเดือน

## ทำอะไรได้แล้ว
- เชิญบอทเข้า LINE กลุ่มเดิมได้
- ตั้งแอดมินประจำกลุ่ม
- นำเข้ารายชื่อ/ยอดจาก Excel ตัวอย่าง
- เปิดรอบเดือน เช่น กรกฎาคม 2569
- สมาชิกลงทะเบียนจับคู่ชื่อกับ LINE ID
- สมาชิกกด/พิมพ์ `ชำระเงิน` เพื่อดู QR PromptPay ตามยอดของตัวเอง
- โอนแล้วพิมพ์ `จ่ายแล้ว 500` ระบบขึ้นสถานะจ่ายแล้ว
- แอดมินบันทึกแทนได้ เช่น `รับเงิน รักษิน 500`
- บันทึกรายจ่าย เช่น `รายจ่าย ค่าน้ำ 1816`
- สรุปยอด/ทวงเงิน/สร้างรายงาน Excel

> หมายเหตุ: เวอร์ชันนี้ “รับรูปสลิปได้” แต่ยังไม่ได้อ่านสลิปจริงอัตโนมัติ ต้องต่อ OCR/Slip Verify API เพิ่มในขั้นถัดไป เพราะการตรวจสลิปจริงต้องใช้บริการตรวจสอบธุรกรรมหรือ OCR ที่แม่นยำ

## 1) สร้าง LINE OA/Bot
1. เข้า LINE Developers
2. สร้าง Provider / Messaging API Channel
3. เปิด `Allow bot to join group chats`
4. เอา Channel Secret และ Channel Access Token มาใส่ `.env`

## 2) ตั้งค่าไฟล์ .env
คัดลอก `.env.example` เป็น `.env`

```env
LINE_CHANNEL_SECRET=xxx
LINE_CHANNEL_ACCESS_TOKEN=xxx
APP_BASE_URL=https://ชื่อโปรเจกต์.up.railway.app
PROMPTPAY_ID=เบอร์พร้อมเพย์หรือเลขบัตรประชาชน
ADMIN_SETUP_CODE=friend1234
```

## 3) รันในเครื่อง
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

ถ้าทดสอบในเครื่องให้ใช้ ngrok:
```bash
ngrok http 8000
```
แล้วเอา URL ไปตั้ง Webhook ใน LINE Developers:
```text
https://xxxxx.ngrok-free.app/callback
```

## 4) Deploy บน Railway
1. สร้างโปรเจกต์ใหม่บน Railway
2. Upload/เชื่อม GitHub repo นี้
3. ตั้ง Variables ตาม `.env.example`
4. Start command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
5. ตั้ง Webhook URL ใน LINE Developers:
```text
https://ชื่อโปรเจกต์.up.railway.app/callback
```

## 5) วิธีใช้ใน LINE กลุ่ม
เชิญบอทเข้ากลุ่มสำนักงานเดิม แล้วพิมพ์ตามนี้

```text
ตั้งแอดมิน friend1234
```

```text
นำเข้าไฟล์ตัวอย่าง
```

```text
เปิดรอบ กรกฎาคม 2569
```

ให้แต่ละคนพิมพ์ครั้งเดียว:
```text
ลงทะเบียน รักษิน
```

เวลาจะจ่าย:
```text
ชำระเงิน
```

โอนเสร็จ:
```text
จ่ายแล้ว 500
```

แอดมินเพิ่มรายจ่าย:
```text
รายจ่าย ค่าน้ำ 1816
```

ดูสรุป:
```text
สรุปยอด
```

ทวงเงิน:
```text
ทวงเงิน
```

สร้าง Excel:
```text
สร้างรายงาน
```

## ขั้นต่อไปที่ควรต่อเพิ่ม
- อ่านสลิปจริงอัตโนมัติ: ต่อ OCR หรือ Slip Verify API
- ส่งข้อความทวงแบบแยกรายคนผ่าน push message
- ทำหน้าเว็บแอดมินสำหรับแก้รายชื่อ/ยอด
- ทำรายงาน Word/PDF ให้หน้าตาเหมือนฟอร์มเดิมของสำนักงาน
