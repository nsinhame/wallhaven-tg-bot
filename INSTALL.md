# Installation Guide for Wallhaven Telegram Bot

## Prerequisites

### On Debian/Ubuntu Server

```bash
# Update package list
sudo apt update

# Install Python 3 and pip
sudo apt install python3 python3-pip python3-venv -y

# Install system dependencies for Pillow (image processing)
sudo apt install libjpeg-dev zlib1g-dev -y
```

## Installation Methods

### Method 1: Virtual Environment (RECOMMENDED ✅)

Virtual environments keep your project dependencies isolated from the system Python. This is the best practice!

**Benefits:**
- No conflicts with other Python projects
- Easy to manage different versions
- Safe to experiment without breaking system Python
- Can delete and recreate easily

**Steps:**

```bash
# Navigate to your project directory
cd /path/to/wallhaven-android

# Create a virtual environment named 'venv'
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Your prompt will now show (venv) - you're in the virtual environment!

# Install all dependencies
pip install -r requirements.txt

# Run your scripts (while venv is activated)
python update-link-db.py
python tg-up-bot.py

# When done, deactivate the virtual environment
deactivate
```

**Next time you want to run the scripts:**
```bash
cd /path/to/wallhaven-android
source venv/bin/activate
python tg-up-bot.py
```

### Method 2: System-Wide Installation (NOT RECOMMENDED ⚠️)

Installs packages for all users and projects on the system.

**Drawbacks:**
- Can cause conflicts between projects
- Might need sudo/admin rights
- Harder to manage different versions
- Can break system tools that depend on Python

**Only use if you really need to:**

```bash
# Install system-wide (not recommended)
sudo pip3 install -r requirements.txt
```

## Dependencies Explained

| Package | Purpose | Used By |
|---------|---------|---------|
| **pymongo** | MongoDB database driver | Both scripts |
| **requests** | HTTP requests to Wallhaven API | update-link-db.py |
| **httpx** | Async HTTP client for downloads | tg-up-bot.py |
| **Pillow** | Image processing (open/hash images) | tg-up-bot.py |
| **imagehash** | Perceptual hash for duplicate detection | tg-up-bot.py |
| **telethon** | Telegram client library | tg-up-bot.py |
| **apscheduler** | Schedule periodic tasks | tg-up-bot.py |

## Quick Start (Copy-Paste Ready)

```bash
# 1. Install system dependencies
sudo apt update
sudo apt install python3 python3-pip python3-venv libjpeg-dev zlib1g-dev -y

# 2. Go to your project
cd /path/to/wallhaven-android

# 3. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 4. Install Python packages
pip install -r requirements.txt

# 5. Configure your credentials in config.txt
# Edit config.txt with your MongoDB URI, API keys, etc.

# 6. Test the scripts
python update-link-db.py  # Fetch wallpapers
python tg-up-bot.py       # Start the bot
```

## Running as a Service (Keep Bot Running 24/7)

To keep the bot running even after you log out, create a systemd service:

```bash
# Create service file
sudo nano /etc/systemd/system/wallhaven-bot.service
```

Add this content (adjust paths to your actual paths):

```ini
[Unit]
Description=Wallhaven Telegram Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/wallhaven-android
Environment="PATH=/path/to/wallhaven-android/venv/bin"
ExecStart=/path/to/wallhaven-android/venv/bin/python tg-up-bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable wallhaven-bot

# Start the service
sudo systemctl start wallhaven-bot

# Check status
sudo systemctl status wallhaven-bot

# View logs
sudo journalctl -u wallhaven-bot -f
```

## Troubleshooting

**If pip install fails with "externally-managed-environment" error on Debian 12+:**
```bash
# This is why we use virtual environments!
# Solution: Use venv (Method 1) instead of system-wide install
```

**If Pillow installation fails:**
```bash
# Install missing image libraries
sudo apt install libjpeg-dev zlib1g-dev libtiff-dev -y
```

**Check if dependencies are installed:**
```bash
pip list
```

**Update all dependencies:**
```bash
pip install --upgrade -r requirements.txt
```
