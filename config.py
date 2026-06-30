import os
from datetime import timezone, timedelta

# Configuration file for weather bot

# Telegram bot token
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# MongoDB connection
MONGO_URI = os.environ.get('MONGO_URI')

# Owner's Telegram ID
OWNER_ID = os.environ.get('OWNER_ID')

# URL prefix for the data source
URL_PREFIX = os.environ.get('URL_PREFIX')

# Indian timezone (UTC+5:30)
INDIAN_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

TARGET_MINUTE = 16
RETRY_MINUTES = [17, 18, 19, 20, 21, 22]

# Choose connection priority:
# "proxy" - Try proxy connections first, fallback to direct
# "direct" - Try direct connection first, fallback to proxies
CONNECTION_PRIORITY = "direct"

# Maximum subscriptions per user
MAX_SUBSCRIPTIONS_PER_USER = 4
