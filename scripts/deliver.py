"""Subscriber-side: deliver digest text via Telegram / Feishu / email / PushPlus / WeCom(企业微信).

Reads delivery config from ~/.ai-signal/config.json and API keys from
~/.ai-signal/.env

Usage:
    echo "digest text" | python scripts/deliver.py
    python scripts/deliver.py --message "digest text"
    python scripts/deliver.py --file /path/to/digest.md
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).parent
USER_DIR = Path.home() / ".ai-signal"
CONFIG_PATH = USER_DIR / "config.json"
ENV_PATH = USER_DIR / ".env"

TELEGRAM_MAX_LEN = 4000


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    print(msg, file=sys.stderr)


def load_env():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def split_message(text, max_len=TELEGRAM_MAX_LEN):
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len * 0.3:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks


def send_telegram(text, bot_token, chat_id):
    for chunk in split_message(text):
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=30,
        )
        if not resp.is_success:
            err = resp.json()
            if "can't parse" in err.get("description", ""):
                httpx.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                           json={"chat_id": chat_id, "text": chunk,
                                 "disable_web_page_preview": True}, timeout=30)
            else:
                log(f"❌ Telegram: {err.get('description', resp.text)}")
                return False
        import time; time.sleep(0.3)
    return True


def send_feishu(text, webhook_url):
    """Send digest to Feishu (飞书) group via custom bot webhook.

    Uses interactive card format with markdown for better readability.
    Long content is split into multiple markdown elements within the card.
    """
    from datetime import datetime

    title = f"AI Signal 日报 — {datetime.now().strftime('%Y-%m-%d')}"
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            break
        if stripped:
            break

    # Feishu markdown element limit: ~4000 bytes; split into chunks
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
            return True
        log(f"❌ Feishu: {r}")
    else:
        log(f"❌ Feishu: HTTP {resp.status_code} — {resp.text[:200]}")
    return False


def send_email(text, api_key, to_email):
    from datetime import datetime
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": "Daily Digest <digest@resend.dev>", "to": [to_email],
              "subject": f"Daily Digest — {datetime.now().strftime('%Y-%m-%d')}",
              "text": text},
        timeout=30,
    )
    return resp.is_success


def send_pushplus(text, token):
    """Send digest to personal WeChat via PushPlus (pushplus.plus)."""
    from datetime import datetime
    title = f"AI Signal 日报 — {datetime.now().strftime('%Y-%m-%d')}"
    # Try to extract a better title from the first heading
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            break
        if stripped:
            break

    # PushPlus content limit is generous; split if extremely long
    max_content = 30000
    chunks = split_message(text, max_content) if len(text) > max_content else [text]

    for i, chunk in enumerate(chunks):
        chunk_title = title if len(chunks) == 1 else f"{title} ({i+1}/{len(chunks)})"
        resp = httpx.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": chunk_title,
                "content": chunk,
                "template": "markdown",
            },
            timeout=30,
        )
        if resp.is_success:
            r = resp.json()
            if r.get("code") != 200:
                log(f"❌ PushPlus: {r.get('msg', resp.text)}")
                return False
        else:
            log(f"❌ PushPlus: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
    return True


def send_wecom(text, webhook_url):
    """Send digest to WeCom group robot (企业微信群机器人) via webhook.

    WeCom markdown supports: bold, links, lists, quotes, color tags.
    Content limit is 4096 bytes per message; long text is split automatically.
    """
    import time
    from datetime import datetime

    title = f"AI Signal 日报 — {datetime.now().strftime('%Y-%m-%d')}"
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            break
        if stripped:
            break

    # WeCom markdown limit: 4096 bytes; use 3800 for safety with UTF-8
    max_bytes = 3800
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

    for i, chunk in enumerate(chunks):
        header = f"**{title}**" + (f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else "")
        content = f"{header}\n\n{chunk}" if i == 0 else f"{header}\n\n{chunk}"
        resp = httpx.post(
            webhook_url,
            json={
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
            timeout=30,
        )
        if resp.is_success:
            r = resp.json()
            if r.get("errcode") != 0:
                log(f"❌ WeCom: {r.get('errmsg', resp.text)}")
                return False
        else:
            log(f"❌ WeCom: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
        if i < len(chunks) - 1:
            time.sleep(0.5)
    return True


def mark_delivered(mark_file):
    if not mark_file:
        return
    import subprocess
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "mark_delivered.py"), "--file", mark_file],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        log("✅ Marked digest as delivered")
    else:
        log(f"⚠️ Could not mark delivered: {result.stderr or result.stdout}")


def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", "-m", type=str)
    parser.add_argument("--file", "-f", type=str)
    parser.add_argument("--mark-delivered-file", type=str,
                        help="Path to delivery-mark.json; marked only after successful delivery")
    args = parser.parse_args()

    if args.message:
        text = args.message
    elif args.file:
        text = Path(args.file).read_text("utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        log("No input. Use --message, --file, or pipe stdin.")
        sys.exit(1)

    if not text.strip():
        log("Empty digest, skipping.")
        return

    load_env()

    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text("utf-8-sig"))

    delivery = config.get("delivery", {"method": "stdout"})
    method = delivery.get("method", "stdout")

    if method == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = delivery.get("chat_id", "")
        if not token or not chat_id:
            log("❌ Set TELEGRAM_BOT_TOKEN in ~/.ai-signal/.env and chat_id in config.json")
            sys.exit(1)
        ok = send_telegram(text, token, chat_id)
        log("✅ Sent to Telegram" if ok else "❌ Telegram failed")
        if ok:
            mark_delivered(args.mark_delivered_file)

    elif method == "feishu":
        webhook = delivery.get("webhook_url", os.environ.get("FEISHU_WEBHOOK_URL", ""))
        if not webhook:
            log("❌ Set webhook_url in config.json delivery section")
            sys.exit(1)
        ok = send_feishu(text, webhook)
        log("✅ Sent to Feishu" if ok else "❌ Feishu failed")
        if ok:
            mark_delivered(args.mark_delivered_file)

    elif method == "email":
        api_key = os.environ.get("RESEND_API_KEY", "")
        email = delivery.get("email", "")
        if not api_key or not email:
            log("❌ Set RESEND_API_KEY in .env and email in config.json")
            sys.exit(1)
        ok = send_email(text, api_key, email)
        log("✅ Sent to email" if ok else "❌ Email failed")
        if ok:
            mark_delivered(args.mark_delivered_file)

    elif method == "pushplus":
        token = delivery.get("token", os.environ.get("PUSHPLUS_TOKEN", ""))
        if not token:
            log("❌ Set PUSHPLUS_TOKEN in ~/.ai-signal/.env or token in config.json delivery section")
            sys.exit(1)
        ok = send_pushplus(text, token)
        log("✅ Sent to WeChat via PushPlus" if ok else "❌ PushPlus failed")
        if ok:
            mark_delivered(args.mark_delivered_file)

    elif method == "wecom":
        webhook = delivery.get("webhook_url", os.environ.get("WECOM_WEBHOOK_URL", ""))
        if not webhook:
            log("❌ Set webhook_url in config.json delivery section or WECOM_WEBHOOK_URL in ~/.ai-signal/.env")
            sys.exit(1)
        ok = send_wecom(text, webhook)
        log("✅ Sent to WeCom (企业微信)" if ok else "❌ WeCom failed")
        if ok:
            mark_delivered(args.mark_delivered_file)

    else:
        print(text)
        mark_delivered(args.mark_delivered_file)


if __name__ == "__main__":
    main()
