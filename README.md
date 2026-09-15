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

The `.github/workflows/deploy.yml` workflow runs on every push to `master` (and
can also be started manually from `master`). It runs the unit tests, builds the
Docker image in GitHub Actions, publishes immutable commit and `latest` tags to
GitHub Container Registry (GHCR), and then tells the production server to pull
and start the immutable commit image.

Create a protected GitHub environment named `production` and add these
environment secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server hostname or IPv4 address, for example `bot.example.com` |
| `DEPLOY_PORT` | SSH port, for example `22` |
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
   `BOT_IMAGE=ghcr.io/despa1r0/tg-by-link-download:latest` for manual Compose
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
