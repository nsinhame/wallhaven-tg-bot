#!/usr/bin/env python3

import sys
import os
import requests
from pathlib import Path
from urllib.parse import quote_plus

def main():
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = input("Search Wallhaven: ").strip()
        if not query:
            query = "anime"
    if len(sys.argv) > 2:
        try:
            max_count = int(sys.argv[2])
        except ValueError:
            print("Error: Count must be a number")
            sys.exit(1)
    else:
        count_input = input("How many wallpapers to download (default 5): ").strip()
        max_count = int(count_input) if count_input else 5
    query = query.replace('#', '')
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
    query = query + " " + " ".join(exclusions)
    print(f"Searching for: {query}")
    print()
    count = 0
    page = 1
    api_url = "https://wallhaven.cc/api/v1/search"
    params = {
        "q": query,
        "categories": "110",
        "purity": "100",
        "ratios": "portrait",
        "sorting": "views",
        "order": "desc",
        "page": page
    }
    while count < max_count:
        params["page"] = page
        try:
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            wallpapers = data.get("data", [])
            if not wallpapers:
                print("No wallpapers found.")
                break
            for wallpaper in wallpapers:
                if count >= max_count:
                    break
                path = wallpaper.get("path", "")
                if not path:
                    continue
                filename = os.path.basename(path)
                print(f"[{count+1}/{max_count}] Downloading {filename}...")
                if os.path.exists(filename):
                    print(f"✓ File already exists: {filename}")
                    count += 1
                    continue
                try:
                    img_response = requests.get(path, timeout=30)
                    img_response.raise_for_status()
                    with open(filename, 'wb') as f:
                        f.write(img_response.content)
                    if os.path.exists(filename) and os.path.getsize(filename) > 0:
                        print(f"✓ Downloaded: {filename}")
                        count += 1
                    else:
                        print(f"✗ Failed to download")
                        if os.path.exists(filename):
                            os.remove(filename)
                except requests.exceptions.RequestException as e:
                    print(f"✗ Failed to download: {e}")
                    if os.path.exists(filename):
                        os.remove(filename)
            if count >= max_count:
                break
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"Error fetching search results: {e}")
            break
    print()
    print(f"Download complete! Downloaded {count} wallpapers.")

if __name__ == "__main__":
    main()
