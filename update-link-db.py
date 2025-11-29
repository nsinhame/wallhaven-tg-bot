#!/usr/bin/env python3

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

import sys
import os
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import requests
from datetime import datetime
import time

# Rate limiting configuration
MAX_REQUESTS_PER_MINUTE = 40  # Set to 40 for safety (API limit is 45)
api_call_times = []  # Track timestamps of API calls

def enforce_rate_limit():
    """Enforce rate limiting: max 40 requests per minute with safety buffer"""
    global api_call_times
    current_time = time.time()
    
    # Remove timestamps older than 60 seconds
    api_call_times = [t for t in api_call_times if current_time - t < 60]
    
    # If we've made 40+ calls in the last 60 seconds, wait
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
    """Fetch detailed wallpaper info including tags from individual wallpaper endpoint"""
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
    """Parse config.txt file and return list of (category, search_terms) tuples"""
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
    """Get MongoDB URI from file or environment variable"""
    # Try to read from file first
    if os.path.exists('mongodb-uri.txt'):
        with open('mongodb-uri.txt', 'r') as f:
            uri = f.read().strip()
            if uri:
                return uri
    
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
    """Get Wallhaven API key from file or prompt user"""
    # Try to read from file first
    if os.path.exists('wallhaven-api.txt'):
        with open('wallhaven-api.txt', 'r') as f:
            api_key = f.read().strip()
            if api_key:
                return api_key
    
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
            
            # API endpoint and parameters
            api_url = "https://wallhaven.cc/api/v1/search"
            params = {
                "q": search_query,
                "categories": "110",  # General + Anime (no People)
                "purity": "110",      # SFW + Sketchy (NO NSFW)
                "ratios": "portrait",
                "sorting": "views",
                "order": "desc",
                "page": page,
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
                        
                        # Prepare document for MongoDB
                        # sfw field: True ONLY if purity is "sfw", False for "sketchy"
                        is_sfw = (purity == "sfw")
                        
                        # Use Unix epoch timestamp (seconds since 1970-01-01 00:00:00 UTC)
                        current_timestamp = int(time.time())
                        
                        document = {
                            "wallpaper_id": wallpaper_id,
                            "category": category,  # Use category from categories.txt
                            "search_term": search_term,  # Store the search term used
                            "wallpaper_url": wallpaper_url,
                            "jpg_url": jpg_url,
                            "tags": tags,
                            "purity": purity,  # Keep purity for detailed tracking (sfw or sketchy only)
                            "sfw": is_sfw,  # Boolean field: True for SFW only, False for Sketchy
                            "status": "link_added",
                            "sha256": None,
                            "phash": None,
                            "tg_response": {},
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
