#!/bin/bash

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

exclusions="+-girl+-girls+-woman+-women+-female+-females+-lady+-ladies+-thigh+-thighs+-skirt+-skirts+-bikini+-bikinis+-leg+-legs+-cleavage+-cleavages+-chest+-chests+-breast+-breasts+-butt+-butts+-boob+-boobs+-sexy+-hot+-babe+-babes+-model+-models+-lingerie+-underwear+-panty+-panties+-bra+-bras+-swimsuit+-swimsuits+-dress+-dresses+-schoolgirl+-schoolgirls+-maid+-maids+-waifu+-waifus+-ecchi+-nude+-nudes+-naked+-nsfw+-lewd+-hentai+-ass+-asses+-booty+-booties+-sideboob+-sideboobs+-underboob+-underboobs"

query="$query$exclusions"

echo "Searching for: $query"
echo ""

count=0
page=1

while [ $count -lt $max_count ]; do
  response=$(curl -s "https://wallhaven.cc/api/v1/search?q=$query&categories=110&purity=100&ratios=portrait&sorting=views&order=desc&page=$page")
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
