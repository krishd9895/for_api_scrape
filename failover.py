#!/usr/bin/env python3
"""
###############################################################################
# PRODUCTION-GRADE HIGH-AVAILABILITY MULTI-MODE PROCESS SUPERVISOR
#
# This script ensures your application (main.py by default) stays online 24/7
# by using a distributed leadership lock via MongoDB.
#
# FEATURES:
# - Automatic failover: If the current leader goes offline, another node takes over
# - Forced leader: You can force a specific node to be leader via MongoDB
# - Graceful shutdown: Handles SIGINT/SIGTERM properly
# - Automatic restart: If the application crashes, it restarts it up to LOCAL_RETRY_LIMIT times
#
# INSTRUCTIONS:
# 1. Provide your 'MONGO_URI' inside your local .env file (Mandatory).
# 2. Select your 'LAUNCH_MODE' below: "PYTHON", "DOCKER_COMPOSE", or "RAW".
# 3. Define your target script or parameters inside the 'TARGET' parameter.
#
# ADMINISTRATIVE OVERRIDE MANUAL:
# - To force a specific node to be leader, update MongoDB:
#   db.Services.updateOne({"_id": "RF_Bot"}, {"$set": {"forced_leader_node": "NODE_ALIAS"}})
# - To disable forced leader (let any node take over):
#   db.Services.updateOne({"_id": "RF_Bot"}, {"$set": {"forced_leader_node": None}})
#   OR
#   db.Services.updateOne({"_id": "RF_Bot"}, {"$set": {"forced_leader_node": ""}})
# - (Find your explicit NODE_ALIAS printed directly inside the startup banner below)
###############################################################################
"""

# --- LAUNCH MODE CONFIGURATION ---
# How should we start your application?
# Options: "PYTHON", "DOCKER_COMPOSE", or "RAW"
LAUNCH_MODE = "PYTHON"

# What should we launch?
# - For "PYTHON": Path to your Python script (e.g., "main.py")
# - For "DOCKER_COMPOSE": Path to your docker-compose.yml (or extra args)
# - For "RAW": The full command to run (used with CUSTOM_RAW_COMMAND below)
TARGET = "main.py"

# Only used if LAUNCH_MODE is "RAW"
CUSTOM_RAW_COMMAND = ""

# --- INFRASTRUCTURE & DISTRIBUTED LOCK PROPERTIES ---
# Unique identifier for your service cluster (all nodes must use the same)
SERVICE_ID = "RF_Bot"

# MongoDB database and collection to use for the leadership lock
DATABASE_NAME = "Failover"
COLLECTION_NAME = "Services"

# --- CLUSTER TIMING GUARDRAILS ---
# How often (in seconds) the current leader sends heartbeats to MongoDB
HEARTBEAT_INTERVAL = 15

# How long (in seconds) to wait before considering a leader offline
# (Must be >= 3-4x HEARTBEAT_INTERVAL to avoid false failures)
HEARTBEAT_TIMEOUT = 60

# How often (in seconds) standby nodes check if they should become leader
CHECK_INTERVAL = 5

# How many times to restart the application locally before giving up
LOCAL_RETRY_LIMIT = 3

# How long (in seconds) to wait after starting the app before verifying it's healthy
STARTUP_GRACE_PERIOD = 5

# Maximum time (in seconds) to tolerate DB disconnections before stepping down
MAX_NETWORK_GRACE_S = 30

# --- STANDARD IMPORTS ---
import os
import sys
import time
import uuid
import shlex
import random
import shutil
import socket
import signal
import hashlib
import platform
import logging
import subprocess
from datetime import datetime, timezone
from dotenv import load_dotenv
import pymongo
from pymongo.errors import PyMongoError, ConnectionFailure, DuplicateKeyError
from webserver import keep_alive

