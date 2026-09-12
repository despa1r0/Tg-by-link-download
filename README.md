# Telegram Media Downloader Bot

A lightweight Telegram bot that downloads videos and audio from YouTube, TikTok, Instagram, Twitter, and Reddit using `yt-dlp`. It can also convert specific video segments to GIFs.

> Telegram stores animations as silent H.264/MPEG-4 videos. Consequently, saving a
> GIF sent as an animation from a Telegram client normally produces an `.mp4` file.

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

   Instagram may require authenticated cookies for restricted posts or when its
   anonymous API is rate-limited. Export a Netscape-format cookies file, mount it
   read-only into the container, and set `YTDLP_COOKIES_FILE` to its container path.

3. Build and start the bot in the background:
   ```bash
   docker-compose up -d --build
   ```

That's it! The bot is now running.
