#!/usr/bin/env python3

"""
Wallhaven Wallpaper Downloader (SFW)

Description: Downloads portrait wallpapers from Wallhaven.cc
             with SFW filters and exclusion tags applied

Usage: python dl-wall-sfw.py [search_query] [count]
       Example: python dl-wall-sfw.py "nature" 10

Parameters:
    search_query - Search query (optional, defaults to "anime")
    count - Number of wallpapers to download (optional, defaults to 5)
"""

import sys
import os
import requests
from pathlib import Path
from urllib.parse import quote_plus

def main():
    # Get search query from command line argument or prompt user
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = input("Search Wallhaven: ").strip()
        if not query:
            query = "anime"  # Default to "anime" if user presses Enter
    
    # Get number of wallpapers to download from argument or prompt user
    if len(sys.argv) > 2:
        try:
            max_count = int(sys.argv[2])
        except ValueError:
            print("Error: Count must be a number")
            sys.exit(1)
    else:
        count_input = input("How many wallpapers to download (default 5): ").strip()
        max_count = int(count_input) if count_input else 5
    
    # Clean up query for URL encoding
    # Remove # symbols and replace spaces with + for URL compatibility
    query = query.replace('#', '')
    
    # Add exclusion tags to filter out NSFW/inappropriate content
    # Each tag with "-" prefix means "exclude wallpapers with this tag"
    # This comprehensive list includes both singular and plural forms for maximum safety
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
    
    # Initialize counters
    count = 0  # Number of wallpapers successfully downloaded
    page = 1   # Current API page number (each page has up to 24 results)
    
    ###################################################################
    # Wallhaven API Parameters Explanation:
    #
    # categories: 110
    #   - First digit (1): General wallpapers - ENABLED
    #   - Second digit (1): Anime wallpapers - ENABLED
    #   - Third digit (0): People wallpapers - DISABLED
    #
    # purity: 100
    #   - First digit (1): SFW (Safe for work) - ENABLED
    #   - Second digit (0): Sketchy - DISABLED
    #   - Third digit (0): NSFW - DISABLED
    #
    # ratios: portrait
    #   - Only download portrait orientation wallpapers
    #
    # sorting: views
    #   - Sort results by most viewed wallpapers first
    #
    # order: desc
    #   - Descending order (highest to lowest views)
    ###################################################################
    
    # API endpoint and parameters
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
    
    # Main download loop - continues until we have the requested number of wallpapers
    while count < max_count:
        # Update page parameter
        params["page"] = page
        
        try:
            # Make API request to Wallhaven search endpoint with all filters applied
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Extract wallpaper data from response
            wallpapers = data.get("data", [])
            
            # Check if we got any results from this page
            if not wallpapers:
                print("No wallpapers found.")
                break
            
            # Download each wallpaper from the current page
            for wallpaper in wallpapers:
                # Stop if we've reached the requested number
                if count >= max_count:
                    break
                
                # Extract image URL and filename
                path = wallpaper.get("path", "")
                if not path:
                    continue
                
                filename = os.path.basename(path)
                print(f"[{count+1}/{max_count}] Downloading {filename}...")
                
                # Check if file already exists
                if os.path.exists(filename):
                    print(f"✓ File already exists: {filename}")
                    count += 1
                    continue
                
                try:
                    # Download the wallpaper
                    img_response = requests.get(path, timeout=30)
                    img_response.raise_for_status()
                    
                    # Save to file
                    with open(filename, 'wb') as f:
                        f.write(img_response.content)
                    
                    # Verify the download was successful (file has content)
                    if os.path.exists(filename) and os.path.getsize(filename) > 0:
                        print(f"✓ Downloaded: {filename}")
                        count += 1
                    else:
                        print(f"✗ Failed to download")
                        # Remove empty file if it exists
                        if os.path.exists(filename):
                            os.remove(filename)
                
                except requests.exceptions.RequestException as e:
                    print(f"✗ Failed to download: {e}")
                    # Remove any partial/failed files
                    if os.path.exists(filename):
                        os.remove(filename)
            
            # Exit loop if we've successfully downloaded the requested number
            if count >= max_count:
                break
            
            # Move to next page for more results
            page += 1
        
        except requests.exceptions.RequestException as e:
            print(f"Error fetching search results: {e}")
            break
    
    # Final summary
    print()
    print(f"Download complete! Downloaded {count} wallpapers.")

if __name__ == "__main__":
    main()
