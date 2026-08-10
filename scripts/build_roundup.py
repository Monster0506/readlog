from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _posts import Post, load_posts


def most_recent_completed_week(today: date) -> tuple[date, date]:
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday, last_sunday


def format_week_title(start: date, end: date) -> str:
    if start.month == end.month:
        return f"Week of {start.strftime('%b')} {start.day}-{end.day}, {end.year}"
    return f"Week of {start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {end.year}"


def render_roundup(posts: list[Post], *, start: date, end: date) -> str:
    lines = [f"- [{post.title}]({post.permalink})" + (f" - {post.description}" if post.description else "") for post in posts]

    end_epoch = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
    frontmatter = (
        "+++\n"
        'type="roundup"\n'
        f'title="{format_week_title(start, end)}"\n'
        f"date={end_epoch}\n"
        "+++\n\n"
    )
    return frontmatter + "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pages-dir", type=Path, default=Path("_pages"))
    parser.add_argument("--site-url", required=True, help="Site origin, e.g. https://user.github.io")
    parser.add_argument("--base-path", default="", help="Root-relative base path, e.g. /readlog")
    parser.add_argument(
        "--week-start",
        type=date.fromisoformat,
        default=None,
        help="Override the week start (must be a Monday, ISO format). Default: most recently completed Mon-Sun week.",
    )
    args = parser.parse_args()

    if args.week_start is not None:
        if args.week_start.weekday() != 0:
            print("--week-start must be a Monday", file=sys.stderr)
            return 1
        start, end = args.week_start, args.week_start + timedelta(days=6)
    else:
        start, end = most_recent_completed_week(datetime.now(timezone.utc).date())

    if not args.pages_dir.is_dir():
        print(f"{args.pages_dir} is not a directory", file=sys.stderr)
        return 1

    all_posts = load_posts(args.pages_dir, site_url=args.site_url, base_path=args.base_path, types={"link"})
    week_posts = [p for p in all_posts if start <= p.date <= end]
    week_posts.sort(key=lambda p: (p.epoch, p.message_id))

    if not week_posts:
        print(f"No links captured for {start.isoformat()}..{end.isoformat()}; skipping roundup.")
        return 0

    output = args.pages_dir / f"{start.isoformat()}.md"
    output.write_text(render_roundup(week_posts, start=start, end=end), encoding="utf-8")
    print(f"Wrote {len(week_posts)} link(s) to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
