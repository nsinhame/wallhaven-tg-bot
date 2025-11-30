#!/usr/bin/env python3

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                WALLHAVEN TO MONGODB LINK UPLOADER                           ║
║                                                                              ║
║  Fetches wallpaper metadata from Wallhaven.cc API and stores in MongoDB      ║
║  for later processing by tg-up-bot.py. Respects API rate limits and         ║
║  prevents duplicates.                                                        ║
║                                                                              ║
║  CONTENT POLICY: SFW + Sketchy ONLY (NO NSFW)                                ║
║  This script strictly enforces purity=110 (SFW + Sketchy, NO NSFW)           ║
║                                                                              ║
║  KEY FEATURES:                                                               ║
║  • Multi-category processing from config.txt                                  ║
║  • Multiple search terms per category                                         ║
║  • Pagination support (fetches all pages until exhausted)                    ║
║  • Rate limiting: 40 requests/minute (safety buffer from 45 limit)           ║
║  • Tag fetching from individual wallpaper endpoint                            ║
║  • Duplicate prevention via unique index on wallpaper_id                     ║
║  • Unix epoch timestamps for cross-platform compatibility                    ║
║                                                                              ║
║  WORKFLOW:                                                                   ║
║  1. Load config.txt (categories and search terms)                            ║
║  2. Connect to MongoDB (database: wallpaper-bot, collection: wallhaven)      ║
║  3. For each category:                                                       ║
║     For each search term:                                                    ║
║       a. Query Wallhaven search API with filters (portrait, SFW+Sketchy)    ║
║       b. For each wallpaper in results:                                      ║
║          - Fetch detailed info including tags from /w/<ID> endpoint         ║
║          - Create document with metadata                                    ║
║          - Insert into MongoDB (skip if duplicate)                          ║
║       c. Paginate through all results until no more found                    ║
║       d. Respect rate limit (40 calls/min with 2s buffer)                    ║
║  4. Display final statistics                                                 ║
║                                                                              ║
║  DATABASE SCHEMA (wallpaper-bot.wallhaven collection):                       ║
║  {                                                                           ║
║    wallpaper_id: "abc123"        // Unique Wallhaven ID                      ║
║    category: "nature"             // From config.txt                         ║
║    search_term: "mountain"        // Specific term that found this           ║
║    wallpaper_url: "https://..."   // Wallhaven page URL                      ║
║    jpg_url: "https://..."         // Direct image URL                        ║
║    tags: ["landscape", "snow"]    // Array of tag strings                    ║
║    purity: "sfw"                  // "sfw" or "sketchy" (never "nsfw")       ║
║    sfw: true                      // Boolean: true=SFW, false=Sketchy        ║
║    status: "link_added"           // Processing status                        ║
║    sha256: null                   // Filled by tg-up-bot.py                   ║
║    phash: null                    // Filled by tg-up-bot.py                   ║
║    tg_response: {}                // Filled by tg-up-bot.py                   ║
║    created_at: 1701234567         // Unix epoch timestamp                     ║
║  }                                                                           ║
║                                                                              ║
║  CONFIGURATION FILES:                                                        ║
║  • config.txt         - Categories and search terms                          ║
║  • wallhaven-api.txt  - Your Wallhaven API key                               ║
║  • mongodb-uri.txt    - MongoDB connection string                            ║
║                                                                              ║
║  DEPENDENCIES:                                                               ║
║  pip install pymongo requests                                                ║
║                                                                              ║
║  SAFE FOR LONG-RUNNING DEPLOYMENTS (4-5 days on server)                      ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
Wallhaven to MongoDB Link Uploader (SFW + Sketchy)

Description: Fetches portrait wallpaper links from Wallhaven.cc
             and uploads them to MongoDB (SFW + Sketchy content only, NO NSFW)

Usage: python update-link-db.py

