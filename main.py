import os
import json
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from keep_alive import keep_alive


# ----------------- Config -----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

USER_CONFIG_PATH = "/app/telegram-config.json"
GSWARM_CMD = "gswarm"
SESSION_TIMEOUT = timedelta(minutes=10)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

active_session = {"chat_id": None, "proc": None, "last_active": None}
session_queue = []

# ----------------- Helpers -----------------
async def send_safe(chat_id: int, text: str, **kwargs):
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print(f"[supervisor] failed to send message to {chat_id}: {e}")

async def stop_active_session(reason: str = "Session ended."):
    global active_session
    proc = active_session.get("proc")

    if proc:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception as e:
            print("[supervisor] error stopping process:", e)

    chat_id = active_session.get("chat_id")
    if chat_id:
        await send_safe(chat_id, f"⚠️ {reason}\n\nIf you still want to monitor, please restart with /start.")

    active_session.update({"chat_id": None, "proc": None, "last_active": None})

    if session_queue:
        next_chat_id, next_evm = session_queue.pop(0)
        await send_safe(next_chat_id, "🚀 Your turn! Starting your GSwarm monitoring session now...")
        asyncio.create_task(start_session(next_chat_id, next_evm))

async def session_timeout_checker():
    while True:
        await asyncio.sleep(30)
        chat_id = active_session.get("chat_id")
        last = active_session.get("last_active")
        if chat_id and last and datetime.utcnow() - last > SESSION_TIMEOUT:
            await stop_active_session("⏰ Session timed out after 10 minutes of inactivity.")

# ----------------- GSwarm logic -----------------
async def start_session(chat_id: int, evm_address: str):
    global active_session, session_queue

    if active_session["chat_id"]:
        position = len(session_queue) + 1
        session_queue.append((chat_id, evm_address))
        await send_safe(chat_id, f"⏳ Another session is active.\nYou're added to the queue at position #{position}.")
        return

    cfg = {"botToken": BOT_TOKEN, "chatID": chat_id, "eoaAddress": evm_address}
    os.makedirs(os.path.dirname(USER_CONFIG_PATH), exist_ok=True)
    with open(USER_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    env = os.environ.copy()
    env["GSWARM_TELEGRAM_CONFIG_PATH"] = USER_CONFIG_PATH
    env["GSWARM_EOA_ADDRESS"] = evm_address
    env["GSWARM_TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
    env["GSWARM_TELEGRAM_CHAT_ID"] = str(chat_id)

    try:
        proc = await asyncio.create_subprocess_exec(
            GSWARM_CMD,
            f"--telegram-config-path={USER_CONFIG_PATH}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        active_session.update({"chat_id": chat_id, "proc": proc, "last_active": datetime.utcnow()})
        await send_safe(chat_id, "✅ GSwarm monitoring started! Updates will appear here.")
        asyncio.create_task(monitor_gswarm_output(proc, chat_id))
    except Exception as e:
        await send_safe(chat_id, f"❌ Failed to start GSwarm: {e}")

async def monitor_gswarm_output(proc, chat_id):
    import re
    verify_re = re.compile(r"verify\s+code[:\s]+([A-Za-z0-9\-]+)", re.IGNORECASE)
    success_indicators = ["account successfully linked", "accounts linked successfully"]
    no_peerid_pattern = re.compile(r"no peer ids found for address", re.IGNORECASE)

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="ignore").strip()
            print(f"[gswarm] {line}", flush=True)

            if verify_re.search(line):
                code = verify_re.search(line).group(1)
                try:
                    if proc.stdin and proc.returncode is None:
                        verify_command = f"/verify {code}\n"
                        proc.stdin.write(verify_command.encode('utf-8'))
                        await proc.stdin.drain()
                        print(f"[supervisor] Auto-sent verification code: {code}", flush=True)
                        await send_safe(chat_id, f"✅ Auto-sent verification code: `{code}`", parse_mode="Markdown")
                    else:
                        await send_safe(chat_id, f"⚠️ Found code `{code}` but process unavailable.", parse_mode="Markdown")
                except Exception as e:
                    print(f"[supervisor] Failed to auto-send verify: {e}", flush=True)
                    await send_safe(chat_id, f"⚠️ Detected code `{code}` but failed to send manually.", parse_mode="Markdown")

            if no_peerid_pattern.search(line):
                await send_safe(chat_id, "⚠️ No peer IDs found. Please use a valid EVM address.")
                await stop_active_session("No peer IDs found.")
                return

            if any(ind in line.lower() for ind in success_indicators):
                await send_safe(chat_id, "🎉 Account linked successfully! Ending session...")
                await stop_active_session("✅ Account linked successfully.")
                return
    except Exception as e:
        print("[supervisor] monitor_gswarm_output exception:", e, flush=True)
    finally:
        if active_session.get("proc") is proc:
            await stop_active_session("GSwarm process exited.")

# ----------------- Telegram handlers -----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    print(f"[handler] /start command received from {message.chat.id}", flush=True)
    try:
        await message.answer("👋 Welcome! Send your EVM address (0x...) to start monitoring.")
        print(f"[handler] /start response sent successfully", flush=True)
    except Exception as e:
        print(f"[handler] Error in /start handler: {e}", flush=True)
        raise

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    chat_id = message.chat.id
    if active_session["chat_id"] == chat_id:
        await stop_active_session("🛑 Session stopped by user.")
    else:
        removed = False
        for i, (cid, _) in enumerate(session_queue):
            if cid == chat_id:
                session_queue.pop(i)
                removed = True
                break
        await message.answer("🟡 Removed from queue." if removed else "ℹ️ No active or queued session.")

@dp.message()
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    print(f"[handler] Message received: chat_id={chat_id}, text={text[:50]}", flush=True)
    if active_session["chat_id"] == chat_id:
        active_session["last_active"] = datetime.utcnow()

    if text.lower().startswith("/verify"):
        proc = active_session.get("proc")
        if proc and proc.stdin and proc.returncode is None:
            try:
                proc.stdin.write((text + "\n").encode("utf-8"))
                await proc.stdin.drain()
                await message.answer("✅ Verification command sent to GSwarm.")
            except Exception as e:
                await message.answer(f"⚠️ Failed to send verify command: {e}")
        else:
            await message.answer("ℹ️ No active session or GSwarm process unavailable.")
        return

    if text.startswith("0x") and len(text) == 42:
        await start_session(chat_id, text)
    else:
        await message.answer("⚠️ Send a valid EVM address (starting with 0x) to start.")

# ----------------- Main -----------------
async def main():
    print("🚀 Starting bot in polling mode...", flush=True)
    print(f"Bot token present: {bool(BOT_TOKEN)}", flush=True)
    
    # Verify bot can connect to Telegram API
    try:
        bot_info = await bot.get_me()
        print(f"✅ Bot connected successfully: @{bot_info.username} (ID: {bot_info.id})", flush=True)
    except Exception as e:
        print(f"❌ Failed to connect to Telegram API: {e}", flush=True)
        raise
    
    # Start Flask health check server
    keep_alive()
    
    # Start session timeout checker
    asyncio.create_task(session_timeout_checker())
    
    # Start polling
    try:
        print("Starting Telegram bot polling...", flush=True)
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"Error in polling: {e}", flush=True)
        raise
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...", flush=True)
    except Exception as e:
        print(f"Fatal error: {e}", flush=True)
        raise
