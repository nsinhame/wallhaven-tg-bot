#!/bin/bash

#################################################################
#          WALLHAVEN STANDALONE SFW DOWNLOADER (BASH)
#################################################################
#
# Purpose:
#   Bash version of standalone SFW wallpaper downloader.
#   No database, no Python - just bash + curl + wget!
#
# Why Bash Version?
#   • Works on systems without Python
#   • Lightweight and fast
#   • Easy to run on servers (no dependencies)
#   • Great for quick testing
#
# Key Differences from Python version:
#   ✓ Same functionality
#   ✓ Uses curl for API, wget for downloads
#   ✓ Grep/sed for JSON parsing (no jq required!)
#   ✗ Simpler error handling
#   ✗ Less detailed progress info
#
# Content Policy: STRICT SFW ONLY
#   • Purity: 100 (SFW only)
#   • Exclusion tags for safety
#   • No API key required
#
# Usage:
#   Interactive mode:
#     ./dl-wall-sfw.sh
#   
#   Command-line mode:
#     ./dl-wall-sfw.sh "nature" 10
#     ./dl-wall-sfw.sh "anime landscape" 25
#
# Parameters:
#   $1 - Search query (optional, defaults to "anime")
#   $2 - Download count (optional, defaults to 5)
#
# Examples:
#   ./dl-wall-sfw.sh
#   ./dl-wall-sfw.sh "mountain"
#   ./dl-wall-sfw.sh "digital art" 15
#
# Requirements:
#   • curl  (for API requests)
#   • wget  (for downloading images)
#   • grep  (for JSON parsing)
#   • sed   (for text processing)
#   All usually pre-installed on Linux/macOS
#
# Output:
#   Wallpapers saved in current directory:
#     wallhaven-abc123.jpg
#     wallhaven-xyz789.jpg
#
#################################################################

#################################################################
# STEP 1: Get Search Query
#################################################################
# Bash parameter expansion:
#   [ -z "$1" ]     = True if $1 is empty (no argument provided)
#   ${var:-default} = Use 'default' if var is empty
#
# Priority: Command-line arg > User input > Default

if [ -z "$1" ]; then
  # No command-line argument - interactive mode
  read -p "Search Wallhaven: " query
  query=${query:-anime}  # If user just pressed Enter, use "anime"
else
  # Command-line argument provided - use it
  query="$1"
fi

#################################################################
# STEP 2: Get Download Count
#################################################################
# Same logic as query parameter

if [ -z "$2" ]; then
  # No command-line argument - ask user
  read -p "How many wallpapers to download (default 5): " max_count
  max_count=${max_count:-5}  # If empty, use 5
else
  # Command-line argument provided
  max_count="$2"
fi

#################################################################
# STEP 3: Prepare Query with Safety Filters
#################################################################

# Clean up query for URL encoding
# sed 's/#//g'   = Remove all # symbols (they break URLs)
# sed 's/ /+/g'  = Replace spaces with + for URL encoding
query=$(echo "$query" | sed 's/#//g' | sed 's/ /+/g')

#################################################################
# EXCLUSION TAGS - Content Safety Layer
#################################################################
# Why exclusion tags in bash?
# • Same logic as Python version
# • purity=100 alone isn't always sufficient
# • Additional safety filtering for SFW-only downloads
#
# Wallhaven tag syntax:
# • "+-tagname" means "exclude wallpapers tagged with 'tagname'"
# • Multiple exclusions concatenated with +- separator
#
# Example query result:
#   "nature+-girl+-woman+-sexy"
#   = Search for "nature" but exclude anything tagged girl/woman/sexy
#
# This comprehensive list covers 50+ inappropriate tags!
#################################################################

exclusions="+-girl+-girls+-woman+-women+-female+-females+-lady+-ladies+-thigh+-thighs+-skirt+-skirts+-bikini+-bikinis+-leg+-legs+-cleavage+-cleavages+-chest+-chests+-breast+-breasts+-butt+-butts+-boob+-boobs+-sexy+-hot+-babe+-babes+-model+-models+-lingerie+-underwear+-panty+-panties+-bra+-bras+-swimsuit+-swimsuits+-dress+-dresses+-schoolgirl+-schoolgirls+-maid+-maids+-waifu+-waifus+-ecchi+-nude+-nudes+-naked+-nsfw+-lewd+-hentai+-ass+-asses+-booty+-booties+-sideboob+-sideboobs+-underboob+-underboobs"

# Append exclusions to query
query="$query$exclusions"

echo "Searching for: $query"
echo ""

# Initialize counters
count=0       # Number of wallpapers successfully downloaded
page=1        # Current API page number (each page has up to 24 results)

#################################################################
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
#################################################################

# Main download loop - continues until we have the requested number of wallpapers
while [ $count -lt $max_count ]; do
  
  # Make API request to Wallhaven search endpoint with all filters applied
  response=$(curl -s "https://wallhaven.cc/api/v1/search?q=$query&categories=110&purity=100&ratios=portrait&sorting=views&order=desc&page=$page")
  
  #################################################################
  # JSON PARSING WITHOUT JQ
  #################################################################
  # Why not use jq? To avoid external dependencies!
  # This grep/cut/sed pipeline parses JSON the old-school way.
  #
  # JSON response looks like:
  # {"data":[{"path":"https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg"}]}
  #
  # Pipeline breakdown:
  # 1. grep -o '"path":"[^"]*"'
  #    Finds all "path":"..." patterns
  #    Output: "path":"https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg"
  #
  # 2. cut -d'"' -f4
  #    Splits by " delimiter, gets 4th field (the URL)
  #    Output: https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg
  #
  # 3. sed 's/\\//g'
  #    Removes escaped forward slashes (\/ becomes /)
  #    Handles JSON escaping if present
  #################################################################
  
  paths=$(echo "$response" | grep -o '"path":"[^"]*"' | cut -d'"' -f4 | sed 's/\\//g')
  
  # Check if we got any results from this page
  if [ -z "$paths" ]; then
    echo "No wallpapers found."
    break
  fi
  
  # Download each wallpaper from the current page
  for path in $paths; do
    # Stop if we've reached the requested number
    if [ $count -ge $max_count ]; then
      break
    fi
    
    # Extract filename from the full URL path
    filename=$(basename "$path")
    echo "[$((count+1))/$max_count] Downloading $filename..."
    
    # Download using wget
    # -q: quiet mode (no progress bar)
    # -nc: no clobber (skip if file already exists)
    wget -q -nc "$path"
    
    # Verify the download was successful
    # -f checks if file exists, -s checks if file has content (not 0 bytes)
    if [ -f "$filename" ] && [ -s "$filename" ]; then
      echo "✓ Downloaded: $filename"
      ((count++))  # Increment successful download counter
    else
      echo "✗ Failed to download"
      # Remove any empty/failed files
      [ -f "$filename" ] && rm "$filename"
    fi
  done
  
  # Exit loop if we've successfully downloaded the requested number
  if [ $count -ge $max_count ]; then
    break
  fi
  
  # Move to next page for more results
  ((page++))
done

# Final summary
echo ""
echo "Download complete! Downloaded $count wallpapers."
