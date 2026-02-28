# 🚀 Koyeb Deployment Guide

Deploy your Wallhaven Telegram Bot to Koyeb cloud platform in minutes!

---

## 📋 Prerequisites

1. **Koyeb Account** - Sign up at [koyeb.com](https://www.koyeb.com/)
2. **Firebase Project** - With Firestore enabled
3. **Telegram Bot Token** - From @BotFather
4. **Wallhaven API Key** - From wallhaven.cc

---

## 🔧 Step 1: Prepare Firebase Credentials (Base64)

On your local machine, encode your Firebase credentials:

### Windows (PowerShell):
```powershell
$content = Get-Content -Path serviceAccountKey.json -Raw
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($content))
```

### Linux/Mac:
```bash
base64 -w 0 serviceAccountKey.json
```

**Copy the output** - you'll need this long base64 string!

---

## 🌐 Step 2: Deploy to Koyeb

### Method 1: GitHub Deployment (Recommended)

1. **Push your code to GitHub** (make sure `.env` is in `.gitignore`)
   ```bash
   git add .
   git commit -m "Prepare for Koyeb deployment"
   git push origin main
   ```

2. **Go to Koyeb Dashboard** → Click "Create App"

3. **Select GitHub** as deployment source
   - Connect your GitHub account
   - Select your repository
   - Branch: `main`

4. **Configure Build Settings**
   - Builder: **Buildpack**
   - Build command: *(leave empty, automatic)*
   - Run command: `python wallhaven-bot.py`

5. **Set Environment Variables** (click "Add Variable" for each):

   | Variable Name | Value |
   |---------------|-------|
   | `FIREBASE_CREDENTIALS_BASE64` | Your base64 string from Step 1 |
   | `WALLHAVEN_API_KEY` | Your Wallhaven API key |
   | `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
   | `CATEGORY_1` | `nature\|-1002996780898\|3050\|tree,water,river` |
   | `CATEGORY_2` | `anime\|-1002935599065\|1000\|anime,manga` |
   | *(Add more categories as needed)* | |

   **Note**: In Koyeb environment variables, use `\|` (backslash + pipe) instead of just `|`

6. **Configure Service**
   - Service name: `wallhaven-bot`
   - Instance type: **Free** (or choose paid for better performance)
   - Regions: Choose closest to you
   - Port: **8000** (or leave default, Koyeb auto-detects)
   - Health check: `/health` (path)

7. **Click "Deploy"**

---

### Method 2: Docker Deployment (Advanced)

If you prefer Docker, Koyeb also supports direct Docker image deployment.

---

## ✅ Step 3: Verify Deployment

1. **Check Logs** in Koyeb dashboard:
   ```
   ✓ Flask web server started in background thread
   ✓ Web server ready for health checks
   ✓ Firebase Admin SDK initialized from Base64 credentials
   ✓ Connected to Firebase Firestore
   🔄 Wallpaper fetcher task started
   ✅ Bot is running
   ```

2. **Visit your app URL** (provided by Koyeb):
   - Example: `https://wallhaven-bot-yourname.koyeb.app/`
   - You should see: "Wallhaven Telegram Bot - Your wallpaper automation service is running smoothly!"

3. **Check health endpoint**:
   - Visit: `https://wallhaven-bot-yourname.koyeb.app/health`
   - Should return: `{"status": "healthy", "service": "wallhaven-telegram-bot"}`

4. **Monitor your Telegram groups** - Wallpapers should start posting!

---

## 🔍 Monitoring & Debugging

### View Logs
- Go to Koyeb Dashboard → Your App → Logs tab
- Real-time logs show fetching and posting activity

### Check Statistics
- Visit: `https://your-app.koyeb.app/stats`
- Shows rate limits, cache info, etc.

### Health Checks
- Koyeb automatically monitors `/health` endpoint
- If bot crashes, Koyeb will restart it

---

## ⚙️ Environment Variables Reference

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `FIREBASE_CREDENTIALS_BASE64` | ✅ | Base64 encoded Firebase JSON | `eyJ0eXBlIjoi...` |
| `WALLHAVEN_API_KEY` | ✅ | Your Wallhaven API key | `abc123xyz...` |
| `TELEGRAM_BOT_TOKEN` | ✅ | Your Telegram bot token | `123456:ABC-DEF...` |
| `CATEGORY_1` | ✅ | First category config | `nature\|-1002...\|3050\|tree,water` |
| `CATEGORY_2` | ⚠️ | Second category (optional) | `anime\|-1002...\|1000\|anime` |
| `PORT` | ⚠️ | Web server port (auto-set by Koyeb) | `8000` |

---

## 🎯 Scaling Tips

### Free Tier Limits
- 1 free service
- 512MB RAM
- Sleeps after inactivity (wakes on HTTP request)

### Prevent Sleep
- Go to Settings → Enable "Keep Alive"
- Or use a free uptime monitor like UptimeRobot to ping `/health` every 5 minutes

### Upgrade for Better Performance
- More RAM → Handle larger images
- More CPU → Faster processing
- More instances → Higher availability

---

## 🛠️ Troubleshooting

### "Firebase credentials not found"
- ✅ Check `FIREBASE_CREDENTIALS_BASE64` is correctly set
- ✅ Ensure no extra spaces or newlines in the base64 string
- ✅ Try encoding again and re-paste

### "Invalid Firebase credentials"
- ✅ Verify your `serviceAccountKey.json` is valid
- ✅ Check if Firestore is enabled in Firebase Console
- ✅ Ensure service account has proper permissions

### "Bot not posting to Telegram"
- ✅ Check bot token is correct
- ✅ Ensure bot is admin in Telegram groups
- ✅ Verify group IDs are correct (should be negative numbers)

### "Out of memory errors"
- ✅ Upgrade to paid tier with more RAM
- ✅ Reduce number of categories
- ✅ Increase posting intervals

### "Port already in use"
- ✅ Don't set `PORT` variable manually - let Koyeb set it
- ✅ If needed, use the value Koyeb provides

---

## 🔄 Updating Your Bot

1. **Update code locally**
2. **Commit and push to GitHub**:
   ```bash
   git add .
   git commit -m "Update bot features"
   git push origin main
   ```
3. **Koyeb auto-deploys** on push (if auto-deploy enabled)
4. Or manually redeploy from Koyeb dashboard

---

## 💾 Database Persistence

**Important**: SQLite cache databases (`*.db` files) are stored in Koyeb's ephemeral storage:
- ✅ **Persists across restarts** (usually)
- ⚠️ **May be lost** on redeployments or instance changes
- 🎯 **Solution**: Bot automatically rebuilds cache from Firebase on startup

---

## 📊 Cost Estimation

### Free Tier (Hobby Projects)
- **Cost**: $0/month
- **Limitations**: Sleeps after inactivity, 512MB RAM
- **Best for**: Testing, low-traffic bots

### Starter Plan
- **Cost**: ~$5/month
- **Benefits**: No sleep, 1GB RAM, better performance
- **Best for**: Personal use, 2-3 active categories

### Pro Plan
- **Cost**: ~$20/month
- **Benefits**: 2GB+ RAM, multiple instances, autoscaling
- **Best for**: Multiple groups, 5+ categories

---

## 🎉 Success!

Your bot is now running 24/7 on Koyeb cloud! 🚀

- ✅ Automatic restarts on crashes
- ✅ Health monitoring
- ✅ Easy scaling
- ✅ Free HTTPS
- ✅ Global CDN

**Questions?** Check Koyeb docs: https://www.koyeb.com/docs

---

## 📝 Quick Checklist

- [ ] Firebase credentials encoded to Base64
- [ ] Code pushed to GitHub
- [ ] Koyeb app created
- [ ] Environment variables configured
- [ ] Health check endpoint set to `/health`
- [ ] Run command: `python wallhaven-bot.py`
- [ ] Deployment successful
- [ ] Web page accessible
- [ ] Logs show "Bot is running"
- [ ] Wallpapers posting to Telegram

**All checked?** You're live! 🎊
