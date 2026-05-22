from flask import Flask, jsonify
from threading import Thread
from datetime import datetime
import os

app = Flask(__name__)
start_time = datetime.now()

@app.route('/')
def home():
    uptime = datetime.now() - start_time
    return jsonify({
        "status": "alive",
        "bot": "OpenClaw Telegram Bot",
        "uptime": str(uptime),
        "message": "Bot 7/24 çalışıyor! 🤖"
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

def run():
    port = int(os.getenv("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
