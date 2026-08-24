import asyncio
import json
import re
from sqlmodel import Session, select
from telethon import TelegramClient, events
from telethon.errors import RPCError

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

async def process_winner_announcement(client: TelegramClient, bot_info: BotAdmin, event):
    text = event.raw_text or ""
    winners = re.findall(WINNER_REGEX, text)
    if not winners:
        print("ℹ️ Pesan terdeteksi di grup target, tetapi tidak mengandung tag @username.", flush=True)
        return

    chat = await event.get_chat()
    chat_username = getattr(chat, 'username', None)
    
    if chat_username:
        announcement_link = f"https://t.me/{chat_username}/{event.id}"
    else:
        clean_id = normalize_id(chat.id)
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
            status, detail = "SUCCESS", "Pesan berhasil terkirim ke PM."
            print(f"✅ Berhasil kirim PM ke @{username}", flush=True)
        except RPCError as e:
            status, detail = "FAILED", f"RPCError: {str(e)}"
            print(f"❌ Gagal kirim PM ke @{username}: {str(e)}", flush=True)
        except Exception as e:
            status, detail = "FAILED", f"Error: {str(e)}"
            print(f"❌ Error kirim PM ke @{username}: {str(e)}", flush=True)

        with Session(engine) as session:
            log_entry = DeliveryLog(
                bot_id=bot_info.id,
                winner_username=username,
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
        
        # 1. Scan seluruh dialogs untuk UI Web
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
                print(f"💾 Berhasil menyimpan {len(scanned_list)} channel/grup ke database!", flush=True)

        # 2. Ambil target channel dari database & normalize ID
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

        # 3. Listener Pesan Baru
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