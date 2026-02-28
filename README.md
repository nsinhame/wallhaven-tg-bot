# 🤖 Wallhaven Telegram Bot

**Single unified bot that intelligently fetches wallpapers and posts them to Telegram**

## 🌐 Deployment Options

- **☁️ Cloud Deployment (Koyeb)**: [See detailed guide →](KOYEB_DEPLOYMENT.md) - Deploy in 5 minutes!
- **💻 Local/VPS Deployment**: Follow the Quick Start guide below

---

## ✨ What's New - Combined Script

The bot now runs **both operations simultaneously**:

### 🔄 **Intelligent Fetcher** (Background Task)
- Continuously fetches wallpapers from Wallhaven
- Progressive fetching strategy:
  - **Round 1:** 100 wallpapers per search term
  - **Round 2:** 200 wallpapers per search term
  - **Round 3:** 300, then 400, 500, etc.
  - **Smart pagination:** At 800+, skips already-fetched top results
- Processes categories → search terms sequentially
- Respects API rate limits (40 req/min)
- Automatic duplicate prevention

### 📤 **Smart Poster** (Scheduled Task)
- Only activates when wallpapers are available
- Posts on your custom intervals
- Random selection (3 wallpapers per batch)
- SHA256 duplicate detection
- Dual upload: Preview + HD version

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
sudo apt install imagemagick  # For thumbnails
```

### 2. Configure Environment
Copy and edit `.env.example`:
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run the Bot
```bash
python wallhaven-bot.py
```

That's it! The bot will:
1. ✅ Start fetching wallpapers in background
2. ✅ Automatically post to Telegram on your schedule
3. ✅ Continue until you stop it (Ctrl+C)

---

## 📊 How It Works

### Intelligent Progressive Fetching

The bot uses a **smart round-based system**:

```
ROUND 1: Fetch 100 wallpapers per search term (top viewed)
  ├─ nature → tree (100 wallpapers)
  ├─ nature → water (100 wallpapers)
  ├─ nature → mountain (100 wallpapers)
  └─ ... all categories/terms

ROUND 2: Fetch 200 wallpapers per search term
  ├─ nature → tree (200 total, skip duplicates)
  └─ ... all categories/terms

ROUND 3-7: 300, 400, 500, 600, 700 wallpapers

