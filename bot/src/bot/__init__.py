from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

import aiohttp
import discord

OWNER_ID = 738015097921732638
USER_AGENT = "Mozilla/5.0 (readlog capture bot)"
TITLE_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=5)
TITLE_FETCH_READ_LIMIT = 2 * 1024 * 1024

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
    body: str


class Listing(Enum):
    HOME = False
    ALL_ONLY = True


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
        logger.warning("title fetch failed for %s: %s", url, exc)
        return TitleResult(url, False)

    match = TITLE_RE.search(text)
    title = html.unescape(match.group(1)).strip() if match else ""
    if not title:
        logger.warning("no <title> found for %s", url)
        return TitleResult(url, False)

    logger.info("title for %s: %r", url, title)
    return TitleResult(title, True)


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

    has_staged_changes = _run_git(repo_dir, "diff", "--cached", "--quiet").returncode != 0
    if has_staged_changes:
        commit = _run_git(repo_dir, "commit", "-m", message)
        if commit.returncode != 0:
            logger.error("git commit failed: %s", _describe(commit))
            return False
    else:
        logger.info("no staged changes for %r; pushing any pending local commits", message)

    return _push_with_rebase_retry(repo_dir)


def write_post(
    path: Path, *, title: str, epoch: int, url: str, message_id: int, jump_url: str, body: str,
    listing: Listing = Listing.ALL_ONLY,
) -> None:
    frontmatter = (
        "+++\n"
        'type="link"\n'
        f"unlisted={str(listing.value).lower()}\n"
        f'title="{escape_toml_string(title)}"\n'
        f"date={epoch}\n"
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
    epoch: int,
    message_id: int,
    jump_url: str,
    listing: Listing,
) -> bool:
    for pending, result in entries:
        write_post(
            pending.path,
            title=result.title,
            epoch=epoch,
            url=pending.url,
            message_id=message_id,
            jump_url=jump_url,
            body=pending.body,
            listing=listing,
        )
    paths = [pending.path for pending, _ in entries]
    return commit_and_push(repo_dir, paths, f"add {len(paths)} link(s) from message {message_id}")


def posts_for(pages_dir: Path, message_id: int) -> list[Path]:
    single = pages_dir / f"{message_id}.md"
    if single.exists():
        return [single]
    return sorted(pages_dir.glob(f"{message_id}-*.md"))


def append_note(path: Path, note: str) -> None:
    content = path.read_text(encoding="utf-8").rstrip()
    path.write_text(f"{content}\n\n{note}\n", encoding="utf-8", newline="\n")


def publish_note(repo_dir: Path, paths: list[Path], note: str, *, message_id: int) -> bool:
    for path in paths:
        append_note(path, note)
    return commit_and_push(repo_dir, paths, f"add note from message {message_id}")


def post_metadata(path: Path) -> dict:
    content = path.read_text(encoding="utf-8")
    parts = content.split("+++\n", 2)
    if len(parts) != 3:
        return {}
    return tomllib.loads(parts[1])


def set_listing(paths: list[Path], listing: Listing) -> list[Path]:
    changed = []
    for path in paths:
        metadata = post_metadata(path)
        if metadata.get("type") != "link" or "unlisted" not in metadata:
            continue
        if metadata["unlisted"] is listing.value:
            continue

        content = path.read_text(encoding="utf-8")
        opening, frontmatter, body = content.split("+++\n", 2)
        frontmatter = re.sub(
            r"(?m)^unlisted[ \t]*=[^\n]*$",
            f"unlisted={str(listing.value).lower()}",
            frontmatter,
            count=1,
        )
        path.write_text(
            f"{opening}+++\n{frontmatter}+++\n{body}", encoding="utf-8", newline="\n"
        )
        changed.append(path)
    return changed


def listing_for(message: discord.Message) -> Listing:
    if any(reaction.count > int(reaction.me) for reaction in message.reactions):
        return Listing.HOME
    return Listing.ALL_ONLY


async def react(message: discord.Message, emoji: str, reason: str) -> None:
    logger.info("message %s: reacting %s (%s)", message.id, emoji, reason)
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as exc:
        logger.error("message %s: failed to add reaction %s: %s", message.id, emoji, exc)


