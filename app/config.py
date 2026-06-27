import os
from dataclasses import dataclass

@dataclass
class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./fundbot.db")
    LINE_CHANNEL_SECRET: str = os.getenv("LINE_CHANNEL_SECRET", "")
    LINE_CHANNEL_ACCESS_TOKEN: str = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    PROMPTPAY_ID: str = os.getenv("PROMPTPAY_ID", "")
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "admin123")
    SLIP_STORAGE_DIR: str = os.getenv("SLIP_STORAGE_DIR", "/data/slips")
    SIGNATURE_STORAGE_DIR: str = os.getenv("SIGNATURE_STORAGE_DIR", "/data/signatures")
    OCR_SPACE_API_KEY: str = os.getenv("OCR_SPACE_API_KEY", "")
    # Optional: LINE user/group/room id ที่จะให้บอทแจ้งเตือนสลิปรอตรวจ
    # ถ้าไม่ใส่ ระบบจะส่งแจ้งเตือนไปที่กลุ่ม/ห้อง LINE ล่าสุดที่คุยกับบอท
    ADMIN_NOTIFY_TARGET_ID: str = os.getenv("ADMIN_NOTIFY_TARGET_ID", "")
    BOT_NAME: str = os.getenv("BOT_NAME", "FundBot")

settings = Settings()
