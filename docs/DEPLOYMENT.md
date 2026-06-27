# Deployment Guide

1. แตก ZIP แล้วอัปโหลดไฟล์ทั้งหมดทับ branch ที่ต้องการ เช่น `fundbot-v2`
2. ใน Railway เลือก branch นั้น
3. ตั้ง Variables:
   - DATABASE_URL
   - ADMIN_TOKEN
   - PUBLIC_BASE_URL
   - PROMPTPAY_ID
   - LINE_CHANNEL_SECRET
   - LINE_CHANNEL_ACCESS_TOKEN
4. แนะนำเพิ่ม Volume path `/data`
5. Deploy
6. เปิด `/health` ต้องได้ `{"ok": true}`
7. เปิด `/dashboard`, `/pay`, `/admin?token=...`

ถ้า deploy fail ให้ดู Build Logs ก่อนเสมอ
