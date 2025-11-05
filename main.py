import os
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import requests

# ----------------- Config -----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

PORT = int(os.environ.get("PORT", 10000))  # Render injects PORT automatically
HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "gswarm-multibot.onrender.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{HOSTNAME}{WEBHOOK_PATH}"

USER_CONFIG_PATH = "/app/telegram-config.json"
GSWARM_CMD = "gswarm"
SESSION_TIMEOUT = timedelta(minutes=10)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# This will hold the asyncio loop reference (set in main())
asyncio_loop = None

# ----------------- Global session state -----------------
active_session = {"chat_id": None, "proc": None, "last_active": None}
session_queue = []


# ----------------- Helper functions -----------------
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

    # Start next queued user
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


# ----------------- GSwarm process control -----------------
async def start_session(chat_id: int, evm_address: str):
    global active_session, session_queue

    if active_session["chat_id"]:
        position = len(session_queue) + 1
        session_queue.append((chat_id, evm_address))
        await send_safe(
            chat_id,
            f"⏳ Another session is active.\nYou're added to the queue at position #{position}."
        )
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
                # Send verification code directly to GSwarm stdin
                try:
                    if proc.stdin and proc.returncode is None:
                        verify_command = f"/verify {code}\n"
                        proc.stdin.write(verify_command.encode('utf-8'))
                        await proc.stdin.drain()
                        print(f"[supervisor] Auto-sent verification code to GSwarm: {code}", flush=True)
                        await send_safe(chat_id, f"✅ Auto-sent verification code: `{code}`", parse_mode="Markdown")
                    else:
                        await send_safe(chat_id, f"⚠️ Detected verification code: `{code}` but GSwarm process unavailable. Please send manually.", parse_mode="Markdown")
                except Exception as e:
                    print(f"[supervisor] Failed to auto-send verification code: {e}", flush=True)
                    await send_safe(chat_id, f"⚠️ Detected verification code: `{code}` but failed to send. Please send `/verify {code}` manually.", parse_mode="Markdown")

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


# ----------------- Telegram Handlers -----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("👋 Welcome! Send your EVM address (0x...) to start monitoring.")


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
    if active_session["chat_id"] == chat_id:
        active_session["last_active"] = datetime.utcnow()

    if text.lower().startswith("/verify"):
        proc = active_session.get("proc")
        if proc and proc.stdin:
            try:
                if proc.returncode is not None:
                    await message.answer("⚠️ GSwarm process has exited. Please restart with /start.")
                    return

                # 1️⃣ Disable Telegram webhook temporarily
                try:
                    resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
                    print(f"[webhook] Deleted webhook temporarily — status {resp.status_code}")
                except Exception as e:
                    print(f"[webhook] Failed to delete webhook: {e}")

                # 2️⃣ Forward verification command to GSwarm
                command = text + "\n"
                proc.stdin.write(command.encode("utf-8"))
                await proc.stdin.drain()
                print(f"[supervisor] Sent to GSwarm stdin: {command.strip()}", flush=True)
                await message.answer("✅ Verification command sent to GSwarm. Webhook temporarily disabled to allow verification.")

                # 3️⃣ Wait a few seconds to let GSwarm finish (optional safeguard)
                await asyncio.sleep(5)

                # 4️⃣ Re-enable webhook for continued bot operation
                try:
                    resp = requests.get(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}"
                    )
                    print(f"[webhook] Restored webhook — status {resp.status_code}")
                except Exception as e:
                    print(f"[webhook] Failed to restore webhook: {e}")

            except BrokenPipeError:
                await message.answer("⚠️ GSwarm process stdin is closed. The process may have exited.")
                print("[supervisor] BrokenPipeError: stdin closed", flush=True)
            except Exception as e:
                await message.answer(f"⚠️ Failed to send verify command: {e}")
                print(f"[supervisor] Error sending verify command: {e}", flush=True)
        else:
            await message.answer("ℹ️ No active session or GSwarm process not available.")
        return

    if text.startswith("0x") and len(text) == 42:
        await start_session(chat_id, text)
    else:
        await message.answer("⚠️ Send a valid EVM address (starting with 0x) to start.")


# ----------------- Webhook HTTP Handler -----------------
class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Health check (optional)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        # Only accept Telegram updates at the configured path
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('content-length', 0))
        body = self.rfile.read(content_length)

        # Parse incoming JSON to aiogram.types.Update and schedule it on the main asyncio loop
        try:
            payload = body.decode("utf-8")
            # model_validate_json is available on aiogram's pydantic models
            update_obj = types.Update.model_validate_json(payload)

            # schedule feeding the update into dispatcher on the running loop
            if asyncio_loop is None:
                # should not happen, but handle gracefully
                print("[webhook] asyncio loop not set - cannot process update")
            else:
                # fire-and-forget: we don't block the HTTP handler waiting for dp to finish
                asyncio.run_coroutine_threadsafe(dp.feed_update(bot, update_obj), asyncio_loop)

            self.send_response(200)
            self.end_headers()
        except Exception as e:
            print(f"[webhook] Failed to process update: {e}", flush=True)
            self.send_response(500)
            self.end_headers()


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"[server] Running webhook on port {PORT} — path {WEBHOOK_PATH}")
    server.serve_forever()


# ----------------- Main entry -----------------
async def on_startup():
    # set webhook so Telegram knows where to POST updates
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to {WEBHOOK_URL}", flush=True)
    # start session timeout checker
    asyncio.create_task(session_timeout_checker())


async def main():
    global asyncio_loop
    asyncio_loop = asyncio.get_running_loop()

    # Ensure webhook is registered and the background logic starts
    await on_startup()

    # start HTTP webhook server in a background thread so it's non-blocking
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # keep the main coroutine alive (dispatcher will be triggered by incoming webhook calls)
    print("🚀 Supervisor is running (webhook mode).", flush=True)
    await asyncio.Event().wait()  # never finish


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
