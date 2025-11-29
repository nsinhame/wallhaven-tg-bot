#!/usr/bin/env python3

"""
Wallhaven to MongoDB Link Uploader (NSFW + SFW)

Description: Fetches portrait wallpaper links from Wallhaven.cc
             and uploads them to MongoDB (includes NSFW content)

Usage: python up-link-db-nsfw.py [search_query] [count]
       Example: python up-link-db-nsfw.py "nature" 100

Parameters:
    search_query - Search query/category (optional, defaults to "anime")
    count - Number of wallpapers to fetch (optional, defaults to 50)

Setup:
    - MongoDB URI: Place in 'mongodb-uri.txt' or set MONGODB_URI env variable
    - API Key: Place your Wallhaven API key in 'wallhaven-api.txt'
              Get it from https://wallhaven.cc/settings/account
"""

import sys
import os
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import requests
from datetime import datetime
import time

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
        print("Error: API key is required to access NSFW content!")
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
    if len(sys.argv) > 2:
        try:
            max_count = int(sys.argv[2])
        except ValueError:
            print("Error: Count must be a number")
            sys.exit(1)
    else:
        count_input = input("How many wallpapers to fetch (default 50): ").strip()
        max_count = int(count_input) if count_input else 50
    
    # Get MongoDB URI
    mongodb_uri = get_mongodb_uri()
    
    # Connect to MongoDB
    try:
        print("Connecting to MongoDB...")
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        # Test connection
        client.admin.command('ping')
        db = client['wallpaper-bot']
        collection = db.wallpapers
        
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
    print()
    
    # Initialize counters
    count = 0  # Number of wallpapers successfully added
    duplicates = 0  # Number of duplicates skipped
    errors = 0  # Number of errors
    page = 1   # Current API page number
    api_calls = 0  # Track API calls for rate limiting
    start_time = time.time()  # Track time for rate limiting
    
    # API endpoint and parameters
    api_url = "https://wallhaven.cc/api/v1/search"
    params = {
        "q": search_query,
        "categories": "110",  # General + Anime
        "purity": "111",      # SFW + Sketchy + NSFW
        "ratios": "portrait",
        "sorting": "views",
        "order": "desc",
        "page": page,
        "apikey": api_key
    }
    
    # Main fetch loop - continues until we have the requested number of wallpapers
    while count < max_count:
        # Update page parameter
        params["page"] = page
        
        # Rate limiting: Max 45 API calls per minute
        api_calls += 1
        if api_calls > 1:
            # Calculate time elapsed and sleep if necessary
            elapsed_time = time.time() - start_time
            if elapsed_time < 60:
                # If we've made 45 calls in less than 60 seconds, wait
                if api_calls > 45:
                    sleep_time = 60 - elapsed_time + 1  # Add 1 second buffer
                    print(f"Rate limit: Waiting {sleep_time:.1f}s before next request...")
                    time.sleep(sleep_time)
                    # Reset counters
                    api_calls = 1
                    start_time = time.time()
            else:
                # More than 60 seconds passed, reset counters
                api_calls = 1
                start_time = time.time()
        
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
                # Stop if we've reached the requested number
                if count >= max_count:
                    break
                
                # Extract required data
                wallpaper_id = wallpaper.get("id", "")
                wallpaper_url = wallpaper.get("url", "")  # Page URL
                jpg_url = wallpaper.get("path", "")        # Image URL
                purity = wallpaper.get("purity", "sfw")    # sfw/sketchy/nsfw
                # Note: Tags are not available in search API, only in individual wallpaper info API
                
                if not wallpaper_url or not jpg_url:
                    errors += 1
                    continue
                
                # Prepare document for MongoDB
                # sfw field: True if purity is "sfw", False for "sketchy" or "nsfw"
                is_sfw = (purity == "sfw")
                
                # Use Unix epoch timestamp (seconds since 1970-01-01 00:00:00 UTC)
                current_timestamp = int(time.time())
                
                document = {
                    "wallpaper_id": wallpaper_id,
                    "category": query,
                    "wallpaper_url": wallpaper_url,
                    "jpg_url": jpg_url,
                    "tags": [],  # Tags not available in search API response
                    "purity": purity,  # Keep purity for detailed tracking
                    "sfw": is_sfw,  # Boolean field: True for SFW, False for NSFW/Sketchy
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
                    print(f"[{count}/{max_count}] ✓ Added: {wallpaper_id} ({purity})")
                
                except DuplicateKeyError:
                    duplicates += 1
                    print(f"[{count}/{max_count}] ⊘ Duplicate: {wallpaper_id}")
                
                except Exception as e:
                    errors += 1
                    print(f"[{count}/{max_count}] ✗ Error adding {wallpaper_id}: {e}")
            
            # Exit loop if we've successfully added the requested number
            if count >= max_count:
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
