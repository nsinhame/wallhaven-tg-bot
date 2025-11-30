# 🖼️ Wallhaven Telegram Bot

**Automated wallpaper fetching and Telegram posting system with intelligent duplicate detection**

**Note: Most of the code and documentation in this project was generated with AI assistance.**

A sophisticated Python-based automation suite that fetches high-quality portrait wallpapers from Wallhaven.cc, stores metadata in MongoDB, and posts them to category-specific Telegram groups with SHA256 and perceptual hash duplicate detection.

---

## 📋 Table of Contents

- [Features](#-features)
- [System Architecture](#-system-architecture)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [Database Schema](#-database-schema)
- [Project Structure](#-project-structure)
- [Rate Limiting](#-rate-limiting)
- [Duplicate Detection](#-duplicate-detection)
- [Content Policy](#-content-policy)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### Core Functionality
- 🔄 **Automated Wallpaper Fetching** - Bulk download from Wallhaven API with category-based organization
- 📱 **Telegram Bot Integration** - Automated posting to multiple Telegram groups
- 🎲 **Random Selection** - Posts random wallpapers from database for natural variety
- ⏰ **Custom Scheduling** - Per-category posting intervals (e.g., nature every 50 min, anime every 16 min)
- 🔍 **Smart Duplicate Detection** - SHA256 (exact) + pHash (similarity) for comprehensive duplicate prevention
- 🏷️ **Tag Management** - Automatic tag fetching and storage for content classification
- 🚦 **Rate Limiting** - Respects Wallhaven API limits (40 requests/min with safety buffer)

### Advanced Features
- 📊 **Database Status Tracking** - Complete workflow monitoring (link_added → posted/failed/skipped)
- 🖼️ **Dual Upload Mode** - Preview (photo) + HD version (document) on Telegram
- 🛡️ **Graceful Shutdown** - Ensures active tasks complete before exit (safe for long-running deployments)
- 📈 **Comprehensive Statistics** - Real-time tracking of added/duplicate/failed wallpapers
- 🔐 **Unique Indexing** - MongoDB unique index prevents database-level duplicates

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        SYSTEM WORKFLOW                          │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│   Wallhaven API      │
│   (wallhaven.cc)     │
└──────────┬───────────┘
           │ Search & fetch wallpaper metadata
           │ (with tags, purity, URLs)
           ▼
┌──────────────────────┐
│ update-link-db.py    │◄──── config.txt (categories & terms)
│                      │◄──── wallhaven-api.txt
│ • Fetches metadata   │
│ • Parses categories  │
│ • Respects rate limit│
│ • Stores in MongoDB  │
└──────────┬───────────┘
           │ Stores documents with status="link_added"
           ▼
┌──────────────────────────────────────────────────────────┐
│                     MongoDB Database                     │
│  Database: wallpaper-bot  |  Collection: wallhaven       │
│                                                           │
│  Documents contain:                                       │
│  • wallpaper_id, category, search_term                   │
│  • jpg_url, tags, purity, sfw                            │
│  • status: "link_added" / "posted" / "failed" / "skipped"│
│  • sha256, phash (filled by bot)                         │
│  • tg_response (Telegram upload details)                 │
└──────────┬───────────────────────────────────────────────┘
           │ Random selection by category
           ▼
┌──────────────────────┐
│   tg-up-bot.py       │◄──── config.txt (groups & intervals)
│                      │◄──── tg-config.txt (Telegram creds)
│ • Scheduled jobs     │
│ • Random selection   │
│ • Download image     │
│ • Calculate hashes   │
│ • Check duplicates   │
│ • Upload to Telegram │
│ • Update status      │
└──────────┬───────────┘
           │ Posts wallpapers
           ▼
┌──────────────────────────────────────┐
│      Telegram Groups (by category)   │
│                                       │
│  • Nature Group  (-1002996780898)    │
│  • Anime Group   (-1002935599065)    │
│  • Vehicle Group (-1002123456789)    │
│  • ... and more                       │
└───────────────────────────────────────┘
```

---

## 📦 Prerequisites

### Software Requirements
- **Python 3.8+** (tested on 3.11)
- **MongoDB 4.0+** (local or Atlas)
- **Wallhaven Account** with API key
- **Telegram Bot** (via @BotFather)

### Python Packages
```bash
# For update-link-db.py
pip install pymongo requests

# For tg-up-bot.py
pip install telethon httpx Pillow imagehash pymongo APScheduler
```

---

## 🚀 Installation

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/wallhaven-telegram-bot.git
cd wallhaven-telegram-bot
```

### 2. Install Dependencies
```bash
pip install pymongo requests telethon httpx Pillow imagehash APScheduler
```

### 3. Set Up MongoDB
**Option A: Local MongoDB**
```bash
# Install MongoDB (varies by OS)
# Ubuntu/Debian:
sudo apt-get install mongodb

# macOS (Homebrew):
brew install mongodb-community

# Windows: Download from mongodb.com
```

**Option B: MongoDB Atlas (Cloud)**
1. Create free account at [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas)
2. Create cluster
3. Get connection string

### 4. Create Telegram Bot
1. Open Telegram, search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow instructions
3. Save the **bot token**
4. Get **API_ID** and **API_HASH** from [my.telegram.org/apps](https://my.telegram.org/apps)

### 5. Get Wallhaven API Key
1. Log in to [wallhaven.cc](https://wallhaven.cc)
2. Go to Settings → Account
3. Copy your API key

---

## ⚙️ Configuration

### 🎯 Important: Comment Handling in Config Files

**All configuration files support inline comments!**

✅ You can keep instruction comments in files - scripts automatically skip lines starting with `#`  
✅ No need to manually remove comments before running  
✅ Makes config files self-documenting

Example (`mongodb-uri.txt`):
```txt
# MongoDB Connection URI
# Instructions: Replace with your URI
mongodb://localhost:27017
```
The script will automatically extract `mongodb://localhost:27017` and ignore comment lines.

---

### File 1: `config.txt` (Main Configuration)
**Used by both `update-link-db.py` and `tg-up-bot.py`**

Format: `category | group_id | interval_seconds | search_term1, search_term2`

```txt
# Example configuration
nature | -1002996780898 | 3050 | tree, water, river, mountain, forest, sunset
anime | -1002935599065 | 1000 | anime, cartoon, manga, digital art
vehicle | -1002123456789 | 1200 | car, bike, motorcycle, racing
technology | -1002234567890 | 900 | computer, laptop, smartphone, robot
```

**Fields Explained:**
- **category**: Name of the category (used for database organization)
- **group_id**: Telegram group ID (get with @userinfobot or @getidsbot)
- **interval_seconds**: Posting interval for this category (e.g., 3050 = ~50 minutes)
- **search_terms**: Comma-separated search terms for Wallhaven API

**Important:** 
- `update-link-db.py` uses: category + search_terms
- `tg-up-bot.py` uses: category + group_id + interval

---

### File 2: `tg-config.txt` (Telegram Credentials)
**Used by `tg-up-bot.py` only**

```txt
API_ID=your_api_id_here
API_HASH=your_api_hash_here
BOT_TOKEN=your_bot_token_here
```

Get these from:
- **API_ID & API_HASH**: [my.telegram.org/apps](https://my.telegram.org/apps)
- **BOT_TOKEN**: [@BotFather](https://t.me/BotFather) on Telegram

---

### File 3: `mongodb-uri.txt` (Database Connection)
**Used by both scripts**

```txt
# Local MongoDB (no authentication)
mongodb://localhost:27017

# Local MongoDB (with authentication)
mongodb://username:password@localhost:27017

# MongoDB Atlas (cloud)
mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

---

### File 4: `wallhaven-api.txt` (API Key)
**Used by `update-link-db.py` only**

```txt
your_wallhaven_api_key_here
```

Get from: [wallhaven.cc/settings/account](https://wallhaven.cc/settings/account)

---

## 🎯 Usage

### Step 1: Populate Database
Run `update-link-db.py` to fetch wallpaper metadata from Wallhaven:

```bash
python update-link-db.py
```

**What it does:**
1. Reads `config.txt` for categories and search terms
2. Queries Wallhaven API with filters:
   - Portrait orientation
   - Purity: 110 (SFW + Sketchy, NO NSFW)
   - Categories: 110 (General + Anime)
   - Sorted by views (most popular first)
3. Fetches tags for each wallpaper
4. Stores in MongoDB with status=`"link_added"`
5. Displays statistics

**Output Example:**
```
======================================================================
Wallhaven to MongoDB Link Uploader
Fetching SFW + Sketchy content (NO NSFW)
======================================================================

✓ Loaded 7 categories from config.txt
Connecting to MongoDB...
✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)

======================================================================
CATEGORY [1/7]: nature
Search terms: tree, water, river, mountain, forest, sunset
======================================================================

--- Processing: nature -> tree [1/6] ---

  [1] Fetching tags for 94x38z (sfw)...
  [1] ✓ Added: 94x38z (sfw) (5 tags)
  ...
  
Search term 'tree' complete: 120 added, 15 duplicates, 0 errors
...
```

**Safe for long-running:**
- Rate limit: 40 requests/minute (respects API limits)
- Can run for 4-5 days on server without issues
- Graceful error handling (continues on network errors)

---

### Step 2: Start Telegram Bot
Run `tg-up-bot.py` to start automated posting:

```bash
python tg-up-bot.py
```

**What it does:**
1. Loads configuration (MongoDB, Telegram, categories)
2. Connects to MongoDB and Telegram
3. Schedules independent jobs for each category
4. Every interval (per category):
   - Fetches ONE random pending wallpaper (status=`"link_added"`)
   - Downloads image temporarily
   - Calculates SHA256 hash (exact duplicate detection)
   - Calculates pHash (similar image detection)
   - Checks database for duplicates
   - If duplicate: marks as `"skipped"`, deletes file
   - If unique: uploads to Telegram (preview + HD), marks as `"posted"`
5. Continues until Ctrl+C (graceful shutdown)

**Output Example:**
```
======================================================================
Telegram Wallpaper Upload Bot Starting...
======================================================================
✓ Loaded 7 category configurations
  - nature: Group -1002996780898, Every 3050s (50min)
  - anime: Group -1002935599065, Every 1000s (16min)
  - vehicle: Group -1002123456789, Every 1200s (20min)
  ...
✓ Connected to MongoDB (database: wallpaper-bot, collection: wallhaven)
✓ Telegram bot connected
✓ Scheduled job for 'nature' (every 3050s / 50min)
✓ Scheduled job for 'anime' (every 1000s / 16min)
...
======================================================================
Bot is running. Press Ctrl+C to stop.
======================================================================

[nature] Processing 94x38z...
[nature] ✓ Posted 94x38z to group -1002996780898
[anime] Processing abc123...
[anime] ⚠ Skipping xyz789: Similar - pHash_match (96.88% similar)
...
```

**Graceful Shutdown:**
Press `Ctrl+C`:
```
Shutdown requested. Waiting for ongoing tasks to complete...
Waiting for 2 active tasks to finish...
======================================================================
Bot stopped gracefully. All tasks completed.
======================================================================
```

---

## 🗄️ Database Schema

**Database:** `wallpaper-bot`  
**Collection:** `wallhaven`

### Document Structure
```javascript
{
  // Unique Wallhaven ID (indexed, prevents duplicates)
  "wallpaper_id": "94x38z",
  
  // Category from config.txt
  "category": "nature",
  
  // Search term that found this wallpaper
  "search_term": "mountain",
  
  // Wallhaven page URL
  "wallpaper_url": "https://wallhaven.cc/w/94x38z",
  
  // Direct image download URL
  "jpg_url": "https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg",
  
  // Array of tag strings from Wallhaven
  "tags": ["landscape", "mountain", "snow", "nature"],
  
  // Purity level: "sfw" or "sketchy" (never "nsfw")
  "purity": "sfw",
  
  // Boolean for quick SFW filtering
  "sfw": true,  // true = SFW, false = Sketchy
  
  // Processing status
  "status": "posted",  // link_added | posted | failed | skipped
  
  // SHA256 hash (filled by tg-up-bot.py)
  "sha256": "a1b2c3d4e5f6...",
  
  // Perceptual hash (filled by tg-up-bot.py)
  "phash": "ff00aa5544bb...",
  
  // Telegram upload response
  "tg_response": {
    "preview": {
      "message_id": 12345,
      "date": "2025-11-30T10:30:00"
    },
    "hd": {
      "message_id": 12346,
      "date": "2025-11-30T10:30:03"
    },
    "group_id": -1002996780898,
    "uploaded_at": "2025-11-30T10:30:05"
  },
  
  // Unix epoch timestamp (seconds since 1970-01-01)
  "created_at": 1701345600
}
```

### Status Flow
```
link_added (by update-link-db.py)
    ↓
    ├─→ posted   (successful Telegram upload)
    ├─→ failed   (download/upload error)
    └─→ skipped  (duplicate detected via SHA256 or pHash)
```

---

## 📁 Project Structure

```
wallhaven-telegram-bot/
│
├── 📄 config.txt              # Main configuration (categories, groups, intervals, terms)
├── 📄 tg-config.txt           # Telegram bot credentials (API_ID, API_HASH, BOT_TOKEN)
├── 📄 mongodb-uri.txt         # MongoDB connection string
├── 📄 wallhaven-api.txt       # Wallhaven API key
│
├── 🐍 update-link-db.py       # Fetches wallpapers from Wallhaven → MongoDB
├── 🤖 tg-up-bot.py            # Posts wallpapers from MongoDB → Telegram
│
├── 📜 dl-wall-sfw.py          # Standalone SFW downloader (no database)
├── 📜 dl-wall-nsfw.py         # Standalone SFW+Sketchy downloader (no database)
├── 📜 dl-wall-sfw.sh          # Bash version of SFW downloader
├── 📜 dl-wall-nsfw.sh         # Bash version of SFW+Sketchy downloader
│
├── 📋 api-instructions.md     # Wallhaven API documentation
└── 📖 README.md               # This file
```

### Core Scripts

#### `update-link-db.py`
**Purpose:** Fetch wallpaper metadata from Wallhaven and store in MongoDB

**Features:**
- Multi-category processing
- Multiple search terms per category
- Pagination (fetches all pages)
- Rate limiting (40/min)
- Tag fetching
- Duplicate prevention
- Unix epoch timestamps

**Use case:** Run periodically (cron job) to populate database with new wallpapers

---

#### `tg-up-bot.py`
**Purpose:** Automated Telegram posting with duplicate detection

**Features:**
- Random wallpaper selection
- Custom intervals per category
- SHA256 + pHash duplicate detection
- Two-stage upload (preview + HD)
- Database status tracking
- Graceful shutdown
- Independent category scheduling

**Use case:** Long-running bot (systemd service, tmux, screen) for continuous posting

---

### Standalone Downloaders
These scripts download wallpapers directly without database:

- **`dl-wall-sfw.py`** - Python, SFW only, with exclusion tags
- **`dl-wall-nsfw.py`** - Python, SFW + Sketchy (requires API key)
- **`dl-wall-sfw.sh`** - Bash version of SFW downloader
- **`dl-wall-nsfw.sh`** - Bash version of SFW+Sketchy downloader

**Usage:**
```bash
# Python versions
python dl-wall-sfw.py "nature" 10
python dl-wall-nsfw.py "anime" 20 "your_api_key"

# Bash versions
./dl-wall-sfw.sh "landscape" 15
./dl-wall-nsfw.sh "digital art" 25 "your_api_key"
```

---

## 🚦 Rate Limiting

### Wallhaven API Limits
- **Official limit:** 45 requests per minute
- **Our limit:** 40 requests per minute (safety buffer)

### Implementation (Sliding Window Algorithm)
```python
# Track API call timestamps in rolling window
api_call_times = []  # [timestamp1, timestamp2, ...]

def enforce_rate_limit():
    # Remove timestamps older than 60 seconds
    # If 40+ calls in last 60 seconds, calculate wait time
    # Sleep with 2-second safety buffer
    # Record current call timestamp
```

### Why Conservative Limit?
- **Clock skew protection** - Server/client time differences
- **Network latency** - Round-trip time variations
- **Long-running safety** - 4-5 day deployments without hitting limit
- **Multiple script instances** - If running scripts separately

### Rate Limit Applied To:
- `update-link-db.py`: Search API + Individual wallpaper endpoints
- Combined tracking across all API calls

---

## 🔍 Duplicate Detection

### Two-Tier System

#### Tier 1: SHA256 (Exact Match)
**Algorithm:** Cryptographic hash of file contents

**Characteristics:**
- Identical files = identical SHA256
- Works even if file is renamed
- O(1) lookup with database index
- 100% accuracy for exact duplicates

**Use case:** Catch re-uploads of identical files

**Implementation:**
```python
sha256_hash = hashlib.sha256()
with open(filepath, "rb") as f:
    for block in iter(lambda: f.read(4096), b""):
        sha256_hash.update(block)
sha256 = sha256_hash.hexdigest()
```

---

#### Tier 2: pHash (Perceptual Hash)
**Algorithm:** Average hash based on visual content

**Characteristics:**
- Similar images have similar hashes
- Resistant to resizing, compression, slight edits
- Hamming distance measures similarity
- O(n) comparison with all existing hashes

**Threshold:** 5 (configurable via `SIMILARITY_THRESHOLD`)
- Range: 0 (identical) to 64 (completely different)
- Distance < 5 = considered duplicate
- Distance 0-3: Very similar (resize/compress)
- Distance 4-7: Similar (minor edits)
- Distance 8+: Different images

**Use case:** Catch similar but not identical images

**Implementation:**
```python
from PIL import Image
import imagehash

p_hash = str(imagehash.average_hash(Image.open(filepath)))
```

**Comparison:**
```python
new_hash = imagehash.hex_to_hash(p_hash)
db_hash = imagehash.hex_to_hash(existing_phash)
hash_diff = new_hash - db_hash  # Hamming distance

if hash_diff < 5:
    # Similar image detected
    similarity_percentage = ((64 - hash_diff) / 64) * 100
```

---

### Why Both Methods?

| Method | Exact Duplicates | Resized/Compressed | Edited | False Positives |
|--------|------------------|---------------------|--------|-----------------|
| **SHA256 only** | ✅ Perfect | ❌ Misses | ❌ Misses | ✅ None |
| **pHash only** | ✅ Good | ✅ Catches | ✅ Catches | ⚠️ Possible |
| **Combined** | ✅ Perfect | ✅ Catches | ✅ Catches | ✅ Minimal |

**Decision Flow:**
```
Download image
    ↓
Calculate SHA256
    ↓
Check database for exact SHA256 match
    ↓
┌──YES──→ Mark as duplicate (exact)
│            ↓
│         Skip upload
│
NO → Calculate pHash
    ↓
Compare with all existing pHashes
    ↓
┌──Distance < 5──→ Mark as similar
│                      ↓
│                  Skip upload
│
NO → Unique image
    ↓
Upload to Telegram
    ↓
Store SHA256 + pHash in database
```

---

## 🔒 Content Policy

### Strict SFW + Sketchy Policy

**Configuration:** `purity = "110"`

**Digit Explanation:**
- **1st digit (1):** SFW (Safe for Work) - **ENABLED**
- **2nd digit (1):** Sketchy (questionable but not explicit) - **ENABLED**
- **3rd digit (0):** NSFW (Not Safe for Work) - **DISABLED**

**Result:** Only SFW and Sketchy content, **NO NSFW**

### Additional Filters

**Category Filter:** `categories = "110"`
- **1st digit (1):** General - **ENABLED**
- **2nd digit (1):** Anime - **ENABLED**
- **3rd digit (0):** People - **DISABLED**

**Orientation Filter:** `ratios = "portrait"`
- Only portrait orientation (height > width)
- Optimized for mobile wallpapers

### Database Boolean
```python
# Quick SFW filtering
sfw = (purity == "sfw")  # True if completely safe

# Query examples
db.find({"sfw": true})      # Only SFW wallpapers
db.find({"purity": "sfw"})  # Same result
db.find({"sfw": false})     # Only Sketchy wallpapers
```

---

## 🛠️ Troubleshooting

### Common Issues

#### 1. MongoDB Connection Failed
**Error:** `ConnectionFailure: [Errno 111] Connection refused`

**Solutions:**
- Check MongoDB is running: `sudo systemctl status mongod`
- Start MongoDB: `sudo systemctl start mongod`
- Verify URI in `mongodb-uri.txt`
- For Atlas: Check firewall, whitelist IP

---

#### 2. Telegram API Errors
**Error:** `401 Unauthorized` or `ApiIdInvalidError`

**Solutions:**
- Verify credentials in `tg-config.txt`
- Ensure API_ID is integer (no quotes)
- Check BOT_TOKEN format: `1234567890:ABC123...`
- Delete `wallpaper_bot_session.session` file and restart

---

#### 3. Rate Limit Exceeded
**Error:** `429 Too Many Requests`

**Solutions:**
- Wait 60 seconds
- Check `MAX_REQUESTS_PER_MINUTE` is set to 40
- Ensure only one instance of script running
- Verify rate limiting is working (check console logs)

---

#### 4. No Wallpapers Found
**Error:** `No pending wallpapers found for category 'nature'`

**Solutions:**
- Run `update-link-db.py` first to populate database
- Check database: `db.wallhaven.count({status: "link_added"})`
- Verify category name matches exactly (case-sensitive)
- Check `config.txt` for typos

---

#### 5. Import Errors
**Error:** `ModuleNotFoundError: No module named 'telethon'`

**Solutions:**
```bash
# Install missing packages
pip install telethon httpx Pillow imagehash pymongo APScheduler requests

# Verify installation
python -c "import telethon; import imagehash; import pymongo"

# If using virtual environment, activate it first
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

---

#### 6. Duplicate Detection Not Working
**Symptoms:** Same wallpaper posted multiple times

**Solutions:**
- Check if hashes are being calculated:
  ```bash
  db.wallhaven.find({sha256: {$ne: null}}).count()
  ```
- Verify `SIMILARITY_THRESHOLD` value (default: 5)
- Check logs for hash calculation errors
- Ensure `imagehash` library is installed: `pip install imagehash`

---

## 🤝 Contributing

We welcome contributions! Here's how:

### Reporting Bugs
1. Check existing issues first
2. Create new issue with:
   - Descriptive title
   - Steps to reproduce
   - Expected vs actual behavior
   - Python version, OS, package versions
   - Relevant logs (remove sensitive data)

### Feature Requests
1. Search existing requests
2. Create issue with:
   - Clear description
   - Use case
   - Proposed implementation (optional)

### Pull Requests
1. Fork repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Make changes with clear commits
4. Add/update tests if applicable
5. Update documentation
6. Submit PR with description

### Code Style
- Follow PEP 8
- Use descriptive variable names
- Add comments for complex logic
- Keep functions focused and small

---

## 📝 License

This project is licensed under the **MIT License**.

```
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 🙏 Acknowledgments

- **Wallhaven.cc** - Excellent wallpaper platform and API
- **Telethon** - Powerful Python Telegram library
- **ImageHash** - Perceptual hashing library
- **MongoDB** - Flexible NoSQL database
- **APScheduler** - Advanced Python scheduling

---

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/yourusername/wallhaven-telegram-bot/issues)
- **Discussions:** [GitHub Discussions](https://github.com/yourusername/wallhaven-telegram-bot/discussions)
- **Email:** your.email@example.com

---

## 🗺️ Roadmap

### Planned Features
- [ ] Web dashboard for monitoring
- [ ] User voting system for wallpapers
- [ ] Advanced filtering (resolution, color)
- [ ] Multi-language support
- [ ] Docker containerization
- [ ] Automated deployment scripts
- [ ] Backup and restore utilities
- [ ] Performance metrics and analytics

---

## 📚 Additional Resources

- [Wallhaven API Documentation](https://wallhaven.cc/help/api)
- [Telethon Documentation](https://docs.telethon.dev/)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [ImageHash Documentation](https://github.com/JohannesBuchner/imagehash)

---

**Made with ❤️ for the wallpaper community**

*Last updated: November 30, 2025*
