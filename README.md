# readlog

readlog is a personal reading log. You send the bot a link by DM. The link appears on [monster0506.github.io/readlog](https://monster0506.github.io/readlog) with a title, an RSS entry, and a place in a weekly roundup.

## How it works

You send the bot a link by DM. The bot reads the page title, then writes a Markdown post into `_pages/`. The bot commits the post and pushes it to `main`. Each push makes GitHub Actions rebuild the RSS feeds and the static site, then deploy it to GitHub Pages. Every Monday, a separate workflow adds the past week's links to a roundup post and deploys again.

1. **`bot/`** - A Discord client that watches DMs from one owner ID. When a message contains a link, the bot reads the title and writes a Markdown post under `_pages/`. The bot commits the post and pushes it to `main`. The bot adds an emoji to the message: a check mark for success, a warning sign for no title, or an X for failure.
2. **`_pages/`** - The content of the site. Each post has TOML frontmatter (`+++ ... +++`) and a body. The `type` field is `link` for a single link or `roundup` for a weekly digest.
3. **`scripts/`** - Python scripts that build the RSS feeds and a weekly roundup post from the files in `_pages/`.
4. **`.github/workflows/`** - Two workflows. `deploy.yml` builds the feeds and passes them to  [`Monster0506/static-site-generator`](https://github.com/Monster0506/static-site-generator), which publishes the site to GitHub Pages. `roundup.yml` runs every Monday. It builds the past week's roundup post. If the roundup post changes, the workflow deploys the site again.

## Running the bot

```bash
cd bot
uv sync
DISCORD_BOT_TOKEN=... uv run bot
```

The bot needs write access to the repo that it runs in, because it commits and pushes new posts. The bot also needs the `message_content` intent enabled for the bot user.

`bot/deploy/readlog-bot@.service` is a systemd unit for the bot. It runs the bot as `readlog-bot@<user>` on a machine that is always on. It reads the bot token from `~/.config/readlog-bot.env`.

## Building the site locally

```bash
uv run scripts/build_feed.py _pages --type link \
  --site-url https://monster0506.github.io --base-path /readlog \
  -o dist/feed-links.xml

uv run scripts/build_roundup.py \
  --site-url https://monster0506.github.io --base-path /readlog
```
