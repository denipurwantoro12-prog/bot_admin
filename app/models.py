from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime, timezone, timedelta

# Fungsi pembantu untuk WIB (UTC+7)
def get_wib_time():
    return datetime.now(timezone(timedelta(hours=7))).replace(tzinfo=None)

class BotAdmin(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone_number: str = Field(index=True, unique=True)
    api_id: int
    api_hash: str
    gemini_api_key: Optional[str] = None
    language: str = "ID"
    status: str = "Inactive"
    target_channels: str = "[]"
    gemini_prompt: Optional[str] = None
    winner_message_template: Optional[str] = None
    session_file: str
    scanned_channels: Optional[str] = None
    channel_name: Optional[str] = None

class WinnerClaim(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="botadmin.id")
    winner_username: str
    announcement_link: str
    ewallet_type: Optional[str] = None
    account_name: Optional[str] = None
    account_number: Optional[str] = None
    proof_media_id: Optional[str] = None
    raw_pm_text: str
    created_at: datetime = Field(default_factory=get_wib_time)

class DeliveryLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="botadmin.id")
    winner_username: str
    channel_name: Optional[str] = None
    announcement_link: str
    status: str
    detail_message: str
    created_at: datetime = Field(default_factory=get_wib_time)