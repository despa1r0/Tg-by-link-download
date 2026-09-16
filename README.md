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

## CI/CD (GitHub Actions)

The workflow has two jobs:

1. **Tests:** one Python 3.12 run on pushes and pull requests to `develop` and
   `master`, or a manual run. No build matrix, duplicate lint jobs or external
   social-network requests.
2. **Build and deploy:** only after passing tests on `master`, outside pull
   requests. Build one Docker image, push its immutable commit tag to GHCR,
   then pull and restart the bot over SSH. Pushes to `develop` never deploy.

Deployments are serialized per branch. There is no `latest` tag; use the commit
SHA shown by the successful workflow when selecting an image manually.

Create a protected GitHub environment named `production` and add these
environment secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server hostname or IPv4 address, for example `bot.example.com` |
| `DEPLOY_PORT` | SSH port; defaults to `22` |
| `DEPLOY_USER` | Unprivileged SSH user used for deployment |
| `DEPLOY_SSH_KEY` | Private OpenSSH key for that user |
| `DEPLOY_KNOWN_HOSTS` | The server's complete trusted `known_hosts` line |
| `DEPLOY_PATH` | Directory containing `docker-compose.yml` and `.env`, for example `/srv/tg-media-bot` |

Generate `DEPLOY_KNOWN_HOSTS` from a trusted machine with
`ssh-keyscan -p <port> -H <host>`, then verify the resulting fingerprint through
an independent trusted channel before saving it. Do not use an unverified scan
performed by the workflow itself.

Prepare the server once:

1. Install Docker Engine and Docker Compose v2 (`docker compose`).
2. Create `DEPLOY_PATH` and place the current `docker-compose.yml` there. A
   one-time clone is fine, but the deployment user does not need Git access and
   the workflow never updates the repository on the server.
3. Create `DEPLOY_PATH/.env` from `.env.example` and set
   `BOT_IMAGE=ghcr.io/despa1r0/tg-by-link-download:<deployed-commit-sha>` for manual Compose
   commands.
   Keep the real `.env` only on the server; the workflow does not overwrite or
   upload it. Create any mounted cookie file on the server in the path configured
   by `YTDLP_COOKIES_FILE`.
4. If the GHCR package is private, log in once on the server with a token that
   has read-only package access:
   ```bash
   echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
   ```
   A public package does not require this step.
5. Add the workflow's public SSH key to the deploy user's
   `~/.ssh/authorized_keys`.

The deploy step performs only `docker compose pull` and `docker compose up` on
the server. It uses the immutable `${GITHUB_SHA}` image tag built by the same
workflow; there is no `git fetch`, checkout, merge, or server-side image build.
If `docker-compose.yml` itself changes, update that one deployment file on the
server separately. Configure required reviewers for the `production`
environment if deployments need manual approval, and protect `master` so CI
must pass before changes are merged.

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