Configuration:
    - config.txt: Define categories, Telegram groups, intervals, and search terms
      Format: category | group_id | interval_seconds | search_term1, term2
      Example:
        nature | -1002996780898 | 3050 | tree, water, river, sky
        vehicle | -1002123456789 | 1200 | car, bike, racing
        anime | -1002935599065 | 1000 | anime, cartoon, digital art
    
    - mongodb-uri.txt: Your MongoDB connection string
    - wallhaven-api.txt: Your Wallhaven API key
      Get it from https://wallhaven.cc/settings/account

Database Fields:
    - sfw: True for SFW content, False for Sketchy content
    - purity: "sfw" or "sketchy" (detailed tracking)
    - category: From categories.txt (left side of colon)
    - search_term: The specific search term that found this wallpaper

Note: This script processes all categories and search terms from config.txt,
      respecting the 40 requests per minute rate limit. Safe for long-running
      server deployments (4-5 days).
"""

# ============================================================================
# IMPORTS
# ============================================================================

# Standard library imports
import sys              # System operations (exit codes)
import os               # File system operations (path checks)
import time             # Time operations (rate limiting, timestamps)
from datetime import datetime  # Timestamp handling

# Third-party imports
from pymongo import MongoClient                          # MongoDB Python driver
from pymongo.errors import DuplicateKeyError, ConnectionFailure  # MongoDB exceptions
import requests                                          # HTTP client for API requests

# ============================================================================
# RATE LIMITING CONFIGURATION
# ============================================================================
# Note: All configuration loaders (get_mongodb_uri, get_wallhaven_api_key, etc.)
# automatically skip comment lines (starting with #) and empty lines in files.
# This allows keeping instructions directly in config files.

# Wallhaven API rate limit: 45 requests per minute
# We use 40 to provide safety buffer (prevents hitting limit on clock skew)
# Long-running server deployments benefit from conservative rate limiting
MAX_REQUESTS_PER_MINUTE = 40

# Rolling window of API call timestamps (Unix epoch seconds)
# Used to track when each API call was made in the last 60 seconds
# Example: [1701234567.123, 1701234568.456, ...]
api_call_times = []

def enforce_rate_limit():
    """
    Enforce API rate limiting using sliding window algorithm.
    
    Algorithm:
    1. Get current time
    2. Remove timestamps older than 60 seconds from tracking list
    3. If we have made 40+ calls in last 60 seconds:
       - Calculate wait time equals 60 seconds minus time since oldest call plus 2 second buffer
       - Sleep for that duration
       - Clean up tracking list again after waking up
    4. Record current API call timestamp
    
    Why Sliding Window?
    - More accurate than fixed time windows
    - Prevents burst then wait pattern
    - Smooths out API calls over time
    
    Why 2-Second Buffer?
    - Accounts for network latency
    - Accounts for clock synchronization differences
    - Prevents edge cases where we hit exactly 60 seconds
    
    Example Scenario:
    If we made 40 calls at timestamps [0, 1, 2, ..., 39] seconds:
    - At 40 seconds, oldest call is at 0 seconds (40 seconds ago)
    - Wait time equals 60 minus 40 plus 2 equals 22 seconds
    - After 22 second wait, oldest call (0 seconds) is now 62 seconds old
    - Gets removed from list, safe to make new call
    
    Global Variables:
        api_call_times: List of Unix epoch timestamps for recent API calls
    """
    global api_call_times
    current_time = time.time()
    
    # Remove timestamps older than 60 seconds
    api_call_times = [t for t in api_call_times if current_time - t < 60]
    
    # If we have made 40+ calls in the last 60 seconds, wait
    if len(api_call_times) >= MAX_REQUESTS_PER_MINUTE:
        oldest_call = api_call_times[0]
        wait_time = 60 - (current_time - oldest_call) + 2  # 2 second safety buffer
        if wait_time > 0:
            print(f"⏱ Rate limit: Waiting {wait_time:.1f}s...")
            time.sleep(wait_time)
            # Clean up old timestamps after waiting
            current_time = time.time()
            api_call_times = [t for t in api_call_times if current_time - t < 60]
    
    # Record this API call
    api_call_times.append(time.time())

def fetch_wallpaper_tags(wallpaper_id, api_key):
    """
    Fetch detailed wallpaper information including tags from Wallhaven API.
    
    Why Separate API Call?
    - Search endpoint (/api/v1/search) does not include full tag list
    - Individual wallpaper endpoint (/api/v1/w/<ID>) has complete metadata
    - Tags are valuable for content classification and filtering
    
    API Endpoint:
        GET https://wallhaven.cc/api/v1/w/<wallpaper_id>?apikey=<key>
    
    Why API Key Required?
    - Some wallpapers are "Sketchy" purity level
    - Sketchy content requires authentication
    - Without API key, you only get SFW wallpapers
    
    Response Structure:
        {
            "data": {
                "id": "abc123",
                "tags": [
                    {"id": 1, "name": "anime", "alias": "...", ...},
                    {"id": 2, "name": "landscape", "alias": "...", ...}
                ],
                ... other fields ...
            }
        }
    
    Tag Extraction:
    - Navigate: response["data"]["tags"]
    - Each tag is a dict with multiple fields
    - We only need the "name" field
    - Filter out any tags without names
    
    Rate Limiting:
    - enforce_rate_limit() called BEFORE making request
    - This ensures we stay within 40 calls per minute
    - Applies to this endpoint AND search endpoint combined
    
    Args:
        wallpaper_id (str): Wallhaven wallpaper ID (e.g., "94x38z")
        api_key (str): Your Wallhaven API key from account settings
    
    Returns:
        list of str: Tag names (e.g., ["anime", "landscape", "sunset"])
        Empty list [] if:
        - API request fails
        - Response is malformed
        - Wallpaper has no tags
        - Network error occurs
    
    Error Handling:
    - Logs warning but does not crash program
    - Wallpaper is still added to database, just without tags
    - Better to have wallpaper with no tags than skip entirely
    """
    enforce_rate_limit()
    
    try:
        url = f"https://wallhaven.cc/api/v1/w/{wallpaper_id}"
        params = {"apikey": api_key}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extract tags from response
        wallpaper_data = data.get("data", {})
        tags_list = wallpaper_data.get("tags", [])
        
        # Extract just the tag names
        tag_names = [tag.get("name", "") for tag in tags_list if tag.get("name")]
        
        return tag_names
    except Exception as e:
        print(f"    ⚠ Could not fetch tags for {wallpaper_id}: {e}")
        return []

def parse_config_file():
    """
    Parse config.txt and extract categories with their search terms.
    
    File Format:
        category | group_id | interval_seconds | search_term1, search_term2
        Example:
        nature | -1002996780898 | 3050 | tree, water, mountain, river
    
    What This Function Extracts:
    - Field 1: category name (e.g., "nature")
    - Field 4: search terms (e.g., ["tree", "water", "mountain"])
    
    What This Function IGNORES:
    - Field 2: group_id (only used by tg-up-bot.py)
    - Field 3: interval (only used by tg-up-bot.py)
    
    Parsing Logic:
    1. Read file line by line
    2. Skip empty lines and comments (starting with #)
    3. Split line by '|' delimiter
    4. Validate 4 parts exist
    5. Extract category (part 0) and search_terms (part 3)
    6. Split search_terms by comma, strip whitespace
    7. Validate both category and search_terms exist
    8. Add to results as tuple: (category, [term1, term2, ...])
    
    Why Tuples?
    • Immutable data structure
    • Clear pairing of category with its terms
    • Easy to iterate in nested loops
    
    Returns:
        list of tuples: [
            ('nature', ['tree', 'water', 'mountain']),
            ('anime', ['cartoon', 'manga']),
            ...
        ]
    
    Exits:
        If config.txt does not exist or contains no valid categories
    
    Example Usage:
        categories = parse_config_file()
        for category, terms in categories:
            for term in terms:
                # Fetch wallpapers for this category/term pair
                ...
    """
    if not os.path.exists('config.txt'):
        print("Error: config.txt file not found!")
        print("Please create config.txt with format:")
        print("  category | group_id | interval_seconds | search_term1, term2")
        print("Example:")
        print("  nature | -1002996780898 | 3050 | tree, water, river, sky")
        print("  vehicle | -1002123456789 | 1200 | car, bike, racing")
        sys.exit(1)
    
    categories = []
    with open('config.txt', 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse line: category | group_id | interval | search_terms
            if '|' not in line:
                print(f"Warning: Skipping invalid line {line_num}: {line}")
                continue
            
            parts = line.split('|')
            if len(parts) != 4:
                print(f"Warning: Skipping invalid line {line_num} (expected 4 parts): {line}")
                continue
            
            category = parts[0].strip()
            # group_id and interval are ignored by update-link-db.py
            search_terms_str = parts[3].strip()
            
            # Split search terms by comma and strip whitespace
            search_terms = [term.strip() for term in search_terms_str.split(',') if term.strip()]
            
            if not category or not search_terms:
                print(f"Warning: Skipping invalid line {line_num}: {line}")
                continue
            
            categories.append((category, search_terms))
    
    if not categories:
        print("Error: No valid categories found in config.txt!")
        sys.exit(1)
    
    return categories

def get_mongodb_uri():
    """
    Get MongoDB URI from file, environment variable, or user input.
    
    Priority Order:
    1. mongodb-uri.txt file (skips comments and empty lines)
    2. MONGODB_URI environment variable
    3. User input (interactive prompt)
    4. Save user input to file for future use
    
    File Parsing:
    - Reads file line by line
    - Skips empty lines and comments (starting with #)
    - Returns first valid line
    
    Returns:
        str: MongoDB connection URI
    """
    # Try to read from file first
    if os.path.exists('mongodb-uri.txt'):
        with open('mongodb-uri.txt', 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    return line
    
    # Try environment variable
    uri = os.getenv('MONGODB_URI')
    if uri:
        return uri
    
    # Prompt user
    uri = input("Enter MongoDB URI: ").strip()
    if not uri:
        print("Error: MongoDB URI is required!")
        sys.exit(1)
    
    # Save to file for future use
    with open('mongodb-uri.txt', 'w') as f:
        f.write(uri)
    
    return uri

def get_wallhaven_api_key():
    """
    Get Wallhaven API key from file or user input.
    
    Priority Order:
    1. wallhaven-api.txt file (skips comments and empty lines)
    2. User input (interactive prompt)
    3. Save user input to file for future use
    
    File Parsing:
    • Reads file line by line
    • Skips empty lines and comments (starting with #)
    • Returns first valid line as API key
    
    Example file content:
        # Wallhaven API Key
        # Get from: https://wallhaven.cc/settings/account
        abc123def456ghi789
    
    Returns:
        str: Wallhaven API key
    """
    # Try to read from file first
    if os.path.exists('wallhaven-api.txt'):
        with open('wallhaven-api.txt', 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    return line
    
    # Prompt user
    api_key = input("Enter your Wallhaven API key: ").strip()
    if not api_key:
        print("Error: API key is required to access Sketchy content!")
        print("Get your API key from: https://wallhaven.cc/settings/account")
        sys.exit(1)
    
    # Save to file for future use
    with open('wallhaven-api.txt', 'w') as f:
        f.write(api_key)
    
    return api_key

def main():
    """
    Main execution function - orchestrates entire wallpaper fetching workflow.
    
    High-Level Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. Load Configuration                                       │
    │    • Parse config.txt for categories/terms                   │
    │    • Load Wallhaven API key                                 │
    │    • Load MongoDB URI                                        │
    └─────────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ 2. Connect to MongoDB                                       │
    │    • Test connection with ping                              │
    │    • Create unique index on wallpaper_id                    │
    └─────────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ 3. Process Each Category                                    │
    │    For each search term in category:                        │
    │    ┌─────────────────────────────────────────────────────┐ │
    │    │ a. Query Wallhaven search API                       │ │
    │    │    - Portrait orientation                           │ │
    │    │    - Purity 110 (SFW + Sketchy, NO NSFW)           │ │
    │    │    - Categories 110 (General + Anime)               │ │
    │    │    - Sorted by views (descending)                   │ │
    │    └─────────────────────────────────────────────────────┘ │
    │    ┌─────────────────────────────────────────────────────┐ │
    │    │ b. For each wallpaper in results:                   │ │
    │    │    - Fetch tags from /w/<ID> endpoint               │ │
    │    │    - Create MongoDB document                        │ │
    │    │    - Insert (skip if duplicate wallpaper_id)        │ │
    │    │    - Respect rate limit (40/min)                    │ │
    │    └─────────────────────────────────────────────────────┘ │
    │    ┌─────────────────────────────────────────────────────┐ │
    │    │ c. Paginate until no more results                   │ │
    │    │    - Each page has up to 24 wallpapers              │ │
    │    │    - Increment page number                          │ │
    │    │    - Stop when API returns empty data array         │ │
    │    └─────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ 4. Display Final Statistics                                 │
    │    • Total added                                             │
    │    • Total duplicates                                        │
    │    • Total errors                                            │
    └─────────────────────────────────────────────────────────────┘
    
    Error Handling Strategy:
    - Configuration errors: Exit immediately (cannot proceed)
    - Network errors: Log warning, continue with next item
    - Database errors: Log error, continue with next item
    - Duplicate key errors: Expected, counted separately
    
    Statistics Tracking:
    • Global counters: total_added, total_duplicates, total_errors
    • Per-search-term counters: count, duplicates, errors
    • Displayed after each search term and at end
    """
    print("=" * 70)
    print("Wallhaven to MongoDB Link Uploader")
    print("Fetching SFW + Sketchy content (NO NSFW)")
    print("=" * 70)
    print()
    
    # Get API key from file
    api_key = get_wallhaven_api_key()
    
    # Parse categories from config.txt file
    categories = parse_config_file()
    print(f"✓ Loaded {len(categories)} categories from config.txt")
    
    # Get MongoDB URI
    mongodb_uri = get_mongodb_uri()
    
    # Connect to MongoDB
    try:
        print("Connecting to MongoDB...")
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        # Test connection
        client.admin.command('ping')
        db = client['wallpaper-bot']
        collection = db.wallhaven
        
        # Create unique index on wallpaper_id to prevent duplicates
        collection.create_index("wallpaper_id", unique=True)
        
        print("✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)")
        print()
    except ConnectionFailure as e:
        print(f"Error: Failed to connect to MongoDB: {e}")
        sys.exit(1)
    
    # Global statistics
    total_added = 0
    total_duplicates = 0
    total_errors = 0
    
    # Process each category from categories.txt
    for category_index, (category, search_terms) in enumerate(categories, 1):
        print("=" * 70)
        print(f"CATEGORY [{category_index}/{len(categories)}]: {category}")
        print(f"Search terms: {', '.join(search_terms)}")
        print("=" * 70)
        print()
        
        # Process each search term for this category
        for term_index, search_term in enumerate(search_terms, 1):
            print(f"\n--- Processing: {category} -> {search_term} [{term_index}/{len(search_terms)}] ---\n")
            
            # Clean up search term for URL encoding
            search_query = search_term.replace('#', '')
            
            # Initialize counters for this search term
            count = 0
            duplicates = 0
            errors = 0
            page = 1
            
            # ============================================================
            # WALLHAVEN API SEARCH PARAMETERS
            # ============================================================
            # Documentation: https://wallhaven.cc/help/api
            
            api_url = "https://wallhaven.cc/api/v1/search"
            params = {
                # Search query (with # symbols removed for URL compatibility)
                "q": search_query,
                
                # categories: 3-digit binary string
                # First digit (1): General wallpapers - ENABLED
                # Second digit (1): Anime wallpapers - ENABLED
                # Third digit (0): People wallpapers - DISABLED
                # Result: "110" = General + Anime only
                "categories": "110",
                
                # purity: 3-digit binary string (CRITICAL PARAMETER)
                # First digit (1): SFW (Safe for Work) - ENABLED
                # Second digit (1): Sketchy (questionable but not explicit) - ENABLED
                # Third digit (0): NSFW (Not Safe for Work) - DISABLED
                # Result: "110" = SFW + Sketchy, NO NSFW
                # Note: This is enforced by user requirement
                "purity": "110",
                
                # ratios: Aspect ratio filter
                # "portrait" = height > width (mobile wallpapers)
                # Other options: "landscape", "16x9", "16x10", etc.
                "ratios": "portrait",
                
                # sorting: How to order results
                # "views" = most viewed wallpapers first (popular)
                # Other options: "date_added", "relevance", "random", "favorites"
                "sorting": "views",
                
                # order: Sorting direction
                # "desc" = descending (highest to lowest)
                # "asc" = ascending (lowest to highest)
                "order": "desc",
                
                # page: Pagination (1-indexed)
                # Each page returns up to 24 results
                # Incremented in loop to fetch all pages
                "page": page,
                
                # apikey: Your Wallhaven API key
                # Required to access Sketchy content
                # Without key, only SFW results returned
                "apikey": api_key
            }
            
            # Fetch all wallpapers for this search term
            while True:
                # Update page parameter
                params["page"] = page
                
                # Enforce rate limiting before making API call
                enforce_rate_limit()
                
                try:
                    # Make API request to Wallhaven
                    response = requests.get(api_url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    
                    # Extract wallpaper data from response
                    wallpapers = data.get("data", [])
                    
                    # Check if we got any results from this page
                    if not wallpapers:
                        print("  No more wallpapers found for this search term.")
                        break
                    
                    # Process each wallpaper from the current page
                    for wallpaper in wallpapers:
                        # Extract required data
                        wallpaper_id = wallpaper.get("id", "")
                        wallpaper_url = wallpaper.get("url", "")  # Page URL
                        jpg_url = wallpaper.get("path", "")        # Image URL
                        purity = wallpaper.get("purity", "sfw")    # sfw/sketchy
                        
                        if not wallpaper_url or not jpg_url:
                            errors += 1
                            continue
                        
                        # Fetch tags from individual wallpaper endpoint (with API key for Sketchy access)
                        print(f"  [{count + 1}] Fetching tags for {wallpaper_id} ({purity})...")
                        tags = fetch_wallpaper_tags(wallpaper_id, api_key)
                        
                        # ====================================================
                        # PREPARE MONGODB DOCUMENT
                        # ====================================================
                        
                        # Calculate sfw boolean field
                        # True: purity="sfw" (completely safe)
                        # False: purity="sketchy" (questionable but not explicit)
                        # This allows easy filtering: db.find({"sfw": true})
                        is_sfw = (purity == "sfw")
                        
                        # Use Unix epoch timestamp for cross-platform compatibility
                        # int(time.time()) returns seconds since 1970-01-01 00:00:00 UTC
                        # Why Unix epoch?
                        # - No timezone issues
                        # - Easy to sort and compare
                        # - Convert to any format later using datetime.fromtimestamp(epoch)
                        current_timestamp = int(time.time())
                        
                        # MongoDB document structure
                        # This matches the schema expected by tg-up-bot.py
                        document = {
                            # Unique Wallhaven ID (e.g., "94x38z")
                            # This field has unique index to prevent duplicates
                            "wallpaper_id": wallpaper_id,
                            
                            # Category from config.txt (e.g., "nature", "anime")
                            # Used by tg-up-bot.py to determine which Telegram group
                            "category": category,
                            
                            # Specific search term that found this wallpaper
                            # Example: category equals nature, search_term equals mountain
                            # Useful for analyzing which terms yield best results
                            "search_term": search_term,
                            
                            # Wallhaven page URL (human-readable)
                            # Example: "https://wallhaven.cc/w/94x38z"
                            "wallpaper_url": wallpaper_url,
                            
                            # Direct image URL (for downloading)
                            # Example: "https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg"
                            # This is what tg-up-bot.py downloads
                            "jpg_url": jpg_url,
                            
                            # Array of tag strings from Wallhaven
                            # Example: ["anime", "landscape", "sunset"]
                            # Fetched from individual wallpaper endpoint
                            "tags": tags,
                            
                            # Purity level string: "sfw" or "sketchy" (never "nsfw")
                            # Kept for detailed tracking and filtering
                            "purity": purity,
                            
                            # Boolean field for quick SFW filtering
                            # True: Completely safe (purity="sfw")
                            # False: Questionable content (purity="sketchy")
                            # Query example: db.find({"sfw": true})
                            "sfw": is_sfw,
                            
                            # Processing status for tg-up-bot.py workflow
                            # Status flow:
                            #   "link_added" → "posted"  (successfully uploaded)
                            #                → "failed"  (download/upload error)
                            #                → "skipped" (duplicate detected)
                            "status": "link_added",
                            
                            # SHA256 hash (filled by tg-up-bot.py after download)
                            # Used for exact duplicate detection
                            "sha256": None,
                            
                            # Perceptual hash (filled by tg-up-bot.py after download)
                            # Used for similar image detection
                            "phash": None,
                            
                            # Telegram upload response (filled by tg-up-bot.py)
                            # Contains message IDs, timestamps, group info
                            "tg_response": {},
                            
                            # Unix epoch timestamp when added to database
                            # Seconds since 1970-01-01 00:00:00 UTC
                            "created_at": current_timestamp
                        }
                        
                        try:
                            # Insert into MongoDB
                            collection.insert_one(document)
                            count += 1
                            tag_info = f" ({len(tags)} tags)" if tags else " (no tags)"
                            print(f"  [{count}] ✓ Added: {wallpaper_id} ({purity}){tag_info}")
                        
                        except DuplicateKeyError:
                            duplicates += 1
                            print(f"  [{count}] ⊘ Duplicate: {wallpaper_id}")
                        
                        except Exception as e:
                            errors += 1
                            print(f"  [{count}] ✗ Error adding {wallpaper_id}: {e}")
                    
                    # Move to next page for more results
                    page += 1
                
                except requests.exceptions.RequestException as e:
                    print(f"  Error fetching search results: {e}")
                    if "401" in str(e):
                        print("  Invalid API key. Please check your API key and try again.")
                    break
            
            # Summary for this search term
            total_added += count
            total_duplicates += duplicates
            total_errors += errors
            
            print(f"\n  Search term '{search_term}' complete: {count} added, {duplicates} duplicates, {errors} errors")
            print()
    
    # Close MongoDB connection
    client.close()
    
    # Final summary for all categories
    print()
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"Total categories processed: {len(categories)}")
    print(f"✓ Total added: {total_added} wallpapers")
    print(f"⊘ Total duplicates: {total_duplicates}")
    print(f"✗ Total errors: {total_errors}")
    print("=" * 70)
    print()
    print("Database: wallpaper-bot.wallhaven")
    print("Content: SFW + Sketchy only (NO NSFW)")
    print("Rate limit: 40 requests per minute (respected)")
    print("=" * 70)

if __name__ == "__main__":
    main()
