import os
from datetime import datetime, timezone, timedelta

# Indian timezone (UTC+5:30)
INDIAN_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
LOG_FILE = "logs.txt"
MAX_LOG_LINES = 4000


def get_indian_time():
    """Get current time formatted in Indian timezone"""
    return datetime.now(INDIAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S IST")


def write_log(level, message):
    """Write a log entry to the log file"""
    try:
        timestamp = get_indian_time()
        log_entry = f"{timestamp} - {level.upper()} - {message}\n"

        lines = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

        lines.append(log_entry)

        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]

        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)

    except Exception as e:
        print(f"LOG ERROR: {e} | Original message: {level.upper()} - {message}")


def replace_last_checking_log(message):
    """Replace last 'Checking Indian time' log with new one"""
    try:
        timestamp = get_indian_time()
        new_log_line = f"{timestamp} - INFO - {message}\n"

        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for i in range(len(lines) - 1, -1, -1):
                if "Checking Indian time:" in lines[i]:
                    lines.pop(i)
                    break

            lines.append(new_log_line)

            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
        else:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(new_log_line)

    except Exception as e:
        write_log("INFO", message)
        print(f"LOG REPLACE ERROR: {e}")
