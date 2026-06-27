# FundBot UI v1

อัปเดต UI หน้ารายการสมาชิกและหน้าชำระเงิน

## สิ่งที่เพิ่ม
- Dashboard สวยขึ้นแบบการ์ด/สถานะสี
- หน้าเลือกชื่อมี radio วงกลมหน้าแต่ละชื่อ
- หน้าโอนเงินมี QR / Copy พร้อมเพย์ / Upload slip แบบเห็นชัด
- Preview รูปสลิปก่อนอัปโหลด
- สลิปเก็บแยกตามโฟลเดอร์เดือนใน `SLIP_STORAGE_DIR`

## Railway Variables ที่ต้องมี
- DATABASE_URL
- LINE_CHANNEL_SECRET
- LINE_CHANNEL_ACCESS_TOKEN
- PROMPTPAY_ID
- PUBLIC_BASE_URL (แนะนำใส่ URL Railway เช่น https://web-production-1b96.up.railway.app)
- ADMIN_TOKEN (ถ้าใช้หลังบ้าน)

## ใช้ใน LINE
พิมพ์ในกลุ่ม:
- `ส่งหน้าเก็บเงิน`
- `ชำระเงิน`
- `สรุป`
