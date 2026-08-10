from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path

FRONTMATTER_DELIM = "+++\n"


def to_int32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def generate_hash(name: str) -> int:
    h = 0
    for ch in name:
        shifted = to_int32(h << 5)
        h = to_int32(shifted - h + ord(ch))
    return h & 0xFFFFFFFF


def permalink_for(short_name: str, *, site_url: str, base_path: str) -> str:
    return f"{site_url}{base_path}/{generate_hash(short_name)}.html"


@dataclass(frozen=True, slots=True)
class Post:
    title: str
    date: date_cls
    epoch: int
    message_id: str
    url: str | None
    permalink: str
    description: str
    short_name: str
    post_type: str


def parse_date(raw_date: object) -> tuple[date_cls, int]:
    """Accepts a bare TOML integer (epoch seconds, current format) or a
    quoted/native date (legacy). Falls back to now if missing/unrecognized."""
    if isinstance(raw_date, bool):
        pass
    elif isinstance(raw_date, int):
        return datetime.fromtimestamp(raw_date, tz=timezone.utc).date(), raw_date
    elif isinstance(raw_date, datetime):
        return raw_date.date(), int(raw_date.replace(tzinfo=raw_date.tzinfo or timezone.utc).timestamp())
    elif isinstance(raw_date, date_cls):
        epoch = int(datetime(raw_date.year, raw_date.month, raw_date.day, tzinfo=timezone.utc).timestamp())
        return raw_date, epoch

    now = datetime.now(timezone.utc)
    return now.date(), int(now.timestamp())


def parse_post(path: Path, *, site_url: str, base_path: str) -> Post | None:
    content = path.read_text(encoding="utf-8")
    start = content.find(FRONTMATTER_DELIM)
    end = content.find(FRONTMATTER_DELIM, start + len(FRONTMATTER_DELIM)) if start >= 0 else -1
    if start < 0 or end < 0:
        print(f"skipping {path}: no frontmatter block", file=sys.stderr)
        return None

    frontmatter = tomllib.loads(content[start + len(FRONTMATTER_DELIM) : end])
    body = content[end + len(FRONTMATTER_DELIM) :].strip()

    post_date, epoch = parse_date(frontmatter.get("date"))

    short_name = path.stem

    return Post(
        title=str(frontmatter.get("title") or short_name),
        date=post_date,
        epoch=epoch,
        message_id=str(frontmatter.get("message_id") or short_name),
        url=frontmatter.get("url"),
        permalink=permalink_for(short_name, site_url=site_url, base_path=base_path),
        description=body,
        short_name=short_name,
        post_type=str(frontmatter.get("type") or "link"),
    )


def load_posts(
    pages_dir: Path, *, site_url: str, base_path: str, types: set[str] | None = None
) -> list[Post]:
    posts = [
        post
        for path in sorted(pages_dir.glob("*.md"))
        if (post := parse_post(path, site_url=site_url, base_path=base_path)) is not None
        and (types is None or post.post_type in types)
    ]
    posts.sort(key=lambda p: (p.epoch, p.message_id), reverse=True)
    return posts
