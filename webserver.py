from flask import Flask
from threading import Thread
import config

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive"

def run():
    app.run(host='0.0.0.0', port=config.PORT)

def keep_alive():
    t = Thread(target=run)
    t.start()
