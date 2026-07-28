"""Cloud-side: fetch central feeds and deliver a digest to Feishu.

Runs on GitHub Actions — no local machine required.
Fetches feed JSONs from the central repo, formats a readable digest,
and pushes to the user's Feishu group via webhook.

Usage:
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/... python scripts/cloud_deliver.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import httpx

RAW_BASE = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main"
MIRROR_BASES = [
    RAW_BASE,
    "https://cdn.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://fastly.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://gcore.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://testingcf.jsdelivr.net/gh/Benboerba620/ai-signal@main",
]

FEED_FILES = {
    "x": "feeds/feed-x.json",
    "podcasts": "feeds/feed-podcasts.json",
    "papers": "feeds/feed-arxiv.json",
    "articles": "feeds/feed-blogs.json",
}


def fetch_feed(feed_path):
    """Fetch a feed JSON from mirrors, trying each until one works."""
    for base in MIRROR_BASES:
        url = f"{base}/{feed_path}"
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            if resp.is_success:
                return resp.json()
        except Exception:
            continue
    return None


def format_digest():
    """Fetch all feeds and format into a markdown digest."""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")
    lines = [f"# AI Signal 日报 — {date_str}\n"]

    # --- X / Twitter ---
    x_data = fetch_feed(FEED_FILES["x"])
    x_accounts = (x_data or {}).get("x", [])
    tweet_count = 0
    x_lines = []
    for acct in x_accounts:
        tweets = acct.get("tweets", [])
        if not tweets:
            continue
        for t in tweets:
            tweet_count += 1
            text = t.get("text", "").strip()
            url = t.get("url", "")
            handle = acct.get("handle", "?")
            name = acct.get("name", "")
            # Truncate long tweets
            if len(text) > 500:
                text = text[:500] + "..."
            x_lines.append(f"**X{tweet_count} @{handle}** ({name})\n{text}")
            if url:
                x_lines.append(f"[原文链接]({url})")
            x_lines.append("")

    if x_lines:
        lines.append(f"## X / Twitter（{tweet_count} 条）\n")
        lines.extend(x_lines)
    else:
        lines.append("## X / Twitter\n\n今日暂无新推文\n")

    # --- Podcasts ---
    pod_data = fetch_feed(FEED_FILES["podcasts"])
    podcasts = (pod_data or {}).get("podcasts", [])
    pod_lines = []
    for i, p in enumerate(podcasts, 1):
        title = p.get("title", "").strip()
        channel = p.get("channel", "")
        link = p.get("link", "")
        desc = p.get("description", "").strip()
        if len(desc) > 300:
            desc = desc[:300] + "..."
        pod_lines.append(f"**P{i} {channel}**\n{title}")
        if desc:
            pod_lines.append(f"> {desc}")
        if link:
            pod_lines.append(f"[收听链接]({link})")
        pod_lines.append("")

    if pod_lines:
        lines.append(f"## 播客（{len(podcasts)} 期）\n")
        lines.extend(pod_lines)
    else:
        lines.append("## 播客\n\n今日暂无新播客\n")

    # --- Blog Articles ---
    blog_data = fetch_feed(FEED_FILES["articles"])
    articles = (blog_data or {}).get("articles", [])
    art_lines = []
    for i, a in enumerate(articles, 1):
        source = a.get("source_name", a.get("source", ""))
        title = a.get("title", "").strip()
        url = a.get("url", "")
        summary = a.get("summary", "").strip()
        if len(summary) > 400:
            summary = summary[:400] + "..."
        art_lines.append(f"**B{i} {source}**\n{title}")
        if summary:
            art_lines.append(f"> {summary}")
        if url:
            art_lines.append(f"[阅读原文]({url})")
        art_lines.append("")

    if art_lines:
        lines.append(f"## 官方博客（{len(articles)} 篇）\n")
        lines.extend(art_lines)
    else:
        lines.append("## 官方博客\n\n今日暂无新文章\n")

    # --- arXiv Papers ---
    paper_data = fetch_feed(FEED_FILES["papers"])
    papers = (paper_data or {}).get("papers", [])
    # Show top 15 papers to keep digest manageable
    shown = min(len(papers), 15)
    paper_lines = []
    for i, p in enumerate(papers[:shown], 1):
        title = p.get("title", "").strip()
        authors = p.get("authors", [])
        if authors:
            author_str = ", ".join(authors[:3])
            if len(authors) > 3:
                author_str += " et al."
        else:
            author_str = ""
        abs_url = p.get("abs_url", p.get("pdf_url", ""))
        cat = p.get("primary_category", "")
        abstract = p.get("abstract", "").strip()
        if len(abstract) > 200:
            abstract = abstract[:200] + "..."

        paper_lines.append(f"**R{i} [{cat}]** {title}")
        if author_str:
            paper_lines.append(f"Authors: {author_str}")
        if abstract:
            paper_lines.append(f"> {abstract}")
        if abs_url:
            paper_lines.append(f"[论文链接]({abs_url})")
        paper_lines.append("")

    if paper_lines:
        extra = f"（共 {len(papers)} 篇，展示前 {shown} 篇）" if len(papers) > shown else f"（{len(papers)} 篇）"
        lines.append(f"## arXiv 论文{extra}\n")
        lines.extend(paper_lines)

    lines.append("---")
    lines.append(f"*由 GitHub Actions 云端推送 · {now.strftime('%Y-%m-%d %H:%M')} CST*")
    lines.append("*电脑开机后将收到 AI 精编版日报*")

    return "\n".join(lines)


def send_feishu(text, webhook_url):
    """Send digest to Feishu via interactive card with markdown."""
    title = "AI Signal 日报"
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            break
        if stripped:
            break

    # Split into chunks of ~4000 bytes for Feishu markdown element limit
    max_bytes = 4000
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = current + line + "\n"
        if len(candidate.encode("utf-8")) > max_bytes and current:
            chunks.append(current)
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current)

    elements = []
    for i, chunk in enumerate(chunks):
        elements.append({"tag": "markdown", "content": chunk})
        if i < len(chunks) - 1:
            elements.append({"tag": "hr"})

    resp = httpx.post(
        webhook_url,
        json={
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": elements,
            },
        },
        timeout=30,
    )
    if resp.is_success:
        r = resp.json()
        if r.get("code") == 0 or r.get("StatusCode") == 0:
            print(f"[cloud_deliver] Sent to Feishu ({len(chunks)} chunks)")
            return True
        print(f"[cloud_deliver] Feishu error: {r}", file=sys.stderr)
    else:
        print(f"[cloud_deliver] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    return False


def main():
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        print("[cloud_deliver] FEISHU_WEBHOOK_URL not set", file=sys.stderr)
        sys.exit(1)

    print("[cloud_deliver] Fetching feeds...")
    digest = format_digest()
    print(f"[cloud_deliver] Digest formatted ({len(digest)} chars)")

    ok = send_feishu(digest, webhook_url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
