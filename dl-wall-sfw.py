#!/usr/bin/env python3

"""
═══════════════════════════════════════════════════════════════════════════════
                    WALLHAVEN STANDALONE SFW DOWNLOADER
═══════════════════════════════════════════════════════════════════════════════

Purpose:
    Standalone script for direct wallpaper downloads from Wallhaven.cc
    WITHOUT database integration. Perfect for quick downloads or testing.

Key Differences from update-link-db.py:
    ✓ No MongoDB required
    ✓ Downloads directly to current directory
    ✓ No metadata storage
    ✓ Immediate results
    ✗ No tag fetching (uses search API only)
    ✗ No duplicate tracking across sessions

Content Policy: STRICT SFW ONLY
    • Purity: 100 (SFW only, no Sketchy content)
    • Additional exclusion tags for safety
    • No API key required

Usage:
    Interactive mode:
        python dl-wall-sfw.py
    
    Command-line mode:
        python dl-wall-sfw.py "nature" 10
        python dl-wall-sfw.py "anime landscape" 25

Parameters:
    search_query (optional) - What to search for (default: "anime")
    count (optional)        - How many to download (default: 5)

Examples:
    python dl-wall-sfw.py
    python dl-wall-sfw.py "mountain"
    python dl-wall-sfw.py "digital art" 15

Output:
    Wallpapers are saved in current directory with original filenames:
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
    Main execution function for standalone SFW wallpaper downloader.
    
    Workflow:
    1. Get search query (from args or user input)
    2. Get count (from args or user input)
    3. Apply exclusion tags for content safety
    4. Query Wallhaven search API
    5. Download images page by page
    6. Display progress and final statistics
    
    No database involved - pure download script!
    """
    
    # ========================================================================
    # STEP 1: Get Search Query
    # ========================================================================
    # Priority: Command-line arg > User input > Default ("anime")
    
    if len(sys.argv) > 1:
        # Command-line argument provided
        query = sys.argv[1]
    else:
        # Interactive mode - ask user
        query = input("Search Wallhaven: ").strip()
        if not query:
            # User pressed Enter without typing - use default
            query = "anime"
    
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
    
    # ========================================================================
    # STEP 3: Prepare Query with Safety Filters
    # ========================================================================
    
    # Clean up query for URL encoding
    # Remove # symbols (hashtags break URL encoding)
    query = query.replace('#', '')
    
    # ========================================================================
    # EXCLUSION TAGS - Content Safety Layer
    # ========================================================================
    # Why exclusion tags?
    # • purity=100 (SFW) alone isn't always sufficient
    # • Some SFW wallpapers may have suggestive tags
    # • Exclusion tags provide additional safety filtering
    #
    # Each tag with "-" prefix means "exclude wallpapers with this tag"
    # Comprehensive list with singular + plural forms for maximum coverage
    #
    # Example: If a wallpaper has tags ["girl", "anime", "landscape"],
    # it will be excluded because it contains "girl" from exclusion list.
    # ========================================================================
    
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
