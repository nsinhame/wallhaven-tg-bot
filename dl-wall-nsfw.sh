#!/bin/bash

if [ -z "$3" ]; then
  read -p "Enter your Wallhaven API key: " api_key
  if [ -z "$api_key" ]; then
    echo "Error: API key is required to access Sketchy content!"
    echo "Get your API key from: https://wallhaven.cc/settings/account"
    exit 1
  fi
else
  api_key="$3"
fi

if [ -z "$1" ]; then
  read -p "Search Wallhaven: " query
  query=${query:-anime}
else
  query="$1"
fi

if [ -z "$2" ]; then
  read -p "How many wallpapers to download (default 5): " max_count
  max_count=${max_count:-5}
else
  max_count="$2"
fi

query=$(echo "$query" | sed 's/#//g' | sed 's/ /+/g')

echo "Searching for: $query"
echo ""

count=0
page=1

while [ $count -lt $max_count ]; do
  response=$(curl -s "https://wallhaven.cc/api/v1/search?q=$query&categories=110&purity=110&ratios=portrait&sorting=views&order=desc&page=$page&apikey=$api_key")
  paths=$(echo "$response" | grep -o '"path":"[^"]*"' | cut -d'"' -f4 | sed 's/\\//g')
  if [ -z "$paths" ]; then
    echo "No wallpapers found."
    break
  fi
  for path in $paths; do
    if [ $count -ge $max_count ]; then
      break
    fi
    filename=$(basename "$path")
    echo "[$((count+1))/$max_count] Downloading $filename..."
    wget -q -nc "$path"
    if [ -f "$filename" ] && [ -s "$filename" ]; then
      echo "✓ Downloaded: $filename"
      ((count++))
    else
      echo "✗ Failed to download"
      [ -f "$filename" ] && rm "$filename"
    fi
  done
  if [ $count -ge $max_count ]; then
    break
  fi
  ((page++))
done

echo ""
echo "Download complete! Downloaded $count wallpapers."
