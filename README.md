# Media Downloader Bot (Telegram & Discord)

Send a link and get the video, photos, or audio back. Supports **YouTube, TikTok,
Instagram, Twitter/X, and Reddit**, including photo carousels and mixed albums.
You can also cut a video fragment into a GIF.

**Try it on Telegram:** [@asrtalinkbot](https://t.me/asrtalinkbot)

## Quick start (Docker)

```bash
git clone https://github.com/despa1r0/Tg-by-link-download.git
cd Tg-by-link-download
cp .env.example .env    # then fill in your tokens
```

Run the Telegram bot:

```bash
docker compose up -d --build bot
```

Run the Discord bot as well:

```bash
docker compose --profile discord up -d --build discord
```

## Configuring `.env`

Only the token of the bot you want to run is required. Everything else has
sensible defaults.

| Variable | What it does |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `DISCORD_BOT_TOKEN` | Discord bot token (see [Discord setup](docs/DISCORD_SETUP.md)) |
| `DISCORD_ALLOWED_CHANNEL_IDS` | Comma-separated Discord channel IDs where the bot responds. Empty = server channels are ignored |
| `DISCORD_ALLOW_DMS` | Allow the Discord bot in direct messages (`true` by default) |
| `MAX_DOWNLOAD_MB` | Maximum file size. Keep `50` for regular Telegram bots |
| `YTDLP_COOKIES_FILE` | Optional. Path to a cookies file for Instagram posts that need a login |

**Instagram cookies (optional).** If some Instagram posts fail to download, export
your browser cookies in Netscape format to `secrets/cookies.txt` and add this to `.env`:

```
YTDLP_COOKIES_FILE=/app/secrets/cookies.txt
```

## Running without Docker

You need Python 3.12 and **FFmpeg** installed.

```bash
pip install -r requirements.txt
python -m bot.main                 # Telegram
python -m bot.discord_app.main     # Discord
```

> In Telegram, GIFs are stored as silent MP4 videos, so a saved "GIF" will
> usually be an `.mp4` file. This is how Telegram works.
