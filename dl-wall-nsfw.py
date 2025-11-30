#!/usr/bin/env python3

"""
═══════════════════════════════════════════════════════════════════════════════
              WALLHAVEN STANDALONE DOWNLOADER (SFW + SKETCHY)
═══════════════════════════════════════════════════════════════════════════════

IMPORTANT NOTE: Despite the filename "nsfw", this script actually downloads
ONLY SFW + Sketchy content (purity=110), NO actual NSFW content!

Filename History:
    Originally named for SFW+Sketchy (which was called "NSFW mode" in early
    versions). Name kept for backward compatibility but content is safe.

Purpose:
    Standalone script for direct wallpaper downloads from Wallhaven.cc
    WITHOUT database integration. Requires API key for Sketchy content.

Key Differences from dl-wall-sfw.py:
    ✓ Includes Sketchy purity level (purity=110 vs 100)
    ✓ Requires API key
    ✗ No additional exclusion tags (relies on purity filter)
    
Key Differences from update-link-db.py:
    ✓ No MongoDB required
    ✓ Downloads directly to current directory
    ✓ No metadata storage
    ✓ Immediate results
    ✗ No tag fetching (uses search API only)
    ✗ No duplicate tracking across sessions

Content Policy: SFW + SKETCHY (NO NSFW)
    • Purity: 110 (SFW + Sketchy, NO actual NSFW)
    • Requires Wallhaven API key for authentication

Usage:
    Interactive mode:
        python dl-wall-nsfw.py
    
    Command-line mode:
        python dl-wall-nsfw.py "nature" 10 "your_api_key"
        python dl-wall-nsfw.py "anime" 25 "abc123def456"

Parameters:
    search_query (optional) - What to search for (default: "anime")
    count (optional)        - How many to download (default: 5)
    api_key (required)      - Your Wallhaven API key

Get API Key:
    https://wallhaven.cc/settings/account

Examples:
    python dl-wall-nsfw.py
    python dl-wall-nsfw.py "mountain" 20 "myapikey123"
    python dl-wall-nsfw.py "digital art" 15 "abc123"

Output:
    Wallpapers saved in current directory:
        wallhaven-abc123.jpg
        wallhaven-xyz789.jpg

Dependencies:
    pip install requests

═══════════════════════════════════════════════════════════════════════════════
"""

# ============================================================================
# IMPORTS
# ============================================================================

import sys              # Command-line arguments and exit codes
import os               # File operations (check existence, file size, delete)
import requests         # HTTP client for API requests and image downloads
from pathlib import Path        # Modern path handling (not actively used but available)
from urllib.parse import quote_plus  # URL encoding (not actively used but available)

# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """
    Main execution function for SFW + Sketchy downloader.
    
    Workflow:
    1. Get API key (from args or user input) - REQUIRED!
    2. Get search query (from args or user input)
    3. Get count (from args or user input)
    4. Query Wallhaven search API with authentication
    5. Download images page by page
    6. Display progress and final statistics
    
    Why API Key Required?
    • Sketchy content requires authentication
    • Without API key, Wallhaven returns only SFW results
    • API key validates your account access level
    """
    
    # ========================================================================
    # STEP 1: Get API Key (REQUIRED for Sketchy content)
    # ========================================================================
    # Priority: Command-line arg > User input > Exit (mandatory)
    # Unlike SFW version, this is NOT optional!
    
    if len(sys.argv) > 3:
        # Command-line argument provided (3rd argument)
        api_key = sys.argv[3]
    else:
        # Interactive mode - ask user
        api_key = input("Enter your Wallhaven API key: ").strip()
        if not api_key:
            # Empty input - cannot proceed without API key
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
