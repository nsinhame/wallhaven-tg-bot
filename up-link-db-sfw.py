#!/usr/bin/env python3

"""
Wallhaven to MongoDB Link Uploader (SFW)

Description: Fetches portrait wallpaper links from Wallhaven.cc
             and uploads them to MongoDB with SFW filters

Usage: python up-link-db-sfw.py

Configuration:
    - categories.txt: Define categories and search terms
      Format: category_name: search_term1, search_term2, search_term3
      Example:
        nature: tree, water, river, sky
        vehicle: car, bike, racing
        anime: anime, cartoon, digital art
    
    - mongodb-uri.txt: Your MongoDB connection string

The script processes each category line by line, performing searches for
each search term and storing results with the corresponding category.
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

def fetch_wallpaper_tags(wallpaper_id):
    """Fetch detailed wallpaper info including tags from individual wallpaper endpoint"""
    enforce_rate_limit()
    
    try:
        url = f"https://wallhaven.cc/api/v1/w/{wallpaper_id}"
        response = requests.get(url, timeout=10)
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

def parse_categories_file():
    """Parse categories.txt file and return list of (category, search_terms) tuples"""
    if not os.path.exists('categories.txt'):
        print("Error: categories.txt file not found!")
        print("Please create categories.txt with format:")
        print("  category_name: search_term1, search_term2, search_term3")
        print("Example:")
        print("  nature: tree, water, river, sky")
        print("  vehicle: car, bike, racing")
        sys.exit(1)
    
    categories = []
    with open('categories.txt', 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse line: category: term1, term2, term3
            if ':' not in line:
                print(f"Warning: Skipping invalid line {line_num}: {line}")
                continue
            
            category, terms_str = line.split(':', 1)
            category = category.strip()
            
            # Split search terms by comma and strip whitespace
            search_terms = [term.strip() for term in terms_str.split(',') if term.strip()]
            
            if not category or not search_terms:
                print(f"Warning: Skipping invalid line {line_num}: {line}")
                continue
            
            categories.append((category, search_terms))
    
    if not categories:
        print("Error: No valid categories found in categories.txt!")
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

def main():
    # Parse categories from categories.txt file
    categories = parse_categories_file()
    
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
    
    # Add exclusion tags to filter out NSFW/inappropriate content
    # Each tag with "-" prefix means "exclude wallpapers with this tag"
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
    exclusions_str = " " + " ".join(exclusions)
    
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
            search_query = search_query + exclusions_str
            
            # Initialize counters for this search term
            count = 0
            duplicates = 0
            errors = 0
            page = 1
            
            # API endpoint and parameters
            api_url = "https://wallhaven.cc/api/v1/search"
            params = {
                "q": search_query,
                "categories": "110",  # General + Anime
                "purity": "100",      # SFW only
                "ratios": "portrait",
                "sorting": "views",
                "order": "desc",
                "page": page
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
                print("No more wallpapers found.")
                break
            
                # Process each wallpaper from the current page
                for wallpaper in wallpapers:
                    # Extract required data
                    wallpaper_id = wallpaper.get("id", "")
                    wallpaper_url = wallpaper.get("url", "")  # Page URL
                    jpg_url = wallpaper.get("path", "")        # Image URL
                    
                    if not wallpaper_url or not jpg_url:
                        errors += 1
                        continue
                    
                    # Fetch tags from individual wallpaper endpoint
                    print(f"  [{count + 1}] Fetching tags for {wallpaper_id}...")
                    tags = fetch_wallpaper_tags(wallpaper_id)
                    
                    # Prepare document for MongoDB
                    # Use Unix epoch timestamp (seconds since 1970-01-01 00:00:00 UTC)
                    current_timestamp = int(time.time())
                    
                    document = {
                        "wallpaper_id": wallpaper_id,
                        "category": category,  # Use category from categories.txt
                        "search_term": search_term,  # Store the search term used
                        "wallpaper_url": wallpaper_url,
                        "jpg_url": jpg_url,
                        "tags": tags,
                        "sfw": True,  # SFW script only fetches safe content
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
                        print(f"  [{count}] ✓ Added: {wallpaper_id}{tag_info}")
                    
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

if __name__ == "__main__":
    main()
