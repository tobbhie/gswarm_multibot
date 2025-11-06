import os
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command


# --- Dummy server for Render: free tier health endpoint ---
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))  # Render injects PORT automatically
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[server] Dummy health server running on port {port}")
    server.serve_forever()


# Run dummy HTTP server in background thread so bot logic still runs
threading.Thread(target=start_dummy_server, daemon=True).start()


# ----------------- config -----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

USER_CONFIG_PATH = "/app/telegram-config.json"
GSWARM_CMD = "gswarm"  # ensure in PATH or use absolute path
SESSION_TIMEOUT = timedelta(minutes=10)


# ------------------------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# single active session state
active_session = {
    "chat_id": None,
    "proc": None,  # asyncio subprocess.Process
    "last_active": None,
}


# simple in-memory queue of tuples (chat_id, evm_address)
session_queue = []


# ----------------- helpers -----------------
async def send_safe(chat_id: int, text: str, **kwargs):
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except Exception:
        # swallow errors so supervisor keeps running
        print(f"[supervisor] failed to send message to {chat_id}")


async def stop_active_session(reason: str = "Session ended."):
    """Stop active gswarm process, notify user, and start next queued session."""
    global active_session

    # terminate process if running
    proc = active_session.get("proc")
    if proc:
        try:
            proc.terminate()  # graceful
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception as e:
            print("[supervisor] error stopping process:", e)

    chat_id = active_session.get("chat_id")
    if chat_id:
        await send_safe(
            chat_id,
            f"⚠️ {reason}\n\nIf you still want to monitor, please restart with /start.",
        )

    # clear active session
    active_session.update({"chat_id": None, "proc": None, "last_active": None})

    # start next queued user if any
    if session_queue:
        next_chat_id, next_evm = session_queue.pop(0)
        await send_safe(
            next_chat_id,
            "🚀 Your turn! Starting your GSwarm monitoring session now...",
        )
        # start in background
        asyncio.create_task(start_session(next_chat_id, next_evm))


async def session_timeout_checker():
    """Background task that checks for inactivity timeout."""
    while True:
        await asyncio.sleep(30)  # check frequently enough
        chat_id = active_session.get("chat_id")
        last = active_session.get("last_active")
        if chat_id and last:
            if datetime.utcnow() - last > SESSION_TIMEOUT:
                await stop_active_session(
                    "⏰ Session timed out after 10 minutes of inactivity."
                )


