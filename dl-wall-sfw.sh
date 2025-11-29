#!/bin/bash

#################################################################
# Wallhaven Wallpaper Downloader
# 
# Description: Downloads portrait wallpapers from Wallhaven.cc
#              with SFW filters and exclusion tags applied
#
# Usage: ./download_wallpapers.sh [search_query] [count]
#        Example: ./download_wallpapers.sh "nature" 10
#
# Parameters:
#   $1 - Search query (optional, defaults to "anime")
#   $2 - Number of wallpapers to download (optional, defaults to 5)
#################################################################

# Get search query from command line argument or prompt user
if [ -z "$1" ]; then
  read -p "Search Wallhaven: " query
  query=${query:-anime}  # Default to "anime" if user presses Enter
else
  query="$1"
fi

# Get number of wallpapers to download from argument or prompt user
if [ -z "$2" ]; then
  read -p "How many wallpapers to download (default 5): " max_count
  max_count=${max_count:-5}  # Default to 5 if user presses Enter
else
  max_count="$2"
fi

# Clean up query for URL encoding
# Remove # symbols and replace spaces with + for URL compatibility
query=$(echo "$query" | sed 's/#//g' | sed 's/ /+/g')

# Add exclusion tags to filter out NSFW/inappropriate content
# Each tag with "+-" prefix means "exclude wallpapers with this tag"
# This comprehensive list includes both singular and plural forms for maximum safety
exclusions="+-girl+-girls+-woman+-women+-female+-females+-lady+-ladies+-thigh+-thighs+-skirt+-skirts+-bikini+-bikinis+-leg+-legs+-cleavage+-cleavages+-chest+-chests+-breast+-breasts+-butt+-butts+-boob+-boobs+-sexy+-hot+-babe+-babes+-model+-models+-lingerie+-underwear+-panty+-panties+-bra+-bras+-swimsuit+-swimsuits+-dress+-dresses+-schoolgirl+-schoolgirls+-maid+-maids+-waifu+-waifus+-ecchi+-nude+-nudes+-naked+-nsfw+-lewd+-hentai+-ass+-asses+-booty+-booties+-sideboob+-sideboobs+-underboob+-underboobs"
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
  
  # Extract image URLs from JSON response
  # 1. grep finds all "path":"url" pairs
  # 2. cut extracts just the URL part
  # 3. sed removes escaped forward slashes (\/ becomes /)
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
