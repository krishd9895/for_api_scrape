import os
import requests
import telebot
from datetime import timezone, timedelta
import time
import threading
import asyncio
import aiohttp
from requests.exceptions import RequestException, ProxyError, ConnectTimeout
from datetime import datetime, timedelta
from uuid import uuid4
import re
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from webserver import keep_alive

# Telegram bot token (replace with your bot token)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# MongoDB connection
MONGO_URI = os.environ.get('MONGO_URI')
if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable is required")

# Initialize MongoDB client
mongo_client = None
db = None


def init_mongodb():
    global mongo_client, db
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # Test the connection
        mongo_client.admin.command('ping')
        db = mongo_client.weather_bot
        write_log("INFO", "MongoDB connection established successfully")
        return True
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        write_log("ERROR", f"Failed to connect to MongoDB: {e}")
        return False


# File path for logs only (other data now in MongoDB)
LOG_FILE = "logs.txt"

# Owner's Telegram ID (replace with your Telegram ID)
OWNER_ID = os.environ.get('OWNER_ID')

# URL prefix for the data source
URL_PREFIX = os.environ.get('URL_PREFIX')

# Maximum subscriptions per user
MAX_SUBSCRIPTIONS_PER_USER = 4

# Indian timezone (UTC+5:30)
INDIAN_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

TARGET_MINUTE = 16
RETRY_MINUTES = [17, 18, 19, 20, 21, 22]

MAX_LOG_LINES = 4000

# Enhanced logging function with error handling
def write_log(level, message):
    try:
        # Use Indian timezone for timestamp
        timestamp = datetime.now(INDIAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S IST")
        log_entry = f"{timestamp} - {level.upper()} - {message}\n"

        lines = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # Append the new log entry
        lines.append(log_entry)

        # Keep only the last MAX_LOG_LINES lines
        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]

        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)

    except Exception as e:
        print(f"LOG ERROR: {e} | Original message: {level.upper()} - {message}")