# ---------------------------------------------------------------------------
# 1. PERSISTENT GLOBAL MACHINE IDENTITY & ADVANCED LOGGING INITIALIZATION
# ---------------------------------------------------------------------------
# Load environment variables from .env file
load_dotenv()

# Get MongoDB URI from environment (REQUIRED)
MONGO_URI = os.getenv("MONGO_URI")

# Are we running on Windows? (Used for process management)
IS_WINDOWS = platform.system() == "Windows"

# Validate required configuration
if not SERVICE_ID.strip() or not MONGO_URI or not MONGO_URI.strip():
    print("CRITICAL CONFIGURATION ERROR: Missing core environment variables or SERVICE_ID!", file=sys.stderr)
    sys.exit(1)

# Get project root directory
PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))

# Natively isolate the tracking files away from the project directory to survive directory clones
SYSTEM_HOME = os.path.expanduser("~")
MACHINE_ID_FILE = os.path.join(SYSTEM_HOME, f".ha_watchdog_{SERVICE_ID}.id")

# Load or create a persistent unique ID for this machine
if os.path.exists(MACHINE_ID_FILE):
    try:
        with open(MACHINE_ID_FILE, "r", encoding="utf-8") as f:
            PERSISTENT_MACHINE_UUID = f.read().strip()
    except Exception:
        PERSISTENT_MACHINE_UUID = str(uuid.uuid4())
else:
    PERSISTENT_MACHINE_UUID = str(uuid.uuid4())
    try:
        with open(MACHINE_ID_FILE, "w", encoding="utf-8") as f:
            f.write(PERSISTENT_MACHINE_UUID)
    except Exception as e:
        print(f"WARNING: Unable to write machine identity token locally: {e}", file=sys.stderr)

# Unique Node ID (used internally for leadership lock)
NODE_ID = f"{PERSISTENT_MACHINE_UUID}:{PROJECT_PATH}"

# Short, human-readable alias for simple administrative database overrides
PATH_HASH = hashlib.sha1(PROJECT_PATH.encode('utf-8')).hexdigest()[:8]
HOSTNAME = socket.gethostname()
NODE_ALIAS = f"{HOSTNAME}:{PATH_HASH}"

# --- RESILIENT LOGGING ROUTER WITH FALLBACK INJECTION ---
# Use LogRecordFactory instead of a Filter to ensure EVERY log record
# (including from third-party libraries like Flask/Werkzeug) gets the node_alias attribute
old_log_record_factory = logging.getLogRecordFactory()

def custom_log_record_factory(*args, **kwargs):
    """Add 'node_alias' to every log record automatically"""
    record = old_log_record_factory(*args, **kwargs)
    record.node_alias = NODE_ALIAS
    return record

# Install our custom log record factory
logging.setLogRecordFactory(custom_log_record_factory)

# Configure logging format and destination
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Node: %(node_alias)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Get our logger instance
logger = logging.getLogger("failover_watchdog")

# --- UNIFIED RESOLVED VECTOR INTERPRETATION ---
# Figure out what command we should run to start your application
final_exec_args = []
mode_upper = LAUNCH_MODE.strip().upper()

if mode_upper == "PYTHON":
    # For Python mode: run with current Python interpreter
    if not TARGET.strip():
        # Auto-detect main script if not specified
        for candidate in ["main.py", "bot.py", "app.py"]:
            if os.path.exists(os.path.join(PROJECT_PATH, candidate)):
                TARGET = candidate
                break
        else:
            TARGET = "main.py"
    final_exec_args = [sys.executable, TARGET]

elif mode_upper == "DOCKER_COMPOSE":
    # For Docker Compose mode: run docker-compose up
    DOCKER_BIN = shutil.which("docker") or "docker"
    additional_flags = shlex.split(TARGET)
    final_exec_args = [DOCKER_BIN, "compose"] + additional_flags + ["up"]

