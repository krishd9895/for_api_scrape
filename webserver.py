from flask import Flask
from threading import Thread
import config

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive"

def run():
    print(f"[webserver.py] Starting web server on 0.0.0.0:{config.PORT}")
    app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

def keep_alive():
    print(f"[webserver.py] keep_alive() called")
    t = Thread(target=run, daemon=True)
    t.start()
    print(f"[webserver.py] Web server thread started")
