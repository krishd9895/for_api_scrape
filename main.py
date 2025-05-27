import os
import json
import requests
import telebot
import schedule
import time
import threading
from requests.exceptions import RequestException, ProxyError, ConnectTimeout
from datetime import datetime, timedelta
from uuid import uuid4
import re
from webserver import keep_alive

# Telegram bot token (replace with your bot token)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# File paths for data storage
SUBSCRIPTIONS_FILE = "subscriptions.json"
PROXIES_FILE = "proxies.json"
LOG_FILE = "logs.txt"

# Owner's Telegram ID (replace with your Telegram ID)
OWNER_ID = os.environ.get('OWNER_ID')

# URL prefix for the data source
URL_PREFIX = os.environ.get('URL_PREFIX')

# Maximum subscriptions per user
MAX_SUBSCRIPTIONS_PER_USER = 4


# Enhanced logging function with error handling
def write_log(level, message):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding='utf-8') as f:
            f.write(f"{timestamp} - {level.upper()} - {message}\n")
    except Exception as e:
        # If logging fails, print to console as fallback
        print(f"LOG ERROR: {e} | Original message: {level.upper()} - {message}")


# Load or initialize subscriptions with error handling
def load_subscriptions():
    try:
        if os.path.exists(SUBSCRIPTIONS_FILE):
            with open(SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Convert old format to new format if needed
                if data and isinstance(list(data.values())[0], str):
                    # Old format: {"chat_id": "suffix"}
                    # New format: {"chat_id": ["suffix1", "suffix2", ...]}
                    new_data = {}
                    for chat_id, suffix in data.items():
                        new_data[chat_id] = [suffix]
                    save_subscriptions(new_data)
                    return new_data
                return data
        return {}
    except Exception as e:
        write_log("ERROR", f"Error loading subscriptions: {e}")
        return {}


def save_subscriptions(subscriptions):
    try:
        with open(SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(subscriptions, f, indent=4)
    except Exception as e:
        write_log("ERROR", f"Error saving subscriptions: {e}")


# Load or initialize proxies with error handling
def load_proxies():
    try:
        if os.path.exists(PROXIES_FILE):
            with open(PROXIES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"proxies": [], "failed": []}
    except Exception as e:
        write_log("ERROR", f"Error loading proxies: {e}")
        return {"proxies": [], "failed": []}


def save_proxies(proxies_data):
    try:
        with open(PROXIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(proxies_data, f, indent=4)
    except Exception as e:
        write_log("ERROR", f"Error saving proxies: {e}")


# Convert 24-hour time to 12-hour AM/PM format with date
def convert_to_12hour(datetime_str):
    try:
        # Handle full date-time string (e.g., "27/05/2025 21:00")
        if ' ' in datetime_str:
            date_part, time_part = datetime_str.split(' ', 1)

            # Parse hour and minute
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
            # If no space, assume it's just time
            hour, minute = map(int, datetime_str.split(':'))
            if hour == 0:
                return f"12:{minute:02d} AM"
            elif hour < 12:
                return f"{hour}:{minute:02d} AM"
            elif hour == 12:
                return f"12:{minute:02d} PM"
            else:
                return f"{hour-12}:{minute:02d} PM"
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
    if any(word in key_lower for word in ['mandal', 'area', 'district', 'region']):
        return 'mandal', '🏘️'

    # Date matching
    if any(word in key_lower for word in ['date', 'day']):
        return 'date', '📅'

    # Time/Updated matching (Last Updated, Updated, Time, etc.)
    if any(word in key_lower for word in ['updated', 'time', 'last']):
        return 'updated', '🕐'

    # Rainfall matching
    if any(word in key_lower for word in ['rainfall', 'rain', 'precipitation']):
        return 'rainfall', '🌧️'

    # Temperature matching
    if any(word in key_lower for word in ['temperature', 'temp', 'celsius', 'fahrenheit']):
        return 'temperature', '🌡️'

    # Humidity matching
    if any(word in key_lower for word in ['humidity', 'moisture', 'rh']):
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
            if value.replace('.', '').replace('-', '').isdigit() and '°' not in value:
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
def check_proxies_and_fetch(url, chat_id, message_id=None, is_manual=False, suffix=None):
    proxies_data = load_proxies()

    # Check if proxies data is valid
    if not proxies_data or "proxies" not in proxies_data or not isinstance(proxies_data["proxies"], list):
        error_msg = "❌ Proxies configuration is invalid or empty."
        write_log("ERROR", "Proxies JSON is empty, has wrong indentation, or invalid structure")

        if str(chat_id) != OWNER_ID:
            bot.send_message(OWNER_ID, "🚨 Proxies configuration is invalid or empty. Check proxies.json file.")

        # Try direct request as fallback
        write_log("INFO", "Attempting direct request without proxy")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data, chat_id, message_id, parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS")
            return
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            final_error = f"❌ All connection methods failed.\n\nDirect request error: {error}"
            if message_id:
                try:
                    bot.edit_message_text(final_error, chat_id, message_id)
                except Exception as e:
                    bot.send_message(chat_id, final_error)
            else:
                bot.send_message(chat_id, final_error)
            return

    proxies = proxies_data["proxies"]
    failed_proxies = proxies_data.get("failed", [])
    success = False

    # Send acknowledgment message for manual fetch
    if is_manual and not message_id:
        ack_msg = bot.send_message(chat_id, "🔄 Fetching latest weather data...")
        message_id = ack_msg.message_id

    # Check if there are any proxies to use
    if not proxies:
        error_msg = "❌ No proxies available in configuration."
        write_log("ERROR", "Proxies list is empty")

        if str(chat_id) != OWNER_ID:
            bot.send_message(OWNER_ID, "🚨 No proxies available. Please add proxies to proxies.json file.")

        # Try direct request as fallback
        write_log("INFO", "No proxies available, attempting direct request")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data, chat_id, message_id, parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS")
            return
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            final_error = f"❌ All connection methods failed.\n\nDirect request error: {error}"
            if message_id:
                try:
                    bot.edit_message_text(final_error, chat_id, message_id)
                except Exception as e:
                    bot.send_message(chat_id, final_error)
            else:
                bot.send_message(chat_id, final_error)
            return

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
                        bot.edit_message_text(formatted_data, chat_id, message_id, parse_mode='HTML')
                    except Exception as e:
                        bot.send_message(chat_id, formatted_data, parse_mode='HTML')
                else:
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')

                write_log("INFO", f"Proxy {proxy} ({scheme}) SUCCESS")
                break
            else:
                if proxy_entry not in failed_proxies:
                    failed_proxies.append(proxy_entry)
                    proxies_data["failed"] = failed_proxies
                    save_proxies(proxies_data)
                    write_log("ERROR", f"Proxy {proxy} ({scheme}) failed: {error}")

                    if str(chat_id) != OWNER_ID:
                        bot.send_message(
                            OWNER_ID,
                            f"🚨 Proxy failed: {proxy} ({scheme})\nError: {error}")
        except Exception as e:
            write_log("ERROR", f"Error processing proxy {proxy_entry}: {e}")
            continue

    # If all proxies failed, try direct request
    if not success:
        write_log("INFO", "All proxies failed, attempting direct request as fallback")
        table_data, error = fetch_table_data_direct(url)

        if table_data:
            formatted_data = format_table_data(table_data, suffix)
            if message_id:
                try:
                    bot.edit_message_text(formatted_data, chat_id, message_id, parse_mode='HTML')
                except Exception as e:
                    bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            else:
                bot.send_message(chat_id, formatted_data, parse_mode='HTML')
            write_log("INFO", "Direct request SUCCESS (fallback)")
        else:
            write_log("ERROR", f"Direct request also failed: {error}")
            final_error = f"❌ All proxies and direct connection failed.\n\nLast error: {error}"
            if message_id:
                try:
                    bot.edit_message_text(final_error, chat_id, message_id)
                except Exception as e:
                    bot.send_message(chat_id, final_error)
            else:
                bot.send_message(chat_id, final_error)


# Hourly update job for subscribed users with error handling
def hourly_update():
    try:
        now = datetime.now()
        if now.minute == 16:
            subscriptions = load_subscriptions()
            for chat_id, suffixes in subscriptions.items():
                try:
                    # Handle both old format (string) and new format (list)
                    if isinstance(suffixes, str):
                        suffixes = [suffixes]

                    for suffix in suffixes:
                        url = f"{URL_PREFIX}{suffix}"
                        check_proxies_and_fetch(url, chat_id, suffix=suffix)
                        time.sleep(1)  # Small delay between multiple subscriptions
                except Exception as e:
                    write_log("ERROR", f"Error updating user {chat_id}: {e}")
                    # Continue with next user even if one fails
                    continue
    except Exception as e:
        write_log("ERROR", f"Hourly update error: {e}")


# Run scheduler in a separate thread with error handling
def run_scheduler():
    schedule.every().minute.do(hourly_update)
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            write_log("ERROR", f"Scheduler error: {e}")
            time.sleep(60)  # Continue running even if there's an error


# Start scheduler in a background thread
threading.Thread(target=run_scheduler, daemon=True).start()


# Command: /help with error handling
@bot.message_handler(commands=['help'])
def send_welcome(message):
    try:
        welcome_msg = f"""
🌦️ <b>Welcome to Weather Update Bot!</b>

<b>Available Commands:</b>
• <code>/subscribe &lt;number&gt;</code> - Subscribe to weather updates
• <code>/list</code> - View your subscriptions
• <code>/unsubscribe &lt;number&gt;</code> - Remove a subscription
• <code>/rf</code> - Get latest weather data (manual refresh)
• <code>/logs</code> - View logs (owner only)

<b>Proxy Management (Owner Only):</b>
• <code>/proxy_list</code> - View all proxies
• <code>/update_proxy ip:port:protocol</code> - Add new proxy
• <code>/delete_proxy ip:port:protocol</code> - Remove proxy

<b>Examples:</b> 
• <code>/subscribe 1057</code>
• <code>/unsubscribe 1057</code>
• <code>/update_proxy 192.168.1.1:8080:http</code>

<b>Limits:</b> Maximum {MAX_SUBSCRIPTIONS_PER_USER} subscriptions per user.

You'll receive automatic updates every hour at 16 minutes past the hour.
        """
        bot.reply_to(message, welcome_msg, parse_mode='HTML')
    except Exception as e:
        write_log("ERROR", f"Error in /start command: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred. Please try again.")
        except:
            pass


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
                    parse_mode='HTML'
                )
                return
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide an integer suffix.\n\n<b>Example:</b> <code>/subscribe 1057</code>",
                parse_mode='HTML'
            )
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
                parse_mode='HTML'
            )
            return

        # Check if already subscribed to this suffix
        if suffix in subscriptions[chat_id]:
            bot.reply_to(
                message,
                f"❌ You are already subscribed to station <b>{suffix}</b>.\n\nUse <code>/list</code> to view all subscriptions.",
                parse_mode='HTML'
            )
            return

        url = f"{URL_PREFIX}{suffix}"

        # Send validation message
        val_msg = bot.reply_to(
            message,
            f"🔄 <b>Validating station ID {suffix}...</b>",
            parse_mode='HTML'
        )

        # Validate station before subscribing
        proxies_data = load_proxies()
        validation_success = False
        validation_error = None

        # Try with proxies first
        if proxies_data and "proxies" in proxies_data and proxies_data["proxies"]:
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
                parse_mode='HTML'
            )
            return

        if not validation_success:
            bot.edit_message_text(
                f"⚠️ <b>Unable to validate station</b>\n\n📡 <b>Station ID:</b> {suffix}\n\n🔄 Network issues detected. You can try subscribing again later.",
                chat_id,
                val_msg.message_id,
                parse_mode='HTML'
            )
            return

        # Add subscription only after successful validation
        subscriptions[chat_id].append(suffix)
        save_subscriptions(subscriptions)
        write_log("INFO", f"{chat_id} subscribed to suffix {suffix}")

        # Update message with success and show data
        bot.edit_message_text(
            f"✅ <b>Successfully subscribed!</b>\n\n📡 <b>Station ID:</b> {suffix}\n📊 <b>Total subscriptions:</b> {len(subscriptions[chat_id])}/{MAX_SUBSCRIPTIONS_PER_USER}\n🔄 Fetching initial data...",
            chat_id,
            val_msg.message_id,
            parse_mode='HTML'
        )

        # Fetch and display initial data
        check_proxies_and_fetch(url, chat_id, val_msg.message_id, suffix=suffix)

    except Exception as e:
        write_log("ERROR", f"Error in /subscribe command for user {chat_id}: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred during subscription. Please try again.")
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
                parse_mode='HTML'
            )
            return

        user_subs = subscriptions[chat_id]
        if isinstance(user_subs, str):
            user_subs = [user_subs]

        msg = f"📋 <b>Your Subscriptions ({len(user_subs)}/{MAX_SUBSCRIPTIONS_PER_USER})</b>\n\n"
        for i, suffix in enumerate(user_subs, 1):
            msg += f"{i}. Station <code>{suffix}</code>\n"

        msg += f"\n💡 Use <code>/unsubscribe &lt;number&gt;</code> to remove a subscription."

        bot.reply_to(message, msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /list command for user {chat_id}: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while fetching subscriptions.")
        except:
            pass


# Command: /unsubscribe <integer> - Remove a subscription
@bot.message_handler(commands=['unsubscribe'])
def unsubscribe(message):
    chat_id = str(message.chat.id)
    try:
        try:
            suffix = message.text.split()[1]
            if not suffix.isdigit():
                bot.reply_to(
                    message,
                    "❌ Please provide a valid integer suffix.\n\n<b>Example:</b> <code>/unsubscribe 1057</code>",
                    parse_mode='HTML'
                )
                return
        except IndexError:
            bot.reply_to(
                message,
                "❌ Please provide an integer suffix.\n\n<b>Example:</b> <code>/unsubscribe 1057</code>",
                parse_mode='HTML'
            )
            return

        subscriptions = load_subscriptions()

        if chat_id not in subscriptions or not subscriptions[chat_id]:
            bot.reply_to(
                message,
                "❌ You have no active subscriptions.\n\nUse <code>/subscribe &lt;number&gt;</code> to subscribe first.",
                parse_mode='HTML'
            )
            return

        user_subs = subscriptions[chat_id]
        if isinstance(user_subs, str):
            user_subs = [user_subs]
            subscriptions[chat_id] = user_subs

        if suffix not in user_subs:
            bot.reply_to(
                message,
                f"❌ You are not subscribed to station <b>{suffix}</b>.\n\nUse <code>/list</code> to view your subscriptions.",
                parse_mode='HTML'
            )
            return

        # Remove subscription
        user_subs.remove(suffix)

        # Clean up empty subscription lists
        if not user_subs:
            del subscriptions[chat_id]
        else:
            subscriptions[chat_id] = user_subs

        save_subscriptions(subscriptions)
        write_log("INFO", f"{chat_id} unsubscribed from suffix {suffix}")

        remaining = len(user_subs) if user_subs else 0
        bot.reply_to(
            message,
            f"✅ <b>Successfully unsubscribed!</b>\n\n📡 <b>Removed station:</b> {suffix}\n📊 <b>Remaining subscriptions:</b> {remaining}/{MAX_SUBSCRIPTIONS_PER_USER}",
            parse_mode='HTML'
        )

    except Exception as e:
        write_log("ERROR", f"Error in /unsubscribe command for user {chat_id}: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred during unsubscription. Please try again.")
        except:
            pass


# Command: /rf with error handling - now supports multiple subscriptions
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
            ack_msg = bot.reply_to(message, f"🔄 Fetching latest weather data for {len(user_subs)} station(s)...")

            for i, suffix in enumerate(user_subs):
                url = f"{URL_PREFIX}{suffix}"
                if i == 0:
                    # Edit the first message
                    check_proxies_and_fetch(url, chat_id, ack_msg.message_id, is_manual=True, suffix=suffix)
                else:
                    # Send new messages for additional subscriptions
                    check_proxies_and_fetch(url, chat_id, is_manual=False, suffix=suffix)
                    time.sleep(1)  # Small delay between requests
        else:
            bot.reply_to(
                message,
                "❌ You are not subscribed to any stations.\n\nUse <code>/subscribe &lt;number&gt;</code> to subscribe first.",
                parse_mode='HTML'
            )
    except Exception as e:
        write_log("ERROR", f"Error in /rf command for user {chat_id}: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while fetching data. Please try again.")
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
                parse_mode='HTML'
            )
            return

        # Validate proxy format
        if proxy_entry.count(':') != 2:
            bot.reply_to(
                message,
                "❌ Invalid proxy format. Use: <code>ip:port:protocol</code>\n\n<b>Examples:</b>\n• <code>192.168.1.1:8080:http</code>\n• <code>10.0.0.1:1080:socks5</code>",
                parse_mode='HTML'
            )
            return

        ip, port, protocol = proxy_entry.split(':')
        
        # Basic validation
        if not ip or not port.isdigit() or protocol.lower() not in ['http', 'https', 'socks4', 'socks5']:
            bot.reply_to(
                message,
                "❌ Invalid proxy details.\n\n<b>Requirements:</b>\n• Valid IP address\n• Numeric port\n• Protocol: http, https, socks4, or socks5",
                parse_mode='HTML'
            )
            return

        proxies_data = load_proxies()
        
        # Check if proxy already exists
        if proxy_entry in proxies_data.get("proxies", []):
            bot.reply_to(
                message,
                f"⚠️ Proxy <code>{proxy_entry}</code> already exists in the list.",
                parse_mode='HTML'
            )
            return

        # Remove from failed list if it exists there
        if proxy_entry in proxies_data.get("failed", []):
            proxies_data["failed"].remove(proxy_entry)
            write_log("INFO", f"Removed {proxy_entry} from failed proxies list")

        # Add to active proxies list
        if "proxies" not in proxies_data:
            proxies_data["proxies"] = []
        
        proxies_data["proxies"].append(proxy_entry)
        save_proxies(proxies_data)
        
        write_log("INFO", f"Owner added new proxy: {proxy_entry}")
        
        bot.reply_to(
            message,
            f"✅ <b>Proxy added successfully!</b>\n\n📡 <b>Proxy:</b> <code>{ip}:{port}</code>\n🔗 <b>Protocol:</b> {protocol.upper()}\n📊 <b>Total proxies:</b> {len(proxies_data['proxies'])}",
            parse_mode='HTML'
        )

    except Exception as e:
        write_log("ERROR", f"Error in /update_proxy command: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while adding proxy. Please try again.")
        except:
            pass


