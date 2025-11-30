#!/usr/bin/env python3

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     TELEGRAM WALLPAPER UPLOAD BOT                            ║
║                                                                              ║
║  A sophisticated automation bot that fetches wallpapers from MongoDB and     ║
║  uploads them to category-specific Telegram groups with intelligent         ║
║  duplicate detection using SHA256 and perceptual hashing (pHash).           ║
║                                                                              ║
║  KEY FEATURES:                                                               ║
║  • Random wallpaper selection per category                                   ║
║  • Custom posting intervals per category (configurable in config.txt)        ║
║  • Duplicate detection: SHA256 (exact) + pHash (similar images)              ║
║  • Two-stage upload: Preview (photo) + HD version (document)                 ║
║  • Database status tracking: link_added → posted/failed/skipped              ║
║  • Graceful shutdown with active task completion                             ║
║  • Independent scheduling per category using APScheduler                     ║
║                                                                              ║
║  WORKFLOW:                                                                   ║
║  1. Load configuration (MongoDB URI, Telegram credentials, categories)       ║
║  2. Connect to MongoDB and Telegram Bot API                                  ║
║  3. Schedule independent jobs for each category with custom intervals        ║
║  4. For each scheduled run per category:                                     ║
║     a. Fetch one RANDOM pending wallpaper (status="link_added")             ║
║     b. Download image temporarily                                            ║
║     c. Calculate SHA256 hash (exact duplicate check)                         ║
║     d. Calculate pHash (perceptual hash for similar image detection)         ║
║     e. Check database for duplicates (SHA256 or pHash similarity < 5)        ║
║     f. If duplicate: mark as "skipped", delete temp file, exit               ║
║     g. If unique: upload to Telegram (preview + HD document)                 ║
║     h. Update database with hashes, Telegram response, status="posted"       ║
║     i. Clean up temporary file                                               ║
║  5. Repeat step 4 at configured intervals until shutdown                     ║
║                                                                              ║
║  CONFIGURATION FILES:                                                        ║
║  • config.txt         - Categories, group IDs, intervals (used by this bot)  ║
║  • tg-config.txt      - Telegram API credentials                             ║
║  • mongodb-uri.txt    - MongoDB connection string                            ║
║                                                                              ║
║  DEPENDENCIES:                                                               ║
║  pip install telethon httpx Pillow imagehash pymongo APScheduler             ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
Telegram Wallpaper Upload Bot

Description: Fetches wallpaper links from MongoDB and uploads them to 
             specific Telegram groups based on category

Usage: python tg-up-bot.py

Configuration Files:
    - mongodb-uri.txt: MongoDB connection string
    - tg-config.txt: Telegram bot credentials
      Format:
        API_ID=your_api_id
        API_HASH=your_api_hash
        BOT_TOKEN=your_bot_token
    
    - config.txt: Category configuration (group ID and timing)
      Format: category | group_id | interval_seconds | search_terms (ignored by bot)
      Example:
        nature | -1002996780898 | 3050 | tree, water, river
        anime | -1002935599065 | 1000 | anime, cartoon
      Note: search_terms are only used by update-link-db.py, not by this bot

Features:
    - Fetches one wallpaper at a time from MongoDB
    - Downloads image and calculates SHA256 and pHash
    - Checks for duplicates using hash comparison
    - Uploads to appropriate Telegram group based on category
    - Updates database with upload status and hashes
    - Scheduled posting with configurable intervals per group