def extract_url_lines(content: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            current = None
            continue
        urls = URL_RE.findall(line)
        if urls:
            current = [line]
            entries.extend((url, current) for url in urls)
        elif current is not None:
            current.append(line)
    return [(url, "\n".join(body_lines)) for url, body_lines in entries]


def pending_links_for(pages_dir: Path, message_id: int, url_lines: list[tuple[str, str]]) -> list[PendingLink]:
    single = len(url_lines) == 1
    candidates = (
        PendingLink(path=pages_dir / f"{message_id}{'' if single else f'-{i}'}.md", url=url, body=body)
        for i, (url, body) in enumerate(url_lines)
    )
    return [link for link in candidates if not link.path.exists()]


class ReadlogClient(discord.Client):
    def __init__(self, *, config: BotConfig, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self.config = config
        self._publish_lock = asyncio.Lock()

    async def on_ready(self) -> None:
        logger.info("logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))
        await self._sync_listings()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id != self.config.owner_id:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        logger.info("incoming DM %s from %s: %r", message.id, message.author, message.content)

        if await self._handle_reply(message):
            return

        url_lines = extract_url_lines(message.content)
        if not url_lines:
            logger.info("ignoring %s: no links found", message.id)
            return
        logger.info("message %s: found %d link(s): %s", message.id, len(url_lines), [u for u, _ in url_lines])

        pending = pending_links_for(self.config.pages_dir, message.id, url_lines)
        if not pending:
            logger.info("message %s: all link(s) already captured, skipping", message.id)
            return

        self.config.pages_dir.mkdir(parents=True, exist_ok=True)

        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            titles = await asyncio.gather(*(fetch_title(session, link.url) for link in pending))

        entries = list(zip(pending, titles))
        epoch = int(message.created_at.timestamp())

        async with self._publish_lock:
            try:
                current = await message.channel.fetch_message(message.id)
            except discord.HTTPException as exc:
                logger.error("message %s: failed to read reactions: %s", message.id, exc)
                await react(message, REACTION_ERROR, "reaction lookup failed")
                return

            published = await asyncio.to_thread(
                publish_links,
                self.config.repo_dir,
                entries,
                epoch=epoch,
                message_id=message.id,
                jump_url=message.jump_url,
                listing=listing_for(current),
            )

        if not published:
            await react(message, REACTION_ERROR, "publish failed")
            return

        any_title_missing = any(not result.found for _, result in entries)
        logger.info(
            "message %s: published %d post(s) (title_missing=%s)", message.id, len(entries), any_title_missing
        )
        await react(message, REACTION_TITLE_MISSING if any_title_missing else REACTION_SUCCESS, "published")

    async def _handle_reply(self, message: discord.Message) -> bool:
        ref = message.reference
        if ref is None or ref.type is not discord.MessageReferenceType.reply or ref.message_id is None:
            return False

        paths = posts_for(self.config.pages_dir, ref.message_id)
        if not paths:
            return False

        note = message.content.strip()
        if not note:
            logger.info("message %s: empty reply to %s, ignoring", message.id, ref.message_id)
            return True

        async with self._publish_lock:
            published = await asyncio.to_thread(
                publish_note, self.config.repo_dir, paths, note, message_id=message.id
            )

        if not published:
            await react(message, REACTION_ERROR, "note publish failed")
            return True

        logger.info("message %s: added note to %d post(s) of %s", message.id, len(paths), ref.message_id)
        await react(message, REACTION_SUCCESS, "note added")
        return True

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.config.owner_id:
            await self._reaction_listing(payload.channel_id, payload.message_id)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.config.owner_id:
            await self._reaction_listing(payload.channel_id, payload.message_id)

    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self._reaction_listing(payload.channel_id, payload.message_id)

    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self._reaction_listing(payload.channel_id, payload.message_id)

    async def _reaction_listing(self, channel_id: int, message_id: int) -> None:
        async with self._publish_lock:
            paths = posts_for(self.config.pages_dir, message_id)
            if not paths:
                return
            try:
                owner = await self.fetch_user(self.config.owner_id)
                channel = await owner.create_dm()
                if channel.id != channel_id:
                    return
                message = await channel.fetch_message(message_id)
                if message.author.id != self.config.owner_id:
                    return

                changed = await asyncio.to_thread(set_listing, paths, listing_for(message))
                if not changed:
                    return
                published = await asyncio.to_thread(
                    commit_and_push, self.config.repo_dir, changed,
                    f"Update listing for message {message_id}",
                )
                if not published:
                    logger.error("message %s: listing publish failed", message_id)
                    return
                logger.info("message %s: listing updated (%s)", message_id, listing_for(message).name)
            except discord.HTTPException as exc:
                logger.error("message %s: failed to sync reactions: %s", message_id, exc)

    async def _sync_listings(self) -> None:
        async with self._publish_lock:
            managed: dict[int, list[Path]] = {}
            for path in self.config.pages_dir.glob("*.md"):
                metadata = await asyncio.to_thread(post_metadata, path)
                if metadata.get("type") != "link" or "unlisted" not in metadata:
                    continue
                managed.setdefault(int(metadata["message_id"]), []).append(path)
            if not managed:
                return

            changed = []
            try:
                owner = await self.fetch_user(self.config.owner_id)
                channel = await owner.create_dm()
                async for message in channel.history(
                    limit=None, after=discord.Object(id=min(managed) - 1), oldest_first=True
                ):
                    if message.author.id != self.config.owner_id or message.id not in managed:
                        continue
                    changed.extend(await asyncio.to_thread(
                        set_listing, managed[message.id], listing_for(message)
                    ))
            except discord.HTTPException as exc:
                logger.error("failed to sync reaction history: %s", exc)

            if not changed:
                return
            published = await asyncio.to_thread(
                commit_and_push, self.config.repo_dir, changed, "Sync article listings from reactions"
            )
            if not published:
                logger.error("reaction history publish failed")


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("Set DISCORD_BOT_TOKEN.")
        sys.exit(1)

    repo_dir = find_repo_root(Path(__file__).resolve().parent)
    config = BotConfig(owner_id=OWNER_ID, repo_dir=repo_dir, pages_dir=repo_dir / "_pages")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_reactions = True

    client = ReadlogClient(config=config, intents=intents)
    client.run(token, log_level=logging.INFO, root_logger=True)