# Command: /delete_proxy - Remove proxy (owner only)
@bot.message_handler(commands=['delete_proxy'])
def delete_proxy(message):
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
                "❌ Please provide proxy to delete in format: <code>ip:port:protocol</code>\n\n<b>Example:</b> <code>/delete_proxy 192.168.1.1:8080:http</code>",
                parse_mode='HTML'
            )
            return

        proxies_data = load_proxies()
        
        # Check if proxy exists in active list
        if proxy_entry in proxies_data.get("proxies", []):
            proxies_data["proxies"].remove(proxy_entry)
            save_proxies(proxies_data)
            write_log("INFO", f"Owner deleted proxy: {proxy_entry}")
            
            bot.reply_to(
                message,
                f"✅ <b>Proxy deleted successfully!</b>\n\n📡 <b>Removed:</b> <code>{proxy_entry}</code>\n📊 <b>Remaining proxies:</b> {len(proxies_data.get('proxies', []))}",
                parse_mode='HTML'
            )
            return

        # Check if proxy exists in failed list
        if proxy_entry in proxies_data.get("failed", []):
            proxies_data["failed"].remove(proxy_entry)
            save_proxies(proxies_data)
            write_log("INFO", f"Owner deleted failed proxy: {proxy_entry}")
            
            bot.reply_to(
                message,
                f"✅ <b>Failed proxy deleted successfully!</b>\n\n📡 <b>Removed:</b> <code>{proxy_entry}</code>",
                parse_mode='HTML'
            )
            return

        # Proxy not found
        bot.reply_to(
            message,
            f"❌ <b>Proxy not found!</b>\n\n📡 <b>Proxy:</b> <code>{proxy_entry}</code>\n\nUse <code>/proxy_list</code> to see available proxies.",
            parse_mode='HTML'
        )

    except Exception as e:
        write_log("ERROR", f"Error in /delete_proxy command: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while deleting proxy. Please try again.")
        except:
            pass


