#!/usr/bin/env python3

import os
import sys
import json
import random
import logging
import asyncio
import hashlib
import signal
from datetime import datetime
from urllib.parse import urlparse
import httpx
from PIL import Image
import imagehash
from telethon import TelegramClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

SIMILARITY_THRESHOLD = 5

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

shutdown_requested = False
ACTIVE_TASKS = set()

def handle_shutdown():
    global shutdown_requested
    shutdown_requested = True
    logging.info("Shutdown requested. Waiting for ongoing tasks to complete...")

# ============================================================================
# CONFIGURATION LOADERS
# ============================================================================
#
# ✅ COMMENT HANDLING - USER-FRIENDLY FEATURE
# ----------------------------------------------------------------------------
# All configuration loaders automatically skip:
# • Lines starting with # (comments)
# • Empty/whitespace-only lines
#
# WHY? This allows you to keep documentation directly in config files!
# No need to manually remove instruction comments before using.
#
# Example - config.txt can contain:
#   [mongodb]
#   # MongoDB Connection URI
#   # Format: mongodb://host:port
#   uri = mongodb://localhost:27017
#
# → Loader extracts: "mongodb://localhost:27017"
# → Ignores all comment lines automatically
#
# This applies to:
# • config.txt [mongodb] section (load_mongodb_uri)
# • config.txt [wallhaven] section (used by update-link-db.py)
# • config.txt [telegram] section (load_telegram_config)
# • categories.txt (load_bot_config) - Pipe-delimited format
#
# ➡️ You can safely leave comments in ALL config files!
# ============================================================================

