"""
notification_tools.py — Aria Code push notification dispatcher.

Channels (tried in order when configured):
  1. macOS native notification  — always available on macOS, zero-config
  2. Webhook                    — 企业微信/飞书/Slack/custom HTTP POST
  3. Email (SMTP)               — opt-in, requires SMTP config

Configuration (in ~/.arthera/config.json):
    "notify_macos":  true                        # default true on macOS
    "notify_webhook": "https://..."              # webhook URL
    "notify_email":  {                           # optional SMTP
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username":  "you@gmail.com",
        "password":  "...",                      # prefer SMTP_PASSWORD env var
        "to":        "you@gmail.com"
    }

Usage:
    from notification_tools import send_notification
    send_notification("HSBC 触发预警", "现价 USD 79.5 已跌破目标 80.0")
"""

from __future__ import annotations

import json
import logging
import os
import platform
import smtplib
import subprocess
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib import request as _urllib_request

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".arthera" / "config.json"


def _load_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── Channel implementations ──────────────────────────────────────────────────

def _notify_macos(title: str, body: str) -> bool:
    """Send a macOS Notification Center alert via osascript."""
    if platform.system() != "Darwin":
        return False
    try:
        script = (
            f'display notification {json.dumps(body)} '
            f'with title {json.dumps(title)} '
            f'subtitle "Aria Code"'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug("macOS notification failed: %s", e)
        return False


def _notify_webhook(url: str, title: str, body: str) -> bool:
    """
    POST to a webhook URL.  Auto-detects format:
      - 企业微信机器人  (qyapi.weixin.qq.com)   → { msgtype: text, text: { content } }
      - 飞书机器人      (open.feishu.cn)         → { msg_type: text, content: { text } }
      - Slack           (hooks.slack.com)         → { text }
      - 钉钉            (oapi.dingtalk.com)       → { msgtype: text, text: { content } }
      - 其他                                      → { title, body }
    """
    try:
        message = f"【{title}】\n{body}"
        if "qyapi.weixin.qq.com" in url:
            payload = {"msgtype": "text", "text": {"content": message}}
        elif "open.feishu.cn" in url or "feishu" in url or "larksuite.com" in url:
            # 飞书交互卡片：带颜色标题 + Markdown 正文
            color = "red" if any(w in title.lower() for w in ("预警", "alert", "错误", "error", "熔断")) else \
                    "green" if any(w in title.lower() for w in ("晨报", "brief", "完成", "done")) else "blue"
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": color,
                    },
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": body[:2000]}},
                        {"tag": "hr"},
                        {"tag": "note", "elements": [
                            {"tag": "plain_text", "content": "Aria Code · " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}
                        ]},
                    ],
                },
            }
        elif "hooks.slack.com" in url:
            payload = {"text": message}
        elif "oapi.dingtalk.com" in url:
            payload = {"msgtype": "text", "text": {"content": message}}
        else:
            payload = {"title": title, "body": body, "text": message}

        data = json.dumps(payload).encode("utf-8")
        req = _urllib_request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=8) as resp:
            return resp.status < 400
    except Exception as e:
        logger.debug("Webhook notification failed (%s): %s", url[:40], e)
        return False


def _notify_telegram(token: str, chat_ids: str, title: str, body: str) -> bool:
    """Send a Telegram message to all allowed chat IDs via Bot API."""
    import urllib.request as _req
    import urllib.parse as _parse

    ids = [cid.strip() for cid in chat_ids.replace(";", ",").split(",") if cid.strip()]
    if not ids:
        return False

    text = f"*{title}*\n{body}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = False
    for chat_id in ids:
        data = _parse.urlencode({
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }).encode()
        req = _req.Request(url, data=data, method="POST")
        try:
            with _req.urlopen(req, timeout=8) as resp:
                if resp.status < 400:
                    success = True
        except Exception as e:
            logger.debug("Telegram notification failed for chat_id %s: %s", chat_id, e)
    return success


def _notify_email(cfg: dict, title: str, body: str) -> bool:
    """Send a plain-text email via SMTP."""
    try:
        host     = cfg.get("smtp_host", "smtp.gmail.com")
        port     = int(cfg.get("smtp_port", 587))
        username = cfg.get("username", "")
        password = os.getenv("SMTP_PASSWORD") or cfg.get("password", "")
        to_addr  = cfg.get("to", username)
        if not (username and password and to_addr):
            return False
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"[Aria] {title}"
        msg["From"]    = username
        msg["To"]      = to_addr
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.sendmail(username, [to_addr], msg.as_string())
        return True
    except Exception as e:
        logger.debug("Email notification failed: %s", e)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def send_notification(
    title: str,
    body: str,
    *,
    config: Optional[dict] = None,
) -> dict:
    """
    Dispatch a notification to all configured channels.

    Returns a dict with which channels succeeded.
    """
    cfg = config or _load_config()
    results: dict[str, bool] = {}

    # 1. macOS native (default on when running on macOS)
    if cfg.get("notify_macos", platform.system() == "Darwin"):
        results["macos"] = _notify_macos(title, body)

    # 2. Telegram Bot
    tg_token    = os.getenv("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token", "")
    tg_chat_ids = os.getenv("TELEGRAM_ALLOWED_IDS") or cfg.get("telegram_chat_ids", "")
    if tg_token and tg_chat_ids and tg_token != "your_bot_token_here":
        results["telegram"] = _notify_telegram(tg_token, tg_chat_ids, title, body)

    # 3. Webhook (企业微信 / 飞书 / Slack / custom)
    webhook_url = cfg.get("notify_webhook") or os.getenv("ARIA_NOTIFY_WEBHOOK")
    if webhook_url:
        results["webhook"] = _notify_webhook(webhook_url, title, body)

    # 4. Email
    email_cfg = cfg.get("notify_email")
    if email_cfg and isinstance(email_cfg, dict):
        results["email"] = _notify_email(email_cfg, title, body)

    if not results:
        logger.debug(
            "No notification channels configured. "
            "Set TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_IDS, "
            "notify_webhook in ~/.arthera/config.json, or ARIA_NOTIFY_WEBHOOK env var."
        )

    return {
        "sent":     any(results.values()),
        "channels": results,
        "title":    title,
        "body":     body,
    }


def send_alert_notification(alert: dict) -> dict:
    """Convenience wrapper for price alert triggers."""
    sym   = alert.get("symbol", "")
    cond  = alert.get("condition", "")
    tgt   = alert.get("price", "")
    cur   = alert.get("triggered_price", "N/A")
    label = alert.get("label") or f"{sym} 价格预警"

    cond_cn = {
        "gt":         f"突破 {tgt}（现价 {cur}）",
        "lt":         f"跌破 {tgt}（现价 {cur}）",
        "cross_up":   f"上穿 {tgt}（现价 {cur}）",
        "cross_down": f"下穿 {tgt}（现价 {cur}）",
    }.get(cond, f"触发条件 {cond} ≈ {tgt}，现价 {cur}")

    return send_notification(
        title=label,
        body=f"{sym}  {cond_cn}\n触发时间：{alert.get('triggered_at', '')}",
    )
