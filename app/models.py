from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime

class BotAdmin(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone_number: str = Field(index=True, unique=True)
    api_id: int
    api_hash: str
    gemini_api_key: Optional[str] = None
    language: str = "ID"
    status: str = "Inactive"  # Status: Pending_OTP, Active, Inactive
    target_channels: str = "[]"  # Format JSON String
    gemini_prompt: Optional[str] = None
    winner_message_template: Optional[str] = None  # Template Pesan Claim Custom
    session_file: str
    scanned_channels: Optional[str] = None

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
    created_at: datetime = Field(default_factory=datetime.utcnow)

class DeliveryLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="botadmin.id")
    winner_username: str
    announcement_link: str
    status: str  # SUCCESS / FAILED
    detail_message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)