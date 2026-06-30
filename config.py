import os
from datetime import timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load environment variables from .env file in the same directory as config.py
    config_dir = Path(__file__).resolve().parent
    env_path = config_dir / '.env'
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print(f"Loaded environment variables from {env_path}")
    else:
        print(f".env file not found at {env_path}, using system environment variables")
except ImportError:
    # If python-dotenv is not installed, just use the system environment variables
    print("python-dotenv not installed, using system environment variables")

# Configuration file for weather bot

# Telegram bot token
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# MongoDB connection
MONGO_URI = os.environ.get('MONGO_URI')

# Owner's Telegram ID
OWNER_ID = os.environ.get('OWNER_ID')

# URL prefix for the data source
URL_PREFIX = os.environ.get('URL_PREFIX')

# Web server port (for Render/other platforms, default to 10000 if not set)
PORT = int(os.environ.get('PORT', 10000))

# Indian timezone (UTC+5:30)
INDIAN_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

TARGET_MINUTE = 16
RETRY_MINUTES = [17, 18, 19, 20, 21, 22]

# Choose connection priority:
# "proxy" - Try proxy connections first, fallback to direct
# "direct" - Try direct connection first, fallback to proxies
CONNECTION_PRIORITY = "proxy"

# Maximum subscriptions per user
MAX_SUBSCRIPTIONS_PER_USER = 4