# Function to delete previous checking time log and append new one at the end
def replace_last_checking_log(message):
    try:
        timestamp = datetime.now(INDIAN_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M:%S IST")
        new_log_line = f"{timestamp} - INFO - {message}\n"

        # Read all existing logs
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                lines = f.readlines()

            # Remove the last "Checking Indian time" log if it exists
            for i in range(len(lines) - 1, -1, -1):
                if "Checking Indian time:" in lines[i]:
                    lines.pop(i)  # Delete the line
                    break

            # Append new checking time log at the end
            lines.append(new_log_line)

            # Write back to file
            with open(LOG_FILE, "w", encoding='utf-8') as f:
                f.writelines(lines)
        else:
            # If log file doesn't exist, create it with the new log
            with open(LOG_FILE, "w", encoding='utf-8') as f:
                f.write(new_log_line)

    except Exception as e:
        # Fallback to regular logging if replacement fails
        write_log("INFO", message)
        print(f"LOG REPLACE ERROR: {e}")


# Load subscriptions from MongoDB
def load_subscriptions():
    try:
        if db is None:
            write_log("ERROR", "MongoDB not initialized")
            return {}

        subscriptions = {}
        cursor = db.subscriptions.find()
        for doc in cursor:
            chat_id = doc['chat_id']
            suffixes = doc.get('suffixes', [])
            # Handle old format conversion
            if isinstance(suffixes, str):
                suffixes = [suffixes]
            subscriptions[chat_id] = suffixes

        return subscriptions
    except Exception as e:
        write_log("ERROR", f"Error loading subscriptions from MongoDB: {e}")
        return {}


def save_subscriptions(subscriptions):
    try:
        if db is None:
            write_log("ERROR", "MongoDB not initialized")
            return

        # Clear existing subscriptions
        db.subscriptions.delete_many({})

        # Insert new subscriptions
        for chat_id, suffixes in subscriptions.items():
            if suffixes:  # Only save if user has subscriptions
                db.subscriptions.insert_one({
                    'chat_id':
                    chat_id,
                    'suffixes':
                    suffixes,
                    'updated_at':
                    datetime.now(INDIAN_TIMEZONE)
                })

        write_log("INFO", "Subscriptions saved to MongoDB successfully")
    except Exception as e:
        write_log("ERROR", f"Error saving subscriptions to MongoDB: {e}")


# Load proxies from MongoDB
def load_proxies():
    try:
        if db is None:
            write_log("ERROR", "MongoDB not initialized")
            return {"proxies": []}

        # Get proxies document
        proxies_doc = db.proxies.find_one({'_id': 'proxy_config'})
        if proxies_doc:
            return {
                'proxies': proxies_doc.get('proxies', [])
            }
        else:
            # Create default document if it doesn't exist
            default_config = {"proxies": []}
            db.proxies.insert_one({
                '_id': 'proxy_config',
                'proxies': [],
                'updated_at': datetime.now(INDIAN_TIMEZONE)
            })
            return default_config
    except Exception as e:
        write_log("ERROR", f"Error loading proxies from MongoDB: {e}")
        return {"proxies": []}


def save_proxies(proxies_data):
    try:
        if db is None:
            write_log("ERROR", "MongoDB not initialized")
            return

        # Update or insert proxies configuration
        db.proxies.replace_one({'_id': 'proxy_config'}, {
            '_id': 'proxy_config',
            'proxies': proxies_data.get('proxies', []),
            'updated_at': datetime.now(INDIAN_TIMEZONE)
        },
                               upsert=True)

        write_log("INFO", "Proxies saved to MongoDB successfully")
    except Exception as e:
        write_log("ERROR", f"Error saving proxies to MongoDB: {e}")


# Convert 24-hour time to 12-hour AM/PM format with date
def convert_to_12hour(datetime_str):
    try:
        # Handle full date-time string (e.g., "27/05/2025 21:00" or "27/05/2025 23:00")
        if ' ' in datetime_str and ':' in datetime_str:
            date_part, time_part = datetime_str.split(' ', 1)

            # Parse hour and minute
            if ':' in time_part:
                hour, minute = map(int, time_part.split(':'))

                # Convert to 12-hour format
                if hour == 0:
                    time_12h = f"12:{minute:02d} AM"
                elif hour < 12:
                    time_12h = f"{hour}:{minute:02d} AM"
                elif hour == 12:
                    time_12h = f"12:{minute:02d} PM"
                else:
                    time_12h = f"{hour-12}:{minute:02d} PM"

                return f"{date_part} {time_12h}"
            else:
                return datetime_str
        elif ':' in datetime_str and '/' not in datetime_str:
            # If no space but has colon, assume it's just time
            hour, minute = map(int, datetime_str.split(':'))
            if hour == 0:
                return f"12:{minute:02d} AM"
            elif hour < 12:
                return f"{hour}:{minute:02d} AM"
            elif hour == 12:
                return f"12:{minute:02d} PM"
            else:
                return f"{hour-12}:{minute:02d} PM"
        else:
            return datetime_str
    except:
        return datetime_str  # Return original if conversion fails


# Flexible field matching function
def match_field_type(key):
    """
    Match field types using approximate/partial string matching.
    Returns the field type and appropriate emoji/formatting.
    """
    key_lower = key.lower()

    # Location matching (AWS Location, Location, Station Location, etc.)
    if any(word in key_lower for word in ['location', 'station', 'site']):
        return 'location', '📍'

    # Mandal/Area matching
    if any(word in key_lower
           for word in ['mandal', 'area']):
        return 'mandal', '🏘️'

    # Last Updated matching - Check for "updated" or "last" specifically first
    if any(word in key_lower for word in ['updated', 'last']):
        return 'updated', '🕐'

    # Date matching - Check for "date" fields (including "Date & Time")
    if any(word in key_lower for word in ['date', 'day']):
        return 'date', '📅'

    # Generic time matching - only if not caught by above
    if 'time' in key_lower and not any(
            word in key_lower for word in ['date', 'updated', 'last']):
        return 'updated', '🕐'

    # Rainfall matching
    if any(word in key_lower
           for word in ['rainfall', 'rain']):
        return 'rainfall', '🌧️'

    # Temperature matching
    if any(word in key_lower
           for word in ['temperature', 'temp']):
        return 'temperature', '🌡️'

    # Humidity matching
    if any(word in key_lower for word in ['humidity', 'rh']):
        return 'humidity', '💧'

    # Wind matching
    if any(word in key_lower for word in ['wind', 'breeze']):
        return 'wind', '🌬️'

    # Pressure matching
    if any(word in key_lower for word in ['pressure', 'barometric']):
        return 'pressure', '📊'

    # Default
    return 'other', ''


# Fetch table data from URL with direct request (no proxy)
def fetch_table_data_direct(url):
    try:
        response = requests.get(url, timeout=10)
        html = response.text

        # Check for invalid range error
        if "Invalid Range" in html:
            return None, "Invalid station ID - station does not exist"

        table_start = html.find('<table')
        table_end = html.find('</table>') + len('</table>')
        if table_start == -1 or table_end == -1:
            return None, "Table not found in HTML"

        table_html = html[table_start:table_end]
        rows = [
            row.strip() for row in table_html.split('<tr>')[1:]
            if '</tr>' in row
        ]
        table_data = []

        for row in rows:
            cells = [
                cell.strip() for cell in row.split('<td>')[1:]
                if '</td>' in cell
            ]
            if len(cells) >= 2:
                key = cells[0].split('</td>')[0].replace(
                    '<span class="style46">', '').replace('</span>',
                                                          '').strip()
                value = cells[1].split('</td>')[0]
                while '<' in value and '>' in value:
                    start = value.find('<')
                    end = value.find('>', start) + 1
                    if end == 0:
                        break
                    value = value[:start] + value[end:]
                value = value.strip()

                # Skip Latitude and Longitude entries
                if key.lower() in ['latitude', 'longitude']:
                    continue

                table_data.append((key, value))

        return table_data, None
    except RequestException as e:
        return None, str(e)


def fetch_table_data(url, proxy, scheme):
    try:
        proxy_url = f"{scheme}://{proxy.split(':')[0]}:{proxy.split(':')[1]}"
        response = requests.get(url,
                                proxies={
                                    "http": proxy_url,
                                    "https": proxy_url
                                },
                                timeout=10)
        html = response.text

        # Check for invalid range error
        if "Invalid Range" in html:
            return None, "Invalid station ID - station does not exist"

        table_start = html.find('<table')
        table_end = html.find('</table>') + len('</table>')
        if table_start == -1 or table_end == -1:
            return None, "Table not found in HTML"

        table_html = html[table_start:table_end]
        rows = [
            row.strip() for row in table_html.split('<tr>')[1:]
            if '</tr>' in row
        ]
        table_data = []

        for row in rows:
            cells = [
                cell.strip() for cell in row.split('<td>')[1:]
                if '</td>' in cell
            ]
            if len(cells) >= 2:
                key = cells[0].split('</td>')[0].replace(
                    '<span class="style46">', '').replace('</span>',
                                                          '').strip()
                value = cells[1].split('</td>')[0]
                while '<' in value and '>' in value:
                    start = value.find('<')
                    end = value.find('>', start) + 1
                    if end == 0:
                        break
                    value = value[:start] + value[end:]
                value = value.strip()

                # Skip Latitude and Longitude entries
                if key.lower() in ['latitude', 'longitude']:
                    continue

                table_data.append((key, value))

        return table_data, None
    except (ProxyError, ConnectTimeout, RequestException) as e:
        return None, str(e)


# Escape HTML special characters
def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# Format table data for Telegram message with flexible field matching
def format_table_data(table_data, suffix=None):
    if not table_data:
        return "No table data extracted"

    message = "🌦️ <b>Weather Update</b>"
    if suffix:
        message += f" - Station {suffix}"
    message += "\n\n"

    for key, value in table_data:
        # Escape HTML characters in key and value
        key = escape_html(str(key))
        value = escape_html(str(value))

        # Get field type and emoji using flexible matching
        field_type, emoji = match_field_type(key)

        # Convert time format for updated fields
        if field_type == 'updated' and ':' in value:
            value = convert_to_12hour(value)

        # Format based on field type
        if field_type == 'location':
            message += f"{emoji} <b>Location:</b> {value}\n"
        elif field_type == 'mandal':
            message += f"{emoji} <b>Mandal:</b> {value}\n"
        elif field_type == 'date':
            message += f"{emoji} <b>Date:</b> {value}\n"
        elif field_type == 'updated':
            message += f"{emoji} <b>Last Updated:</b> {value}\n"
        elif field_type == 'rainfall':
            message += f"{emoji} <b>{key}:</b> {value}\n"
        elif field_type == 'temperature':
            # Add °C if not present and value is numeric
            if value.replace('.', '').replace(
                    '-', '').isdigit() and '°' not in value:
                message += f"{emoji} <b>{key}:</b> {value}°C\n"
            else:
                message += f"{emoji} <b>{key}:</b> {value}\n"
        elif field_type == 'humidity':
            message += f"{emoji} <b>{key}:</b> {value}\n"
        elif field_type == 'wind':
            message += f"{emoji} <b>{key}:</b> {value}\n"
        elif field_type == 'pressure':
            message += f"{emoji} <b>{key}:</b> {value}\n"
        else:
            # Use emoji if available, otherwise just bold formatting
            if emoji:
                message += f"{emoji} <b>{key}:</b> {value}\n"
            else:
                message += f"<b>{key}:</b> {value}\n"

    return message


# Check proxies and fetch data for a user
def check_proxies_and_fetch(url,
                            chat_id,
                            message_id=None,
                            is_manual=False,
                            suffix=None):
    proxies_data = load_proxies()

    # Check if proxies data is valid
    if not proxies_data or "proxies" not in proxies_data or not isinstance(
            proxies_data["proxies"], list):
        error_msg = "Proxies configuration is invalid or empty."
        write_log("ERROR", "Proxies configuration is empty or invalid structure")

        if str(chat_id) != OWNER_ID:
            bot.send_message(
                OWNER_ID,
                "🚨 Proxies configuration is invalid or empty. Check MongoDB proxy configuration."
            )

        # Try direct request as fallback
        write_log("INFO", "Attempting direct request without proxy")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data,
                                          chat_id,
                                          message_id,
                                          parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS")
            return True
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            return False

    proxies = proxies_data["proxies"]
    success = False

    # Send acknowledgment message for manual fetch
    if is_manual and not message_id:
        ack_msg = bot.send_message(chat_id,
                                   "🔄 Fetching latest weather data...")
        message_id = ack_msg.message_id

    # Check if there are any proxies to use
    if not proxies:
        error_msg = "No proxies available in configuration."
        write_log("ERROR", "Proxies list is empty")

        # Try direct request as fallback
        write_log("INFO", "No proxies available, attempting direct request")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data,
                                          chat_id,
                                          message_id,
                                          parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS")
            return True
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            return False

    # Try each proxy
    for proxy_entry in proxies:
        try:
            if ':' not in proxy_entry:
                write_log("ERROR", f"Invalid proxy format: {proxy_entry}")
                continue

            proxy, scheme = proxy_entry.rsplit(':', 1)
            table_data, error = fetch_table_data(url, proxy, scheme)

            if table_data:
                success = True
                formatted_data = format_table_data(table_data, suffix)

                if message_id:
                    try:
                        bot.edit_message_text(formatted_data,
                                              chat_id,
                                              message_id,
                                              parse_mode='HTML')
                    except Exception as e:
                        bot.send_message(chat_id,
                                         formatted_data,
                                         parse_mode='HTML')
                else:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')

                write_log("INFO", f"Proxy {proxy} ({scheme}) SUCCESS")
                return True
            else:
                write_log("ERROR", f"Proxy {proxy} ({scheme}) failed: {error}")
        except Exception as e:
            write_log("ERROR", f"Error processing proxy {proxy_entry}: {e}")
            continue

    # If all proxies failed, try direct request
    if not success:
        write_log("INFO",
                  "All proxies failed, attempting direct request as fallback")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data,
                                          chat_id,
                                          message_id,
                                          parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS (fallback)")
            return True
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            return False
    return False


def check_indian_time_and_update():
    try:
        # Get current time in Indian timezone
        indian_time = datetime.now(INDIAN_TIMEZONE)
        current_minute = indian_time.minute

        # Replace the last checking time log instead of appending
        replace_last_checking_log(
            f"Checking Indian time: {indian_time.strftime('%Y-%m-%d %H:%M:%S IST')}, minute: {current_minute}"
        )

        if current_minute == TARGET_MINUTE:
            write_log(
                "INFO",
                f"Indian time minute is {TARGET_MINUTE}, running automatic /rf command")
            subscriptions = load_subscriptions()

            if not subscriptions:  
                write_log("INFO",
                          "No subscriptions found for automatic update")
                return

            for chat_id, suffixes in subscriptions.items():
                try:
                    # Handle both old format (string) and new format (list)
                    if isinstance(suffixes, str):
                        suffixes = [suffixes]

                    write_log(
                        "INFO",
                        f"Running automatic /rf for user {chat_id} with {len(suffixes)} subscription(s)"
                    )

                    all_failed = True  # Assume all requests will fail initially

                    # Use the multi-station concurrent function for faster processing
                    if len(suffixes) > 1:
                        # Process all stations concurrently
                        if fetch_multiple_stations_concurrent(chat_id, suffixes):
                            all_failed = False  # At least one request succeeded
                    else:
                        # For a single subscription, use the regular concurrent function
                        url = f"{URL_PREFIX}{suffixes[0]}"
                        if check_proxies_and_fetch_concurrent(url, chat_id, suffix=suffixes[0]):
                            all_failed = False  # At least one request succeeded

                    # Retry mechanism for proxy failures
                    if all_failed:
                        write_log(
                            "INFO",
                            f"All proxies and direct connection failed at {TARGET_MINUTE} minutes, implementing proxy retry mechanism"
                        )
                        
                        # Reload proxies to get any updated ones
                        proxies_data = load_proxies()
                        if proxies_data and "proxies" in proxies_data and isinstance(
                                proxies_data["proxies"], list) and proxies_data["proxies"]:
                            
                            # Try up to 3 times with exponential backoff, but only with proxies
                            for retry_attempt in range(3):
                                # Calculate backoff time: 10, 20, 40 seconds
                                backoff_time = 10 * (2 ** retry_attempt)
                                write_log("INFO", f"Proxy retry attempt {retry_attempt+1}/3 after {backoff_time} seconds")
                                time.sleep(backoff_time)
                                
                                # Use the async functions to retry with proxies only
                                retry_success = False
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                
                                try:
                                    if len(suffixes) > 1:
                                        # For multiple stations, prepare URLs dictionary
                                        urls = {suffix: f"{URL_PREFIX}{suffix}" for suffix in suffixes}
                                        results = loop.run_until_complete(
                                            fetch_multiple_urls_async(urls, proxies_data["proxies"]))
                                        
                                        # Process results
                                        success_count = 0
                                        for suffix, (table_data, result) in results.items():
                                            if table_data:
                                                formatted_data = format_table_data(table_data, suffix)
                                                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                                                write_log("INFO", f"Proxy {result} SUCCESS for station {suffix} (retry {retry_attempt+1})")
                                                success_count += 1
                                        
                                        retry_success = success_count > 0
                                    else:
                                        # For a single subscription
                                        url = f"{URL_PREFIX}{suffixes[0]}"
                                        table_data, result = loop.run_until_complete(
                                            fetch_with_proxies_async(url, proxies_data["proxies"]))
                                        
                                        if table_data:
                                            formatted_data = format_table_data(table_data, suffixes[0])
                                            bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                                            write_log("INFO", f"Proxy {result} SUCCESS (retry {retry_attempt+1})")
                                            retry_success = True
                                except Exception as e:
                                    write_log("ERROR", f"Error in proxy retry attempt {retry_attempt+1}: {e}")
                                finally:
                                    loop.close()
                                
                                # If successful, break out of retry loop
                                if retry_success:
                                    write_log("INFO", f"Proxy retry attempt {retry_attempt+1} succeeded")
                                    break
                                elif retry_attempt == 2:  # Last attempt
                                    write_log("INFO", "All proxy retry attempts failed")
                        else:
                            write_log("INFO", "No valid proxies available for retry")
                        
                        # Also check at specific minutes as a fallback
                        indian_time = datetime.now(INDIAN_TIMEZONE)
                        if indian_time.minute in RETRY_MINUTES:
                            write_log("INFO", f"Additional retry at minute {indian_time.minute}")
                            # Use the multi-station concurrent function for retry
                            if len(suffixes) > 1:
                                # Process all stations concurrently
                                fetch_multiple_stations_concurrent(chat_id, suffixes)
                            else:
                                # For a single subscription, use the regular concurrent function
                                url = f"{URL_PREFIX}{suffixes[0]}"
                                check_proxies_and_fetch_concurrent(url, chat_id, suffix=suffixes[0])

                except Exception as e:
                    write_log(
                        "ERROR",
                        f"Error in automatic update for user {chat_id}: {e}")
                    # Continue with next user even if one fails
                    continue

            write_log("INFO", "Completed automatic /rf command for all users")

    except Exception as e:
        write_log("ERROR", f"Error in check_indian_time_and_update: {e}")


# Run Indian time checker in a separate thread
def run_indian_time_checker():
    write_log(
        "INFO",
        f"Starting Indian time checker - checking every minute for minute {TARGET_MINUTE}")
    while True:
        try:
            check_indian_time_and_update()
            time.sleep(60)  # Check every minute
        except Exception as e:
            write_log("ERROR", f"Indian time checker error: {e}")
            time.sleep(60)  # Continue running even if there's an error


# Command: /help - Show help text (different for users and owner)
@bot.message_handler(commands=['help'])
def send_help(message):
    try:
        chat_id = str(message.chat.id)
        is_owner = chat_id == OWNER_ID

        if is_owner:
            help_msg = f"""
🌦️ <b>Weather Update Bot - Owner Help</b>

<b>User Commands:</b>
• <code>/start</code> - Start the bot and see welcome message
• <code>/help</code> - Show this help message
• <code>/subscribe</code> number - Subscribe to weather updates for a station
• <code>/list</code> - View your subscriptions with serial numbers
• <code>/unsubscribe</code> serial_number  - Remove a subscription
• <code>/rf</code> - Get latest weather data (manual refresh)

<b>Owner Commands:</b>
• <code>/logs</code> - Download bot logs
• <code>/proxy_list</code> - View all proxies with serial numbers
• <code>/update_proxy</code> ip:port:protocol - Add a new proxy
• <code>/delete_proxy</code> serial_number - Remove a proxy
• <code>/user_data</code> - Download all user subscriptions
• <code>/user_info chat_id</code> - Get specific user info
• <code>/modify_user action chat_id stations</code> - Modify user subscriptions

<b>Examples:</b>
• <code>/subscribe 1057</code>
• <code>/unsubscribe 1</code>
• <code>/update_proxy 192.168.1.1:8080:http</code>
• <code>/delete_proxy 1</code>
• <code>/modify_user add 123456789 1057,1058</code>

<b>Limits:</b>
• Maximum {MAX_SUBSCRIPTIONS_PER_USER} subscriptions per user
• Automatic updates at {TARGET_MINUTE} minutes past every hour (Indian time)
            """
        else:
            help_msg = f"""
🌦️ <b>Weather Update Bot - Help</b>

<b>Available Commands:</b>
• <code>/start</code> - Start the bot and see welcome message
• <code>/help</code> - Show this help message
• <code>/subscribe number</code> - Subscribe to weather updates for a station
• <code>/list</code> - View your subscriptions with serial numbers
• <code>/unsubscribe</code> serial_number - Remove a subscription
• <code>/rf</code> - Get latest weather data (manual refresh)

<b>Examples:</b>
• <code>/subscribe</code>  1057
• <code>/unsubscribe</code>  1

<b>Limits:</b>
• Maximum {MAX_SUBSCRIPTIONS_PER_USER} subscriptions per user
• Automatic updates at {TARGET_MINUTE} minutes past every hour (Indian time)
            """

        bot.reply_to(message, help_msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /help command for user {chat_id}: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while fetching help information.")
        except Exception as reply_error:
            write_log("ERROR", f"Failed to send error message for /help command: {reply_error}")


# Command: /subscribe <integer> with error handling and subscription limits
@bot.message_handler(commands=['subscribe'])
def subscribe(message):
    chat_id = str(message.chat.id)
    try:
        try:
            suffix = message.text.split()[1]
            if not suffix.isdigit():
                bot.reply_to(
                    message,
                    "❌ Please provide a valid integer suffix.\n\n<b>Example:</b> <code>/subscribe 1057</code>",
                    parse_mode='HTML')
                return
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide an integer suffix.\n\n<b>Example:</b> <code>/subscribe 1057</code>",
                parse_mode='HTML')
            return

        subscriptions = load_subscriptions()

        # Initialize user subscriptions if not exists
        if chat_id not in subscriptions:
            subscriptions[chat_id] = []
        elif isinstance(subscriptions[chat_id], str):
            # Convert old format to new format
            subscriptions[chat_id] = [subscriptions[chat_id]]

        # Check subscription limit
        if len(subscriptions[chat_id]) >= MAX_SUBSCRIPTIONS_PER_USER:
            bot.reply_to(
                message,
                f"❌ <b>Subscription limit reached!</b>\n\nYou can have maximum {MAX_SUBSCRIPTIONS_PER_USER} subscriptions.\n\nUse <code>/list</code> to view current subscriptions or <code>/unsubscribe &lt;number&gt;</code> to remove one.",
                parse_mode='HTML')
            return

        # Check if already subscribed to this suffix
        if suffix in subscriptions[chat_id]:
            bot.reply_to(
                message,
                f"❌ You are already subscribed to station <b>{suffix}</b>.\n\nUse <code>/list</code> to view all subscriptions.",
                parse_mode='HTML')
            return

        url = f"{URL_PREFIX}{suffix}"

        # Send validation message
        val_msg = bot.reply_to(message,
                               f"🔄 <b>Validating station ID {suffix}...</b>",
                               parse_mode='HTML')

        # Validate station before subscribing
        proxies_data = load_proxies()
        validation_success = False
        validation_error = None

        # Try with proxies first
        if proxies_data and "proxies" in proxies_data and proxies_data[
                "proxies"]:
            for proxy_entry in proxies_data["proxies"]:
                try:
                    if ':' not in proxy_entry:
                        continue
                    proxy, scheme = proxy_entry.rsplit(':', 1)
                    table_data, error = fetch_table_data(url, proxy, scheme)

                    if table_data:
                        validation_success = True
                        break
                    elif error and "Invalid station ID" in error:
                        validation_error = error
                        break
                except:
                    continue

        # Try direct request if proxies failed
        if not validation_success and not validation_error:
            table_data, error = fetch_table_data_direct(url)
            if table_data:
                validation_success = True
            elif error and "Invalid station ID" in error:
                validation_error = error

        # Handle validation results
        if validation_error and "Invalid station ID" in validation_error:
            bot.edit_message_text(
                f"❌ <b>Invalid station ID!</b>\n\n📡 <b>Station ID:</b> {suffix}\n\n❗ This station does not exist. Please check the station ID and try again.",
                chat_id,
                val_msg.message_id,
                parse_mode='HTML')
            return

        if not validation_success:
            bot.edit_message_text(
                f"⚠️ <b>Unable to validate station</b>\n\n📡 <b>Station ID:</b> {suffix}\n\n🔄 Network issues detected. You can try subscribing again later.",
                chat_id,
                val_msg.message_id,
                parse_mode='HTML')
            return

        # Add subscription only after successful validation
        subscriptions[chat_id].append(suffix)
        save_subscriptions(subscriptions)
        write_log("INFO", f"{chat_id} subscribed to suffix {suffix}")

        # Update message with success and show data
        bot.edit_message_text(
            f"✅ <b>Successfully subscribed!</b>\n\n\n📡 <b>Station ID:</b> {suffix}\n📊 <b>Total subscriptions:</b> {len(subscriptions[chat_id])}/{MAX_SUBSCRIPTIONS_PER_USER}\n🔄 Fetching initial data...",
            chat_id,
            val_msg.message_id,
            parse_mode='HTML')

        # Fetch and display initial data using concurrent function
        check_proxies_and_fetch_concurrent(url,
                                chat_id,
                                val_msg.message_id,
                                suffix=suffix)

    except Exception as e:
        write_log("ERROR",
                  f"Error in /subscribe command for user {chat_id}: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred during subscription. Please try again.")
        except:
            pass


# Command: /list - Show user's subscriptions
@bot.message_handler(commands=['list'])
def list_subscriptions(message):
    chat_id = str(message.chat.id)
    try:
        subscriptions = load_subscriptions()

        if chat_id not in subscriptions or not subscriptions[chat_id]:
            bot.reply_to(
                message,
                "📋 <b>No active subscriptions</b>\n\nUse <code>/subscribe &lt;number&gt;</code> to subscribe to a weather station.",
                parse_mode='HTML')
            return

        user_subs = subscriptions[chat_id]
        if isinstance(user_subs, str):
            user_subs = [user_subs]

        msg = f"📋 <b>Your Subscriptions ({len(user_subs)}/{MAX_SUBSCRIPTIONS_PER_USER})</b>\n\n"
        for i, suffix in enumerate(user_subs, 1):
            msg += f"{i}. Station <code>{suffix}</code>\n"

        msg += f"\n💡 Use <code>/unsubscribe &lt;serial_number&gt;</code> to remove a subscription."

        bot.reply_to(message, msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /list command for user {chat_id}: {e}")
        try:
            bot.reply_to(message,
                         "❌ Error occurred while fetching subscriptions.")
        except:
            pass


# Command: /unsubscribe <serial_number> - Remove a subscription by serial number
@bot.message_handler(commands=['unsubscribe'])
def unsubscribe(message):
    chat_id = str(message.chat.id)
    try:
        try:
            serial_num = message.text.split()[1]
            if not serial_num.isdigit():
                bot.reply_to(
                    message,
                    "❌ Please provide a valid serial number.\n\n<b>Example:</b> <code>/unsubscribe 1</code>\n\nUse <code>/list</code> to see your subscriptions with serial numbers.",
                    parse_mode='HTML')
                return
            serial_num = int(serial_num)
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide a serial number.\n\n<b>Example:</b> <code>/unsubscribe 1</code>\n\nUse <code>/list</code> to see your subscriptions with serial numbers.",
                parse_mode='HTML')
            return

        subscriptions = load_subscriptions()

        if chat_id not in subscriptions or not subscriptions[chat_id]:
            bot.reply_to(
                message,
                "❌ You have no active subscriptions.\n\nUse <code>/subscribe &lt;number&gt;</code> to subscribe first.",
                parse_mode='HTML')
            return

        user_subs = subscriptions[chat_id]
        if isinstance(user_subs, str):
            user_subs = [user_subs]
            subscriptions[chat_id] = user_subs

        if serial_num < 1 or serial_num > len(user_subs):
            bot.reply_to(
                message,
                f"❌ Invalid serial number. Please choose between 1 and {len(user_subs)}.\n\nUse <code>/list</code> to view your subscriptions.",
                parse_mode='HTML')
            return

        # Get the suffix at the serial position (subtract 1 for 0-based index)
        suffix_to_remove = user_subs[serial_num - 1]

        # Remove subscription
        user_subs.remove(suffix_to_remove)

        # Clean up empty subscription lists
        if not user_subs:
            del subscriptions[chat_id]
        else:
            subscriptions[chat_id] = user_subs

        save_subscriptions(subscriptions)
        write_log("INFO",
                  f"{chat_id} unsubscribed from suffix {suffix_to_remove}")

        remaining = len(user_subs) if user_subs else 0
        bot.reply_to(
            message,
            f"✅ <b>Successfully unsubscribed!</b>\n\n📡 <b>Removed station:</b> {suffix_to_remove} (Serial #{serial_num})\n📊 <b>Remaining subscriptions:</b> {remaining}/{MAX_SUBSCRIPTIONS_PER_USER}",
            parse_mode='HTML')

    except Exception as e:
        write_log("ERROR",
                  f"Error in /unsubscribe command for user {chat_id}: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred during unsubscription. Please try again.")
        except:
            pass


# Command: /rf with error handling - now supports multiple subscriptions with fully concurrent requests
@bot.message_handler(commands=['rf'])
def manual_fetch(message):
    chat_id = str(message.chat.id)
    try:
        subscriptions = load_subscriptions()
        if chat_id in subscriptions and subscriptions[chat_id]:
            user_subs = subscriptions[chat_id]
            if isinstance(user_subs, str):
                user_subs = [user_subs]

            # Send acknowledgment first
            bot.reply_to(
                message,
                f"🔄 Fetching latest weather data for {len(user_subs)} station(s)..."
            )
            
            # If user has multiple subscriptions, use the multi-station concurrent function
            if len(user_subs) > 1:
                # Process all stations concurrently
                fetch_multiple_stations_concurrent(chat_id, user_subs)
            else:
                # For a single subscription, use the regular concurrent function
                url = f"{URL_PREFIX}{user_subs[0]}"
                check_proxies_and_fetch_concurrent(url,
                                        chat_id,
                                        is_manual=True,
                                        suffix=user_subs[0])
        else:
            bot.reply_to(
                message,
                "❌ You are not subscribed to any stations.\n\nUse <code>/subscribe &lt;number&gt;</code> to subscribe first.",
                parse_mode='HTML')
    except Exception as e:
        write_log("ERROR", f"Error in /rf command for user {chat_id}: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred while fetching data. Please try again.")
        except:
            pass


# Command: /logs with error handling
@bot.message_handler(commands=['logs'])
def send_logs(message):
    try:
        if str(message.chat.id) == OWNER_ID:
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, 'rb') as f:
                        bot.send_document(message.chat.id, f)
                except Exception as e:
                    write_log("ERROR", f"Error sending log file: {e}")
                    bot.reply_to(message, "❌ Error sending log file.")
            else:
                bot.reply_to(message, "📄 Log file not found.")
        else:
            bot.reply_to(message, "❌ Only the owner can access the logs.")
    except Exception as e:
        write_log("ERROR", f"Error in /logs command: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred. Please try again.")
        except:
            pass


