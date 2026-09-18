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

   Instagram Reels and other restricted posts may require authenticated cookies.
   Put a Netscape-format file at `secrets/cookies.txt` and set
   `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt`. The `secrets/` directory is
   already mounted read-only and its contents are ignored by Git.

3. Build and start the bot, Loki, and Grafana Alloy in the background:
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

## Centralized logs (Loki)

The bot writes one JSON object per line to stdout. Grafana Alloy discovers the
Compose `bot` container through the Docker socket, enriches the records, and
ships them to Loki. Loki persists its data in the `loki-data` Docker volume and
is reachable only from the server itself at `http://127.0.0.1:3100` by default.
It must remain behind an authenticated proxy if it is ever exposed remotely.

Media failure events include the platform, operation stage, media type, error
type and message, request ID, and safe URL metadata. URL paths, query strings,
credentials, and raw Telegram user IDs are not logged. Set a long random
`LOG_CONTEXT_SALT` in `.env` if stable pseudonymous user references are useful
for correlating repeated failures.

For example, query recent download failures directly from the server:

```bash
curl -G http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="tg-media-bot",event="media_operation_failed"} | json'
```

`level`, `event`, and `source_platform` are low-cardinality Loki labels. Request
IDs and failure details are structured metadata and remain in each JSON log
line, avoiding a high-cardinality stream for every user or URL.

## CI/CD (GitHub Actions)

Automation is split into two independent workflows:

- `.github/workflows/ci.yml` validates Python and Compose configuration, runs
  Ruff, and runs unit tests for pushes and pull requests. Markdown-only and
  `docs/`-only changes do not start CI.
- `.github/workflows/deploy.yml` builds and publishes Docker images independently
  of CI. Every `master` build receives immutable `${GITHUB_SHA}` and `edge` tags.
  A semantic release tag such as `v0.0.1`, `v0.1.0`, or `v1.0.0` also publishes
  that human-readable tag and `latest`, then deploys the immutable SHA image.

Before an SSH connection is opened, the deployment workflow authenticates to
GHCR and inspects the exact SHA-tagged image. The deployment job cannot run when
that image is absent. A manual workflow run builds the selected commit but only
deploys it when the `deploy` input is enabled.

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server hostname or IPv4 address, for example `bot.example.com` |
| `DEPLOY_PORT` | SSH port, for example `22` |
| `DEPLOY_USER` | Unprivileged SSH user used for deployment |
| `DEPLOY_SSH_KEY` | Private OpenSSH key for that user |
| `DEPLOY_PATH` | Directory containing `docker-compose.yml` and `.env`, for example `/srv/tg-media-bot` |

The workflow accepts a previously unseen SSH host key automatically. This removes
the `DEPLOY_KNOWN_HOSTS` secret, but also means the ephemeral runner does not pin
the VPS fingerprint between deployments.

Prepare the server once:

1. Install Docker Engine and Docker Compose v2 (`docker compose`).
2. Create `DEPLOY_PATH` and make sure the deployment user can write to it. The
   workflow uploads `docker-compose.yml` and the `observability/` configuration;
   the server does not need Git access or a repository clone.
3. Create `DEPLOY_PATH/.env` from `.env.example` and set
   `BOT_IMAGE=ghcr.io/despa1r0/tg-by-link-download:latest` for manual Compose
   commands.
   Keep the real `.env` only on the server; the workflow does not overwrite or
   upload it. If authenticated extraction is needed, place the cookie file at
   `DEPLOY_PATH/secrets/cookies.txt`, restrict it with `chmod 600`, and configure
   `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt`.
4. Add the workflow's public SSH key to the deploy user's
   `~/.ssh/authorized_keys`.

The deploy job uploads the Compose and observability configuration, then runs
an authenticated GHCR pull followed by `docker compose up`. It uses the immutable
`${GITHUB_SHA}` image built by the same workflow; there is no Git checkout or
server-side image build on the VPS. The workflow never overwrites `.env` or the
`secrets/` directory. Configure required reviewers for the `production`
environment if deployments need manual approval, and protect `master` so CI
must pass before changes are merged. Create and push a release only after the
intended commit has passed CI, for example:

```bash
git tag v0.1.0
git push origin v0.1.0
```
