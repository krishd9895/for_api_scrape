from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))  # Get PORT from environment, fallback to 5000
    app.run(host='0.0.0.0', port=port)
