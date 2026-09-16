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
   docker compose up -d --build
   ```

That's it! The bot is now running.

## Media detection and local checks

Instagram photos and carousels use an adapted yt-dlp extractor that preserves
explicit photo items alongside videos. The tested yt-dlp version is pinned;
run the regression tests before updating it. Structured proxy metadata is the
fallback. An ambiguous `og:image` alone is not accepted as an Instagram photo.
Twitter mixed posts and Reddit JSON galleries preserve item order. Playlists
are not offered as photo albums, and TikTok thumbnails are not treated as photos.

The bot sends mixed albums in groups of up to ten and a remaining single item
separately. Downloaded bytes and FFprobe streams determine the Telegram media
method. Local execution needs **FFmpeg and FFprobe on PATH**, both already
included in the Docker image.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The fixtures in `tests/fixtures` are synthetic, anonymized examples of provider
payload shapes, not captured live posts. Tests cover extraction, buttons,
cache reuse, ordering and Telegram send methods without contacting Telegram or
social networks. Real-site availability still depends on cookies, rate limits
and the external providers.