elif mode_upper == "RAW":
    # For Raw mode: run the exact command specified
    if not CUSTOM_RAW_COMMAND.strip():
        print("CRITICAL LOGIC ERROR: LAUNCH_MODE is set to 'RAW' but 'CUSTOM_RAW_COMMAND' is empty!", file=sys.stderr)
        sys.exit(1)
    final_exec_args = shlex.split(CUSTOM_RAW_COMMAND)

# Compute a unique fingerprint for our current configuration
# If this changes, the node will refuse to start (prevents config mismatches in cluster)
START_COMMAND_STRING = " ".join(f'"{arg}"' for arg in final_exec_args)
if os.getenv("PYCHARM_HOSTED"):
    IDE_CONTEXT = "PyCharm"
elif os.getenv("VSCODE_PID"):
    IDE_CONTEXT = "VS Code"
else:
    IDE_CONTEXT = "Terminal/Shell"

CONFIG_PAYLOAD = f"{START_COMMAND_STRING}|{HEARTBEAT_INTERVAL}|{HEARTBEAT_TIMEOUT}"
CONFIG_FINGERPRINT = hashlib.sha256(CONFIG_PAYLOAD.encode('utf-8')).hexdigest()

# --- GLOBAL STATE VARIABLES ---
child_process = None          # Handle to the running application process
is_running = True             # Should the main loop keep running?
db_disconnect_tracker = None  # When did we last lose DB connection?

# --- MONGODB CLIENT INITIALIZATION ---
try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000, retryReads=True, retryWrites=True)
    db_collection = mongo_client[DATABASE_NAME][COLLECTION_NAME]
except Exception as init_err:
    print(f"CRITICAL: Failed to initialize PyMongo pool: {init_err}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# 2. SYSTEM SIGNAL INTERCEPTION & CONTAINER-AWARE LIFECYCLE
# ---------------------------------------------------------------------------
def handle_shutdown_signal(signum, frame):
    """
    Handle SIGINT (Ctrl+C) and SIGTERM signals gracefully.
    Cleans up the child process and releases the leadership lock.
    """
    global is_running
    logger.info(f"Received termination signal ({signal.Signals(signum).name}). Cleaning local environment...")
    is_running = False

# Register our shutdown handlers
signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)