def load_mongodb_uri():
    """
    Load MongoDB URI from config.txt file.
    
    File Parsing:
    • Reads file line by line
    • Skips empty lines
    • Skips comment lines (starting with #)
    • Looks for [mongodb] section
    • Returns value of 'uri' key in that section
    
    This allows users to keep comments/instructions in the file:
        [mongodb]
        # MongoDB Connection URI
        uri = mongodb://localhost:27017
    
    Returns:
        str: MongoDB connection URI
    
    Exits:
        If file doesn't exist or contains no valid URI
    """
    if not os.path.exists('config.txt'):
        logging.error("config.txt not found!")
        sys.exit(1)
    
    with open('config.txt', 'r') as f:
        in_mongodb_section = False
        for line in f:
            line = line.strip()
            # Check for section headers
            if line == '[mongodb]':
                in_mongodb_section = True
                continue
            if line.startswith('[') and line.endswith(']'):
                in_mongodb_section = False
                continue
            # Skip empty lines and comments
            if in_mongodb_section and line and not line.startswith('#'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip() == 'uri':
                        return value.strip()
        
        # No valid URI found
        logging.error("config.txt contains no valid MongoDB URI in [mongodb] section!")
        sys.exit(1)

def load_telegram_config():
    if not os.path.exists('config.txt'):
        logging.error("config.txt not found!")
        logging.error("Please create config.txt with [telegram] section:")
        logging.error("  api_id = your_api_id")
        logging.error("  api_hash = your_api_hash")
        logging.error("  bot_token = your_bot_token")
        sys.exit(1)
    config = {}
    in_telegram_section = False
    with open('config.txt', 'r') as f:
        for line in f:
            line = line.strip()
            # Check for section headers
            if line == '[telegram]':
                in_telegram_section = True
                continue
            if line.startswith('[') and line.endswith(']'):
                in_telegram_section = False
                continue
            if in_telegram_section and line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().upper()
                value = value.strip()
                if key in ['API_ID', 'API_HASH', 'BOT_TOKEN']:
                    config[key] = value
    required_keys = ['API_ID', 'API_HASH', 'BOT_TOKEN']
    for key in required_keys:
        if key not in config:
            logging.error(f"Missing {key} in [telegram] section of config.txt")
            sys.exit(1)
    return config

def load_bot_config():
    if not os.path.exists('categories.txt'):
        logging.error("categories.txt not found!")
        logging.error("Please create categories.txt with format:")
        logging.error("  category | group_id | interval_seconds | search_term1, term2")
        logging.error("Example:")
        logging.error("  nature | -1002996780898 | 3050 | tree, water, river")
        logging.error("  anime | -1002935599065 | 1000 | anime, cartoon")
        sys.exit(1)
    config = {}
    with open('categories.txt', 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '|' not in line:
                logging.warning(f"Skipping invalid line {line_num}: {line}")
                continue
            parts = line.split('|')
            if len(parts) < 3:
                logging.warning(f"Skipping invalid line {line_num} (expected at least 3 parts): {line}")
                continue
            category = parts[0].strip()
            group_id_str = parts[1].strip()
            interval_str = parts[2].strip()
            try:
                group_id = int(group_id_str)
                interval = int(interval_str)
            except ValueError:
                logging.warning(f"Invalid group_id or interval on line {line_num}: {line}")
                continue
            if category and group_id and interval > 0:
                config[category] = {
                    'group_id': group_id,
                    'interval': interval
                }
    if not config:
        logging.error("No valid category configurations found in categories.txt!")
        sys.exit(1)
    return config

def connect_to_mongodb(uri):
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client['wallpaper-bot']
        collection = db.wallhaven
        logging.info("✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)")
        return collection
    except ConnectionFailure as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

def calculate_hashes(filepath):
    try:
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(block)
        sha256 = sha256_hash.hexdigest()
        p_hash = str(imagehash.average_hash(Image.open(filepath)))
        return sha256, p_hash
    except Exception as e:
        logging.error(f"Error calculating hashes for {filepath}: {e}")
        return None, None

async def check_duplicate_hashes(collection, sha256, p_hash):
    exact_match = collection.find_one({"sha256": sha256})
    if exact_match:
        return "duplicate", {
            "reason": "Duplicate",
            "details": {
                "type": "SHA256_match",
                "wallpaper_id": exact_match.get('wallpaper_id')
            }
        }
    all_phashes = collection.find(
        {"phash": {"$ne": None, "$exists": True}},
        {"phash": 1, "wallpaper_id": 1}
    )
    max_diff = 64
    new_hash = imagehash.hex_to_hash(p_hash)
    for doc in all_phashes:
        phash_hex = doc.get('phash')
        if not phash_hex or not phash_hex.strip():
            continue
        try:
            db_hash = imagehash.hex_to_hash(phash_hex)
            hash_diff = new_hash - db_hash
            if hash_diff < SIMILARITY_THRESHOLD:
                similarity_percentage = ((max_diff - hash_diff) / max_diff) * 100
                return "similar", {
                    "reason": "Similar",
                    "details": {
                        "type": "pHash_match",
                        "diff": hash_diff,
                        "similarity_percentage": round(similarity_percentage, 2),
                        "wallpaper_id": doc.get('wallpaper_id')
                    }
                }
        except Exception as e:
            logging.warning(f"Error comparing pHash: {e}")
            continue
    return "proceed", None

async def download_image(url, filename):
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            with open(filename, "wb") as f:
                f.write(response.content)
        return filename
    except Exception as e:
        logging.warning(f"Download failed for {url}: {e}")
        return None

def get_pending_wallpaper(collection, category):
    try:
        count = collection.count_documents(
            {"category": category, "status": "link_added"}
        )
        if count == 0:
            return None
        pipeline = [
            {"$match": {"category": category, "status": "link_added"}},
            {"$sample": {"size": 1}}
        ]
        result = list(collection.aggregate(pipeline))
        return result[0] if result else None
    except Exception as e:
        logging.error(f"Database query failed: {e}")
        return None

def update_wallpaper_status(collection, wallpaper_id, status, sha256=None, phash=None, 
                           tg_response=None, reasons=None):
    try:
        update_data = {"status": status}
        if sha256:
            update_data["sha256"] = sha256
        if phash:
            update_data["phash"] = phash
        if tg_response:
            update_data["tg_response"] = tg_response
        if reasons:
            existing = collection.find_one({"wallpaper_id": wallpaper_id})
            if existing and existing.get('tg_response'):
                tg_resp = existing['tg_response']
                if isinstance(tg_resp, str):
                    try:
                        tg_resp = json.loads(tg_resp)
                    except:
                        tg_resp = {}
                tg_resp.update(reasons)
                update_data["tg_response"] = tg_resp
            else:
                update_data["tg_response"] = reasons
        collection.update_one(
            {"wallpaper_id": wallpaper_id},
            {"$set": update_data}
        )
    except Exception as e:
        logging.error(f"Failed to update wallpaper {wallpaper_id}: {e}")

async def send_wallpaper_to_group(client, collection, category, group_id):
    if shutdown_requested:
        logging.info(f"Skipping wallpaper send for {category} due to shutdown request.")
        return
    task = asyncio.current_task()
    ACTIVE_TASKS.add(task)
    try:
        wallpaper = get_pending_wallpaper(collection, category)
        if not wallpaper:
            logging.info(f"No pending wallpapers found for category '{category}'")
            return
        wallpaper_id = wallpaper.get('wallpaper_id')
        jpg_url = wallpaper.get('jpg_url')
        tags = wallpaper.get('tags', [])
        search_term = wallpaper.get('search_term', category)
        filename = f"{category}_{random.randint(1000, 9999)}_{os.path.basename(urlparse(jpg_url).path)}"
        logging.info(f"[{category}] Processing {wallpaper_id}...")
        path = await download_image(jpg_url, filename)
        if not path:
            reasons = {"reason": "Download failed", "url": jpg_url}
            update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
            logging.error(f"[{category}] Download failed for {wallpaper_id}")
            return
        sha256, phash = calculate_hashes(path)
        if not sha256 or not phash:
            reasons = {"reason": "Hashing failed"}
            update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
            os.remove(path)
            logging.error(f"[{category}] Hashing failed for {wallpaper_id}")
            return
        status_check, reasons = await check_duplicate_hashes(collection, sha256, phash)
        if status_check in ["duplicate", "similar"]:
            log_details = f"{reasons['details']['type']}"
            if 'similarity_percentage' in reasons['details']:
                log_details += f" ({reasons['details']['similarity_percentage']}% similar)"
            logging.warning(f"[{category}] Skipping {wallpaper_id}: {reasons['reason']} - {log_details}")
            update_wallpaper_status(collection, wallpaper_id, "skipped", sha256, phash, reasons=reasons)
            os.remove(path)
            return
        try:
            preview_response = await client.send_file(
                group_id,
                path,
                force_document=False
            )
            await asyncio.sleep(3)
            hd_response = await client.send_file(
                group_id,
                path,
                caption="🖼️ HD Download",
                force_document=True
            )
            tg_response = {
                "preview": {
                    "message_id": preview_response.id,
                    "date": preview_response.date.isoformat() if preview_response.date else None
                },
                "hd": {
                    "message_id": hd_response.id,
                    "date": hd_response.date.isoformat() if hd_response.date else None
                },
                "group_id": group_id,
                "uploaded_at": datetime.utcnow().isoformat()
            }
            update_wallpaper_status(
                collection,
                wallpaper_id,
                "posted",
                sha256,
                phash,
                tg_response=tg_response
            )
            logging.info(f"[{category}] ✓ Posted {wallpaper_id} to group {group_id}")
        except Exception as telegram_e:
            reasons = {"reason": "Telegram upload failed", "error": str(telegram_e)}
            update_wallpaper_status(collection, wallpaper_id, "failed", sha256, phash, reasons=reasons)
            logging.error(f"[{category}] Telegram upload failed for {wallpaper_id}: {telegram_e}")
        finally:
            if os.path.exists(path):
                os.remove(path)
    finally:
        ACTIVE_TASKS.discard(task)

async def main():
    logging.info("=" * 70)
    logging.info("Telegram Wallpaper Upload Bot Starting...")
    logging.info("=" * 70)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)
    loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
    mongodb_uri = load_mongodb_uri()
    tg_config = load_telegram_config()
    bot_config = load_bot_config()
    logging.info(f"✓ Loaded {len(bot_config)} category configurations")
    for cat, cfg in bot_config.items():
        logging.info(f"  - {cat}: Group {cfg['group_id']}, Every {cfg['interval']}s ({cfg['interval']//60}min)")
    collection = connect_to_mongodb(mongodb_uri)
    client = TelegramClient(
        'wallpaper_bot_session',
        int(tg_config['API_ID']),
        tg_config['API_HASH']
    )
    await client.start(bot_token=tg_config['BOT_TOKEN'])
    logging.info("✓ Telegram bot connected")
    scheduler = AsyncIOScheduler()
    for category, cfg in bot_config.items():
        group_id = cfg['group_id']
        interval = cfg['interval']
        scheduler.add_job(
            send_wallpaper_to_group,
            'interval',
            args=[client, collection, category, group_id],
            seconds=interval,
            id=f'job_{category}',
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60
        )
        logging.info(f"✓ Scheduled job for '{category}' (every {interval}s / {interval//60}min)")
    scheduler.start()
    logging.info("=" * 70)
    logging.info("Bot is running. Press Ctrl+C to stop.")
    logging.info("=" * 70)
    while not shutdown_requested:
        await asyncio.sleep(1)
    if ACTIVE_TASKS:
        logging.info(f"Waiting for {len(ACTIVE_TASKS)} active tasks to finish...")
        await asyncio.gather(*ACTIVE_TASKS)
    scheduler.shutdown()
    await client.disconnect()
    
    logging.info("=" * 70)
    logging.info("Bot stopped gracefully. All tasks completed.")
    logging.info("=" * 70)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Program interrupted by user.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1)
