#!/usr/bin/env python3
"""
Wallhaven Telegram Bot - Combined Fetcher & Poster
Intelligently fetches wallpapers and posts them to Telegram groups
"""

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
import time
import shutil
import re
import sqlite3
import threading
from functools import partial, wraps
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core.exceptions import ResourceExhausted, RetryError
from PIL import Image

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

shutdown_requested = False
ACTIVE_TASKS = set()
BOT_TOKEN = None
MAX_REQUESTS_PER_MINUTE = 40
api_call_times = []
rate_limit_lock = None  # Will be initialized in main()

# =============================================================================
# DUAL-CACHE ARCHITECTURE FOR FIREBASE OPTIMIZATION
# =============================================================================
# This bot uses TWO separate SQLite cache databases to minimize Firebase costs:
#
# 1. HASH CACHE (wallhaven_cache.db) - For image duplicate detection
#    - Stores: SHA256 hashes of downloaded images
#    - Used during: POSTING phase (after image download)
#    - Purpose: Detect duplicate images (same content, different IDs)
#    - Capacity: 1 million entries (~120MB disk)
#
# 2. METADATA CACHE (wallhaven_metadata_cache.db) - For wallpaper ID tracking
#    - Stores: wallpaper_id, category, search_term
#    - Used during: FETCHING phase (before image download)
#    - Purpose: Avoid Firebase reads when checking if wallpaper exists
#    - Capacity: 500k entries (~50MB disk)
#    - Recovery: Syncs from Firebase on startup if cache is empty
#
# Cost Savings:
# - Without metadata cache: ~100-200 Firebase reads per fetch cycle
# - With metadata cache: ~1-5 Firebase reads per fetch cycle (99% reduction!)
# =============================================================================

# SQLite-based disk cache for duplicate hash checks (minimal RAM usage)
CACHE_DB_FILE = "wallhaven_cache.db"  # SQLite database file
CACHE_MAX_ENTRIES = 1000000  # 1 million entries (~120MB disk, excellent coverage)
CACHE_CLEANUP_THRESHOLD = 0.9  # Cleanup when 90% full (900k entries)
cache_db_conn = None  # Database connection
cache_db_lock = None  # Thread lock for database access

# Metadata cache for wallpaper IDs (avoid Firebase reads during fetching)
METADATA_CACHE_DB_FILE = "wallhaven_metadata_cache.db"  # Metadata cache database
METADATA_CACHE_MAX_ENTRIES = 500000  # 500k entries (lighter, faster lookups)
metadata_cache_conn = None  # Metadata database connection
metadata_cache_lock = None  # Thread lock for metadata database access

# Rate limiting for database writes (prevent Firebase quota exhaustion)
MAX_WALLPAPERS_PER_PERIOD = 2000  # Maximum new wallpapers to add per period
RATE_LIMIT_PERIOD_HOURS = 28  # Period duration in hours
rate_limit_state = {  # Global rate limit state
    'period_start': 0,
    'wallpapers_added': 0,
    'is_paused': False
}

FIRESTORE_QUOTA_BACKOFF = 60  # seconds to wait after quota error

