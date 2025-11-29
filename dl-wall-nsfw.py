#!/usr/bin/env python3

"""
Wallhaven Wallpaper Downloader (SFW + Sketchy)

Description: Downloads portrait wallpapers from Wallhaven.cc
             including SFW and Sketchy content (NO NSFW)

Usage: python dl-wall-nsfw.py [search_query] [count] [api_key]
       Example: python dl-wall-nsfw.py "nature" 10 "your_api_key_here"

Parameters:
    search_query - Search query (optional, defaults to "anime")
    count - Number of wallpapers to download (optional, defaults to 5)
    api_key - Wallhaven API key (required for Sketchy content)

Note: Get your API key from https://wallhaven.cc/settings/account
"""

import sys
import os
import requests
from pathlib import Path
from urllib.parse import quote_plus

def main():
    # Get API key from command line argument or prompt user
    if len(sys.argv) > 3:
        api_key = sys.argv[3]
    else:
        api_key = input("Enter your Wallhaven API key: ").strip()
        if not api_key:
            print("Error: API key is required to access Sketchy content!")
            print("Get your API key from: https://wallhaven.cc/settings/account")
            sys.exit(1)
    
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
    # purity: 110
    #   - First digit (1): SFW (Safe for work) - ENABLED
    #   - Second digit (1): Sketchy - ENABLED
    #   - Third digit (0): NSFW - DISABLED (NO NSFW CONTENT)
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
        "purity": "110",
        "ratios": "portrait",
        "sorting": "views",
        "order": "desc",
        "page": page,
        "apikey": api_key
    }
    
    # Main download loop - continues until we have the requested number of wallpapers
    while count < max_count:
        # Update page parameter
        params["page"] = page
        
        try:
            # Make API request to Wallhaven search endpoint with all filters applied
            # Note: purity=110 includes SFW and Sketchy only (NO NSFW)
            # API key is required to access Sketchy content
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
            if "401" in str(e):
                print("Invalid API key. Please check your API key and try again.")
            break
    
    # Final summary
    print()
    print(f"Download complete! Downloaded {count} wallpapers.")

if __name__ == "__main__":
    main()