ROUND 8+: Smart skip strategy
  ├─ 800 wallpapers → Skip top 300 (they're likely seen)
  ├─ 900 wallpapers → Skip top 400
  ├─ 1000 wallpapers → Skip top 500
  └─ Keeps fetching fresh content!
```

**Why this approach?**
- ✅ Gets most popular wallpapers first
- ✅ Gradually expands to less popular ones
- ✅ Skips redundant API calls for already-fetched content
- ✅ Continuous fresh content as new wallpapers get views

---

## 📁 Database Collections

The bot uses 2 Firebase Firestore collections:

### 1. `wallhaven` (Wallpaper Storage)
```javascript
{
  wallpaper_id: "abc123",  // Document ID
  category: "nature",
  search_term: "mountain",
  jpg_url: "https://...",
  tags: ["landscape", "snow"],
  status: "link_added",  // → posted/failed/skipped
  sha256: "hash...",
  tg_response: {...}
}
```

### 2. `fetch_state` (Round Tracking)
```javascript
{
  // Document ID: category_search_term (e.g., "nature_tree")
  category: "nature",
  search_term: "tree",
  round: 3,              // Current round
  target_count: 300,     // Target wallpapers for this round
  skip_count: 0,         // Skip top N results
  last_updated: 1707741234  // Epoch timestamp (integer)
}
```

---

## 🎛️ Configuration (.env)

### Firebase Setup

1. **Create Firebase Project**: Go to [Firebase Console](https://console.firebase.google.com/)
2. **Enable Firestore**: Create a Firestore database
3. **Generate Service Account Key**:
   - Go to Project Settings → Service Accounts
   - Click "Generate New Private Key"
   - Save as `serviceAccountKey.json` in your project folder

### Environment Variables

```env
# Firebase Credentials
FIREBASE_CREDENTIALS=./serviceAccountKey.json

# API Keys
WALLHAVEN_API_KEY=your_api_key
TELEGRAM_BOT_TOKEN=your_bot_token

# Categories (sequential numbering)
CATEGORY_1=nature|-1002996780898|3050|tree,water,river,sky,mountain
CATEGORY_2=anime|-1002935599065|1000|anime,cartoon,manga
# Format: name|group_id|interval_seconds|search_term1,search_term2,...
```

---

## 📈 Example Output

```
======================================================================
Wallhaven Telegram Bot - Combined Fetcher & Poster
======================================================================
✓ Created cache directory: wall-cache
✓ Loaded 7 category configurations
  - nature: Group -1002996780898, Every 3050s, 9 terms
  - anime: Group -1002935599065, Every 1000s, 6 terms
✓ Connected to Firebase Firestore
✓ Firebase Firestore collections initialized
✓ Telegram bot configured
🔄 Wallpaper fetcher task started
✓ Scheduled 'nature' (every 3050s / 50min) - 245 wallpapers available
⏸ Skipping 'anime' - No wallpapers available yet
======================================================================
✅ Bot is running
🔄 Fetcher: Continuously fetching wallpapers
📤 Poster: Posting on schedule
Press Ctrl+C to stop
======================================================================

======================================================================
Fetching: nature → tree | Round 1
Target: 100 wallpapers (skipping top 0)
======================================================================
  [10/100] ✓ Added: abc123 (sfw) (5 tags)
  [20/100] ✓ Added: def456 (sketchy) (3 tags)
  ...
✓ Complete: 100 added, 23 duplicates, 0 errors

[nature] Processing 3 wallpapers as a group...
[nature] Sending 3 wallpapers to Telegram...
[nature] ✓ Posted abc123 to group -1002996780898 (album 1/3)
```

---

## 🔧 Advanced Features

### Web Server (Cloud Platform Support)
- Built-in Flask web server for Koyeb/Heroku/Railway compatibility
- Health check endpoint: `/health`
- Statistics endpoint: `/stats`
- Beautiful status page at root URL
- Runs on port 8000 (or `PORT` env variable)
- Zero configuration needed - starts automatically

### Automatic Category Activation
- Categories without wallpapers are **auto-scheduled**
- Start posting as soon as first wallpapers are fetched
- No manual intervention needed

### Graceful Shutdown
- Press `Ctrl+C` to stop
- Waits for active uploads to complete
- Clean shutdown, no data loss

### Memory Efficient
- ImageMagick for thumbnails (external process)
- Streams downloads (no full file in memory)
- Garbage collection after each batch
- ~40-60MB RAM usage

### Content Safety
- Multiple NSFW filter layers:
  - Categories: General + Anime only (no People)
  - Purity: SFW + Sketchy (no NSFW)
  - 50+ exclusion tags in search queries
  - Portrait orientation only

---

## 🛠️ Monitoring

### Check Database Status

You can monitor your data using the [Firebase Console](https://console.firebase.google.com/):

1. **Navigate to Firestore Database**
2. **View Collections**:
   - `wallhaven`: All wallpaper documents
   - `fetch_state`: Current fetching progress per category/term
3. **Use Firebase Console Queries**:
   - Filter by `category`, `status`, etc.
   - Check document counts
   - View real-time updates

### Programmatic Monitoring

You can create a simple Python script to query Firestore:
```python
from firebase_admin import credentials, firestore, initialize_app

cred = credentials.Certificate('serviceAccountKey.json')
initialize_app(cred)
db = firestore.client()

# Count wallpapers by status
wallpapers = db.collection('wallhaven').stream()
status_counts = {}
for doc in wallpapers:
    status = doc.to_dict().get('status', 'unknown')
    status_counts[status] = status_counts.get(status, 0) + 1
print(status_counts)
```

---

## 🚨 Troubleshooting

**Bot stops fetching after some time:**
- Check API key validity
- Monitor rate limit messages
- Check Firebase credentials and connection
- Verify Firestore access permissions

**No wallpapers posting to Telegram:**
- Verify bot has wallpapers: `status="link_added"`
- Check bot token validity
- Ensure bot is admin in groups
- Check Firebase Console for data

**Many duplicates being skipped:**
- Normal behavior! Means database is working
- Bot only posts unique images (SHA256 hash)

---

## 📜 License

This project was created with AI assistance. Use freely!

---

**Ready to start?**
```bash
python wallhaven-bot.py
```

🎨 **Happy wallpapering!**