# Command: /proxy_list - List all proxies with protocols (owner only)
@bot.message_handler(commands=['proxy_list'])
def proxy_list(message):
    try:
        if str(message.chat.id) != OWNER_ID:
            bot.reply_to(message, "❌ Only the owner can view proxy lists.")
            return

        proxies_data = load_proxies()
        active_proxies = proxies_data.get("proxies", [])
        failed_proxies = proxies_data.get("failed", [])

        msg = "🔗 <b>Proxy Configuration</b>\n\n"

        # Active proxies
        if active_proxies:
            msg += f"✅ <b>Active Proxies ({len(active_proxies)}):</b>\n"
            for i, proxy in enumerate(active_proxies, 1):
                try:
                    ip_port, protocol = proxy.rsplit(':', 1)
                    msg += f"{i}. <code>{ip_port}</code> ({protocol.upper()})\n"
                except:
                    msg += f"{i}. <code>{proxy}</code> (Invalid format)\n"
        else:
            msg += "✅ <b>Active Proxies:</b> None\n"

        msg += "\n"

        # Failed proxies
        if failed_proxies:
            msg += f"❌ <b>Failed Proxies ({len(failed_proxies)}):</b>\n"
            for i, proxy in enumerate(failed_proxies, 1):
                try:
                    ip_port, protocol = proxy.rsplit(':', 1)
                    msg += f"{i}. <code>{ip_port}</code> ({protocol.upper()})\n"
                except:
                    msg += f"{i}. <code>{proxy}</code> (Invalid format)\n"
        else:
            msg += "❌ <b>Failed Proxies:</b> None\n"

        msg += f"\n💡 <b>Commands:</b>\n• <code>/update_proxy ip:port:protocol</code>\n• <code>/delete_proxy ip:port:protocol</code>"

        bot.reply_to(message, msg, parse_mode='HTML')

    except Exception as e:
        write_log("ERROR", f"Error in /proxy_list command: {e}")
        try:
            bot.reply_to(message, "❌ Error occurred while fetching proxy list. Please try again.")
        except:
            pass


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
        # Start scheduler in a background thread
        threading.Thread(target=run_scheduler, daemon=True).start()
        write_log("INFO", "Bot started successfully")
        start_bot()
    except KeyboardInterrupt:
        write_log("INFO", "Bot stopped by user")
        print("Bot stopped by user")
    except Exception as e:
        write_log("CRITICAL", f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        print("Bot will restart automatically...")
