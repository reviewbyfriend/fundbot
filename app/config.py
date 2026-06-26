from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    line_channel_access_token: str = Field(default="", alias="LINE_CHANNEL_ACCESS_TOKEN")
    line_channel_secret: str = Field(default="", alias="LINE_CHANNEL_SECRET")
    promptpay_id: str = Field(default="", alias="PROMPTPAY_ID")
    database_url: str = Field(default="sqlite:///./fundbot.db", alias="DATABASE_URL")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    office_name: str = Field(default="สำนักงาน", alias="OFFICE_NAME")
    fund_name: str = Field(default="เงินกองกลาง", alias="FUND_NAME")
    ocr_space_api_key: str = Field(default="", alias="OCR_SPACE_API_KEY")
    slip_receiver_keywords: str = Field(default="", alias="SLIP_RECEIVER_KEYWORDS")

    @property
    def admin_ids(self) -> set[str]:
        return {x.strip() for x in self.admin_user_ids.split(",") if x.strip()}

    class Config:
        env_file = ".env"
        populate_by_name = True

settings = Settings()
