from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BANGKOK_TZ = ZoneInfo('Asia/Bangkok')

def now_bangkok() -> datetime:
    return datetime.now(BANGKOK_TZ)

def to_bangkok(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        # DB timestamps in this app are stored as UTC-naive.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BANGKOK_TZ)

def format_th(dt: datetime | None, fmt: str = '%d/%m/%Y %H:%M', default: str = '-') -> str:
    bkk = to_bangkok(dt)
    return bkk.strftime(fmt) if bkk else default

def format_now_th(fmt: str = '%d/%m/%Y %H:%M') -> str:
    return now_bangkok().strftime(fmt)
