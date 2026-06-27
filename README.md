# FundBot v4 Manual Approval

เวอร์ชันนี้ไม่ใช้ OCR อนุมัติอัตโนมัติแล้ว

Flow ใหม่:
1. สมาชิกเลือกชื่อและโอนเงิน
2. สมาชิกอัปโหลดสลิป
3. ระบบเปลี่ยนสถานะเป็น `รอตรวจสอบ`
4. ระบบแจ้งแอดมินใน LINE พร้อมปุ่ม `อนุมัติ` / `ไม่ผ่าน`
5. แอดมินกดอนุมัติแล้ว Dashboard + LINE Group อัปเดตสถานะเป็น `ชำระแล้ว`

## Railway Variables
ต้องมี:

```
DATABASE_URL=
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
PROMPTPAY_ID=
ADMIN_TOKEN=ตั้งรหัสหลังบ้าน เช่น friend123
PUBLIC_BASE_URL=https://web-production-xxxx.up.railway.app
```

ไม่จำเป็นต้องใช้แล้ว:

```
OCR_SPACE_API_KEY
```

ใส่ก็ได้ แต่ระบบ v4 จะไม่เอา OCR มาอนุมัติอัตโนมัติ

## ตัวเลือกเพิ่มเติม
ถ้าอยากให้แจ้งเตือนไป LINE ส่วนตัว/กลุ่มแอดมินโดยเฉพาะ ให้ใส่:

```
ADMIN_NOTIFY_TARGET_ID=
```

ถ้าไม่ใส่ ระบบจะส่งแจ้งเตือนไปที่กลุ่ม/ห้อง LINE ล่าสุดที่คุยกับบอท

## หน้าเว็บ
- Dashboard: `/dashboard`
- ชำระเงิน: `/pay`
- หลังบ้าน: `/admin?token=ADMIN_TOKEN`

## หมายเหตุเรื่องเก็บสลิป
ค่าเริ่มต้นเก็บที่ `/data/slips` และเปิดดูผ่าน `/slips/...`
ถ้าใช้ Railway ระยะยาว ควรผูก Volume กับ path `/data` เพื่อให้รูปสลิปไม่หายหลัง redeploy