def terminate_child():
    """
    Terminate the child application process (and any subprocesses it started).
    Handles both Windows and POSIX systems properly.
    """
    global child_process

    # For Docker Compose mode, try to stop the stack cleanly first
    if LAUNCH_MODE.strip().upper() == "DOCKER_COMPOSE":
        logger.warning("Executing proactive Docker Compose stack teardown sequence...")
        try:
            down_args = list(final_exec_args)
            # Replace "up" with "down" if present
            if down_args[-1] == "up" and "compose" in down_args:
                down_args[-1] = "down"
            else:
                down_args = [shutil.which("docker") or "docker", "compose", "down"]
            subprocess.run(down_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
            logger.info("Docker infrastructure successfully verified down.")
        except Exception as e:
            logger.error(f"Failed to cleanly invoke stack teardown sequence: {e}")

    # Terminate the actual child process
    if child_process and child_process.poll() is None:
        if IS_WINDOWS:
            # On Windows, use taskkill to kill the process tree
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(child_process.pid)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except Exception as e:
                logger.error(f"Windows taskkill tree termination failed: {e}")
        else:
            # On POSIX, use process groups to kill all subprocesses
            try:
                os.killpg(os.getpgid(child_process.pid), signal.SIGTERM)
                # Wait up to 5 seconds for graceful exit
                for _ in range(5):
                    if child_process.poll() is not None:
                        break
                    time.sleep(1)
                else:
                    # Force kill if still running
                    os.killpg(os.getpgid(child_process.pid), signal.SIGKILL)
                    child_process.wait()
            except Exception as e:
                logger.error(f"POSIX process tree kill sequence failed: {e}")

    child_process = None

# ---------------------------------------------------------------------------
# 3. TRANSITIONAL LEADER ELECTORATE (DETERMINISTIC LEASE MANAGEMENT)
# ---------------------------------------------------------------------------
def setup_database_indexes():
    """
    Creates an index on owner_node_id to speed up leadership queries.
    Safe to run multiple times (idempotent).
    """
    try:
        db_collection.create_index([("owner_node_id", pymongo.ASCENDING)], background=True)
        return True
    except PyMongoError as e:
        logger.error(f"Failed to optimize indexing configuration: {e}")
        return False

def bootstrap_and_validate_lock():
    """
    1. Creates the initial cluster control document in MongoDB if it doesn't exist
    2. Normalizes forced_leader_node (treats empty string as None)
    3. Validates that all nodes are running the same configuration
    """
    try:
        doc = db_collection.find_one({"_id": SERVICE_ID})
        
        if not doc:
            # Document doesn't exist yet - create it with default values
            initial_state = {
                "_id": SERVICE_ID,
                "owner_node_id": None,
                "forced_leader_node": None,
                "status": "offline",
                "last_heartbeat": datetime.fromtimestamp(0, tz=timezone.utc),
                "config_fingerprint": CONFIG_FINGERPRINT
            }
            try:
                db_collection.insert_one(initial_state)
                logger.info("Successfully bootstrapped the missing cluster control record.")
            except DuplicateKeyError:
                # Another node just created it - that's okay
                pass 
        else:
            # Document exists - normalize forced_leader_node if needed
            fl = doc.get("forced_leader_node")
            if fl == "":
                # Convert empty string to None for consistency
                logger.info(f"Normalizing forced_leader_node from empty string to None")
                db_collection.update_one(
                    {"_id": SERVICE_ID},
                    {"$set": {"forced_leader_node": None}}
                )

        # Verify that all nodes are running the same configuration
        if doc and doc.get("config_fingerprint") != CONFIG_FINGERPRINT:
            logger.critical("🚨 CONFIGURATION FINGERPRINT MISMATCH! Execution immediately halted.")
            sys.exit(1)
        
        return True

    except (ConnectionFailure, PyMongoError) as e:
        logger.error(f"Error checking cluster validation status: {e}. Retrying pool in 5s...")
        time.sleep(5)
        return False

def release_leadership():
    """
    Voluntarily step down as leader:
    1. Clears owner_node_id
    2. Sets status to "offline"
    3. Resets last_heartbeat to epoch
    """
    try:
        query = {"_id": SERVICE_ID, "owner_node_id": NODE_ID}
        update = {
            "$set": {
                "owner_node_id": None,
                "status": "offline",
                "last_heartbeat": datetime.fromtimestamp(0, tz=timezone.utc)
            }
        }
        db_collection.update_one(query, update)
        logger.info("Released leadership lock successfully.")
    except Exception as e:
        logger.error(f"Failed to issue clean leadership release: {e}")

def try_acquire_or_maintain_leadership(force_check_only=False, update_telemetry=False):
    """
    The core leadership election function.

    Args:
        force_check_only: Just check if we should still be leader (don't try to acquire)
        update_telemetry: Update extra fields (project_path, node_alias, etc.) when we become leader

    Returns:
        True if we are the current leader, False otherwise
    """
    global db_disconnect_tracker

    try:
        doc = db_collection.find_one({"_id": SERVICE_ID})
        if not doc:
            return False

        # Get forced leader and normalize it (treat empty string as None)
        forced_leader = doc.get("forced_leader_node")
        if forced_leader == "":
            forced_leader = None
        this_node_is_forced_leader = (forced_leader == NODE_ALIAS)

        if force_check_only:
            # We are already leader - just check if we should stay leader
            db_disconnect_tracker = None 
            is_owner = (doc.get("owner_node_id") == NODE_ID)

            if is_owner and forced_leader and not this_node_is_forced_leader:
                # We are active, but not the forced leader
                # Check two things:
                # 1. Is the document's node_alias the forced leader?
                # 2. Or is forced leader the one with the recent heartbeat?
                doc_node_alias = doc.get("node_alias")
                last_heartbeat = doc.get("last_heartbeat")
                forced_leader_is_active = False

                if doc_node_alias == forced_leader:
                    # Document is already from forced leader - definitely step down
                    forced_leader_is_active = True
                elif last_heartbeat:
                    try:
                        if last_heartbeat.tzinfo is None:
                            last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
                        time_since_heartbeat = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                        if time_since_heartbeat < HEARTBEAT_TIMEOUT:
                            forced_leader_is_active = True
                    except Exception as e:
                        logger.error(f"Error checking forced leader heartbeat: {e}")
                        forced_leader_is_active = False

                if forced_leader_is_active:
                    logger.warning(f"[LEADER STATUS] Administrative override active! Forced leader '{forced_leader}' is back online. Stepping down.")
                    return False

            return is_owner

        # --- NOT FORCE CHECK ONLY: TRY TO ACQUIRE OR MAINTAIN LEADERSHIP ---

        # Get current leader state
        current_owner_alias = doc.get("node_alias")
        last_heartbeat = doc.get("last_heartbeat")
        current_owner_active = False

        # Is the current owner still active?
        if doc.get("owner_node_id") and last_heartbeat:
            try:
                if last_heartbeat.tzinfo is None:
                    last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
                time_since_heartbeat = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                if time_since_heartbeat < HEARTBEAT_TIMEOUT:
                    current_owner_active = True
            except Exception as e:
                logger.error(f"Error checking current owner heartbeat: {e}")
                current_owner_active = False

        # Log detailed status
        if forced_leader:
            logger.info(f"[LEADER STATUS] Forced leader: '{forced_leader}', This node is forced leader: {this_node_is_forced_leader}")
        logger.info(f"[LEADER STATUS] Current leader: '{current_owner_alias}', Active: {current_owner_active}")
        logger.info(f"[LEADER STATUS] This node: '{NODE_ALIAS}'")

        # Determine what filter to use for MongoDB update
        if this_node_is_forced_leader:
            # THIS NODE IS THE FORCED LEADER: take over UNCONDITIONALLY!
            logger.info(f"[LEADER STATUS] This is the forced leader! Attempting to take over leadership...")
            filter_query = {
                "_id": SERVICE_ID  # Match the document no matter what state it's in
            }
        elif forced_leader:
            # THERE IS A FORCED LEADER, BUT IT'S NOT THIS NODE
            forced_leader_is_active = False
            doc_node_alias = doc.get("node_alias")

            # Check if forced leader is the one in the document and has active heartbeat
            if doc_node_alias == forced_leader:
                if doc.get("status") == "active":
                    forced_leader_is_active = True
                elif last_heartbeat:
                    try:
                        if last_heartbeat.tzinfo is None:
                            last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
                        time_since_heartbeat = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                        if time_since_heartbeat < HEARTBEAT_TIMEOUT:
                            forced_leader_is_active = True
                    except Exception as e:
                        logger.error(f"Error checking forced leader heartbeat: {e}")
                        forced_leader_is_active = False

            if forced_leader_is_active:
                # Forced leader is active - wait for it
                logger.info(f"[LEADER STATUS] Waiting for forced leader '{forced_leader}' to be active")
                return False
            else:
                # Forced leader is NOT active - anyone can take over!
                logger.info(f"[LEADER STATUS] Forced leader '{forced_leader}' is offline, allowing any node to take over")
                filter_query = {
                    "_id": SERVICE_ID,
                    "$expr": {
                        "$or": [
                            {"$eq": ["$owner_node_id", NODE_ID]},
                            {"$eq": ["$owner_node_id", None]},
                            {"$gt": [
                                "$$NOW", 
                                {"$add": ["$last_heartbeat", HEARTBEAT_TIMEOUT * 1000]}
                            ]}
                        ]
                    }
                }
        else:
            # NO FORCED LEADER: normal operation
            filter_query = {
                "_id": SERVICE_ID,
                "$expr": {
                    "$or": [
                        {"$eq": ["$owner_node_id", NODE_ID]},
                        {"$eq": ["$owner_node_id", None]},
                        {"$gt": [
                            "$$NOW", 
                            {"$add": ["$last_heartbeat", HEARTBEAT_TIMEOUT * 1000]}
                        ]}
                    ]
                }
            }

        # Build the update document
        update_modifier = {
            "$set": {
                "owner_node_id": NODE_ID,
                "status": "active",
                "config_fingerprint": CONFIG_FINGERPRINT
            },
            "$currentDate": {
                "last_heartbeat": True  # Set last_heartbeat to current time (MongoDB server time)
            }
        }

        # Add extra telemetry data if requested
        if update_telemetry:
            update_modifier["$set"].update({
                "project_path": PROJECT_PATH,
                "launch_mode": LAUNCH_MODE,
                "node_alias": NODE_ALIAS,
                "context": IDE_CONTEXT,
                "hostname": HOSTNAME
            })

        # Execute the atomic findOneAndUpdate!
        # This is the magic: only one node will successfully update the document
        result = db_collection.find_one_and_update(
            filter_query, update_modifier, upsert=False, return_document=pymongo.ReturnDocument.AFTER
        )

        db_disconnect_tracker = None 
        return result and result.get("owner_node_id") == NODE_ID

    except (ConnectionFailure, PyMongoError) as e:
        logger.error(f"Database network communication fault: {e}")

        # Track how long we've been disconnected
        if db_disconnect_tracker is None:
            db_disconnect_tracker = time.time()

        # If we've been disconnected too long, step down
        if (time.time() - db_disconnect_tracker) > MAX_NETWORK_GRACE_S:
            logger.critical(f"🚨 CIRCUIT BREAKER TRIPPED: DB offline >{MAX_NETWORK_GRACE_S}s. Dropping lease.")
            return False 

        return True

# ---------------------------------------------------------------------------
# 4. RUNTIME MAIN LOOP
# ---------------------------------------------------------------------------
def main():
    global child_process, is_running

    # Print startup banner
    print(f"======================================================================", flush=True)
    print(f"🔥 HA PROCESS WATCHDOG ACTIVE | Mode: {LAUNCH_MODE}", flush=True)
    print(f"Service ID : {SERVICE_ID}", flush=True)
    print(f"NODE ALIAS : {NODE_ALIAS}", flush=True) 
    print(f"Vector     : {final_exec_args}", flush=True)
    print(f"======================================================================\n", flush=True)

    # Wait a random short time (avoids thundering herd on startup)
    time.sleep(random.uniform(0.5, 3.5))

    # Set up database and bootstrap
    if not setup_database_indexes() or not bootstrap_and_validate_lock():
        return

    # Initialize state
    is_leader = False
    local_failures = 0
    last_heartbeat_time = 0

    # MAIN LOOP
    while is_running:
        try:
            # --- STANDBY MONITORING LAYER ---
            if not is_leader:
                logger.info(f"[STATUS] This node is standby, checking for leadership...")
                if not try_acquire_or_maintain_leadership(update_telemetry=True):
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Double-check we really got leadership
                if not bootstrap_and_validate_lock():
                    release_leadership()
                    time.sleep(CHECK_INTERVAL)
                    continue

                # We are now leader!
                logger.info(f"🎉 SUCCESS: This node '{NODE_ALIAS}' captured distributed leadership! Transitioning to ACTIVE.")
                is_leader = True
                local_failures = 0

            # --- ACTIVE SUPERVISOR LAYER ---
            if is_leader:
                # Check if we need to start/restart the application
                if child_process is None or child_process.poll() is not None:
                    if child_process and child_process.poll() is not None:
                        # Application crashed!
                        exit_code = child_process.poll()
                        local_failures += 1
                        logger.warning(f"[BOT STATUS] Bot process crashed (Exit code: {exit_code}). Failures: {local_failures}/{LOCAL_RETRY_LIMIT}")
                        child_process = None

                        # If we've failed too many times, give up and step down
                        if local_failures > LOCAL_RETRY_LIMIT:
                            logger.critical(f"[BOT STATUS] Local recovery limit breached. Relinquishing leadership lock.")
                            terminate_child() 
                            release_leadership()
                            is_leader = False
                            time.sleep(CHECK_INTERVAL)
                            continue

                    # Check we still have leadership before starting the app
                    if not try_acquire_or_maintain_leadership():
                        logger.warning(f"[LEADER STATUS] Split-brain caught during crash recovery phase. Reverting to standby.")
                        is_leader = False
                        continue

                    # Start the application!
                    logger.info(f"[BOT STATUS] Starting bot process with: {final_exec_args}")
                    try:
                        if IS_WINDOWS:
                            child_process = subprocess.Popen(final_exec_args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                        else:
                            child_process = subprocess.Popen(final_exec_args, start_new_session=True)

                        # Wait for app to start up
                        time.sleep(STARTUP_GRACE_PERIOD)

                        # Check if app crashed during startup
                        if child_process.poll() is not None:
                            logger.error(f"[BOT STATUS] Bot process died within startup grace window.")
                            continue

                        # Verify we still have leadership after startup
                        if try_acquire_or_maintain_leadership():
                            last_heartbeat_time = time.time()
                            logger.info(f"[BOT STATUS] Bot passed initial checks. Heartbeat tracking active.")
                        else:
                            logger.critical(f"[LEADER STATUS] Failed to retain lock during verification. Stopping bot.")
                            terminate_child()
                            is_leader = False
                            continue
                    except Exception as e:
                        logger.error(f"[BOT STATUS] System failure attempting to start bot: {e}")
                        child_process = None
                        time.sleep(CHECK_INTERVAL)
                        continue

                # --- STEADY-STATE RUNTIME OPERATION ---
                current_time = time.time()

                # Check every second if we should still be leader
                if not try_acquire_or_maintain_leadership(force_check_only=True):
                    logger.critical(f"[LEADER STATUS] STEPDOWN TRIGGERED: Node identity overtaken or manual override active! Stopping bot.")
                    terminate_child()
                    is_leader = False
                    continue

                # Send heartbeat if it's time
                if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                    if child_process.poll() is None:
                        if try_acquire_or_maintain_leadership(update_telemetry=False):
                            logger.info(f"[HEARTBEAT] Heartbeat logged successfully.")
                            last_heartbeat_time = current_time
                            local_failures = 0 
                        else:
                            logger.critical(f"[LEADER STATUS] LEASE LOST: Lock overridden during heartbeat update! Stopping bot.")
                            terminate_child()
                            is_leader = False
                    else:
                        logger.warning(f"[BOT STATUS] Bot process died inside scheduled pulse window.")

                time.sleep(1)

        except PyMongoError as e:
            logger.error(f"[DATABASE] Database infrastructure connectivity issue: {e}. Re-verifying pool...")
            time.sleep(2)
        except Exception as e:
            logger.error(f"[SYSTEM] Unhandled exception in runtime supervisor loop: {e}")
            time.sleep(2)

    # --- SHUTDOWN SEQUENCE ---
    terminate_child()
    if is_leader:
        release_leadership()
    logger.info(f"[STATUS] Watchdog cleanup executed cleanly. Shutting down wrapper.")

# Start the keep-alive web server (prevents platforms like Render from sleeping your app)
keep_alive()

# Run the main loop!
if __name__ == "__main__":
    main()
