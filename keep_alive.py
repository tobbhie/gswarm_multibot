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

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
