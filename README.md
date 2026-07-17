# Telegram Media Downloader Bot

A lightweight Telegram bot that downloads videos and audio from YouTube, TikTok, Instagram, and Twitter using `yt-dlp`. It can also convert specific video segments to GIFs.

## Deployment (Docker)

1. Clone the repository and enter the directory:
   ```bash
   git clone https://github.com/despa1r0/Tg-by-link-download.git
   cd Tg-by-link-download
   ```

2. Configure your environment variables:
   ```bash
   cp .env.example .env
   # Open .env and add your Telegram BOT_TOKEN
   ```

3. Build and start the bot in the background:
   ```bash
   docker-compose up -d --build
   ```

That's it! The bot is now running.