"""

# ============================================================================
# IMPORTS
# ============================================================================

# Standard library imports
import os              # File system operations (path checks, file deletion)
import sys             # System operations (exit codes)
import json            # JSON parsing for database responses
import random          # Random number generation for filenames
import logging         # Logging framework for status messages
import asyncio         # Asynchronous I/O for concurrent operations
import hashlib         # SHA256 hash calculation for exact duplicate detection
import signal          # Signal handling for graceful shutdown (SIGINT, SIGTERM)
from datetime import datetime        # Timestamp handling
from urllib.parse import urlparse    # URL parsing to extract filenames

# Third-party imports
import httpx                                          # Async HTTP client for image downloads
from PIL import Image                                 # Image processing library
import imagehash                                      # Perceptual hash calculation
from telethon import TelegramClient                   # Telegram Bot API client
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # Job scheduler
from pymongo import MongoClient                       # MongoDB Python driver
from pymongo.errors import ConnectionFailure          # MongoDB connection error handling

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

# Perceptual Hash (pHash) similarity threshold for duplicate detection
# pHash compares images based on visual similarity, not exact pixel match
# Range: 0 (identical) to 64 (completely different)
# Threshold of 5 means: if pHash difference < 5, images are considered similar
# Adjust this value based on your duplicate detection sensitivity:
#   • Lower value (1-3): Very strict, only nearly identical images
#   • Medium value (5-10): Moderate, catches resized/compressed versions
#   • Higher value (15+): Loose, may catch false positives
SIMILARITY_THRESHOLD = 5

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ============================================================================
# GRACEFUL SHUTDOWN MANAGEMENT
# ============================================================================

# Global flag to signal shutdown request (set to True when SIGINT/SIGTERM received)
shutdown_requested = False

# Set of currently active upload tasks (tracked to ensure completion before exit)
# Each task is added when starting upload, removed when finished
# On shutdown, bot waits for all tasks in this set to complete
ACTIVE_TASKS = set()

def handle_shutdown():
    """
    Signal handler for graceful shutdown (Ctrl+C or kill signal).
    
    Flow:
    1. Set global shutdown_requested flag to True
    2. Prevent new tasks from starting (checked in send_wallpaper_to_group)
    3. Allow active tasks in ACTIVE_TASKS set to complete
    4. Main loop exits once all tasks are done
    
    This ensures:
    - No partial uploads left in database with wrong status
    - Temporary files are cleaned up properly
    - Database connections closed cleanly
    """
    global shutdown_requested
    shutdown_requested = True
    logging.info("Shutdown requested. Waiting for ongoing tasks to complete...")

# ============================================================================
# CONFIGURATION LOADERS
# ============================================================================
#
# \u2705 COMMENT HANDLING - USER-FRIENDLY FEATURE
# ----------------------------------------------------------------------------
# All configuration loaders automatically skip:
# \u2022 Lines starting with # (comments)
# \u2022 Empty/whitespace-only lines
#
# WHY? This allows you to keep documentation directly in config files!
# No need to manually remove instruction comments before using.
#
# Example - mongodb-uri.txt can contain:
#   # MongoDB Connection URI
#   # Format: mongodb://host:port
#   # Instructions: Replace with your actual URI
#   mongodb://localhost:27017
#
# \u2192 Loader extracts: "mongodb://localhost:27017"
# \u2192 Ignores all comment lines automatically
#
# This applies to:
# \u2022 mongodb-uri.txt (load_mongodb_uri)
# \u2022 wallhaven-api.txt (used by update-link-db.py)
# \u2022 tg-config.txt (load_telegram_config) - KEY=VALUE format
# \u2022 config.txt (load_bot_config) - Pipe-delimited format
#
# \u27a1\ufe0f You can safely leave comments in ALL config files!
# ============================================================================

def load_mongodb_uri():
    """
    Load MongoDB URI from mongodb-uri.txt file.
    
    File Parsing:
    • Reads file line by line
    • Skips empty lines
    • Skips comment lines (starting with #)
    • Returns first non-comment, non-empty line
    
    This allows users to keep comments/instructions in the file:
        # MongoDB Connection URI
        # Instructions: Replace with your connection string
        mongodb://localhost:27017
    
    Returns:
        str: MongoDB connection URI
    
    Exits:
        If file doesn't exist or contains no valid URI
    """
    if not os.path.exists('mongodb-uri.txt'):
        logging.error("mongodb-uri.txt not found!")
        sys.exit(1)
    
    with open('mongodb-uri.txt', 'r') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith('#'):
                return line
        
        # No valid URI found
        logging.error("mongodb-uri.txt contains no valid URI (only comments/empty lines)!")
        sys.exit(1)

def load_telegram_config():
    """
    Load Telegram bot credentials from tg-config.txt file.
    
    File Format:
        API_ID=12345678
        API_HASH=abcdef1234567890abcdef1234567890
        BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
    
    File Parsing:
    • Reads line by line
    • Skips empty lines
    • Skips comment lines (starting with #)
    • Splits by '=' to extract key-value pairs
    • Validates all required keys are present
    
    Allowed in file (will be ignored):
        # Telegram Bot Configuration
        # Get from https://my.telegram.org/apps
        
        API_ID=12345678
        # API_HASH from Telegram
        API_HASH=abc123...
        BOT_TOKEN=...
    
    Returns:
        dict: {'API_ID': '...', 'API_HASH': '...', 'BOT_TOKEN': '...'}
    
    Exits:
        If file doesn't exist or missing required keys
    """
    if not os.path.exists('tg-config.txt'):
        logging.error("tg-config.txt not found!")
        logging.error("Please create tg-config.txt with format:")
        logging.error("  API_ID=your_api_id")
        logging.error("  API_HASH=your_api_hash")
        logging.error("  BOT_TOKEN=your_bot_token")
        sys.exit(1)
    
    config = {}
    with open('tg-config.txt', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    
    required_keys = ['API_ID', 'API_HASH', 'BOT_TOKEN']
    for key in required_keys:
        if key not in config:
            logging.error(f"Missing {key} in tg-config.txt")
            sys.exit(1)
    
    return config

def load_bot_config():
    """
    Load consolidated bot configuration from config.txt file.
    
    File Format:
        category | group_id | interval_seconds | search_terms
        Example:
        nature | -1002996780898 | 3050 | tree, water, river
    
    Parsing Logic:
    • Split each line by '|' delimiter (expecting 4 parts minimum)
    • Extract category name, Telegram group ID, posting interval
    • Ignore search_terms (4th field) - only used by update-link-db.py
    • Skip comments (lines starting with #) and empty lines
    • Validate group_id and interval are valid integers
    • Validate interval is positive (> 0)
    
    Returns:
        dict: {category: {group_id: int, interval: int}}
        Example:
        {
            'nature': {'group_id': -1002996780898, 'interval': 3050},
            'anime': {'group_id': -1002935599065, 'interval': 1000}
        }
    
    Why search_terms are ignored:
    • This bot fetches ANY wallpaper from MongoDB with matching category
    • It doesn't care which search term originally found the wallpaper
    • search_terms are only needed by update-link-db.py for API queries
    
    Exits:
        If config.txt doesn't exist or contains no valid categories
    """
    if not os.path.exists('config.txt'):
        logging.error("config.txt not found!")
        logging.error("Please create config.txt with format:")
        logging.error("  category | group_id | interval_seconds | search_term1, term2")
        logging.error("Example:")
        logging.error("  nature | -1002996780898 | 3050 | tree, water, river")
        logging.error("  anime | -1002935599065 | 1000 | anime, cartoon")
        sys.exit(1)
    
    config = {}
    with open('config.txt', 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse line: category | group_id | interval | search_terms (ignored)
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
            # parts[3] (search_terms) is ignored by this bot, only used by update-link-db.py
            
            # Validate and convert types
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
        logging.error("No valid category configurations found in config.txt!")
        sys.exit(1)
    
    return config

# --- MongoDB Connection ---

def connect_to_mongodb(uri):
    """Connect to MongoDB and return collection"""
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # Test connection
        client.admin.command('ping')
        db = client['wallpaper-bot']
        collection = db.wallhaven
        logging.info("✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)")
        return collection
    except ConnectionFailure as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

# --- Hash Calculation ---

def calculate_hashes(filepath):
    """Calculate SHA256 and pHash for an image file"""
    try:
        # Calculate SHA256
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(block)
        sha256 = sha256_hash.hexdigest()
        
        # Calculate perceptual hash
        p_hash = str(imagehash.average_hash(Image.open(filepath)))
        
        return sha256, p_hash
    except Exception as e:
        logging.error(f"Error calculating hashes for {filepath}: {e}")
        return None, None

async def check_duplicate_hashes(collection, sha256, p_hash):
    """
    Two-tier duplicate detection: exact (SHA256) + similarity (pHash).
    
    TIER 1: SHA256 Exact Match
    • SHA256 is cryptographic hash of file contents
    • Identical files = identical SHA256 (even if renamed)
    • Use case: Catch exact duplicates, re-uploads, or same file
    
    TIER 2: Perceptual Hash (pHash) Similarity
    • pHash analyzes visual content, not bytes
    • Similar looking images have similar pHash values
    • Use case: Catch resized, compressed, or slightly edited versions
    • Hamming distance measures how many bits differ between hashes
    • Lower distance = more similar images
    
    Why Both Methods?
    • SHA256 alone misses resized/compressed duplicates
    • pHash alone might have false positives
    • Combined approach: strict exact check + fuzzy similarity check
    
    Algorithm:
    1. Quick check: SHA256 exact match in database
       → If found: immediate duplicate, return details
    2. Thorough check: Compare pHash with ALL posted images
       → Calculate Hamming distance for each
       → If distance < SIMILARITY_THRESHOLD: similar image found
    3. If both checks pass: image is unique, proceed with upload
    
    Performance Optimization:
    • SHA256 check is O(1) with database index
    • pHash check is O(n) but only for non-exact duplicates
    • Could optimize with vector similarity search (future enhancement)
    
    Args:
        collection: MongoDB collection
        sha256: SHA256 hex string of downloaded image
        p_hash: Perceptual hash hex string (from imagehash library)
    
    Returns:
        tuple: (status, reasons)
        
        status values:
        • "duplicate" - Exact SHA256 match found
        • "similar"   - pHash similarity below threshold
        • "proceed"   - No duplicates found, safe to upload
        
        reasons: dict with duplicate details (or None if unique)
        Example for duplicate:
        {
            "reason": "Duplicate",
            "details": {
                "type": "SHA256_match",
                "wallpaper_id": "abc123"
            }
        }
        
        Example for similar:
        {
            "reason": "Similar",
            "details": {
                "type": "pHash_match",
                "diff": 3,
                "similarity_percentage": 95.31,
                "wallpaper_id": "xyz789"
            }
        }
    """
    # Check exact SHA256 match
    exact_match = collection.find_one({"sha256": sha256})
    if exact_match:
        return "duplicate", {
            "reason": "Duplicate",
            "details": {
                "type": "SHA256_match",
                "wallpaper_id": exact_match.get('wallpaper_id')
            }
        }
    
    # Check similar pHash
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

# --- Image Download ---

async def download_image(url, filename):
    """Download image from URL"""
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

# --- Database Operations ---

def get_pending_wallpaper(collection, category):
    """
    Fetch one RANDOM pending wallpaper from MongoDB for the specified category.
    
    Why Random Selection?
    • Previous version fetched oldest first (FIFO queue)
    • Random selection provides better variety in posts
    • Prevents predictable posting patterns
    • Users requested this feature for more natural feed appearance
    
    Database Query Strategy:
    1. First, count documents matching criteria (for logging purposes)
    2. Use MongoDB aggregation pipeline with $sample stage
    3. $sample efficiently selects random document from matched set
    
    Query Criteria:
    • category: Must match the category being processed
    • status: "link_added" = fetched by update-link-db.py but not yet posted
    
    Status Flow in Database:
    link_added → posted     (successful upload)
                → failed     (download/upload error)
                → skipped    (duplicate detected)
    
    Args:
        collection: PyMongo collection object (wallpaper-bot.wallhaven)
        category: Category name (e.g., "nature", "anime")
    
    Returns:
        dict: Wallpaper document with fields:
              - wallpaper_id: Wallhaven ID
              - category: Category name
              - search_term: Original search term that found it
              - jpg_url: Direct image URL
              - tags: List of tag strings
              - purity: "sfw" or "sketchy"
              - sfw: Boolean (True=SFW, False=Sketchy)
              - status: "link_added"
              - created_at: Unix timestamp
        None: If no pending wallpapers found or error occurs
    
    MongoDB Aggregation Pipeline Explained:
    • $match: Filter documents (like WHERE clause in SQL)
    • $sample: Randomly select N documents from matched set
    • This is more efficient than fetching all and picking random in Python
    """
    try:
        # Get count of pending wallpapers (useful for monitoring/debugging)
        count = collection.count_documents(
            {"category": category, "status": "link_added"}
        )
        
        if count == 0:
            return None
        
        # MongoDB aggregation pipeline for random selection
        pipeline = [
            # Stage 1: Filter by category and status
            {"$match": {"category": category, "status": "link_added"}},
            # Stage 2: Randomly sample 1 document from matched set
            {"$sample": {"size": 1}}
        ]
        
        result = list(collection.aggregate(pipeline))
        return result[0] if result else None
        
    except Exception as e:
        logging.error(f"Database query failed: {e}")
        return None

def update_wallpaper_status(collection, wallpaper_id, status, sha256=None, phash=None, 
                           tg_response=None, reasons=None):
    """Update wallpaper status in database"""
    try:
        update_data = {"status": status}
        
        if sha256:
            update_data["sha256"] = sha256
        if phash:
            update_data["phash"] = phash
        if tg_response:
            update_data["tg_response"] = tg_response
        if reasons:
            # Merge with existing tg_response if it exists
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

# --- Telegram Upload ---

async def send_wallpaper_to_group(client, collection, category, group_id):
    """Fetch and send one wallpaper to Telegram group"""
    if shutdown_requested:
        logging.info(f"Skipping wallpaper send for {category} due to shutdown request.")
        return
    
    task = asyncio.current_task()
    ACTIVE_TASKS.add(task)
    
    try:
        # Get one pending wallpaper
        wallpaper = get_pending_wallpaper(collection, category)
        if not wallpaper:
            logging.info(f"No pending wallpapers found for category '{category}'")
            return
        
        wallpaper_id = wallpaper.get('wallpaper_id')
        jpg_url = wallpaper.get('jpg_url')
        tags = wallpaper.get('tags', [])
        search_term = wallpaper.get('search_term', category)
        
        # Generate filename
        filename = f"{category}_{random.randint(1000, 9999)}_{os.path.basename(urlparse(jpg_url).path)}"
        
        # Download image
        logging.info(f"[{category}] Processing {wallpaper_id}...")
        path = await download_image(jpg_url, filename)
        if not path:
            reasons = {"reason": "Download failed", "url": jpg_url}
            update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
            logging.error(f"[{category}] Download failed for {wallpaper_id}")
            return
        
        # Calculate hashes
        sha256, phash = calculate_hashes(path)
        if not sha256 or not phash:
            reasons = {"reason": "Hashing failed"}
            update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
            os.remove(path)
            logging.error(f"[{category}] Hashing failed for {wallpaper_id}")
            return
        
        # Check for duplicates
        status_check, reasons = await check_duplicate_hashes(collection, sha256, phash)
        if status_check in ["duplicate", "similar"]:
            log_details = f"{reasons['details']['type']}"
            if 'similarity_percentage' in reasons['details']:
                log_details += f" ({reasons['details']['similarity_percentage']}% similar)"
            logging.warning(f"[{category}] Skipping {wallpaper_id}: {reasons['reason']} - {log_details}")
            update_wallpaper_status(collection, wallpaper_id, "skipped", sha256, phash, reasons=reasons)
            os.remove(path)
            return
        
        # Upload to Telegram
        try:
            # Send preview (as photo)
            preview_response = await client.send_file(
                group_id,
                path,
                force_document=False
            )
            
            # Wait a bit before sending HD version
            await asyncio.sleep(3)
            
            # Send HD version (as document)
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

# --- Main ---

async def main():
    """Main bot execution"""
    logging.info("=" * 70)
    logging.info("Telegram Wallpaper Upload Bot Starting...")
    logging.info("=" * 70)
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)
    loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
    
    # Load configurations
    mongodb_uri = load_mongodb_uri()
    tg_config = load_telegram_config()
    bot_config = load_bot_config()
    
    logging.info(f"✓ Loaded {len(bot_config)} category configurations")
    for cat, cfg in bot_config.items():
        logging.info(f"  - {cat}: Group {cfg['group_id']}, Every {cfg['interval']}s ({cfg['interval']//60}min)")
    
    # Connect to MongoDB
    collection = connect_to_mongodb(mongodb_uri)
    
    # Initialize Telegram client
    client = TelegramClient(
        'wallpaper_bot_session',
        int(tg_config['API_ID']),
        tg_config['API_HASH']
    )
    await client.start(bot_token=tg_config['BOT_TOKEN'])
    logging.info("✓ Telegram bot connected")
    
    # Setup scheduler
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
    
    # Keep running until shutdown requested
    while not shutdown_requested:
        await asyncio.sleep(1)
    
    # Wait for active tasks to complete
    if ACTIVE_TASKS:
        logging.info(f"Waiting for {len(ACTIVE_TASKS)} active tasks to finish...")
        await asyncio.gather(*ACTIVE_TASKS)
    
    # Cleanup
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
