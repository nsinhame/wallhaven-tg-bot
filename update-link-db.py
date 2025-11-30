#!/usr/bin/env python3

import sys
import os
import time
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import requests

MAX_REQUESTS_PER_MINUTE = 40
api_call_times = []

def enforce_rate_limit():
    global api_call_times
    current_time = time.time()
    api_call_times = [t for t in api_call_times if current_time - t < 60]
    if len(api_call_times) >= MAX_REQUESTS_PER_MINUTE:
        oldest_call = api_call_times[0]
        wait_time = 60 - (current_time - oldest_call) + 2
        if wait_time > 0:
            print(f"⏱ Rate limit: Waiting {wait_time:.1f}s...")
            time.sleep(wait_time)
            current_time = time.time()
            api_call_times = [t for t in api_call_times if current_time - t < 60]
    api_call_times.append(time.time())

def fetch_wallpaper_tags(wallpaper_id, api_key):
    enforce_rate_limit()
    try:
        url = f"https://wallhaven.cc/api/v1/w/{wallpaper_id}"
        params = {"apikey": api_key}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        wallpaper_data = data.get("data", {})
        tags_list = wallpaper_data.get("tags", [])
        tag_names = [tag.get("name", "") for tag in tags_list if tag.get("name")]
        return tag_names
    except Exception as e:
        print(f"    ⚠ Could not fetch tags for {wallpaper_id}: {e}")
        return []

def parse_config_file():
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
            if not line or line.startswith('#'):
                continue
            if '|' not in line:
                print(f"Warning: Skipping invalid line {line_num}: {line}")
                continue
            parts = line.split('|')
            if len(parts) != 4:
                print(f"Warning: Skipping invalid line {line_num} (expected 4 parts): {line}")
                continue
            category = parts[0].strip()
            search_terms_str = parts[3].strip()
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
    if os.path.exists('mongodb-uri.txt'):
        with open('mongodb-uri.txt', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line
    uri = os.getenv('MONGODB_URI')
    if uri:
        return uri
    uri = input("Enter MongoDB URI: ").strip()
    if not uri:
        print("Error: MongoDB URI is required!")
        sys.exit(1)
    with open('mongodb-uri.txt', 'w') as f:
        f.write(uri)
    return uri

def get_wallhaven_api_key():
    if os.path.exists('wallhaven-api.txt'):
        with open('wallhaven-api.txt', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line
    api_key = input("Enter your Wallhaven API key: ").strip()
    if not api_key:
        print("Error: API key is required to access Sketchy content!")
        print("Get your API key from: https://wallhaven.cc/settings/account")
        sys.exit(1)
    with open('wallhaven-api.txt', 'w') as f:
        f.write(api_key)
    return api_key

def main():
    print("=" * 70)
    print("Wallhaven to MongoDB Link Uploader")
    print("Fetching SFW + Sketchy content (NO NSFW)")
    print("=" * 70)
    print()
    api_key = get_wallhaven_api_key()
    categories = parse_config_file()
    print(f"✓ Loaded {len(categories)} categories from config.txt")
    mongodb_uri = get_mongodb_uri()
    try:
        print("Connecting to MongoDB...")
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client['wallpaper-bot']
        collection = db.wallhaven
        collection.create_index("wallpaper_id", unique=True)
        print("✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)")
        print()
    except ConnectionFailure as e:
        print(f"Error: Failed to connect to MongoDB: {e}")
        sys.exit(1)
    total_added = 0
    total_duplicates = 0
    total_errors = 0
    for category_index, (category, search_terms) in enumerate(categories, 1):
        print("=" * 70)
        print(f"CATEGORY [{category_index}/{len(categories)}]: {category}")
        print(f"Search terms: {', '.join(search_terms)}")
        print("=" * 70)
        print()
        for term_index, search_term in enumerate(search_terms, 1):
            print(f"\n--- Processing: {category} -> {search_term} [{term_index}/{len(search_terms)}] ---\n")
            search_query = search_term.replace('#', '')
            count = 0
            duplicates = 0
            errors = 0
            page = 1
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
            while True:
                params["page"] = page
                enforce_rate_limit()
                try:
                    response = requests.get(api_url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    wallpapers = data.get("data", [])
                    if not wallpapers:
                        print("  No more wallpapers found for this search term.")
                        break
                    for wallpaper in wallpapers:
                        wallpaper_id = wallpaper.get("id", "")
                        wallpaper_url = wallpaper.get("url", "")
                        jpg_url = wallpaper.get("path", "")
                        purity = wallpaper.get("purity", "sfw")
                        if not wallpaper_url or not jpg_url:
                            errors += 1
                            continue
                        print(f"  [{count + 1}] Fetching tags for {wallpaper_id} ({purity})...")
                        tags = fetch_wallpaper_tags(wallpaper_id, api_key)
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
                            "phash": None,
                            "tg_response": {},
                            "created_at": current_timestamp
                        }
                        try:
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
                    page += 1
                except requests.exceptions.RequestException as e:
                    print(f"  Error fetching search results: {e}")
                    if "401" in str(e):
                        print("  Invalid API key. Please check your API key and try again.")
                    break
            total_added += count
            total_duplicates += duplicates
            total_errors += errors
            print(f"\n  Search term '{search_term}' complete: {count} added, {duplicates} duplicates, {errors} errors")
            print()
    client.close()
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
