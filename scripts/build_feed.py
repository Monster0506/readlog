from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from _posts import Post, load_posts


def render_feed(posts: list[Post], *, title: str, site_url: str, base_path: str, description: str) -> str:
    items = []
    for post in posts:
        pub_date = datetime(post.date.year, post.date.month, post.date.day, tzinfo=timezone.utc)
        items.append(
            "<item>"
            f"<title>{escape(post.title)}</title>"
            f"<link>{escape(post.permalink)}</link>"
            f"<guid isPermaLink=\"true\">{escape(post.permalink)}</guid>"
            f"<pubDate>{pub_date.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>"
            f"<description><![CDATA[{post.description}]]></description>"
            "</item>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "<channel>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(site_url + base_path)}</link>"
        f"<description>{escape(description)}</description>"
        + "".join(items)
        + "</channel>\n</rss>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pages_dir", type=Path, help="Directory of *.md posts (e.g. _pages)")
    parser.add_argument("--type", dest="post_type", required=True, help='Only include posts with this frontmatter type, e.g. "link" or "roundup"')
    parser.add_argument("--site-url", required=True, help="Site origin, e.g. https://user.github.io")
    parser.add_argument("--base-path", default="", help="Root-relative base path, e.g. /readlog")
    parser.add_argument("--title", default="readlog", help="Feed <title>")
    parser.add_argument("--description", default="", help="Feed <description>")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output feed.xml path")
    args = parser.parse_args()

    if not args.pages_dir.is_dir():
        print(f"{args.pages_dir} is not a directory", file=sys.stderr)
        return 1

    posts = load_posts(args.pages_dir, site_url=args.site_url, base_path=args.base_path, types={args.post_type})
    feed_xml = render_feed(
        posts, title=args.title, site_url=args.site_url, base_path=args.base_path, description=args.description
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(feed_xml, encoding="utf-8")
    print(f"Wrote {len(posts)} item(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
