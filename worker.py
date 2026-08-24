import asyncio
import json
import re
from sqlmodel import Session, select
from telethon import TelegramClient, events
from telethon.errors import (
    RPCError,
    UserIsBlockedError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    FloodWaitError,
)

from app.database import engine
from app.models import BotAdmin, DeliveryLog

WINNER_REGEX = r"@([a-zA-Z0-9_]{5,32})"

def normalize_id(chat_id) -> str:
    """Mengubah ID berbagai format (-100xxx, -xxx, xxx) menjadi string angka murni."""
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    elif s.startswith("-"):
        return s[1:]
    return s

async def get_channel_name(event, bot_info: BotAdmin) -> str:
    """Mencari nama grup/channel secara presisi dari event, entity, atau daftar scanned_channels."""
    chat = None
    try:
        chat = await event.get_chat()
    except Exception:
        chat = event.chat

    channel_name = getattr(chat, 'title', None) or getattr(chat, 'username', None)
    if not channel_name and hasattr(chat, 'first_name'):
        channel_name = chat.first_name

    if not channel_name and bot_info.scanned_channels:
        try:
            scanned_list = json.loads(bot_info.scanned_channels or "[]")
            incoming_norm = normalize_id(event.chat_id)
            for item in scanned_list:
                if normalize_id(item.get("id")) == incoming_norm:
                    channel_name = item.get("name")
                    break
        except Exception:
            pass

    return channel_name or "Grup/Channel Tanpa Nama"

async def process_winner_announcement(client: TelegramClient, bot_info: BotAdmin, event):
    # Fetch data bot terbaru dari database agar template selalu real-time
    with Session(engine) as session:
        db_bot = session.get(BotAdmin, bot_info.id)
        if db_bot:
            bot_info = db_bot

    text = event.raw_text or ""
    winners = re.findall(WINNER_REGEX, text)
    if not winners:
        print("ℹ️ Pesan terdeteksi di grup target, tetapi tidak mengandung tag @username.", flush=True)
        return

    channel_name = await get_channel_name(event, bot_info)

    chat = None
    try:
        chat = await event.get_chat()
    except Exception:
        chat = event.chat

    chat_username = getattr(chat, 'username', None) if chat else None
    
    if chat_username:
        announcement_link = f"https://t.me/{chat_username}/{event.id}"
    else:
        clean_id = normalize_id(event.chat_id)
        announcement_link = f"https://t.me/c/{clean_id}/{event.id}"

    template = bot_info.winner_message_template or "Selamat! Kamu menang event.\nLink: {link}"
    message_to_send = template.replace("{link}", announcement_link)

    me = await client.get_me()
    my_username = (me.username or "").lower()

    for username in set(winners):
        if username.lower() == my_username:
            print(f"⏭️ Melewati pengiriman ke @{username} (Username bot sendiri).", flush=True)
            continue

        try:
            await client.send_message(f"@{username}", message_to_send)
            status = "SUCCESS"
            detail = "Pesan berhasil terkirim ke PM."
            print(f"✅ [{channel_name}] Berhasil kirim PM ke @{username}", flush=True)

        except UserIsBlockedError:
            status = "FAILED"
            detail = "Gagal: User telah memblokir akun bot."
            print(f"❌ [{channel_name}] @{username} memblokir bot.", flush=True)

        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            status = "FAILED"
            detail = "Gagal: Privasi user membatasi PM (Hanya Kontak / Private)."
            print(f"❌ [{channel_name}] @{username} membatasi PM.", flush=True)

        except (UsernameInvalidError, UsernameNotOccupiedError):
            status = "FAILED"
            detail = "Gagal: Username Telegram tidak ditemukan / sudah tidak aktif."
            print(f"❌ [{channel_name}] @{username} username tidak valid.", flush=True)

        except FloodWaitError as e:
            status = "FAILED"
            detail = f"Gagal: Kena Limit Telegram (FloodWait {e.seconds} detik)."
            print(f"⚠️ [{channel_name}] FloodWait limit {e.seconds}s.", flush=True)

        except RPCError as e:
            status = "FAILED"
            detail = f"Telethon RPCError: {e.message}"
            print(f"❌ [{channel_name}] RPCError @{username}: {e.message}", flush=True)

        except Exception as e:
            status = "FAILED"
            detail = f"Error: {str(e)}"
            print(f"❌ [{channel_name}] Error @{username}: {str(e)}", flush=True)

        with Session(engine) as session:
            log_entry = DeliveryLog(
                bot_id=bot_info.id,
                winner_username=username,
                channel_name=channel_name,
                announcement_link=announcement_link,
                status=status,
                detail_message=detail
            )
            session.add(log_entry)
            session.commit()

async def start_worker():
    print("🤖 Memulai Bot Worker Listener...", flush=True)
    with Session(engine) as session:
        active_bots = session.exec(select(BotAdmin).where(BotAdmin.status == "Active")).all()

    if not active_bots:
        print("⚠️ Tidak ada bot berstatus 'Active'. Selesaikan verifikasi OTP di web dashboard.", flush=True)
        return

    clients = []
    for bot_info in active_bots:
        client = TelegramClient(bot_info.session_file, bot_info.api_id, bot_info.api_hash)
        await client.connect()

        print(f"🔄 Memuat dialog & memindai channel untuk '{bot_info.name}'...", flush=True)
        
        scanned_list = []
        async for dialog in client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                scanned_list.append({
                    "id": dialog.id,
                    "name": dialog.name,
                    "username": getattr(dialog.entity, 'username', None)
                })

        with Session(engine) as session:
            db_bot = session.get(BotAdmin, bot_info.id)
            if db_bot:
                db_bot.scanned_channels = json.dumps(scanned_list)
                session.commit()
                bot_info.scanned_channels = json.dumps(scanned_list)
                print(f"💾 Berhasil menyimpan {len(scanned_list)} channel/grup ke database!", flush=True)

        raw_targets = json.loads(bot_info.target_channels or "[]")
        target_norm_ids = []
        for t in raw_targets:
            tid = t['id'] if isinstance(t, dict) else t
            try:
                entity = await client.get_entity(tid)
                norm_id = normalize_id(entity.id)
                target_norm_ids.append(norm_id)
                print(f"📌 Target Terdaftar: {getattr(entity, 'title', tid)} (Norm ID: {norm_id})", flush=True)
            except Exception as e:
                print(f"⚠️ Gagal me-load target {tid}: {e}", flush=True)

        @client.on(events.NewMessage)
        async def handler(event, b=bot_info, c=client, targets=target_norm_ids):
            incoming_norm_id = normalize_id(event.chat_id)
            print(f"📩 [Pesan Masuk] Chat ID Raw: {event.chat_id} (Norm: {incoming_norm_id}) | Teks: {event.raw_text[:30]}...", flush=True)

            is_matched = not targets or incoming_norm_id in targets

            if is_matched:
                print("🎯 Match Target Channel/Grup! Memproses pengumuman pemenang...", flush=True)
                await process_winner_announcement(c, b, event)
            else:
                print(f"⏭️ Diabaikan (Chat Norm ID {incoming_norm_id} tidak ada di list target).", flush=True)

        clients.append(client)
        print(f"✅ Bot '{bot_info.name}' aktif mendengarkan pesan!", flush=True)

    await asyncio.gather(*(c.run_until_disconnected() for c in clients))

if __name__ == "__main__":
    asyncio.run(start_worker())