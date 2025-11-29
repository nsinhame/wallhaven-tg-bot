#!/usr/bin/env python3

"""
Wallhaven to MongoDB Link Uploader (SFW + Sketchy)

Description: Fetches portrait wallpaper links from Wallhaven.cc
             and uploads them to MongoDB (SFW + Sketchy content only, NO NSFW)

Usage: python up-link-db-nsfw.py [search_query] [count]
       Example: python up-link-db-nsfw.py "nature" 100

Parameters:
    search_query - Search query/category (optional, defaults to "anime")
    count - Number of wallpapers to fetch (optional, defaults to 50)

Setup:
    - MongoDB URI: Place in 'mongodb-uri.txt' or set MONGODB_URI env variable
    - API Key: Place your Wallhaven API key in 'wallhaven-api.txt'
              Get it from https://wallhaven.cc/settings/account
              
Note: Only SFW content is marked as sfw=True. Sketchy content is marked as sfw=False.
"""

import sys
import os
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import requests
from datetime import datetime
import time

# Rate limiting configuration
MAX_REQUESTS_PER_MINUTE = 45
api_call_times = []  # Track timestamps of API calls

def enforce_rate_limit():
    """Enforce rate limiting: max 45 requests per minute with safety buffer"""
    global api_call_times
    current_time = time.time()
    
    # Remove timestamps older than 60 seconds
    api_call_times = [t for t in api_call_times if current_time - t < 60]
    
    # If we've made 45+ calls in the last 60 seconds, wait
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
    # Get API key from file
    api_key = get_wallhaven_api_key()
    
    # Get search query from command line argument or prompt user
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = input("Search Wallhaven (category): ").strip()
        if not query:
            query = "anime"  # Default to "anime" if user presses Enter
    
    # Get number of wallpapers to fetch from argument or prompt user
    # If not specified, fetch ALL wallpapers
    max_count = None  # None means fetch all available
    if len(sys.argv) > 2:
        try:
            max_count = int(sys.argv[2])
        except ValueError:
            print("Error: Count must be a number")
            sys.exit(1)
    else:
        count_input = input("How many wallpapers to fetch (press Enter for ALL): ").strip()
        if count_input:
            try:
                max_count = int(count_input)
            except ValueError:
                print("Error: Count must be a number")
                sys.exit(1)
    
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
        
        print("✓ Connected to MongoDB")
        print()
    except ConnectionFailure as e:
        print(f"Error: Failed to connect to MongoDB: {e}")
        sys.exit(1)
    
    # Clean up query for URL encoding
    # Remove # symbols
    search_query = query.replace('#', '')
    
    print(f"Category: {query}")
    print(f"Searching for: {search_query}")
    if max_count:
        print(f"Target: {max_count} wallpapers")
    else:
        print(f"Target: ALL available wallpapers")
    print()
    
    # Initialize counters
    count = 0  # Number of wallpapers successfully added
    duplicates = 0  # Number of duplicates skipped
    errors = 0  # Number of errors
    page = 1   # Current API page number
    
    # API endpoint and parameters
    api_url = "https://wallhaven.cc/api/v1/search"
    params = {
        "q": search_query,
        "categories": "110",  # General + Anime
        "purity": "110",      # SFW + Sketchy (NO NSFW)
        "ratios": "portrait",
        "sorting": "views",
        "order": "desc",
        "page": page,
        "apikey": api_key
    }
    
    # Main fetch loop - continues until we have the requested number of wallpapers (or all if max_count is None)
    while max_count is None or count < max_count:
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
                print("No more wallpapers found.")
                break
            
            # Process each wallpaper from the current page
            for wallpaper in wallpapers:
                # Stop if we've reached the requested number (skip check if max_count is None)
                if max_count is not None and count >= max_count:
                    break
                
                # Extract required data
                wallpaper_id = wallpaper.get("id", "")
                wallpaper_url = wallpaper.get("url", "")  # Page URL
                jpg_url = wallpaper.get("path", "")        # Image URL
                purity = wallpaper.get("purity", "sfw")    # sfw/sketchy/nsfw
                
                if not wallpaper_url or not jpg_url:
                    errors += 1
                    continue
                
                # Fetch tags from individual wallpaper endpoint (with API key for NSFW access)
                print(f"[{count + 1}] Fetching tags for {wallpaper_id} ({purity})...")
                tags = fetch_wallpaper_tags(wallpaper_id, api_key)
                
                # Prepare document for MongoDB
                # sfw field: True ONLY if purity is "sfw", False for "sketchy"
                is_sfw = (purity == "sfw")
                
                # Use Unix epoch timestamp (seconds since 1970-01-01 00:00:00 UTC)
                current_timestamp = int(time.time())
                
                document = {
                    "wallpaper_id": wallpaper_id,
                    "category": query,
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
                    count_display = f"{count}/{max_count}" if max_count else str(count)
                    tag_info = f" ({len(tags)} tags)" if tags else " (no tags)"
                    print(f"[{count_display}] ✓ Added: {wallpaper_id} ({purity}){tag_info}")
                
                except DuplicateKeyError:
                    duplicates += 1
                    count_display = f"{count}/{max_count}" if max_count else str(count)
                    print(f"[{count_display}] ⊘ Duplicate: {wallpaper_id}")
                
                except Exception as e:
                    errors += 1
                    count_display = f"{count}/{max_count}" if max_count else str(count)
                    print(f"[{count_display}] ✗ Error adding {wallpaper_id}: {e}")
            
            # Exit loop if we've successfully added the requested number (skip check if max_count is None)
            if max_count is not None and count >= max_count:
                break
            
            # Move to next page for more results
            page += 1
        
        except requests.exceptions.RequestException as e:
            print(f"Error fetching search results: {e}")
            if "401" in str(e):
                print("Invalid API key. Please check your API key and try again.")
            break
    
    # Close MongoDB connection
    client.close()
    
    # Final summary
    print()
    print("=" * 50)
    print(f"Upload complete!")
    print(f"✓ Added: {count} wallpapers")
    print(f"⊘ Duplicates: {duplicates}")
    print(f"✗ Errors: {errors}")
    print("=" * 50)

if __name__ == "__main__":
    main()
