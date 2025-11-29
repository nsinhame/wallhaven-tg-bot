#!/bin/bash

#################################################################
# Wallhaven Wallpaper Downloader (NSFW + SFW)
# 
# Description: Downloads portrait wallpapers from Wallhaven.cc
#              including both SFW and NSFW content (requires API key)
#
# Usage: ./dl-wall-nsfw.sh [search_query] [count] [api_key]
#        Example: ./dl-wall-nsfw.sh "nature" 10 "your_api_key_here"
#
# Parameters:
#   $1 - Search query (optional, defaults to "anime")
#   $2 - Number of wallpapers to download (optional, defaults to 5)
#   $3 - Wallhaven API key (REQUIRED for NSFW content)
#
# Note: Get your API key from https://wallhaven.cc/settings/account
#################################################################

# Get API key from command line argument or prompt user
if [ -z "$3" ]; then
  read -p "Enter your Wallhaven API key: " api_key
  if [ -z "$api_key" ]; then
    echo "Error: API key is required to access NSFW content!"
    echo "Get your API key from: https://wallhaven.cc/settings/account"
    exit 1
  fi
else
  api_key="$3"
fi

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
# purity: 111
#   - First digit (1): SFW (Safe for work) - ENABLED
#   - Second digit (1): Sketchy - ENABLED
#   - Third digit (1): NSFW - ENABLED
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
  # Note: purity=111 includes SFW, Sketchy, and NSFW content
  # API key is required to access NSFW content
  response=$(curl -s "https://wallhaven.cc/api/v1/search?q=$query&categories=110&purity=111&ratios=portrait&sorting=views&order=desc&page=$page&apikey=$api_key")
  
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
