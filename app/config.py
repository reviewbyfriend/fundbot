import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fundbot.db")
    PROMPTPAY_ID = os.getenv("PROMPTPAY_ID", "")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")
    BOT_NAME = os.getenv("BOT_NAME", "FundBot")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    SLIP_STORAGE_DIR = os.getenv("SLIP_STORAGE_DIR", "/data/slips")
    OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")

settings = Settings()
