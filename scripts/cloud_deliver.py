"""Cloud-side: fetch central feeds, AI-remix into Chinese digest, deliver to Feishu.

Runs on GitHub Actions — no local machine required.
Fetches feed JSONs from the central repo, uses DeepSeek API for AI remixing
(Chinese translation + editorial commentary + content curation),
and pushes to the user's Feishu group via webhook.

Usage:
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/... \
    DEEPSEEK_API_KEY=sk-... \
    python scripts/cloud_deliver.py
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


def format_raw_digest():
    """Fetch all feeds and format into a raw markdown digest for AI processing."""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")
    lines = [f"# AI Signal Raw Feeds - {date_str}\n"]

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
            domain = acct.get("domain", "")
            if len(text) > 500:
                text = text[:500] + "..."
            x_lines.append(f"X{tweet_count} @{handle} ({name}) [domain: {domain}]")
            x_lines.append(f"  Text: {text}")
            if url:
                x_lines.append(f"  URL: {url}")
            x_lines.append("")

    if x_lines:
        lines.append(f"## X / Twitter ({tweet_count} posts)\n")
        lines.extend(x_lines)
    else:
        lines.append("## X / Twitter\nNo new posts today.\n")

    # --- Podcasts ---
    pod_data = fetch_feed(FEED_FILES["podcasts"])
    podcasts = (pod_data or {}).get("podcasts", [])
    pod_lines = []
    for i, p in enumerate(podcasts, 1):
        title = p.get("title", "").strip()
        channel = p.get("channel", "")
        link = p.get("link", "")
        desc = p.get("description", "").strip()
        if len(desc) > 400:
            desc = desc[:400] + "..."
        pod_lines.append(f"P{i} [{channel}]")
        pod_lines.append(f"  Title: {title}")
        if desc:
            pod_lines.append(f"  Description: {desc}")
        if link:
            pod_lines.append(f"  URL: {link}")
        pod_lines.append("")

    if pod_lines:
        lines.append(f"## Podcasts ({len(podcasts)} episodes)\n")
        lines.extend(pod_lines)
    else:
        lines.append("## Podcasts\nNo new episodes today.\n")

    # --- Blog Articles ---
    blog_data = fetch_feed(FEED_FILES["articles"])
    articles = (blog_data or {}).get("articles", [])
    art_lines = []
    for i, a in enumerate(articles, 1):
        source = a.get("source_name", a.get("source", ""))
        title = a.get("title", "").strip()
        url = a.get("url", "")
        summary = a.get("summary", "").strip()
        if len(summary) > 500:
            summary = summary[:500] + "..."
        art_lines.append(f"B{i} [{source}]")
        art_lines.append(f"  Title: {title}")
        if summary:
            art_lines.append(f"  Summary: {summary}")
        if url:
            art_lines.append(f"  URL: {url}")
        art_lines.append("")

    if art_lines:
        lines.append(f"## Blog Articles ({len(articles)} posts)\n")
        lines.extend(art_lines)
    else:
        lines.append("## Blog Articles\nNo new articles today.\n")

    # --- arXiv Papers ---
    paper_data = fetch_feed(FEED_FILES["papers"])
    papers = (paper_data or {}).get("papers", [])
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
        if len(abstract) > 300:
            abstract = abstract[:300] + "..."

        paper_lines.append(f"R{i} [{cat}]")
        paper_lines.append(f"  Title: {title}")
        if author_str:
            paper_lines.append(f"  Authors: {author_str}")
        if abstract:
            paper_lines.append(f"  Abstract: {abstract}")
        if abs_url:
            paper_lines.append(f"  URL: {abs_url}")
        paper_lines.append("")

    if paper_lines:
        extra = f" (total {len(papers)}, showing top {shown})" if len(papers) > shown else f" ({len(papers)} papers)"
        lines.append(f"## arXiv Papers{extra}\n")
        lines.extend(paper_lines)

    return "\n".join(lines)


SYSTEM_PROMPT = """你是 AI Signal 日报的资深编辑，擅长将 AI 一线动态整理为简洁有力的中文日报。

你的任务：
1. 将所有英文内容翻译为中文，保留原文术语（如 LLM、RAG、agent、inference 等）和 URL
2. 每条内容用 3-5 句话概括，突出关键数据、观点和趋势
3. 筛选最重要的内容，去掉不重要或重复的条目。通常保留 5-10 条推文、5-8 个播客、全部博客、5-8 篇论文
4. 按以下格式排列并编号：

## X / Twitter
**X1 @handle（姓名）**
概括内容（3-5句）...
[原文链接](url)

**X2 @handle（姓名）**
概括内容（3-5句）...
[原文链接](url)

## 播客
**P1 频道名**
标题：xxx
概括内容（3-5句）...
[收听链接](url)

## 官方博客
**B1 来源**
标题：xxx
概括内容（3-5句）...
[阅读原文](url)

## arXiv 论文
**R1 [分类]**
标题：xxx
概括内容（3-5句）...
[论文链接](url)

5. 在开头（标题之后）用 2-3 句话概述今日核心主题和趋势
6. 保持 Markdown 格式
7. 不要添加多余的分隔线或页脚，我会在代码中添加"""


def remix_with_deepseek(raw_digest, api_key):
    """Use DeepSeek API to remix the raw digest into a curated Chinese digest."""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")

    user_prompt = f"""请将以下原始信息源整理为今日（{date_str}）的 AI Signal 中文日报。

原始信息源：

{raw_digest}"""

    print(f"[cloud_deliver] Calling DeepSeek API (input: {len(raw_digest)} chars)...")

    try:
        resp = httpx.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 8192,
                "stream": False,
            },
            timeout=120,
        )

        if resp.is_success:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            print(f"[cloud_deliver] DeepSeek OK: {usage.get('total_tokens', '?')} tokens used")
            # Prepend title
            title_line = f"# AI Signal 日报 - {date_str}\n"
            if not content.startswith("# AI Signal"):
                content = title_line + "\n" + content
            return content
        else:
            print(f"[cloud_deliver] DeepSeek API error: {resp.status_code} - {resp.text[:300]}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[cloud_deliver] DeepSeek API exception: {e}", file=sys.stderr)
        return None


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

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")

    print("[cloud_deliver] Fetching feeds...")
    raw_digest = format_raw_digest()
    print(f"[cloud_deliver] Raw digest formatted ({len(raw_digest)} chars)")

    if deepseek_key:
        print("[cloud_deliver] AI remixing with DeepSeek...")
        remixed = remix_with_deepseek(raw_digest, deepseek_key)
        if remixed:
            now = datetime.now(timezone(timedelta(hours=8)))
            digest = remixed + f"\n\n---\n*GitHub Actions AI 精编 - {now.strftime('%Y-%m-%d %H:%M')} CST*"
            print(f"[cloud_deliver] AI digest ready ({len(digest)} chars)")
        else:
            print("[cloud_deliver] AI remix failed, falling back to raw digest", file=sys.stderr)
            digest = raw_digest + "\n\n---\n*GitHub Actions - raw format (AI remix failed)*"
    else:
        print("[cloud_deliver] No DEEPSEEK_API_KEY, using raw format", file=sys.stderr)
        digest = raw_digest + "\n\n---\n*GitHub Actions - raw format (no AI)*"

    ok = send_feishu(digest, webhook_url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
