from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import aiohttp
import discord

OWNER_ID = 738015097921732638
USER_AGENT = "Mozilla/5.0 (readlog capture bot)"
TITLE_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=5)
TITLE_FETCH_READ_LIMIT = 65_536

REACTION_SUCCESS = "\u2705"
REACTION_TITLE_MISSING = "\u26a0\ufe0f"
REACTION_ERROR = "\u274c"

URL_RE = re.compile(r"https?://\S+")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

logger = logging.getLogger("readlog.bot")


@dataclass(frozen=True, slots=True)
class BotConfig:
    owner_id: int
    repo_dir: Path
    pages_dir: Path


class TitleResult(NamedTuple):
    title: str
    found: bool


@dataclass(frozen=True, slots=True)
class PendingLink:
    path: Path
    url: str


def find_repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


async def fetch_title(session: aiohttp.ClientSession, url: str) -> TitleResult:
    try:
        async with session.get(url, timeout=TITLE_FETCH_TIMEOUT, max_redirects=5) as resp:
            raw = await resp.content.read(TITLE_FETCH_READ_LIMIT)
            text = raw.decode(resp.charset or "utf-8", errors="replace")
    except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError) as exc:
        logger.debug("title fetch failed for %s: %s", url, exc)
        return TitleResult(url, False)

    match = TITLE_RE.search(text)
    title = html.unescape(match.group(1)).strip() if match else ""
    return TitleResult(title, True) if title else TitleResult(url, False)


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)


def _describe(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(s.strip() for s in (result.stdout, result.stderr) if s.strip())
    return output or f"exit code {result.returncode}, no output"


def _push_with_rebase_retry(repo_dir: Path) -> bool:
    push = _run_git(repo_dir, "push")
    if push.returncode == 0:
        return True

    pull = _run_git(repo_dir, "pull", "--rebase")
    if pull.returncode != 0:
        logger.error("git pull --rebase failed: %s", _describe(pull))
        return False

    retry = _run_git(repo_dir, "push")
    if retry.returncode != 0:
        logger.error("git push failed after rebase: %s", _describe(retry))
        return False
    return True


def commit_and_push(repo_dir: Path, paths: list[Path], message: str) -> bool:
    _run_git(repo_dir, "add", "--", *(str(p) for p in paths))
    commit = _run_git(repo_dir, "commit", "-m", message)
    if commit.returncode != 0:
        logger.error("git commit failed: %s", _describe(commit))
        return False
    return _push_with_rebase_retry(repo_dir)


def write_post(
    path: Path, *, title: str, date: str, url: str, message_id: int, jump_url: str, body: str
) -> None:
    frontmatter = (
        "+++\n"
        'type="link"\n'
        f'title="{escape_toml_string(title)}"\n'
        f'date="{date}"\n'
        f'url="{escape_toml_string(url)}"\n'
        f'message_id="{message_id}"\n'
        f'jump_url="{escape_toml_string(jump_url)}"\n'
        "+++\n\n"
    )
    path.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")


def publish_links(
    repo_dir: Path,
    entries: list[tuple[PendingLink, TitleResult]],
    *,
    date: str,
    message_id: int,
    jump_url: str,
    body: str,
) -> bool:
    for pending, result in entries:
        write_post(
            pending.path,
            title=result.title,
            date=date,
            url=pending.url,
            message_id=message_id,
            jump_url=jump_url,
            body=body,
        )
    paths = [pending.path for pending, _ in entries]
    return commit_and_push(repo_dir, paths, f"add {len(paths)} link(s) from message {message_id}")


def pending_links_for(pages_dir: Path, message_id: int, urls: list[str]) -> list[PendingLink]:
    single = len(urls) == 1
    candidates = (
        PendingLink(path=pages_dir / f"{message_id}{'' if single else f'-{i}'}.md", url=url)
        for i, url in enumerate(urls)
    )
    return [link for link in candidates if not link.path.exists()]


class ReadlogClient(discord.Client):
    def __init__(self, *, config: BotConfig, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self.config = config

    async def on_ready(self) -> None:
        logger.info("logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id != self.config.owner_id:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        urls = URL_RE.findall(message.content)
        if not urls:
            return

        pending = pending_links_for(self.config.pages_dir, message.id, urls)
        if not pending:
            return

        self.config.pages_dir.mkdir(parents=True, exist_ok=True)

        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            titles = await asyncio.gather(*(fetch_title(session, link.url) for link in pending))

        entries = list(zip(pending, titles))
        date = message.created_at.strftime("%Y-%m-%d")

        published = await asyncio.to_thread(
            publish_links,
            self.config.repo_dir,
            entries,
            date=date,
            message_id=message.id,
            jump_url=message.jump_url,
            body=message.content,
        )

        if not published:
            await message.add_reaction(REACTION_ERROR)
            return

        any_title_missing = any(not result.found for _, result in entries)
        await message.add_reaction(REACTION_TITLE_MISSING if any_title_missing else REACTION_SUCCESS)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("Set DISCORD_BOT_TOKEN.")
        sys.exit(1)

    repo_dir = find_repo_root(Path(__file__).resolve().parent)
    config = BotConfig(owner_id=OWNER_ID, repo_dir=repo_dir, pages_dir=repo_dir / "_pages")

    intents = discord.Intents.default()
    intents.message_content = True

    client = ReadlogClient(config=config, intents=intents)
    client.run(token, log_level=logging.INFO, root_logger=True)
