from flask import Flask, request, jsonify
from threading import Thread
import os
import asyncio
from aiogram import types

app = Flask(__name__)

# These will be set by main.py
bot = None
dp = None
asyncio_loop = None

@app.route('/')
def index():
    return "✅ Bot is running (webhook mode).", 200

@app.route('/health')
def health():
    return {"status": "ok", "mode": "webhook"}, 200

@app.route('/webhook/<path:path>', methods=['GET', 'POST'])
def webhook_handler(path):
    if request.method == 'GET':
        return {"status": "webhook endpoint active"}, 200
    
    if request.method == 'POST':
        try:
            # Get the update from Telegram
            update_data = request.get_json()
            if not update_data:
                print("[webhook] Received empty POST request", flush=True)
                return jsonify({"status": "no data"}), 400
            
            # Parse and process the update
            if asyncio_loop and bot and dp:
                # Parse update using Pydantic model_validate
                update_obj = types.Update.model_validate(update_data)
                # Schedule processing on the asyncio loop
                asyncio.run_coroutine_threadsafe(
                    dp.feed_update(bot, update_obj),
                    asyncio_loop
                )
                return jsonify({"status": "ok"}), 200
            else:
                print("[webhook] ⚠️ Bot or dispatcher not initialized", flush=True)
                return jsonify({"status": "not ready"}), 503
        except Exception as e:
            print(f"[webhook] Error processing update: {e}", flush=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    
    return jsonify({"status": "method not allowed"}), 405

def run():
    port = int(os.environ.get("PORT", 8080))
    print(f"[server] Flask server running on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

def init_webhook(bot_instance, dispatcher_instance, loop):
    global bot, dp, asyncio_loop
    bot = bot_instance
    dp = dispatcher_instance
    asyncio_loop = loop
    print("[webhook] Webhook handler initialized", flush=True)
