import os
import json
from typing import Dict
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import SQLModel, Session, select
from telethon import TelegramClient

from app.database import create_db_and_tables, get_session, engine
from app.models import BotAdmin, WinnerClaim, DeliveryLog

app = FastAPI(title="Veniw Userbot Panel")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

active_clients: Dict[str, Dict] = {}

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    os.makedirs("sessions", exist_ok=True)
    SQLModel.metadata.create_all(engine)

@app.get("/", response_class=HTMLResponse)
def index_page(request: Request, session: Session = Depends(get_session)):
    bots = session.exec(select(BotAdmin)).all()
    bot_list = []
    for bot in bots:
        target_names = []
        try:
            channels = json.loads(bot.target_channels or "[]")
            for item in channels:
                if isinstance(item, dict):
                    target_names.append(item.get("name", str(item.get("id"))))
                elif isinstance(item, (int, str)):
                    target_names.append(str(item))
        except Exception:
            target_names = []

        bot_list.append({"info": bot, "target_names": target_names})

    return templates.TemplateResponse(request=request, name="index.html", context={"bot_list": bot_list})

@app.get("/edit/{bot_id}", response_class=HTMLResponse)
def edit_page(bot_id: int, request: Request, session: Session = Depends(get_session)):
    bot = session.get(BotAdmin, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot tidak ditemukan")
    claims = session.exec(select(WinnerClaim).where(WinnerClaim.bot_id == bot_id)).all()
    return templates.TemplateResponse(request=request, name="edit.html", context={"bot": bot, "claims": claims})

@app.post("/api/bot/register-step1")
async def register_step1(
    name: str = Form(...),
    phone_number: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
    gemini_api_key: str = Form(None),
    language: str = Form("ID"),
    db: Session = Depends(get_session)
):
    session_path = os.path.join("sessions", f"{phone_number.replace('+', '')}.session")
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone_code_hash = await client.send_code_request(phone_number)
        active_clients[phone_number] = {"client": client, "phone_code_hash": phone_code_hash.phone_code_hash}

        bot = db.exec(select(BotAdmin).where(BotAdmin.phone_number == phone_number)).first()
        if not bot:
            bot = BotAdmin(
                name=name, phone_number=phone_number, api_id=api_id, api_hash=api_hash,
                gemini_api_key=gemini_api_key, language=language, status="Pending_OTP", session_file=session_path
            )
            db.add(bot)
        else:
            bot.status = "Pending_OTP"
        db.commit()

        return {"status": "OTP_SENT", "phone_number": phone_number, "message": "Kode OTP dikirim"}
    else:
        await client.disconnect()
        return {"status": "ALREADY_AUTHORIZED"}

@app.post("/api/bot/verify-otp")
async def verify_otp(
    phone_number: str = Form(...),
    otp_code: str = Form(...),
    password_2fa: str = Form(None),
    db: Session = Depends(get_session)
):
    if phone_number not in active_clients:
        raise HTTPException(status_code=400, detail="Sesi registrasi kadaluarsa")

    session_data = active_clients[phone_number]
    client: TelegramClient = session_data["client"]
    phone_code_hash = session_data["phone_code_hash"]

    try:
        await client.sign_in(phone=phone_number, code=otp_code, phone_code_hash=phone_code_hash)
    except Exception as e:
        if "two-steps verification" in str(e).lower() and password_2fa:
            await client.sign_in(password=password_2fa)
        else:
            raise HTTPException(status_code=400, detail=f"Gagal Login: {str(e)}")

    bot = db.exec(select(BotAdmin).where(BotAdmin.phone_number == phone_number)).first()
    if bot:
        bot.status = "Active"
        db.commit()

    del active_clients[phone_number]
    await client.disconnect()

    return {"status": "SUCCESS", "message": "Bot Admin berhasil diotorisasi!"}

@app.post("/api/bot/update/{bot_id}")
def update_bot_config(
    bot_id: int,
    target_channels: str = Form(...),
    gemini_api_key: str = Form(None),
    gemini_prompt: str = Form(None),
    winner_message_template: str = Form(None),
    db: Session = Depends(get_session)
):
    bot = db.get(BotAdmin, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot tidak ditemukan")

    bot.gemini_api_key = gemini_api_key
    bot.target_channels = target_channels
    bot.gemini_prompt = gemini_prompt
    bot.winner_message_template = winner_message_template
    db.commit()

    return RedirectResponse(url=f"/edit/{bot_id}", status_code=303)

@app.get("/api/bot/{bot_id}/logs")
def get_bot_logs(bot_id: int, db: Session = Depends(get_session)):
    logs = db.exec(
        select(DeliveryLog)
        .where(DeliveryLog.bot_id == bot_id)
        .order_by(DeliveryLog.created_at.desc())
    ).all()
    
    logs_data = [
        {
            "id": log.id,
            "winner_username": log.winner_username,
            "announcement_link": log.announcement_link,
            "status": log.status,
            "detail_message": log.detail_message,
            "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }
        for log in logs
    ]
    return {"status": "SUCCESS", "logs": logs_data}

@app.get("/api/bot/{bot_id}/scan-channels")
async def scan_channels(bot_id: int, db: Session = Depends(get_session)):
    bot = db.get(BotAdmin, bot_id)
    if not bot or bot.status != "Active":
        raise HTTPException(status_code=400, detail="Bot tidak aktif")

    client = TelegramClient(bot.session_file, bot.api_id, bot.api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=400, detail="Sesi bot belum terotorisasi")

        channels = []
        async for dialog in client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                channels.append({
                    "id": dialog.id,
                    "name": dialog.name,
                    "username": getattr(dialog.entity, 'username', None)
                })

        return {"status": "SUCCESS", "channels": channels}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal scan: {str(e)}")
    finally:
        if client.is_connected():
            await client.disconnect()