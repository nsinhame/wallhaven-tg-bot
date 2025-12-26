#!/usr/bin/env python3

import os
import sys
import json
import random
import string
import logging
import asyncio
import hashlib
import signal
import gc
import subprocess
from datetime import datetime
from urllib.parse import urlparse
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

shutdown_requested = False
ACTIVE_TASKS = set()
BOT_TOKEN = None

def handle_shutdown():
    global shutdown_requested
    shutdown_requested = True
    logging.info("Shutdown requested. Waiting for ongoing tasks to complete...")

def load_mongodb_uri():
    if not os.path.exists('config.txt'):
        logging.error("config.txt not found!")
        sys.exit(1)
    
    with open('config.txt', 'r') as f:
        in_mongodb_section = False
        for line in f:
            line = line.strip()
            if line == '[mongodb]':
                in_mongodb_section = True
                continue
            if line.startswith('[') and line.endswith(']'):
                in_mongodb_section = False
                continue
            if in_mongodb_section and line and not line.startswith('#'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip() == 'uri':
                        return value.strip()
        
        logging.error("config.txt contains no valid MongoDB URI in [mongodb] section!")
        sys.exit(1)

def load_telegram_config():
    if not os.path.exists('config.txt'):
        logging.error("config.txt not found!")
        sys.exit(1)
    
    config = {}
    in_telegram_section = False
    with open('config.txt', 'r') as f:
        for line in f:
            line = line.strip()
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
                if key == 'BOT_TOKEN':
                    config[key] = value
    
    if 'BOT_TOKEN' not in config:
        logging.error("Missing BOT_TOKEN in [telegram] section of config.txt")
        sys.exit(1)
    
    return config

def load_bot_config():
    if not os.path.exists('categories.txt'):
        logging.error("categories.txt not found!")
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
        return sha256
    except Exception as e:
        logging.error(f"Error calculating hashes for {filepath}: {e}")
        return None

def check_duplicate_hashes(collection, sha256):
    exact_match = collection.find_one({"sha256": sha256})
    if exact_match:
        return "duplicate", {
            "reason": "Duplicate",
            "details": {
                "type": "SHA256_match",
                "wallpaper_id": exact_match.get('wallpaper_id')
            }
        }
    return "proceed", None

def download_image(url, filename):
    try:
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()
        with open(filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except Exception as e:
        logging.warning(f"Download failed for {url}: {e}")
        return None

def generate_thumbnail(image_path, max_size_kb=200):
    """
    Generate thumbnail using ImageMagick (convert command).
    This uses zero Python memory - all processing is done by external process.
    
    Args:
        image_path: Path to the original image
        max_size_kb: Maximum thumbnail size in KB (default: 200)
    
    Returns:
        Path to thumbnail file or None if generation fails
    """
    try:
        thumb_path = image_path.replace(os.path.splitext(image_path)[1], '_thumb.jpg')
        
        # ImageMagick command to create thumbnail:
        # - Resize to fit within 320x320 while maintaining aspect ratio
        # - Flatten alpha channel to white background
        # - Convert to JPEG with quality 50 (lower quality for smaller size)
        # - Strip metadata to reduce size
        cmd = [
            'convert',
            image_path,
            '-thumbnail', '320x320>',
            '-background', 'white',
            '-alpha', 'remove',
            '-alpha', 'off',
            '-quality', '50',
            '-strip',
            thumb_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            logging.error(f"ImageMagick failed: {result.stderr}")
            return None
        
        # Check size and reduce quality if needed
        if os.path.exists(thumb_path):
            size_kb = os.path.getsize(thumb_path) / 1024
            
            # If still too large, reduce quality and size further
            if size_kb > max_size_kb:
                cmd = [
                    'convert',
                    image_path,
                    '-thumbnail', '160x160>',
                    '-background', 'white',
                    '-alpha', 'remove',
                    '-alpha', 'off',
                    '-quality', '20',
                    '-strip',
                    thumb_path
                ]
                subprocess.run(cmd, capture_output=True, timeout=30)
                size_kb = os.path.getsize(thumb_path) / 1024
            
            logging.info(f"Generated thumbnail: {size_kb:.1f}KB")
            return thumb_path
        
        return None
            
    except subprocess.TimeoutExpired:
        logging.error(f"ImageMagick timeout for {image_path}")
        return None
    except FileNotFoundError:
        logging.error("ImageMagick not found! Install with: sudo apt install imagemagick")
        return None
    except Exception as e:
        logging.error(f"Failed to generate thumbnail for {image_path}: {e}")
        return None
    finally:
        # Force garbage collection
        gc.collect()

def get_pending_wallpapers(collection, category, count=3):
    try:
        available = collection.count_documents(
            {"category": category, "status": "link_added"}
        )
        if available == 0:
            return []
        
        fetch_count = min(count, available)
        
        pipeline = [
            {"$match": {"category": category, "status": "link_added"}},
            {"$sample": {"size": fetch_count}}
        ]
        result = list(collection.aggregate(pipeline))
        return result
    except Exception as e:
        logging.error(f"Database query failed: {e}")
        return []

def update_wallpaper_status(collection, wallpaper_id, status, sha256=None, 
                           tg_response=None, reasons=None):
    try:
        update_data = {"status": status}
        if sha256:
            update_data["sha256"] = sha256
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

def telegram_send_photo(chat_id, photo_path):
    """Send photo using Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': chat_id}
            response = requests.post(url, data=data, files=files, timeout=120)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logging.error(f"Telegram sendPhoto failed: {e}")
        return None

def telegram_send_document(chat_id, document_path, thumbnail_path=None):
    """Send document using Telegram Bot API with optional thumbnail"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(document_path, 'rb') as document:
            files = {'document': document}
            data = {'chat_id': chat_id}
            
            if thumbnail_path and os.path.exists(thumbnail_path):
                with open(thumbnail_path, 'rb') as thumb:
                    files['thumbnail'] = thumb
                    response = requests.post(url, data=data, files=files, timeout=120)
            else:
                response = requests.post(url, data=data, files=files, timeout=120)
            
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logging.error(f"Telegram sendDocument failed: {e}")
        return None

def telegram_send_media_group(chat_id, media_list, is_document=False):
    """Send media group (album) using Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    
    try:
        # Prepare media array
        media = []
        files_dict = {}
        
        for idx, item in enumerate(media_list):
            file_key = f"file{idx}"
            files_dict[file_key] = open(item['path'], 'rb')
            
            media_item = {
                "type": "document" if is_document else "photo",
                "media": f"attach://{file_key}"
            }
            
            # Add thumbnail for documents if available
            if is_document and item.get('thumbnail'):
                thumb_key = f"thumb{idx}"
                files_dict[thumb_key] = open(item['thumbnail'], 'rb')
                media_item["thumbnail"] = f"attach://{thumb_key}"
            
            media.append(media_item)
        
        data = {
            'chat_id': chat_id,
            'media': json.dumps(media)
        }
        
        response = requests.post(url, data=data, files=files_dict, timeout=120)
        
        # Close all file handles
        for f in files_dict.values():
            f.close()
        
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        logging.error(f"Telegram sendMediaGroup failed: {e}")
        # Make sure files are closed even on error
        for f in files_dict.values():
            if not f.closed:
                f.close()
        return None

async def send_wallpaper_to_group(collection, category, group_id):
    if shutdown_requested:
        logging.info(f"Skipping wallpaper send for {category} due to shutdown request.")
        return
    
    task = asyncio.current_task()
    ACTIVE_TASKS.add(task)
    
    try:
        wallpapers = get_pending_wallpapers(collection, category, count=3)
        if not wallpapers:
            logging.info(f"No pending wallpapers found for category '{category}'")
            return
        
        logging.info(f"[{category}] Processing {len(wallpapers)} wallpapers as a group...")
        
        wallpaper_data = []
        
        for wallpaper in wallpapers:
            wallpaper_id = wallpaper.get('wallpaper_id')
            jpg_url = wallpaper.get('jpg_url')
            tags = wallpaper.get('tags', [])
            search_term = wallpaper.get('search_term', category)
            
            ext = os.path.splitext(urlparse(jpg_url).path)[1]
            random_name = (
                ''.join(random.choices(string.ascii_lowercase, k=2)) +
                ''.join(random.choices(string.digits, k=2)) +
                ''.join(random.choices(string.ascii_lowercase, k=3)) +
                ext
            )
            filename = os.path.join("wall-cache", random_name)
            
            logging.info(f"[{category}] Processing {wallpaper_id}...")
            
            path = download_image(jpg_url, filename)
            if not path:
                reasons = {"reason": "Download failed", "url": jpg_url}
                update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
                logging.error(f"[{category}] Download failed for {wallpaper_id}")
                continue
            
            file_size_mb = os.path.getsize(path) / (1024 * 1024)
            thumbnail_path = None
            if file_size_mb > 9.5:
                logging.info(f"[{category}] File size {file_size_mb:.2f}MB > 9.5MB, generating thumbnail...")
                thumbnail_path = generate_thumbnail(path)
                if not thumbnail_path:
                    logging.warning(f"[{category}] Failed to generate thumbnail for {wallpaper_id}, proceeding without it")
            
            sha256 = calculate_hashes(path)
            if not sha256:
                reasons = {"reason": "Hashing failed"}
                update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
                os.remove(path)
                logging.error(f"[{category}] Hashing failed for {wallpaper_id}")
                continue
            
            status_check, reasons = check_duplicate_hashes(collection, sha256)
            if status_check in ["duplicate", "similar"]:
                log_details = f"{reasons['details']['type']}"
                logging.warning(f"[{category}] Skipping {wallpaper_id}: {reasons['reason']} - {log_details}")
                update_wallpaper_status(collection, wallpaper_id, "skipped", sha256, reasons=reasons)
                os.remove(path)
                if thumbnail_path and os.path.exists(thumbnail_path):
                    os.remove(thumbnail_path)
                continue
            
            wallpaper_data.append({
                'wallpaper_id': wallpaper_id,
                'path': path,
                'thumbnail': thumbnail_path,
                'sha256': sha256,
                'tags': tags,
                'search_term': search_term
            })
        
        if not wallpaper_data:
            logging.warning(f"[{category}] No valid wallpapers to send after filtering")
            return
        
        logging.info(f"[{category}] Sending {len(wallpaper_data)} wallpapers to Telegram...")
        
        try:
            # Send preview photos
            preview_responses = telegram_send_media_group(group_id, wallpaper_data, is_document=False)
            if not preview_responses:
                raise Exception("Failed to send preview images")
            
            await asyncio.sleep(3)
            
            # Send HD versions
            has_large_files = any(item['thumbnail'] is not None for item in wallpaper_data)
            
            if has_large_files:
                # Send individually with thumbnails
                hd_responses = []
                for item in wallpaper_data:
                    response = telegram_send_document(group_id, item['path'], item['thumbnail'])
                    if response:
                        hd_responses.append(response)
                    await asyncio.sleep(0.5)
            else:
                # Send as media group
                hd_responses = telegram_send_media_group(group_id, wallpaper_data, is_document=True)
                if not hd_responses:
                    hd_responses = []
            
            # Update database
            for i, item in enumerate(wallpaper_data):
                preview_result = preview_responses.get('result', [{}])
                hd_result = hd_responses[i].get('result', {}) if isinstance(hd_responses, list) and i < len(hd_responses) else {}
                
                if not hd_result and isinstance(hd_responses, dict):
                    hd_result = hd_responses.get('result', [{}])[i] if i < len(hd_responses.get('result', [])) else {}
                
                tg_response = {
                    "preview": {
                        "message_id": preview_result[i].get('message_id') if i < len(preview_result) else None,
                        "date": preview_result[i].get('date') if i < len(preview_result) else None
                    },
                    "hd": {
                        "message_id": hd_result.get('message_id'),
                        "date": hd_result.get('date')
                    },
                    "group_id": group_id,
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "album_size": len(wallpaper_data)
                }
                
                update_wallpaper_status(collection, item['wallpaper_id'], "posted", item['sha256'], tg_response=tg_response)
                logging.info(f"[{category}] ✓ Posted {item['wallpaper_id']} to group {group_id} (album {i+1}/{len(wallpaper_data)})")
            
        except Exception as telegram_e:
            logging.error(f"[{category}] Telegram upload failed: {telegram_e}")
            for item in wallpaper_data:
                reasons = {"reason": "Telegram upload failed", "error": str(telegram_e)}
                update_wallpaper_status(collection, item['wallpaper_id'], "failed", item['sha256'], reasons=reasons)
        
        finally:
            for item in wallpaper_data:
                if os.path.exists(item['path']):
                    os.remove(item['path'])
                if item['thumbnail'] and os.path.exists(item['thumbnail']):
                    os.remove(item['thumbnail'])
            
            # Force garbage collection after processing batch
            gc.collect()
    
    finally:
        ACTIVE_TASKS.discard(task)

async def main():
    global BOT_TOKEN
    
    logging.info("=" * 70)
    logging.info("Telegram Wallpaper Upload Bot (Lightweight) Starting...")
    logging.info("=" * 70)
    
    cache_dir = "wall-cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        logging.info(f"✓ Created cache directory: {cache_dir}")
    else:
        logging.info(f"✓ Using existing cache directory: {cache_dir}")
    
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)
    loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
    
    mongodb_uri = load_mongodb_uri()
    tg_config = load_telegram_config()
    BOT_TOKEN = tg_config['BOT_TOKEN']
    bot_config = load_bot_config()
    
    logging.info(f"✓ Loaded {len(bot_config)} category configurations")
    for cat, cfg in bot_config.items():
        logging.info(f"  - {cat}: Group {cfg['group_id']}, Every {cfg['interval']}s ({cfg['interval']//60}min)")
    
    collection = connect_to_mongodb(mongodb_uri)
    logging.info("✓ Telegram bot configured (using direct API)")
    
    scheduler = AsyncIOScheduler()
    for category, cfg in bot_config.items():
        group_id = cfg['group_id']
        interval = cfg['interval']
        scheduler.add_job(
            send_wallpaper_to_group,
            'interval',
            args=[collection, category, group_id],
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