# ----------------- core: start / monitor -----------------
async def start_session(chat_id: int, evm_address: str):
    """Start gswarm for user if none active; otherwise queue user."""
    global active_session, session_queue

    # If active exists, queue and inform user their position
    if active_session["chat_id"]:
        position = len(session_queue) + 1
        session_queue.append((chat_id, evm_address))
        await send_safe(
            chat_id,
            (
                "⏳ Another session is currently active.\n"
                f"You've been added to the queue at position #{position}.\n"
                "You’ll be notified automatically when it’s your turn."
            ),
        )
        return

    # write config file (gswarm expects telegram-config.json name)
    cfg = {"botToken": BOT_TOKEN, "chatID": chat_id, "eoaAddress": evm_address}
    os.makedirs(os.path.dirname(USER_CONFIG_PATH), exist_ok=True)
    with open(USER_CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())

    # set env
    env = os.environ.copy()
    env["GSWARM_TELEGRAM_CONFIG_PATH"] = USER_CONFIG_PATH
    env["GSWARM_UPDATE_TELEGRAM_CONFIG"] = "false"
    env["GSWARM_EOA_ADDRESS"] = evm_address
    env["GSWARM_TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
    env["GSWARM_TELEGRAM_CHAT_ID"] = str(chat_id)

    # start as asyncio subprocess (non-blocking)
    try:
        proc = await asyncio.create_subprocess_exec(
            GSWARM_CMD,
            f"--telegram-config-path={USER_CONFIG_PATH}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=os.path.dirname(USER_CONFIG_PATH) or "/app",
        )

        active_session.update(
            {"chat_id": chat_id, "proc": proc, "last_active": datetime.utcnow()}
        )

        await send_safe(
            chat_id,
            (
                "✅ GSwarm monitoring service started successfully!\n"
                "You’ll receive updates directly in this chat.\n\n"
                "⚠️ Note: If you stay inactive for more than 10 minutes, the session will auto-stop."
            ),
        )

        # spawn log reader task
        asyncio.create_task(monitor_gswarm_output(proc, chat_id))
    except FileNotFoundError:
        await send_safe(
            chat_id,
            "❌ GSwarm binary not found in PATH. Ensure gswarm is installed in the container.",
        )
    except Exception as e:
        await send_safe(chat_id, f"❌ Failed to start GSwarm service: {e}")


async def monitor_gswarm_output(proc: asyncio.subprocess.Process, chat_id: int):
    """Asynchronously read gswarm stdout and react (auto /verify, detect success, handle errors)."""
    import re

    verify_re = re.compile(r"verify\s+code[:\s]+([A-Za-z0-9\-]+)", re.IGNORECASE)
    success_indicators = [
        "account successfully linked",
        "accounts linked successfully",
        "you can now use both discord and telegram",
    ]
    no_peerid_pattern = re.compile(r"no peer ids found for address", re.IGNORECASE)

    try:
        # read lines until process finishes
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            print(f"[gswarm] {line}", flush=True)

            # detect verify code and auto-send /verify <code>
            m = verify_re.search(line)
            if m:
                code = m.group(1)
                await send_safe(chat_id, f"/verify {code}")
                await send_safe(
                    chat_id,
                    f"✅ Auto-sent verification code: {code}",
                    parse_mode="Markdown",
                )

            # detect "no peer IDs found" and handle gracefully
            lower = line.lower()
            if no_peerid_pattern.search(lower):
                await send_safe(
                    chat_id,
                    (
                        "⚠️ No peer IDs found for this EOA on-chain.\n\n"
                        "This means the address isn’t registered with GSwarm.\n"
                        "Please send another valid EVM address to start monitoring."
                    ),
                )
                await stop_active_session("No peer IDs found for this address.")
                return

            # detect success messages to auto-close session and start next
            if any(ind in lower for ind in success_indicators):
                await send_safe(
                    chat_id,
                    "🎉 Account successfully linked with Discord! Ending session...",
                )
                await stop_active_session("✅ Account linked successfully. Session closed.")
                return
    except Exception as e:
        print("[supervisor] monitor_gswarm_output exception:", e, flush=True)
    finally:
        if active_session.get("proc") is proc:
            await stop_active_session("GSwarm process exited.")


# ----------------- Telegram handlers -----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Welcome! Send your EVM address (0x...) to start monitoring. This bot allows a single active session; others are queued."
    )


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    chat_id = message.chat.id
    if active_session["chat_id"] == chat_id:
        await stop_active_session("🛑 Session stopped by user.")
    else:
        # if user is queued, remove them
        removed = False
        for i, (qid, _) in enumerate(session_queue):
            if qid == chat_id:
                session_queue.pop(i)
                removed = True
                break
        if removed:
            await message.answer("🟡 You’ve been removed from the queue.")
        else:
            await message.answer("ℹ️ You don’t have an active or queued session.")


@dp.message()
async def handle_message(message: types.Message):
    """Handle all non-command messages intelligently."""
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # refresh active user's last_active
    if active_session["chat_id"] == chat_id:
        active_session["last_active"] = datetime.utcnow()

    # handle /verify <code> — forward to gswarm
    if text.lower().startswith("/verify"):
        proc = active_session.get("proc")
        if proc:
            try:
                # send the verify command into gswarm's stdin
                proc.stdin.write((text + "\n").encode())
                await proc.stdin.drain()
                await message.answer("✅ Verification command sent to GSwarm.")
            except Exception as e:
                await message.answer(f"⚠️ Failed to send verify command: {e}")
        else:
            await message.answer("ℹ️ No active session to verify. Please start with /start.")
        return

    # if message looks like an EVM address
    if text.startswith("0x") and len(text) == 42:
        await start_session(chat_id, text)
        return

    await message.answer(
        "⚠️ To start monitoring, send your EVM address (starting with 0x). Use /stop to end your active session."
    )


# ----------------- run -----------------
async def main():
    print("🚀 Supervisor bot running (single active session, queued).", flush=True)
    # start timeout checker
    asyncio.create_task(session_timeout_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())