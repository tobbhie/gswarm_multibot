from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def index():
    return "✅ Bot is running (polling mode).", 200

@app.route('/health')
def health():
    return {"status": "ok", "mode": "polling"}, 200

@app.route('/webhook/<path:path>', methods=['GET', 'POST'])
def webhook_handler(path):
    # Catch-all for webhook requests (we're using polling, not webhooks)
    # Return 200 to acknowledge, but don't process
    return {"status": "polling mode active"}, 200

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