# Command: /update_proxy - Add new proxy (owner only)
@bot.message_handler(commands=['update_proxy'])
def update_proxy(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can manage proxies.")
            return

        try:
            proxy_entry = message.text.split(' ', 1)[1].strip()
            if not proxy_entry:
                raise IndexError
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide proxy in format: <code>ip:port:protocol</code>\n\n<b>Example:</b> <code>/update_proxy 192.168.1.1:8080:http</code>",
                parse_mode='HTML')
            return

        # Validate proxy format
        if proxy_entry.count(':') != 2:
            bot.reply_to(
                message,
                "❌ Invalid proxy format. Use: <code>ip:port:protocol</code>\n\n<b>Examples:</b>\n• <code>192.168.1.1:8080:http</code>\n• <code>10.0.0.1:1080:socks5</code>",
                parse_mode='HTML')
            return

        ip, port, protocol = proxy_entry.split(':')

        # Basic validation
        if not ip or not port.isdigit() or protocol.lower() not in [
                'http', 'https', 'socks4', 'socks5'
        ]:
            bot.reply_to(
                message,
                "❌ Invalid proxy details.\n\n<b>Requirements:</b>\n• Valid IP address\n• Numeric port\n• Protocol: http, https, socks4, or socks5",
                parse_mode='HTML')
            return

        proxies_data = load_proxies()

        # Check if proxy already exists
        if proxy_entry in proxies_data.get("proxies", []):
            bot.reply_to(
                message,
                f"⚠️ Proxy <code>{proxy_entry}</code> already exists in the list.",
                parse_mode='HTML')
            return

        # Proxy is new, proceed with adding it

        # Add to active proxies list
        if "proxies" not in proxies_data:
            proxies_data["proxies"] = []

        proxies_data["proxies"].append(proxy_entry)
        save_proxies(proxies_data)

        write_log("INFO", f"Owner added new proxy: {proxy_entry}")

        bot.reply_to(
            message,
            f"✅ <b>Proxy added successfully!</b>\n\n📡 <b>Proxy:</b> <code>{ip}:{port}</code>\n🔗 <b>Protocol:</b> {protocol.upper()}\n📊 <b>Total proxies:</b> {len(proxies_data['proxies'])}",
            parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /update_proxy command: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred while adding proxy. Please try again.")
        except:
            pass


# Command: /delete_proxy - Remove proxy by serial number (owner only)
@bot.message_handler(commands=['delete_proxy'])
def delete_proxy(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can manage proxies.")
            return

        try:
            serial_num = message.text.split()[1]
            if not serial_num.isdigit():
                bot.reply_to(
                    message,
                    "❌ Please provide a valid serial number.\n\n<b>Example:</b> <code>/delete_proxy 1</code>\n\nUse <code>/proxy_list</code> to see proxies with serial numbers.",
                    parse_mode='HTML')
                return
            serial_num = int(serial_num)
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide a serial number.\n\n<b>Example:</b> <code>/delete_proxy 1</code>\n\nUse <code>/proxy_list</code> to see proxies with serial numbers.",
                parse_mode='HTML')
            return

        proxies_data = load_proxies()
        proxies = proxies_data.get("proxies", [])

        # Get all proxies for serial numbering
        all_proxies = proxies

        if not all_proxies:
            bot.reply_to(
                message,
                "❌ No proxies available to delete.\n\nUse <code>/update_proxy</code> to add proxies first.",
                parse_mode='HTML')
            return

        if serial_num < 1 or serial_num > len(all_proxies):
            bot.reply_to(
                message,
                f"❌ Invalid serial number. Please choose between 1 and {len(all_proxies)}.\n\nUse <code>/proxy_list</code> to see available proxies.",
                parse_mode='HTML')
            return

        # Get the proxy at the serial position (subtract 1 for 0-based index)
        proxy_to_remove = all_proxies[serial_num - 1]

        # Remove the proxy from the list
        proxies_data["proxies"].remove(proxy_to_remove)

        save_proxies(proxies_data)
        write_log("INFO", f"Owner deleted proxy: {proxy_to_remove}")

        bot.reply_to(
            message,
            f"✅ <b>Proxy deleted successfully!</b>\n\n📡 <b>Removed:</b> <code>{proxy_to_remove}</code> (Serial #{serial_num})\n📊 <b>Remaining proxies:</b> {len(proxies_data.get('proxies', []))}",
            parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /delete_proxy command: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred while deleting proxy. Please try again.")
        except:
            pass


# Command: /proxy_list - List all proxies with protocols and serial numbers (owner only)
@bot.message_handler(commands=['proxy_list'])
def proxy_list(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can view proxy lists.")
            return

        proxies_data = load_proxies()
        active_proxies = proxies_data.get("proxies", [])

        msg = "🔗 <b>Proxy Configuration</b>\n\n"
        serial_counter = 1

        # Active proxies with serial numbers
        if active_proxies:
            msg += f"✅ <b>Proxies ({len(active_proxies)}):</b>\n"
            for proxy in active_proxies:
                try:
                    ip_port, protocol = proxy.rsplit(':', 1)
                    msg += f"{serial_counter}. <code>{ip_port}</code> ({protocol.upper()})\n"
                except:
                    msg += f"{serial_counter}. <code>{proxy}</code> (Invalid format)\n"
                serial_counter += 1
        else:
            msg += "✅ <b>Proxies:</b> None\n"

        msg += f"\n💡 <b>Commands:</b>\n• <code>/update_proxy ip:port:protocol</code>\n• <code>/delete_proxy &lt;serial_number&gt;</code>"

        bot.reply_to(message, msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /proxy_list command: {e}")
        try:
            bot.reply_to(
                message,
                "❌ Error occurred while fetching proxy list. Please try again."
            )
        except:
            pass


# Command: /user_data - Download all user subscriptions with username (owner only)
@bot.message_handler(commands=['user_data'])
def download_user_data(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can access user data.")
            return

        subscriptions = load_subscriptions()

        if not subscriptions:
            bot.reply_to(message,
                         "📄 No user subscriptions found.",
                         parse_mode='HTML')
            return

        # Create detailed user data report
        report = "👥 USER SUBSCRIPTIONS REPORT\n"
        report += f"📅 Generated: {datetime.now(INDIAN_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        report += "=" * 50 + "\n\n"

        total_users = len(subscriptions)
        total_subscriptions = sum(
            len(subs) if isinstance(subs, list) else 1
            for subs in subscriptions.values())

        report += f"📊 SUMMARY:\n"
        report += f"Total Users: {total_users}\n"
        report += f"Total Subscriptions: {total_subscriptions}\n"
        report += f"Average Subscriptions per User: {total_subscriptions/total_users:.2f}\n\n"

        report += "👤 USER DETAILS:\n"
        report += "-" * 30 + "\n"

        for i, (chat_id, suffixes) in enumerate(subscriptions.items(), 1):
            if isinstance(suffixes, str):
                suffixes = [suffixes]

            # Try to get user info
            username = "Unknown"
            try:
                user = bot.get_chat(chat_id)
                if user.username:
                    username = f"@{user.username}"
                elif user.first_name:
                    username = user.first_name
                    if user.last_name:
                        username += f" {user.last_name}"
            except Exception as e:
                write_log(
                    "ERROR",
                    f"Error fetching username for chat_id {chat_id}: {e}")

            report += f"{i}. Chat ID: {chat_id}\n"
            report += f"   Username: {username}\n"
            report += f"   Subscriptions ({len(suffixes)}): {', '.join(suffixes)}\n"
            report += f"   Stations: {' | '.join([f'Station {s}' for s in suffixes])}\n\n"

        # Save to temporary file and send
        temp_filename = f"user_data_{datetime.now(INDIAN_TIMEZONE).strftime('%Y%m%d_%H%M%S')}.txt"
        with open(temp_filename, 'w', encoding='utf-8') as f:
            f.write(report)

        try:
            with open(temp_filename, 'rb') as f:
                bot.send_document(message.chat.id,
                                  f,
                                  caption="📋 Complete user subscriptions data")

            # Clean up temporary file
            os.remove(temp_filename)

        except Exception as e:
            write_log("ERROR", f"Error sending user data file: {e}")
            bot.reply_to(message, "❌ Error sending user data file.")

    except Exception as e:
        write_log("ERROR", f"Error in /user_data command: {e}")
        try:
            bot.reply_to(message,
                         "❌ Error occurred while generating user data.")
        except:
            pass


# Command: /modify_user - Modify user subscriptions (owner only)
@bot.message_handler(commands=['modify_user'])
def modify_user(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can modify user data.")
            return

        try:
            parts = message.text.split(' ', 3)
            if len(parts) < 4:
                raise IndexError

            action = parts[1].lower()  # add, remove, or replace
            target_chat_id = parts[2]
            station_ids = parts[3]

        except IndexError:
            bot.reply_to(
                message,
                "❌ Invalid format. Use:\n\n<b>Examples:</b>\n• <code>/modify_user add 123456789 1057,1058</code>\n• <code>/modify_user remove 123456789 1057</code>\n• <code>/modify_user replace 123456789 1059,1060</code>\n• <code>/modify_user clear 123456789</code>",
                parse_mode='HTML')
            return

        if action not in ['add', 'remove', 'replace', 'clear']:
            bot.reply_to(
                message,
                "❌ Invalid action. Use: <code>add</code>, <code>remove</code>, <code>replace</code>, or <code>clear</code>",
                parse_mode='HTML')
            return

        subscriptions = load_subscriptions()

        # Initialize user if not exists
        if target_chat_id not in subscriptions:
            subscriptions[target_chat_id] = []
        elif isinstance(subscriptions[target_chat_id], str):
            subscriptions[target_chat_id] = [subscriptions[target_chat_id]]

        current_subs = subscriptions[target_chat_id][:]

        if action == 'clear':
            subscriptions[target_chat_id] = []
            result_msg = f"✅ <b>User data cleared!</b>\n\n👤 <b>Chat ID:</b> {target_chat_id}\n📊 <b>Previous subscriptions:</b> {len(current_subs)}\n📊 <b>Current subscriptions:</b> 0"

        else:
            # Parse station IDs
            try:
                station_list = [
                    s.strip() for s in station_ids.split(',')
                    if s.strip().isdigit()
                ]
                if not station_list:
                    bot.reply_to(
                        message,
                        "❌ Please provide valid station IDs (numbers only).",
                        parse_mode='HTML')
                    return
            except:
                bot.reply_to(message,
                             "❌ Invalid station IDs format.",
                             parse_mode='HTML')
                return

            if action == 'add':
                for station in station_list:
                    if station not in subscriptions[target_chat_id] and len(
                            subscriptions[target_chat_id]
                    ) < MAX_SUBSCRIPTIONS_PER_USER:
                        subscriptions[target_chat_id].append(station)

                result_msg = f"✅ <b>Stations added!</b>\n\n👤 <b>Chat ID:</b> {target_chat_id}\n➕ <b>Added:</b> {', '.join(station_list)}\n📊 <b>Total subscriptions:</b> {len(subscriptions[target_chat_id])}/{MAX_SUBSCRIPTIONS_PER_USER}"

            elif action == 'remove':
                removed = []
                for station in station_list:
                    if station in subscriptions[target_chat_id]:
                        subscriptions[target_chat_id].remove(station)
                        removed.append(station)

                result_msg = f"✅ <b>Stations removed!</b>\n\n👤 <b>Chat ID:</b> {target_chat_id}\n➖ <b>Removed:</b> {', '.join(removed)}\n📊 <b>Remaining subscriptions:</b> {len(subscriptions[target_chat_id])}/{MAX_SUBSCRIPTIONS_PER_USER}"

            elif action == 'replace':
                # Limit to MAX_SUBSCRIPTIONS_PER_USER
                subscriptions[
                    target_chat_id] = station_list[:MAX_SUBSCRIPTIONS_PER_USER]

                result_msg = f"✅ <b>Subscriptions replaced!</b>\n\n👤 <b>Chat ID:</b> {target_chat_id}\n🔄 <b>New subscriptions:</b> {', '.join(subscriptions[target_chat_id])}\n📊 <b>Total subscriptions:</b> {len(subscriptions[target_chat_id])}/{MAX_SUBSCRIPTIONS_PER_USER}"

        # Clean up empty subscription lists
        if not subscriptions[target_chat_id]:
            del subscriptions[target_chat_id]

        save_subscriptions(subscriptions)
        write_log(
            "INFO",
            f"Owner modified user {target_chat_id} subscriptions: {action}")

        bot.reply_to(message, result_msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /modify_user command: {e}")
        try:
            bot.reply_to(message,
                         "❌ Error occurred while modifying user data.")
        except:
            pass

    # Command: /user_info - Get specific user information with username (owner only)
    @bot.message_handler(commands=['user_info'])
    def user_info(message):
        try:
            if str(message.chat.id) != OWNER_ID:
                bot.reply_to(message,
                             "❌ Only the owner can access user information.")
                return

            try:
                target_chat_id = message.text.split()[1]
            except IndexError:
                bot.reply_to(
                    message,
                    "❌ Please provide a chat ID.\n\n<b>Example:</b> <code>/user_info 123456789</code>",
                    parse_mode='HTML')
                return

            subscriptions = load_subscriptions()

            if target_chat_id not in subscriptions:
                bot.reply_to(
                    message,
                    f"❌ <b>User not found!</b>\n\n👤 <b>Chat ID:</b> {target_chat_id}\n📊 <b>Status:</b> No subscriptions",
                    parse_mode='HTML')
                return

            user_subs = subscriptions[target_chat_id]
            if isinstance(user_subs, str):
                user_subs = [user_subs]

            # Get username
            username = "Unknown"
            try:
                user = bot.get_chat(target_chat_id)
                if user.username:
                    username = f"@{user.username}"
                elif user.first_name:
                    username = user.first_name
                    if user.last_name:
                        username += f" {user.last_name}"
                # Note: At least one of these fields is guaranteed to be filled out
            except Exception as e:
                write_log(
                    "ERROR",
                    f"Error fetching username for chat_id {target_chat_id}: {e}"
                )

            msg = f"👤 <b>User Information</b>\n\n"
            msg += f"💬 <b>Chat ID:</b> <code>{target_chat_id}</code>\n"
            msg += f"👤 <b>Username:</b> {username}\n"
            msg += f"📊 <b>Subscriptions:</b> {len(user_subs)}/{MAX_SUBSCRIPTIONS_PER_USER}\n\n"

            if user_subs:
                msg += f"🌦️ <b>Subscribed Stations:</b>\n"
                for i, suffix in enumerate(user_subs, 1):
                    msg += f"{i}. Station <code>{suffix}</code>\n"
            else:
                msg += "📭 <b>No active subscriptions</b>\n"

            msg += f"\n💡 <b>Owner Commands:</b>\n"
            msg += f"• <code>/modify_user add {target_chat_id} 1057,1058</code>\n"
            msg += f"• <code>/modify_user remove {target_chat_id} 1057</code>\n"
            msg += f"• <code>/modify_user replace {target_chat_id} 1059</code>\n"
            msg += f"• <code>/modify_user clear {target_chat_id}</code>"

            bot.reply_to(message, msg, parse_mode='HTML')

        except Exception as e:
            write_log("ERROR", f"Error in /user_info command: {e}")
            try:
                bot.reply_to(
                    message,
                    "❌ Error occurred while fetching user information.")
            except:
                pass


# Async function to fetch data from a URL using a proxy
async def fetch_data_async(url, proxy_entry, session):
    try:
        if ':' not in proxy_entry:
            return None, f"Invalid proxy format: {proxy_entry}"

        proxy, scheme = proxy_entry.rsplit(':', 1)
        proxy_url = f"{scheme}://{proxy.split(':')[0]}:{proxy.split(':')[1]}"
        
        async with session.get(url, proxy=proxy_url, timeout=10) as response:
            html = await response.text()
            
            # Check for invalid range error
            if "Invalid Range" in html:
                return None, "Invalid station ID - station does not exist"

            table_start = html.find('<table')
            table_end = html.find('</table>') + len('</table>')
            if table_start == -1 or table_end == -1:
                return None, "Table not found in HTML"

            table_html = html[table_start:table_end]
            rows = [
                row.strip() for row in table_html.split('<tr>')[1:]
                if '</tr>' in row
            ]
            table_data = []

            for row in rows:
                cells = [
                    cell.strip() for cell in row.split('<td>')[1:]
                    if '</td>' in cell
                ]
                if len(cells) >= 2:
                    key = cells[0].split('</td>')[0].replace(
                        '<span class="style46">', '').replace('</span>',
                                                              '').strip()
                    value = cells[1].split('</td>')[0]
                    while '<' in value and '>' in value:
                        start = value.find('<')
                        end = value.find('>', start) + 1
                        if end == 0:
                            break
                        value = value[:start] + value[end:]
                    value = value.strip()

                    # Skip Latitude and Longitude entries
                    if key.lower() in ['latitude', 'longitude']:
                        continue

                    table_data.append((key, value))

            return table_data, proxy_entry
    except Exception as e:
        return None, str(e)

# Async function to try multiple proxies concurrently with retry mechanism
async def fetch_with_proxies_async(url, proxies):
    # First attempt with standard timeout
    async with aiohttp.ClientSession() as session:
        tasks = []
        for proxy_entry in proxies:
            task = asyncio.create_task(fetch_data_async(url, proxy_entry, session))
            tasks.append(task)
        
        # Wait for first successful result or all to complete
        for completed_task in asyncio.as_completed(tasks):
            table_data, result = await completed_task
            if table_data:  # If we got data successfully
                # Cancel all other tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()
                return table_data, result  # Return the successful result
    
    # If we get here, all proxies failed on first attempt, try retries
    write_log("INFO", f"All proxies failed for URL {url}, implementing async retry")
    
    # Try up to 2 more times with different timeouts
    for retry_attempt in range(2):
        write_log("INFO", f"Async retry attempt {retry_attempt+1}/2")
        
        # Increase timeout for retries: 15s, 20s
        timeout = 15 + (retry_attempt * 5)
        
        # Try each proxy again with increased timeout
        for proxy_entry in proxies:
            try:
                # Create a custom session with longer timeout for retries
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as retry_session:
                    table_data, result = await fetch_data_async(url, proxy_entry, retry_session)
                    if table_data:  # Success
                        return table_data, f"{result} (retry {retry_attempt+1})"  # Return the successful result
            except Exception as e:
                continue  # Try next proxy
    
    # If we get here, all proxies failed after retries
    return None, "All proxies failed after retries"

# Async function to fetch multiple URLs concurrently with retry mechanism
async def fetch_multiple_urls_async(urls, proxies):
    results = {}
    async with aiohttp.ClientSession() as session:
        tasks = {}
        
        # Create tasks for each URL
        for url_key, url in urls.items():
            # For each URL, try all proxies concurrently
            url_tasks = []
            for proxy_entry in proxies:
                task = asyncio.create_task(fetch_data_async(url, proxy_entry, session))
                url_tasks.append(task)
            tasks[url_key] = url_tasks
        
        # Process each URL's tasks
        for url_key, url_tasks in tasks.items():
            # Wait for first successful result for this URL or all to complete
            success = False
            for completed_task in asyncio.as_completed(url_tasks):
                table_data, result = await completed_task
                if table_data:  # If we got data successfully
                    # Cancel all other tasks for this URL
                    for task in url_tasks:
                        if not task.done():
                            task.cancel()
                    results[url_key] = (table_data, result)
                    success = True
                    break
            
            # If no successful result for this URL, try with retry
            if not success:
                write_log("INFO", f"All proxies failed for {url_key}, implementing async retry")
                
                # Try up to 2 more times with different timeouts
                for retry_attempt in range(2):
                    write_log("INFO", f"Async retry attempt {retry_attempt+1}/2 for {url_key}")
                    
                    # Increase timeout for retries: 15s, 20s
                    timeout = 15 + (retry_attempt * 5)
                    
                    # Try each proxy again with increased timeout
                    retry_tasks = []
                    for proxy_entry in proxies:
                        # Create a custom session with longer timeout for retries
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as retry_session:
                            try:
                                table_data, result = await fetch_data_async(urls[url_key], proxy_entry, retry_session)
                                if table_data:  # Success
                                    results[url_key] = (table_data, f"{result} (retry {retry_attempt+1})")
                                    success = True
                                    break
                            except Exception as e:
                                continue  # Try next proxy
                    
                    if success:  # If any proxy succeeded in this retry attempt
                        break
                
                # If still no successful result after retries, store None
                if not success:
                    results[url_key] = (None, "All proxies failed after retries")
        
        return results

# Function to fetch multiple URLs concurrently for a user with multiple subscriptions
def fetch_multiple_stations_concurrent(chat_id, suffixes):
    proxies_data = load_proxies()
    
    # Check if proxies data is valid
    if not proxies_data or "proxies" not in proxies_data or not isinstance(
            proxies_data["proxies"], list) or not proxies_data["proxies"]:
        # Try direct requests as fallback (no retry for direct requests)
        write_log("INFO", "No valid proxies, attempting direct requests")
        success_count = 0
        
        for suffix in suffixes:
            url = f"{URL_PREFIX}{suffix}"
            # Direct request without retry
            write_log("INFO", f"Direct request for station {suffix}")
            table_data, error = fetch_table_data_direct(url)
            
            if table_data:
                formatted_data = format_table_data(table_data, suffix)
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                success_count += 1
                write_log("INFO", f"Direct request SUCCESS for station {suffix}")
            else:
                write_log("ERROR", f"Direct request failed for station {suffix}: {error}")
        
        return success_count > 0
    
    # Prepare URLs dictionary
    urls = {suffix: f"{URL_PREFIX}{suffix}" for suffix in suffixes}
    
    # Run the async function to fetch data concurrently
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(
            fetch_multiple_urls_async(urls, proxies_data["proxies"]))
        
        # Process results
        success_count = 0
        for suffix, (table_data, result) in results.items():
            if table_data:
                # Success with one of the proxies
                formatted_data = format_table_data(table_data, suffix)
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                write_log("INFO", f"Proxy {result} SUCCESS for station {suffix} (concurrent)")
                success_count += 1
            else:
                # All proxies failed, try direct request for this URL
                url = urls[suffix]
                write_log("INFO", f"All proxies failed for station {suffix}, trying direct request")
                table_data, error = fetch_table_data_direct(url)
                
                if table_data:
                    formatted_data = format_table_data(table_data, suffix)
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                    write_log("INFO", f"Direct request SUCCESS for station {suffix} (fallback)")
                    success_count += 1
                else:
                    write_log("ERROR", f"Direct request also failed for station {suffix}: {error}")
        
        return success_count > 0
    except Exception as e:
        write_log("ERROR", f"Error in concurrent multi-station fetch: {e}")
        return False
    finally:
        loop.close()

# Function to check proxies and fetch data concurrently
def check_proxies_and_fetch_concurrent(url, chat_id, message_id=None, is_manual=False, suffix=None):
    proxies_data = load_proxies()
    
    # Check if proxies data is valid
    if not proxies_data or "proxies" not in proxies_data or not isinstance(
            proxies_data["proxies"], list) or not proxies_data["proxies"]:
        # Try direct request as fallback (no retry for direct requests)
        write_log("INFO", "No valid proxies, attempting direct request")
        
        # Single direct request without retry
        table_data, error = fetch_table_data_direct(url)
        
        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data,
                                          chat_id,
                                          message_id,
                                          parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS")
            return True
        else:
            write_log("ERROR", f"Direct request failed: {error}")
            return False
    
    # Send acknowledgment message for manual fetch
    if is_manual and not message_id:
        ack_msg = bot.send_message(chat_id,
                                   "🔄 Fetching latest weather data...")
        message_id = ack_msg.message_id
    
    # Run the async function to fetch data concurrently
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        table_data, result = loop.run_until_complete(
            fetch_with_proxies_async(url, proxies_data["proxies"]))
        
        if table_data:
            # Success with one of the proxies
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data,
                                          chat_id,
                                          message_id,
                                          parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id,
                                     formatted_data,
                                     parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            
            write_log("INFO", f"Proxy {result} SUCCESS (concurrent)")
            return True
        else:
            # All proxies failed, try direct request (no retry for direct requests)
            write_log("INFO", "All proxies failed concurrently, trying direct request")
            
            # Single direct request without retry
            table_data, error = fetch_table_data_direct(url)
            
            if table_data:
                formatted_data = format_table_data(table_data, suffix)
                if message_id:
                    try:
                        bot.edit_message_text(formatted_data,
                                              chat_id,
                                              message_id,
                                              parse_mode='HTML')
                    except Exception as e:
                        bot.send_message(chat_id,
                                         formatted_data,
                                         parse_mode='HTML')
                else:
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                write_log("INFO", "Direct request SUCCESS (fallback after concurrent failure)")
                return True
            else:
                write_log("ERROR", f"Direct request failed: {error}")
                return False
    except Exception as e:
        write_log("ERROR", f"Error in concurrent proxy fetch: {e}")
        return False
    finally:
        loop.close()

# Start the bot with infinite polling and comprehensive error handling
def start_bot():
    while True:
        try:
            write_log("INFO", "Starting bot polling...")
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            write_log("CRITICAL", f"Bot polling crashed: {e}")
            print(f"Bot polling error: {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)  # Wait before restarting
            continue

keep_alive()
# Start the bot
if __name__ == "__main__":
    try:
        # Initialize MongoDB connection
        if not init_mongodb():
            write_log("CRITICAL", "Failed to initialize MongoDB. Exiting...")
            print(
                "Failed to connect to MongoDB. Please check your MONGO_URI environment variable."
            )
            exit(1)

        # Start Indian time checker in a background thread
        threading.Thread(target=run_indian_time_checker, daemon=True).start()
        write_log("INFO", "Bot started successfully")
        start_bot()
    except KeyboardInterrupt:
        write_log("INFO", "Bot stopped by user")
        print("Bot stopped by user")
    except Exception as e:
        write_log("CRITICAL", f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        print("Bot will restart automatically...")
    finally:
        # Close MongoDB connection
        if mongo_client:
            mongo_client.close()
            write_log("INFO", "MongoDB connection closed")