# Retry decorator for transient failures
def retry_on_failure(max_attempts=3, delay=2, backoff=2):
    """Retry decorator with exponential backoff"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logging.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logging.warning(f"{func.__name__} attempt {attempt} failed: {e}. Retrying in {current_delay}s...")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            return None
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logging.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logging.warning(f"{func.__name__} attempt {attempt} failed: {e}. Retrying in {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
            return None
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

def init_cache_db():
    """Initialize SQLite cache database optimized for long-term stability and minimal resources"""
    global cache_db_conn, cache_db_lock
    
    try:
        cache_db_lock = threading.Lock()
        cache_db_conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)
        cursor = cache_db_conn.cursor()
        
        # Create table with index on sha256 for fast lookups
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duplicate_cache (
                sha256 TEXT PRIMARY KEY,
                wallpaper_id TEXT NOT NULL,
                last_accessed INTEGER NOT NULL
            )
        ''')
        
        # Create index on last_accessed for efficient cleanup
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_last_accessed 
            ON duplicate_cache(last_accessed)
        ''')
        
        # Create table for rate limiting state
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rate_limit_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                period_start INTEGER NOT NULL,
                wallpapers_added INTEGER NOT NULL,
                last_updated INTEGER NOT NULL
            )
        ''')
        
        # Optimize SQLite for stability and minimal resource usage (not performance)
        cursor.execute('PRAGMA journal_mode=DELETE')  # More stable than WAL, less disk usage
        cursor.execute('PRAGMA synchronous=FULL')  # Maximum safety against corruption
        cursor.execute('PRAGMA cache_size=-2000')  # Negative = KB, so 2MB cache (minimal)
        cursor.execute('PRAGMA temp_store=MEMORY')  # Small temp tables in memory
        cursor.execute('PRAGMA page_size=4096')  # Standard page size
        cursor.execute('PRAGMA auto_vacuum=INCREMENTAL')  # Gradual space reclamation
        
        # Perform incremental vacuum to reclaim space
        cursor.execute('PRAGMA incremental_vacuum(100)')  # Reclaim up to 100 pages
        
        cache_db_conn.commit()
        
        # Get cache statistics
        cursor.execute('SELECT COUNT(*) FROM duplicate_cache')
        count = cursor.fetchone()[0]
        
        db_size_mb = os.path.getsize(CACHE_DB_FILE) / (1024 * 1024) if os.path.exists(CACHE_DB_FILE) else 0
        
        logging.info(f"✓ Cache database initialized: {count:,} entries, {db_size_mb:.1f}MB on disk")
        logging.info(f"  Mode: Stability-optimized (max {CACHE_MAX_ENTRIES:,} entries, ~{CACHE_MAX_ENTRIES*0.12:.0f}MB disk)")
        
        # Load rate limit state
        load_rate_limit_state()
        
    except Exception as e:
        logging.error(f"Failed to initialize cache database: {e}")
        raise

def check_cache_db(sha256):
    """Check if SHA256 exists in cache database (disk-based, minimal RAM)"""
    global cache_db_conn, cache_db_lock
    
    try:
        with cache_db_lock:
            cursor = cache_db_conn.cursor()
            cursor.execute(
                'SELECT wallpaper_id FROM duplicate_cache WHERE sha256 = ?',
                (sha256,)
            )
            result = cursor.fetchone()
            
            if result:
                # Update last_accessed timestamp
                cursor.execute(
                    'UPDATE duplicate_cache SET last_accessed = ? WHERE sha256 = ?',
                    (int(time.time()), sha256)
                )
                cache_db_conn.commit()
                return result[0]  # Return wallpaper_id
            
            return None
    except Exception as e:
        logging.error(f"Error checking cache database: {e}")
        return None

def add_to_cache_db(sha256, wallpaper_id):
    """Add SHA256 to cache database"""
    global cache_db_conn, cache_db_lock
    
    try:
        with cache_db_lock:
            cursor = cache_db_conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO duplicate_cache (sha256, wallpaper_id, last_accessed) VALUES (?, ?, ?)',
                (sha256, wallpaper_id, int(time.time()))
            )
            cache_db_conn.commit()
    except Exception as e:
        logging.error(f"Error adding to cache database: {e}")

def cleanup_old_cache_entries(max_entries=None):
    """Remove oldest cache entries when threshold is reached"""
    global cache_db_conn, cache_db_lock
    
    if max_entries is None:
        max_entries = CACHE_MAX_ENTRIES
    
    try:
        with cache_db_lock:
            cursor = cache_db_conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM duplicate_cache')
            count = cursor.fetchone()[0]
            
            # Start cleanup when 90% full to keep database smaller
            cleanup_threshold = int(max_entries * CACHE_CLEANUP_THRESHOLD)
            
            if count > cleanup_threshold:
                # Keep only 70% of max entries (remove oldest 30%)
                target_size = int(max_entries * 0.7)
                entries_to_delete = count - target_size
                
                cursor.execute('''
                    DELETE FROM duplicate_cache 
                    WHERE sha256 IN (
                        SELECT sha256 FROM duplicate_cache 
                        ORDER BY last_accessed ASC 
                        LIMIT ?
                    )
                ''', (entries_to_delete,))
                
                cache_db_conn.commit()
                
                # Reclaim disk space after deletion
                cursor.execute('PRAGMA incremental_vacuum')
                
                logging.info(f"Cleaned up {entries_to_delete:,} old cache entries (kept {target_size:,} most recent)")
                
                db_size_mb = os.path.getsize(CACHE_DB_FILE) / (1024 * 1024)
                logging.info(f"  Cache database: {target_size:,} entries, {db_size_mb:.1f}MB")
            else:
                logging.debug(f"Cache size OK: {count:,}/{cleanup_threshold:,} entries")
                
    except Exception as e:
        logging.error(f"Error cleaning up cache: {e}")

async def verify_cache_integrity():
    """Verify database integrity for long-term stability"""
    global cache_db_conn, cache_db_lock
    
    try:
        logging.info("Running cache database integrity check...")
        
        loop = asyncio.get_event_loop()
        
        def check_integrity():
            with cache_db_lock:
                cursor = cache_db_conn.cursor()
                cursor.execute('PRAGMA integrity_check')
                result = cursor.fetchone()[0]
                return result
        
        result = await loop.run_in_executor(None, check_integrity)
        
        if result == 'ok':
            logging.info("✓ Cache database integrity check passed")
        else:
            logging.error(f"Cache database integrity check failed: {result}")
            logging.error("Consider rebuilding cache database")
            
    except Exception as e:
        logging.error(f"Cache integrity check failed: {e}")

async def cleanup_cache_task():
    """Async wrapper for cache cleanup task"""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cleanup_old_cache_entries)
        await loop.run_in_executor(None, cleanup_metadata_cache)
        logging.info("✓ Cache cleanup completed (hash + metadata)")
    except Exception as e:
        logging.error(f"Cache cleanup failed: {e}")

async def maintenance_task():
    """Weekly maintenance: integrity check and optimization"""
    try:
        logging.info("Starting weekly maintenance...")
        
        # Check integrity
        await verify_cache_integrity()
        
        # Optimize databases
        loop = asyncio.get_event_loop()
        
        def optimize_hash_db():
            with cache_db_lock:
                cursor = cache_db_conn.cursor()
                cursor.execute('ANALYZE')
                cursor.execute('PRAGMA incremental_vacuum')
                cache_db_conn.commit()
        
        def optimize_metadata_db():
            with metadata_cache_lock:
                cursor = metadata_cache_conn.cursor()
                cursor.execute('ANALYZE')
                cursor.execute('PRAGMA incremental_vacuum')
                metadata_cache_conn.commit()
        
        await loop.run_in_executor(None, optimize_hash_db)
        await loop.run_in_executor(None, optimize_metadata_db)
        
        logging.info("✓ Weekly maintenance completed")
    except Exception as e:
        logging.error(f"Maintenance task failed: {e}")

def close_cache_db():
    """Close cache database connection with cleanup"""
    global cache_db_conn
    
    try:
        if cache_db_conn:
            cursor = cache_db_conn.cursor()
            
            # Final optimization before closing
            logging.info("Optimizing cache database before shutdown...")
            cursor.execute('ANALYZE')  # Update statistics
            cursor.execute('PRAGMA incremental_vacuum')  # Reclaim space
            
            cache_db_conn.commit()
            cache_db_conn.close()
            logging.info("✓ Cache database closed and optimized")
    except Exception as e:
        logging.error(f"Error closing cache database: {e}")

def init_metadata_cache_db():
    """Initialize metadata cache database for wallpaper IDs (avoids Firebase reads)"""
    global metadata_cache_conn, metadata_cache_lock
    
    try:
        metadata_cache_lock = threading.Lock()
        metadata_cache_conn = sqlite3.connect(METADATA_CACHE_DB_FILE, check_same_thread=False)
        cursor = metadata_cache_conn.cursor()
        
        # Create table for wallpaper metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallpaper_metadata (
                wallpaper_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                search_term TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_accessed INTEGER NOT NULL
            )
        ''')        
        # Create index for category lookups (useful for syncing)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_category 
            ON wallpaper_metadata(category)
        ''')        
        # Create index on last_accessed for cleanup
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_meta_last_accessed 
            ON wallpaper_metadata(last_accessed)
        ''')        
        # Optimize SQLite for stability
        cursor.execute('PRAGMA journal_mode=DELETE')
        cursor.execute('PRAGMA synchronous=FULL')
        cursor.execute('PRAGMA cache_size=-2000')  # 2MB cache
        cursor.execute('PRAGMA temp_store=MEMORY')
        cursor.execute('PRAGMA page_size=4096')
        cursor.execute('PRAGMA auto_vacuum=INCREMENTAL')
        cursor.execute('PRAGMA incremental_vacuum(100)')
        
        metadata_cache_conn.commit()
        
        # Get cache statistics
        cursor.execute('SELECT COUNT(*) FROM wallpaper_metadata')
        count = cursor.fetchone()[0]
        
        db_size_mb = os.path.getsize(METADATA_CACHE_DB_FILE) / (1024 * 1024) if os.path.exists(METADATA_CACHE_DB_FILE) else 0
        
        logging.info(f"✓ Metadata cache initialized: {count:,} wallpaper IDs, {db_size_mb:.1f}MB on disk")
        logging.info(f"  Mode: Fast lookups (max {METADATA_CACHE_MAX_ENTRIES:,} entries)")
        
    except Exception as e:
        logging.error(f"Failed to initialize metadata cache: {e}")
        raise

def check_metadata_cache(wallpaper_id):
    """Check if wallpaper_id exists in metadata cache"""
    global metadata_cache_conn, metadata_cache_lock
    
    try:
        with metadata_cache_lock:
            cursor = metadata_cache_conn.cursor()
            cursor.execute(
                'SELECT wallpaper_id FROM wallpaper_metadata WHERE wallpaper_id = ?',
                (wallpaper_id,)
            )
            result = cursor.fetchone()
            
            if result:
                # Update last_accessed timestamp
                cursor.execute(
                    'UPDATE wallpaper_metadata SET last_accessed = ? WHERE wallpaper_id = ?',
                    (int(time.time()), wallpaper_id)
                )
                metadata_cache_conn.commit()
                return True
            
            return False
    except Exception as e:
        logging.error(f"Error checking metadata cache: {e}")
        return False  # On error, return False to check Firebase as fallback

def add_to_metadata_cache(wallpaper_id, category, search_term):
    """Add wallpaper metadata to cache"""
    global metadata_cache_conn, metadata_cache_lock
    
    try:
        with metadata_cache_lock:
            cursor = metadata_cache_conn.cursor()
            current_time = int(time.time())
            cursor.execute(
                '''INSERT OR REPLACE INTO wallpaper_metadata 
                   (wallpaper_id, category, search_term, created_at, last_accessed) 
                   VALUES (?, ?, ?, ?, ?)''',
                (wallpaper_id, category, search_term, current_time, current_time)
            )
            metadata_cache_conn.commit()
    except Exception as e:
        logging.error(f"Error adding to metadata cache: {e}")

def cleanup_metadata_cache():
    """Clean up old metadata cache entries when threshold is reached"""
    global metadata_cache_conn, metadata_cache_lock
    
    try:
        with metadata_cache_lock:
            cursor = metadata_cache_conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM wallpaper_metadata')
            count = cursor.fetchone()[0]
            
            # Start cleanup when 90% full
            cleanup_threshold = int(METADATA_CACHE_MAX_ENTRIES * 0.9)
            
            if count > cleanup_threshold:
                # Keep only 70% of max entries (remove oldest 30%)
                target_size = int(METADATA_CACHE_MAX_ENTRIES * 0.7)
                entries_to_delete = count - target_size
                
                cursor.execute('''
                    DELETE FROM wallpaper_metadata 
                    WHERE wallpaper_id IN (
                        SELECT wallpaper_id FROM wallpaper_metadata 
                        ORDER BY last_accessed ASC 
                        LIMIT ?
                    )
                ''', (entries_to_delete,))
                
                metadata_cache_conn.commit()
                cursor.execute('PRAGMA incremental_vacuum')
                
                logging.info(f"Cleaned up {entries_to_delete:,} old metadata cache entries")
                
    except Exception as e:
        logging.error(f"Error cleaning up metadata cache: {e}")

async def sync_metadata_cache_from_firebase(wallpaper_collection):
    """Sync metadata cache from Firebase (run on startup or after crash)"""
    global metadata_cache_conn, metadata_cache_lock
    
    try:
        logging.info("Syncing metadata cache from Firebase...")
        
        # Check if cache is empty (fresh deployment or corrupted)
        with metadata_cache_lock:
            cursor = metadata_cache_conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM wallpaper_metadata')
            cache_count = cursor.fetchone()[0]
        
        if cache_count > 0:
            logging.info(f"  Metadata cache has {cache_count:,} entries, skipping full sync")
            return
        
        # Cache is empty - rebuild from Firebase
        logging.info("  Cache is empty, rebuilding from Firebase...")
        
        loop = asyncio.get_event_loop()
        
        # Fetch all wallpaper IDs from Firebase (only the fields we need)
        def fetch_all_metadata():
            docs = wallpaper_collection.select(['wallpaper_id', 'category', 'search_term', 'created_at']).stream()
            metadata_list = []
            for doc in docs:
                data = doc.to_dict()
                metadata_list.append((
                    data.get('wallpaper_id', doc.id),
                    data.get('category', ''),
                    data.get('search_term', ''),
                    data.get('created_at', int(time.time())),
                    int(time.time())  # last_accessed
                ))
            return metadata_list
        
        metadata_list = await loop.run_in_executor(None, fetch_all_metadata)
        
        if not metadata_list:
            logging.info("  No wallpapers found in Firebase, cache remains empty")
            return
        
        # Batch insert into cache
        def batch_insert(metadata_list):
            with metadata_cache_lock:
                cursor = metadata_cache_conn.cursor()
                cursor.executemany(
                    '''INSERT OR REPLACE INTO wallpaper_metadata 
                       (wallpaper_id, category, search_term, created_at, last_accessed) 
                       VALUES (?, ?, ?, ?, ?)''',
                    metadata_list
                )
                metadata_cache_conn.commit()
        
        await loop.run_in_executor(None, batch_insert, metadata_list)
        
        db_size_mb = os.path.getsize(METADATA_CACHE_DB_FILE) / (1024 * 1024)
        logging.info(f"✓ Synced {len(metadata_list):,} wallpaper IDs from Firebase ({db_size_mb:.1f}MB)")
        
    except Exception as e:
        logging.error(f"Failed to sync metadata cache from Firebase: {e}")
        logging.error("Bot will continue but may have higher Firebase read costs")

def close_metadata_cache_db():
    """Close metadata cache database connection"""
    global metadata_cache_conn
    
    try:
        if metadata_cache_conn:
            cursor = metadata_cache_conn.cursor()
            logging.info("Optimizing metadata cache before shutdown...")
            cursor.execute('ANALYZE')
            cursor.execute('PRAGMA incremental_vacuum')
            metadata_cache_conn.commit()
            metadata_cache_conn.close()
            logging.info("✓ Metadata cache closed and optimized")
    except Exception as e:
        logging.error(f"Error closing metadata cache: {e}")

def load_rate_limit_state():
    """Load rate limiting state from database"""
    global rate_limit_state, cache_db_conn, cache_db_lock
    
    try:
        with cache_db_lock:
            cursor = cache_db_conn.cursor()
            cursor.execute('SELECT period_start, wallpapers_added FROM rate_limit_state WHERE id = 1')
            result = cursor.fetchone()
            
            if result:
                period_start, wallpapers_added = result
                current_time = int(time.time())
                period_duration_seconds = RATE_LIMIT_PERIOD_HOURS * 3600
                
                # Check if period has expired
                if current_time - period_start >= period_duration_seconds:
                    # Start new period
                    rate_limit_state['period_start'] = current_time
                    rate_limit_state['wallpapers_added'] = 0
                    rate_limit_state['is_paused'] = False
                    save_rate_limit_state()
                    logging.info(f"✓ New rate limit period started (limit: {MAX_WALLPAPERS_PER_PERIOD} wallpapers per {RATE_LIMIT_PERIOD_HOURS}h)")
                else:
                    # Continue existing period
                    rate_limit_state['period_start'] = period_start
                    rate_limit_state['wallpapers_added'] = wallpapers_added
                    rate_limit_state['is_paused'] = wallpapers_added >= MAX_WALLPAPERS_PER_PERIOD
                    
                    remaining = MAX_WALLPAPERS_PER_PERIOD - wallpapers_added
                    time_left_hours = (period_start + period_duration_seconds - current_time) / 3600
                    
                    if rate_limit_state['is_paused']:
                        logging.info(f"⏸ Rate limit reached: {wallpapers_added}/{MAX_WALLPAPERS_PER_PERIOD} wallpapers added")
                        logging.info(f"  Fetching paused. Resumes in {time_left_hours:.1f} hours. Posting continues.")
                    else:
                        logging.info(f"✓ Rate limit state: {wallpapers_added}/{MAX_WALLPAPERS_PER_PERIOD} wallpapers added")
                        logging.info(f"  Remaining: {remaining} wallpapers, Period resets in {time_left_hours:.1f}h")
            else:
                # Initialize new state
                current_time = int(time.time())
                rate_limit_state['period_start'] = current_time
                rate_limit_state['wallpapers_added'] = 0
                rate_limit_state['is_paused'] = False
                save_rate_limit_state()
                logging.info(f"✓ Rate limit initialized (limit: {MAX_WALLPAPERS_PER_PERIOD} wallpapers per {RATE_LIMIT_PERIOD_HOURS}h)")
                
    except Exception as e:
        logging.error(f"Error loading rate limit state: {e}")
        # Use defaults on error
        rate_limit_state['period_start'] = int(time.time())
        rate_limit_state['wallpapers_added'] = 0
        rate_limit_state['is_paused'] = False

def save_rate_limit_state():
    """Save rate limiting state to database"""
    global rate_limit_state, cache_db_conn, cache_db_lock
    
    try:
        with cache_db_lock:
            cursor = cache_db_conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO rate_limit_state (id, period_start, wallpapers_added, last_updated)
                VALUES (1, ?, ?, ?)
            ''', (
                rate_limit_state['period_start'],
                rate_limit_state['wallpapers_added'],
                int(time.time())
            ))
            cache_db_conn.commit()
    except Exception as e:
        logging.error(f"Error saving rate limit state: {e}")

def check_rate_limit():
    """Check if we can add more wallpapers (returns True if allowed)"""
    global rate_limit_state
    
    current_time = int(time.time())
    period_duration_seconds = RATE_LIMIT_PERIOD_HOURS * 3600
    
    # Check if period has expired
    if current_time - rate_limit_state['period_start'] >= period_duration_seconds:
        # Start new period
        rate_limit_state['period_start'] = current_time
        rate_limit_state['wallpapers_added'] = 0
        rate_limit_state['is_paused'] = False
        save_rate_limit_state()
        logging.info(f"✓ New rate limit period started (limit: {MAX_WALLPAPERS_PER_PERIOD} wallpapers per {RATE_LIMIT_PERIOD_HOURS}h)")
        return True
    
    # Check if limit reached
    if rate_limit_state['wallpapers_added'] >= MAX_WALLPAPERS_PER_PERIOD:
        if not rate_limit_state['is_paused']:
            rate_limit_state['is_paused'] = True
            time_left_hours = (rate_limit_state['period_start'] + period_duration_seconds - current_time) / 3600
            logging.info(f"⏸ Rate limit reached: {rate_limit_state['wallpapers_added']}/{MAX_WALLPAPERS_PER_PERIOD} wallpapers added")
            logging.info(f"  Fetching paused for {time_left_hours:.1f} hours. Posting continues normally.")
        return False
    
    return True

def increment_wallpaper_count():
    """Increment wallpaper counter after successful add"""
    global rate_limit_state
    
    rate_limit_state['wallpapers_added'] += 1
    save_rate_limit_state()
    
    remaining = MAX_WALLPAPERS_PER_PERIOD - rate_limit_state['wallpapers_added']
    
    # Log progress at milestones
    if rate_limit_state['wallpapers_added'] % 100 == 0:
        logging.info(f"  Rate limit: {rate_limit_state['wallpapers_added']}/{MAX_WALLPAPERS_PER_PERIOD} wallpapers added ({remaining} remaining)")
    
    # Check if limit just reached
    if rate_limit_state['wallpapers_added'] >= MAX_WALLPAPERS_PER_PERIOD:
        current_time = int(time.time())
        period_duration_seconds = RATE_LIMIT_PERIOD_HOURS * 3600
        time_left_hours = (rate_limit_state['period_start'] + period_duration_seconds - current_time) / 3600
        logging.info(f"🛑 Rate limit reached: {MAX_WALLPAPERS_PER_PERIOD}/{MAX_WALLPAPERS_PER_PERIOD} wallpapers added")
        logging.info(f"  Fetching will pause. Resumes in {time_left_hours:.1f} hours. Posting continues.")

def handle_shutdown():
    global shutdown_requested
    shutdown_requested = True
    logging.info("Shutdown requested. Closing cache databases and waiting for ongoing tasks to complete...")
    close_cache_db()  # Close hash cache database
    close_metadata_cache_db()  # Close metadata cache database

async def enforce_rate_limit():
    """Enforce Wallhaven API rate limit of 40 requests per minute"""
    global api_call_times
    
    # Check rate limit with lock
    wait_time = 0
    async with rate_limit_lock:
        current_time = time.time()
        api_call_times = [t for t in api_call_times if current_time - t < 60]
        if len(api_call_times) >= MAX_REQUESTS_PER_MINUTE:
            oldest_call = api_call_times[0]
            wait_time = 60 - (current_time - oldest_call) + 2
    
    # Sleep without holding the lock (FIX #11)
    if wait_time > 0:
        logging.info(f"⏱ Rate limit: Waiting {wait_time:.1f}s...")
        for _ in range(int(wait_time)):
            if shutdown_requested:
                return
            await asyncio.sleep(1)
        if wait_time % 1 > 0:
            await asyncio.sleep(wait_time % 1)
    
    # Record this API call
    async with rate_limit_lock:
        api_call_times.append(time.time())

# =============================================================================
# CONFIGURATION LOADERS
# =============================================================================

def load_firebase_config():
    cred_path = os.getenv('FIREBASE_CREDENTIALS')
    if not cred_path:
        logging.error("FIREBASE_CREDENTIALS not found in environment variables!")
        logging.error("Please create a .env file with FIREBASE_CREDENTIALS=path/to/serviceAccountKey.json")
        sys.exit(1)
    if not os.path.exists(cred_path):
        logging.error(f"Firebase credentials file not found: {cred_path}")
        sys.exit(1)
    return cred_path

def load_telegram_config():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logging.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        logging.error("Please create a .env file with TELEGRAM_BOT_TOKEN=your_bot_token")
        sys.exit(1)
    return {'BOT_TOKEN': bot_token}

def load_wallhaven_api_key():
    api_key = os.getenv('WALLHAVEN_API_KEY')
    if not api_key:
        logging.error("WALLHAVEN_API_KEY not found in environment variables!")
        logging.error("Please create a .env file with WALLHAVEN_API_KEY=your_api_key")
        sys.exit(1)
    return api_key

def connect_to_firebase(cred_path):
    try:
        logging.info(f"Connecting to Firebase using credentials: {cred_path}")
        if not firebase_admin._apps:
            logging.info("Initializing Firebase Admin SDK...")
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logging.info("✓ Firebase Admin SDK initialized")
        logging.info("Creating Firestore client...")
        db = firestore.client()
        logging.info("✓ Connected to Firebase Firestore")
        return db
    except FileNotFoundError as e:
        logging.error(f"Firebase credentials file not found: {cred_path}")
        logging.error("Please ensure the FIREBASE_CREDENTIALS path is correct in your .env file")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Failed to connect to Firebase: {e}")
        logging.error("This could be due to:")
        logging.error("  - Invalid credentials file")
        logging.error("  - Network connectivity issues")
        logging.error("  - Firestore not enabled in your Firebase project")
        sys.exit(1)

def load_categories_config():
    """Load all categories from environment variables"""
    categories = []
    category_num = 1
    
    while True:
        env_var = f'CATEGORY_{category_num}'
        category_line = os.getenv(env_var)
        
        if not category_line:
            break
        
        parts = category_line.split('|')
        if len(parts) < 4:
            logging.warning(f"Skipping invalid {env_var}: {category_line}")
            category_num += 1
            continue
        
        category = parts[0].strip()
        # Sanitize category name - only allow alphanumeric, dash, underscore
        if not category or not all(c.isalnum() or c in '-_' for c in category):
            logging.warning(f"Invalid category name in {env_var}: {category}")
            category_num += 1
            continue
        
        group_id_str = parts[1].strip()
        interval_str = parts[2].strip()
        search_terms_str = parts[3].strip()
        search_terms = [term.strip() for term in search_terms_str.split(',') if term.strip()]
        
        try:
            group_id = int(group_id_str)
            interval = int(interval_str)
        except ValueError:
            logging.warning(f"Invalid group_id or interval in {env_var}: {category_line}")
            category_num += 1
            continue
        
        # Validate interval (FIX #15)
        if interval < 60:
            logging.warning(f"Interval too short for {category}: {interval}s, using 60s minimum")
            interval = max(60, interval)
        
        if category and group_id and interval > 0 and search_terms:
            categories.append({
                'name': category,
                'group_id': group_id,
                'interval': interval,
                'search_terms': search_terms
            })
        
        category_num += 1
    
    if not categories:
        logging.error("No valid category configurations found in environment variables!")
        logging.error("Please create a .env file with CATEGORY_1, CATEGORY_2, etc.")
        sys.exit(1)
    
    return categories

# =============================================================================
# WALLPAPER FETCHING FUNCTIONS
# =============================================================================

def get_fetch_state(state_collection, category, search_term):
    """Get the current fetch state for a category/search_term combination"""
    try:
        # Create document ID from category and search_term
        doc_id = f"{category}_{search_term}".replace(' ', '_').replace('/', '_')
        doc_ref = state_collection.document(doc_id)
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            # Create default state if doesn't exist
            default_state = {
                "category": category,
                "search_term": search_term,
                "round": 1,
                "target_count": 100,
                "skip_count": 0,
                "last_updated": int(time.time())
            }
            doc_ref.set(default_state)
            return default_state
    except Exception as e:
        logging.error(f"Error accessing fetch state for {category}:{search_term}: {e}")
        # Return default state on error
        return {
            "category": category,
            "search_term": search_term,
            "round": 1,
            "target_count": 100,
            "skip_count": 0,
            "last_updated": int(time.time())
        }

def update_fetch_state(state_collection, category, search_term):
    """Update fetch state to next round with smart pagination"""
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            state = get_fetch_state(state_collection, category, search_term)
            
            current_round = state['round']
            next_round = current_round + 1
            next_target = next_round * 100
            
            # Smart skip calculation for high rounds
            if next_target >= 800:
                next_skip = next_target - 500
            else:
                next_skip = 0
            
            doc_id = f"{category}_{search_term}".replace(' ', '_').replace('/', '_')
            state_collection.document(doc_id).update({
                "round": next_round,
                "target_count": next_target,
                "skip_count": next_skip,
                "last_updated": int(time.time())
            })
            
            logging.info(f"[{category}:{search_term}] Advanced to round {next_round} (target: {next_target}, skip: {next_skip})")
            return  # Success
        except (ResourceExhausted, RetryError) as e:
            if "Quota exceeded" in str(e):
                if attempt < max_retries - 1:
                    logging.warning(f"Quota exceeded updating fetch state for {category}:{search_term}, retry {attempt + 1}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logging.error(f"Error updating fetch state for {category}:{search_term}: {e}")
            else:
                logging.error(f"Error updating fetch state for {category}:{search_term}: {e}")
                return
        except Exception as e:
            logging.error(f"Error updating fetch state for {category}:{search_term}: {e}")
            return

def sanitize_search_term(search_term):
    """Sanitize search term to prevent injection and API errors"""
    # Remove potentially dangerous characters
    search_term = search_term.strip()
    # Remove special characters that could cause issues
    search_term = re.sub(r'[|&;<>$`"\\]', '', search_term)
    # Remove hash symbols
    search_term = search_term.replace('#', '')
    # Collapse multiple spaces
    search_term = re.sub(r'\s+', ' ', search_term)
    return search_term

def extract_tag_names(tags_data):
    """Extract tag names from API tag data (FIX #4 - avoid extra API calls)"""
    tag_names = []
    if not tags_data:
        return tag_names
    
    for tag in tags_data:
        if isinstance(tag, dict):
            name = tag.get("name", "")
            if isinstance(name, str) and name:
                tag_names.append(name)
        elif isinstance(tag, str):
            tag_names.append(tag)
    
    return tag_names

async def fetch_wallpapers_for_term(wallpaper_collection, state_collection, category, search_term, api_key):
    """
    Fetch wallpapers for a specific category and search term
    
    Duplicate Detection Strategy (Two-Tier Caching):
    1. Check metadata cache (SQLite) first for wallpaper_id - FAST, no Firebase read
    2. If cache miss, check Firebase and update cache - only on first encounter
    3. This reduces Firebase read costs by ~99% for duplicate checks during fetching
    
    Note: SHA256-based duplicate detection happens later during posting phase
    """
    
    if shutdown_requested:
        return
    
    # Check rate limit before starting fetch
    if not check_rate_limit():
        current_time = int(time.time())
        period_duration_seconds = RATE_LIMIT_PERIOD_HOURS * 3600
        time_left_hours = (rate_limit_state['period_start'] + period_duration_seconds - current_time) / 3600
        logging.debug(f"[{category}:{search_term}] Skipping fetch - rate limit reached. Resumes in {time_left_hours:.1f}h")
        return
    
    state = get_fetch_state(state_collection, category, search_term)
    target_count = state['target_count']
    skip_count = state['skip_count']
    round_num = state['round']
    
    logging.info("=" * 70)
    logging.info(f"Fetching: {category} → {search_term} | Round {round_num}")
    logging.info(f"Target: {target_count} wallpapers (skipping top {skip_count})")
    logging.info("=" * 70)
    
    # Build search query with exclusions
    search_query = sanitize_search_term(search_term)
    exclusions = [
        "-girl", "-girls", "-woman", "-women", "-female", "-females",
        "-lady", "-ladies", "-thigh", "-thighs", "-skirt", "-skirts",
        "-bikini", "-bikinis", "-leg", "-legs", "-cleavage", "-cleavages",
        "-chest", "-chests", "-breast", "-breasts", "-butt", "-butts",
        "-boob", "-boobs", "-sexy", "-hot", "-babe", "-babes",
        "-model", "-models", "-lingerie", "-underwear", "-panty", "-panties",
        "-bra", "-bras", "-swimsuit", "-swimsuits", "-dress", "-dresses",
        "-schoolgirl", "-schoolgirls", "-maid", "-maids", "-waifu", "-waifus",
        "-ecchi", "-nude", "-nudes", "-naked", "-nsfw", "-lewd",
        "-hentai", "-ass", "-asses", "-booty", "-booties",
        "-sideboob", "-sideboobs", "-underboob", "-underboobs"
    ]
    search_query = search_query + " " + " ".join(exclusions)
    
    added = 0
    duplicates = 0
    errors = 0
    processed = 0
    results_per_page = 24  # Track for better pagination (FIX #8)
    
    # Calculate starting page based on skip_count (using ceiling division)
    start_page = max(1, -(-skip_count // results_per_page) + 1)
    page = start_page
    
    api_url = "https://wallhaven.cc/api/v1/search"
    params = {
        "q": search_query,
        "categories": "110",
        "purity": "110",
        "ratios": "portrait",
        "sorting": "views",
        "order": "desc",
        "page": page,
        "apikey": api_key
    }
    
    loop = asyncio.get_event_loop()
    
    no_more_results = False
    # Allow some over-fetching if many duplicates, but cap it (FIX #5)
    max_processed = target_count * 2
    while added < target_count and processed < max_processed and not shutdown_requested and not no_more_results:
        params["page"] = page
        await enforce_rate_limit()
        
        try:
            response = await loop.run_in_executor(None, partial(requests.get, api_url, params=params, timeout=10))
            response.raise_for_status()
            data = response.json()
            
            # Validate API response structure (FIX #14)
            if not isinstance(data, dict) or "data" not in data:
                logging.error(f"Invalid API response format: {data}")
                break
            
            wallpapers = data.get("data", [])
            if wallpapers and isinstance(wallpapers, list):
                # Update results_per_page based on actual API response
                results_per_page = len(wallpapers)
            
            if not wallpapers:
                logging.info(f"No more wallpapers found (page {page})")
                no_more_results = True
                break
            
            for wallpaper in wallpapers:
                # Check if we should stop (shutdown, target reached, or rate limit hit)
                if shutdown_requested or added >= target_count or not check_rate_limit():
                    if not check_rate_limit():
                        logging.info(f"\n🛑 Rate limit reached. Stopping fetch for {category}:{search_term}")
                    break
                
                processed += 1
                
                wallpaper_id = wallpaper.get("id", "")
                wallpaper_url = wallpaper.get("url", "")
                jpg_url = wallpaper.get("path", "")
                purity = wallpaper.get("purity", "sfw")
                
                # Validate wallpaper_id (FIX #3)
                if not wallpaper_id or not wallpaper_url or not jpg_url:
                    errors += 1
                    continue
                
                # Extract tags from search results (FIX #4 - no extra API call)
                tags = extract_tag_names(wallpaper.get("tags", []))
                is_sfw = (purity == "sfw")
                current_timestamp = int(time.time())
                
                document = {
                    "wallpaper_id": wallpaper_id,
                    "category": category,
                    "search_term": search_term,
                    "wallpaper_url": wallpaper_url,
                    "jpg_url": jpg_url,
                    "tags": tags,
                    "purity": purity,
                    "sfw": is_sfw,
                    "status": "link_added",
                    "sha256": None,
                    "tg_response": {},
                    "created_at": current_timestamp
                }
                
                # Add quota-aware error handling
                max_retries = 3
                retry_delay = 5
                added_flag = False
                
                # Check metadata cache first to avoid Firebase read
                if check_metadata_cache(wallpaper_id):
                    duplicates += 1
                    if duplicates % 20 == 0:
                        logging.info(f"  [{added}/{target_count}] ⊘ {duplicates} duplicates (cached)...")
                    continue  # Skip to next wallpaper
                
                for attempt in range(max_retries):
                    try:
                        # Check Firebase as fallback (cache miss)
                        existing = wallpaper_collection.document(wallpaper_id).get()
                        if existing.exists:
                            duplicates += 1
                            # Add to metadata cache for future
                            add_to_metadata_cache(wallpaper_id, category, search_term)
                            if duplicates % 20 == 0:
                                logging.info(f"  [{added}/{target_count}] ⊘ {duplicates} duplicates so far...")
                        else:
                            wallpaper_collection.document(wallpaper_id).set(document)
                            added += 1
                            # Add to metadata cache after successful insert
                            add_to_metadata_cache(wallpaper_id, category, search_term)
                            increment_wallpaper_count()  # Track for rate limiting
                            tag_info = f" ({len(tags)} tags)" if tags else " (no tags)"
                            if added % 10 == 0 or added == target_count:
                                logging.info(f"  [{added}/{target_count}] ✓ Added: {wallpaper_id} ({purity}){tag_info}")
                        added_flag = True
                        break  # Success, exit retry loop
                    except (ResourceExhausted, RetryError) as e:
                        if "Quota exceeded" in str(e):
                            if attempt < max_retries - 1:
                                logging.warning(f"Quota exceeded while adding {wallpaper_id}, waiting {retry_delay}s before retry...")
                                await asyncio.sleep(retry_delay)
                                retry_delay *= 2
                            else:
                                errors += 1
                                logging.error(f"Error adding {wallpaper_id}: {e}")
                        else:
                            errors += 1
                            logging.error(f"Error adding {wallpaper_id}: {e}")
                            break
                    except Exception as e:
                        errors += 1
                        logging.error(f"Error adding {wallpaper_id}: {e}")
                        break
                
                # If quota errors persist, slow down
                if not added_flag and errors > 0:
                    logging.warning("Rate limiting due to quota issues, sleeping 30s...")
                    await asyncio.sleep(30)
            
            # Check rate limit after processing wallpaper
            if not check_rate_limit():
                logging.info(f"\n🛑 Rate limit reached during fetch. Stopping early.")
                break
            
            page += 1
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching search results: {e}")
            if "401" in str(e):
                logging.error("Invalid API key!")
            break
    
    logging.info(f"✓ Complete: {added} added, {duplicates} duplicates, {errors} errors")
    logging.info("")
    
    # Update state for next round if we reached target
    if added >= target_count:
        update_fetch_state(state_collection, category, search_term)
    elif no_more_results:
        # Don't advance round if we've exhausted results - wait for new content
        logging.info(f"[{category}:{search_term}] Exhausted results for round {round_num}. Will retry same round later.")
        # Mark as exhausted with timestamp for potential backoff logic
        try:
            doc_id = f"{category}_{search_term}".replace(' ', '_').replace('/', '_')
            state_collection.document(doc_id).update({
                "exhausted_at": int(time.time()),
                "last_updated": int(time.time())
            })
        except Exception as e:
            logging.error(f"Error marking exhausted state: {e}")

async def wallpaper_fetcher_task(db, api_key, categories):
    """Background task that continuously fetches wallpapers"""
    wallpaper_collection = db.collection('wallhaven')
    state_collection = db.collection('fetch_state')
    
    logging.info("🔄 Wallpaper fetcher task started")
    
    while not shutdown_requested:
        # Check rate limit at start of each cycle
        if not check_rate_limit():
            current_time = int(time.time())
            period_duration_seconds = RATE_LIMIT_PERIOD_HOURS * 3600
            time_left_seconds = rate_limit_state['period_start'] + period_duration_seconds - current_time
            time_left_hours = time_left_seconds / 3600
            
            logging.info(f"⏸ Fetcher paused - rate limit reached ({rate_limit_state['wallpapers_added']}/{MAX_WALLPAPERS_PER_PERIOD})")
            logging.info(f"  Will resume in {time_left_hours:.1f} hours. Sleeping for 1 hour, then checking again...")
            
            # Sleep for 1 hour and check again (in case bot restarted or period expired)
            await asyncio.sleep(3600)  # 1 hour
            continue
        
        for category_config in categories:
            if shutdown_requested:
                break
            
            category = category_config['name']
            search_terms = category_config['search_terms']
            
            for search_term in search_terms:
                if shutdown_requested:
                    break
                
                try:
                    await fetch_wallpapers_for_term(
                        wallpaper_collection,
                        state_collection,
                        category,
                        search_term,
                        api_key
                    )
                except Exception as e:
                    logging.error(f"Error fetching wallpapers for {category}:{search_term}: {e}")
                    logging.error("Continuing with next search term...")
                
                # Small delay between search terms
                await asyncio.sleep(2)
            
            # Delay between categories
            await asyncio.sleep(5)
        
        if not shutdown_requested:
            logging.info("✓ Completed full cycle through all categories")
            logging.info("🔄 Starting next round...")
            await asyncio.sleep(10)
    
    logging.info("🔄 Wallpaper fetcher task stopped")

# =============================================================================
# TELEGRAM POSTING FUNCTIONS
# =============================================================================

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
    # Check disk-based cache first to reduce Firestore reads (minimal RAM usage)
    cached_wallpaper_id = check_cache_db(sha256)
    if cached_wallpaper_id:
        return "duplicate", {
            "reason": "Duplicate",
            "details": {
                "type": "SHA256_match_cached",
                "wallpaper_id": cached_wallpaper_id
            }
        }
    
    # Query Firestore with retry logic for quota errors
    max_retries = 3
    retry_delay = FIRESTORE_QUOTA_BACKOFF
    
    for attempt in range(max_retries):
        try:
            docs = collection.where(filter=FieldFilter('sha256', '==', sha256)).limit(1).stream()
            for doc in docs:
                wallpaper_id = doc.to_dict().get('wallpaper_id')
                # Add to disk cache
                add_to_cache_db(sha256, wallpaper_id)
                
                return "duplicate", {
                    "reason": "Duplicate",
                    "details": {
                        "type": "SHA256_match",
                        "wallpaper_id": wallpaper_id
                    }
                }
            return "proceed", None
        except (ResourceExhausted, RetryError) as e:
            if "Quota exceeded" in str(e):
                if attempt < max_retries - 1:
                    logging.warning(f"Firestore quota exceeded, waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logging.error(f"Firestore quota exceeded after {max_retries} retries. Treating as non-duplicate to continue.")
                    return "proceed", None
            else:
                raise
        except Exception as e:
            logging.error(f"Error checking duplicate hashes: {e}")
            # On other errors, proceed to avoid blocking
            return "proceed", None
    
    return "proceed", None

@retry_on_failure(max_attempts=3, delay=2, backoff=2)
async def download_image(url, filename):
    """Download image asynchronously with retry logic"""
    try:
        # Check disk space before download (FIX #7)
        stats = shutil.disk_usage(os.path.dirname(filename))
        if stats.free < 100 * 1024 * 1024:  # Less than 100MB
            logging.error(f"Low disk space: {stats.free / (1024*1024):.1f}MB available")
            return None
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, partial(requests.get, url, timeout=60, stream=True))
        response.raise_for_status()
        
        def write_file():
            with open(filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        await loop.run_in_executor(None, write_file)
        return filename
    except Exception as e:
        # Clean up partial file on failure (FIX #6)
        if os.path.exists(filename):
            try:
                os.remove(filename)
                logging.debug(f"Cleaned up partial file: {filename}")
            except:
                pass
        raise  # Re-raise for retry decorator

def validate_image_dimensions(image_path):
    """Validate image dimensions for Telegram compatibility"""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            # Telegram photo requirements:
            # - Sum of width and height must not exceed 10000
            # - Aspect ratio must be reasonable (not too extreme)
            if width + height > 10000:
                logging.warning(f"Image dimensions too large: {width}x{height}")
                return False
            if width < 1 or height < 1:
                logging.warning(f"Invalid image dimensions: {width}x{height}")
                return False
            # Check aspect ratio (avoid extreme ratios)
            aspect_ratio = max(width, height) / min(width, height)
            if aspect_ratio > 20:
                logging.warning(f"Extreme aspect ratio: {aspect_ratio:.1f}")
                return False
            return True
    except Exception as e:
        logging.error(f"Error validating image dimensions: {e}")
        return False

async def generate_thumbnail(image_path, max_size_kb=200):
    """Generate thumbnail using PIL to ensure compatibility and size limits"""
    try:
        # Validate file extension
        base, ext = os.path.splitext(image_path)
        if not ext:
            logging.error(f"No file extension for: {image_path}")
            return None
        thumb_path = base + '_thumb.jpg'
        
        # Use PIL for more reliable thumbnail generation
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _create_thumbnail_sync, image_path, thumb_path, max_size_kb)
        
        if os.path.exists(thumb_path):
            size_kb = os.path.getsize(thumb_path) / 1024
            logging.info(f"Generated thumbnail: {size_kb:.1f}KB")
            return thumb_path
        return None
    except Exception as e:
        logging.error(f"Error generating thumbnail: {e}")
        return None

def _create_thumbnail_sync(image_path, thumb_path, max_size_kb):
    """Synchronous thumbnail creation with size optimization"""
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Create thumbnail
            img.thumbnail((320, 320), Image.Resampling.LANCZOS)
            
            # Save with progressively lower quality until under max_size_kb
            quality = 85
            while quality >= 20:
                img.save(thumb_path, 'JPEG', quality=quality, optimize=True)
                size_kb = os.path.getsize(thumb_path) / 1024
                if size_kb <= max_size_kb:
                    break
                quality -= 10
    except Exception as e:
        logging.error(f"Error in _create_thumbnail_sync: {e}")
        raise

async def generate_thumbnail_legacy(image_path, max_size_kb=200):
    """Legacy ImageMagick thumbnail generation (fallback)"""
    try:
        # Validate file extension (FIX #9)
        base, ext = os.path.splitext(image_path)
        if not ext:
            logging.error(f"No file extension for: {image_path}")
            return None
        thumb_path = base + '_thumb.jpg'
        
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
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(subprocess.run, cmd, capture_output=True, text=True, timeout=30)
        )
        
        if result.returncode != 0:
            logging.error(f"ImageMagick failed: {result.stderr}")
            return None
        
        if os.path.exists(thumb_path):
            size_kb = os.path.getsize(thumb_path) / 1024
            
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
                result = await loop.run_in_executor(
                    None,
                    partial(subprocess.run, cmd, capture_output=True, timeout=30)
                )
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
        gc.collect()

def get_pending_wallpapers(collection, category, count=3):
    max_retries = 3
    retry_delay = FIRESTORE_QUOTA_BACKOFF
    
    for attempt in range(max_retries):
        try:
            # Query all matching documents
            query = collection.where(filter=FieldFilter('category', '==', category)).where(filter=FieldFilter('status', '==', 'link_added'))
            docs = list(query.stream())
            
            if not docs:
                return []
            
            # Randomly sample from results
            fetch_count = min(count, len(docs))
            sampled_docs = random.sample(docs, fetch_count)
            
            # Convert to dictionaries and add wallpaper_id from document ID
            result = []
            for doc in sampled_docs:
                data = doc.to_dict()
                if 'wallpaper_id' not in data:
                    data['wallpaper_id'] = doc.id
                result.append(data)
            
            return result
        except (ResourceExhausted, RetryError) as e:
            if "Quota exceeded" in str(e):
                if attempt < max_retries - 1:
                    logging.warning(f"Database query quota exceeded for {category}, waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logging.error(f"Database query failed for category {category} after {max_retries} retries: {e}")
                    return []  # Return empty to avoid crash
            else:
                raise
        except Exception as e:
            logging.error(f"Database query failed for category {category}: {e}")
            # Return empty on other errors to continue operation
            return []

def update_wallpaper_status(collection, wallpaper_id, status, sha256=None, 
                           tg_response=None, reasons=None):
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            update_data = {"status": status}
            if sha256:
                update_data["sha256"] = sha256
                # Add to disk cache when status is updated with sha256
                if status == "posted":
                    add_to_cache_db(sha256, wallpaper_id)
            
            # Build tg_response atomically
            if tg_response:
                if not isinstance(tg_response, dict):
                    logging.error(f"Invalid tg_response type for {wallpaper_id}: {type(tg_response)}. Rejecting update.")
                    return  # Don't update with invalid data
                update_data["tg_response"] = tg_response
            
            if reasons:
                if not isinstance(reasons, dict):
                    logging.error(f"Invalid reasons type for {wallpaper_id}: {type(reasons)}. Rejecting update.")
                    return  # Don't update with invalid data
                
                # Merge reasons into tg_response
                if "tg_response" in update_data:
                    update_data["tg_response"].update(reasons)
                else:
                    # Get existing tg_response and merge
                    doc = collection.document(wallpaper_id).get()
                    if doc.exists:
                        existing_tg = doc.to_dict().get('tg_response', {})
                        existing_tg.update(reasons)
                        update_data["tg_response"] = existing_tg
                    else:
                        update_data["tg_response"] = reasons
            
            # Update using document reference
            collection.document(wallpaper_id).update(update_data)
            return  # Success
        except (ResourceExhausted, RetryError) as e:
            if "Quota exceeded" in str(e):
                if attempt < max_retries - 1:
                    logging.warning(f"Quota exceeded updating {wallpaper_id}, retry {attempt + 1}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logging.error(f"Failed to update wallpaper {wallpaper_id} after {max_retries} retries: {e}")
            else:
                logging.error(f"Failed to update wallpaper {wallpaper_id}: {e}")
                return
        except Exception as e:
            logging.error(f"Failed to update wallpaper {wallpaper_id}: {e}")
            return

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
    
    # Use list to track files in order for proper cleanup
    opened_files = []
    try:
        media = []
        files_dict = {}
        
        for idx, item in enumerate(media_list):
            if not os.path.exists(item['path']):
                logging.error(f"File not found: {item['path']}")
                continue
            
            # Open files and track them
            file_key = f"file{idx}"
            file_obj = open(item['path'], 'rb')
            opened_files.append(file_obj)
            files_dict[file_key] = file_obj
            
            media_item = {
                "type": "document" if is_document else "photo",
                "media": f"attach://{file_key}"
            }
            
            if is_document and item.get('thumbnail') and os.path.exists(item['thumbnail']):
                thumb_key = f"thumb{idx}"
                thumb_obj = open(item['thumbnail'], 'rb')
                opened_files.append(thumb_obj)
                files_dict[thumb_key] = thumb_obj
                media_item["thumbnail"] = f"attach://{thumb_key}"
            
            media.append(media_item)
        
        if not media:
            logging.error("No valid media items to send")
            return None
        
        data = {
            'chat_id': chat_id,
            'media': json.dumps(media)
        }
        
        response = requests.post(url, data=data, files=files_dict, timeout=120)
        
        if response.status_code != 200:
            try:
                error_data = response.json()
                logging.error(f"Telegram API error: {error_data}")
            except:
                logging.error(f"Telegram API error: {response.text}")
        
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        logging.error(f"Telegram sendMediaGroup failed: {e}")
        return None
    finally:
        # Always close all file handles in reverse order
        for file_obj in reversed(opened_files):
            try:
                if file_obj and not file_obj.closed:
                    file_obj.close()
            except Exception as close_error:
                logging.debug(f"Error closing file: {close_error}")

async def send_wallpaper_to_group(collection, category, group_id):
    if shutdown_requested:
        logging.info(f"Skipping wallpaper send for {category} due to shutdown request.")
        return
    
    task = asyncio.current_task()
    ACTIVE_TASKS.add(task)
    
    # Track all downloaded files for cleanup
    downloaded_files = []
    
    try:
        try:
            wallpapers = get_pending_wallpapers(collection, category, count=3)
        except Exception as e:
            logging.error(f"[{category}] Failed to fetch wallpapers from database: {e}")
            return
        
        if not wallpapers:
            return  # Silently skip if no wallpapers
        
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
            
            # Note: Redundant check removed (FIX #12)
            # get_pending_wallpapers already filters for status='link_added'
            
            path = await download_image(jpg_url, filename)
            if not path:
                reasons = {"reason": "Download failed", "url": jpg_url}
                update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
                logging.error(f"[{category}] Download failed for {wallpaper_id}")
                continue
            
            # Track file for cleanup
            downloaded_files.append(path)
            
            # Validate image dimensions for Telegram
            if not validate_image_dimensions(path):
                reasons = {"reason": "Invalid dimensions for Telegram"}
                update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
                logging.error(f"[{category}] Invalid dimensions for {wallpaper_id}")
                continue
            
            file_size_mb = os.path.getsize(path) / (1024 * 1024)
            thumbnail_path = None
            
            # Only generate thumbnail for HD document if file > 9MB
            # Preview photos will be sent without thumbnail (Telegram auto-compresses)
            if file_size_mb > 9.0:
                logging.info(f"[{category}] File size {file_size_mb:.2f}MB > 9.0MB, generating thumbnail for HD document...")
                thumbnail_path = await generate_thumbnail(path, max_size_kb=150)
                if thumbnail_path:
                    downloaded_files.append(thumbnail_path)
                    # Verify thumbnail is reasonable
                    thumb_size_mb = os.path.getsize(thumbnail_path) / (1024 * 1024)
                    if thumb_size_mb > 1.0:  # Thumbnail shouldn't be > 1MB
                        logging.warning(f"[{category}] Thumbnail too large ({thumb_size_mb:.2f}MB), creating smaller one...")
                        thumbnail_path = await generate_thumbnail(path, max_size_kb=100)
                        if thumbnail_path:
                            downloaded_files.append(thumbnail_path)
                else:
                    logging.warning(f"[{category}] Failed to generate thumbnail for {wallpaper_id}")
                    # Continue anyway - document can be sent without thumbnail
            
            sha256 = calculate_hashes(path)
            if not sha256:
                reasons = {"reason": "Hashing failed"}
                update_wallpaper_status(collection, wallpaper_id, "failed", reasons=reasons)
                os.remove(path)
                logging.error(f"[{category}] Hashing failed for {wallpaper_id}")
                continue
            
            status_check, reasons = check_duplicate_hashes(collection, sha256)
            if status_check == "duplicate":
                log_details = f"{reasons['details']['type']}"
                logging.warning(f"[{category}] Skipping {wallpaper_id}: {reasons['reason']} - {log_details}")
                update_wallpaper_status(collection, wallpaper_id, "skipped", sha256, reasons=reasons)
                # Cleanup will happen in finally block
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
            # Send preview photos (Telegram will auto-compress, we don't need thumbnails)
            preview_responses = telegram_send_media_group(group_id, wallpaper_data, is_document=False)
            if not preview_responses:
                raise Exception("Failed to send preview images")
            
            await asyncio.sleep(3)
            
            # Send HD versions as documents
            # Only files > 9MB have thumbnails attached
            hd_responses_map = {}  # wallpaper_id -> response
            
            # Always send documents individually for better control
            for idx, item in enumerate(wallpaper_data):
                # thumbnail_path will be None if file < 9MB (fine, optional parameter)
                response = telegram_send_document(group_id, item['path'], item['thumbnail'])
                hd_responses_map[item['wallpaper_id']] = response
                await asyncio.sleep(0.5)
            
            # Update database - only mark as posted if both uploads succeeded
            preview_result_list = preview_responses.get('result', [])
            for i, item in enumerate(wallpaper_data):
                preview_msg = preview_result_list[i] if i < len(preview_result_list) else {}
                
                # Get HD response for this specific wallpaper
                hd_response = hd_responses_map.get(item['wallpaper_id'])
                if hd_response and isinstance(hd_response, dict):
                    hd_result = hd_response.get('result', {})
                else:
                    hd_result = {}
                
                # Check if both preview and HD were successful
                preview_success = bool(preview_msg.get('message_id'))
                hd_success = bool(hd_result.get('message_id'))
                
                tg_response = {
                    "preview": {
                        "message_id": preview_msg.get('message_id'),
                        "date": preview_msg.get('date'),
                        "success": preview_success
                    },
                    "hd": {
                        "message_id": hd_result.get('message_id'),
                        "date": hd_result.get('date'),
                        "success": hd_success
                    },
                    "group_id": group_id,
                    "uploaded_at": int(time.time()),
                    "album_size": len(wallpaper_data)
                }
                
                if preview_success and hd_success:
                    update_wallpaper_status(collection, item['wallpaper_id'], "posted", item['sha256'], tg_response=tg_response)
                    logging.info(f"[{category}] ✓ Posted {item['wallpaper_id']} to group {group_id} (album {i+1}/{len(wallpaper_data)})")
                else:
                    # Mark as failed if either upload didn't complete
                    failed_part = "preview" if not preview_success else "HD"
                    tg_response["failure_reason"] = f"{failed_part} upload failed"
                    update_wallpaper_status(collection, item['wallpaper_id'], "failed", item['sha256'], tg_response=tg_response)
                    logging.error(f"[{category}] ✗ Failed to post {item['wallpaper_id']}: {failed_part} upload failed")
            
        except Exception as telegram_e:
            logging.error(f"[{category}] Telegram upload failed: {telegram_e}")
            for item in wallpaper_data:
                reasons = {"reason": "Telegram upload failed", "error": str(telegram_e)}
                update_wallpaper_status(collection, item['wallpaper_id'], "failed", item['sha256'], reasons=reasons)
    
    finally:
        # Cleanup all downloaded files
        for filepath in downloaded_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logging.warning(f"Failed to remove {filepath}: {e}")
        
        gc.collect()
        ACTIVE_TASKS.discard(task)

# =============================================================================
# MAIN FUNCTION
# =============================================================================

async def main():
    global BOT_TOKEN, rate_limit_lock
    
    logging.info("=" * 70)
    logging.info("Wallhaven Telegram Bot - Combined Fetcher & Poster")
    logging.info("=" * 70)
    
    # Initialize rate limit lock
    rate_limit_lock = asyncio.Lock()
    
    # Create cache directory
    cache_dir = "wall-cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        logging.info(f"✓ Created cache directory: {cache_dir}")
    else:
        logging.info(f"✓ Using existing cache directory: {cache_dir}")
    
    # Signal handling
    if sys.platform != 'win32':
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, handle_shutdown)
        loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
    else:
        # Windows only supports SIGINT (Ctrl+C), not SIGTERM
        signal.signal(signal.SIGINT, lambda sig, frame: handle_shutdown())
    
    # Load configuration
    firebase_cred_path = load_firebase_config()
    tg_config = load_telegram_config()
    BOT_TOKEN = tg_config['BOT_TOKEN']
    
    # Validate BOT_TOKEN
    if not BOT_TOKEN or not isinstance(BOT_TOKEN, str) or len(BOT_TOKEN) < 20:
        logging.error("Invalid BOT_TOKEN configuration!")
        sys.exit(1)
    
    api_key = load_wallhaven_api_key()
    categories = load_categories_config()
    
    logging.info(f"✓ Loaded {len(categories)} category configurations")
    for cat in categories:
        logging.info(f"  - {cat['name']}: Group {cat['group_id']}, Every {cat['interval']}s, {len(cat['search_terms'])} terms")
    
    # Initialize disk-based cache databases
    logging.info("Initializing cache databases...")
    init_cache_db()  # SHA256 hash cache for duplicate detection
    
    logging.info("Initializing metadata cache...")
    init_metadata_cache_db()  # Wallpaper ID cache to avoid Firebase reads
    
    # Connect to Firebase
    logging.info("")
    logging.info("Connecting to Firebase Firestore...")
    db = connect_to_firebase(firebase_cred_path)
    wallpaper_collection = db.collection('wallhaven')
    state_collection = db.collection('fetch_state')
    
    # Sync metadata cache from Firebase (handles fresh deployment/crash recovery)
    await sync_metadata_cache_from_firebase(wallpaper_collection)
    
    # Note: Firestore indexes are created automatically or via Firebase Console
    # Composite indexes needed:
    # - wallhaven collection: category + status
    # - wallhaven collection: sha256 (single field)
    logging.info("✓ Firebase Firestore collections initialized")
    logging.info("  Note: Ensure composite indexes are created in Firebase Console if needed")
    
    logging.info("✓ Telegram bot configured")
    
    # Start background fetcher task
    fetcher_task = asyncio.create_task(
        wallpaper_fetcher_task(db, api_key, categories)
    )
    ACTIVE_TASKS.add(fetcher_task)
    
    # Setup scheduler for Telegram posting
    scheduler = AsyncIOScheduler()
    
    for category_config in categories:
        category = category_config['name']
        group_id = category_config['group_id']
        interval = category_config['interval']
        
        # Check if category has wallpapers before scheduling
        try:
            query = wallpaper_collection.where(filter=FieldFilter('category', '==', category)).where(filter=FieldFilter('status', '==', 'link_added'))
            available = len(list(query.stream()))
        except Exception as e:
            logging.warning(f"Error checking wallpapers for {category}: {e}")
            available = 0
        
        # Always schedule, but log differently based on availability
        scheduler.add_job(
            send_wallpaper_to_group,
            'interval',
            args=[wallpaper_collection, category, group_id],
            seconds=interval,
            id=f'job_{category}',
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60
        )
        
        if available > 0:
            logging.info(f"✓ Scheduled '{category}' (every {interval}s / {interval//60}min) - {available} wallpapers available")
        else:
            logging.info(f"⏸ Scheduled '{category}' (every {interval}s / {interval//60}min) - Waiting for wallpapers...")
    
    # Schedule daily cache cleanup (runs when 90% full)
    scheduler.add_job(
        cleanup_cache_task,
        'interval',
        hours=24,
        id='cache_cleanup',
        max_instances=1
    )
    logging.info(f"✓ Scheduled daily cache cleanup (max {CACHE_MAX_ENTRIES:,} entries)")
    
    # Schedule weekly maintenance (integrity check + optimization)
    scheduler.add_job(
        maintenance_task,
        'interval',
        days=7,
        id='cache_maintenance',
        max_instances=1
    )
    logging.info("✓ Scheduled weekly cache maintenance (integrity check + optimization)")
    
    scheduler.start()
    logging.info("=" * 70)
    logging.info("✅ Bot is running")
    logging.info("🔄 Fetcher: Continuously fetching wallpapers")
    logging.info("📤 Poster: Posting on schedule")
    logging.info("Press Ctrl+C to stop")
    logging.info("=" * 70)
    
    # Keep running until shutdown
    while not shutdown_requested:
        await asyncio.sleep(1)
    
    # Graceful shutdown
    if ACTIVE_TASKS:
        logging.info(f"Waiting for {len(ACTIVE_TASKS)} active tasks to finish...")
        await asyncio.gather(*ACTIVE_TASKS, return_exceptions=True)
    
    scheduler.shutdown()
    
    # Close cache database
    logging.info("Closing cache database...")
    close_cache_db()
    
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
