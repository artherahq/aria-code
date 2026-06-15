"""
computer_use_tools.py — Browser automation + desktop control tools for Aria Code.

Provides three LOCAL_TOOLS-compatible tool functions:
  browser_navigate     Open a URL and return page text + links (Playwright with requests fallback)
  browser_screenshot   Navigate to URL and capture a full-page screenshot
  computer_screenshot  Capture a screenshot of the current desktop
  computer_action      Control mouse / keyboard (click, type, scroll, move, hotkey)

Screenshot tools store the image in _PENDING_VISION_IMAGE so the agent loop can
inject it into the follow-up user message for vision-capable models.

Install:
    pip install playwright mss pyautogui pillow
    playwright install chromium
"""

from __future__ import annotations

import base64
import io
import logging
import os
import subprocess
import sys
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level slot for the last screenshot (base64 PNG).
# build_tool_followup in runtime/agent_loop.py checks this and injects
# the image into the follow-up user message for vision models.
_PENDING_VISION_IMAGE: Optional[str] = None   # base64-encoded PNG


def _store_screenshot(b64: str) -> None:
    global _PENDING_VISION_IMAGE
    _PENDING_VISION_IMAGE = b64


def pop_pending_vision_image() -> Optional[str]:
    """Consume and return the pending screenshot (None if none pending)."""
    global _PENDING_VISION_IMAGE
    val = _PENDING_VISION_IMAGE
    _PENDING_VISION_IMAGE = None
    return val


# ── Browser navigate ──────────────────────────────────────────────────────────

def _tool_browser_navigate(params: dict) -> dict:
    """
    Open a URL in a headless browser and return the page text content + links.
    Uses Playwright if installed; falls back to requests+BeautifulSoup.
    """
    url = params.get("url", "").strip()
    if not url:
        return {"success": False, "error": "Missing 'url' parameter"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_chars = min(int(params.get("max_chars", 12000)), 40000)
    wait_for = params.get("wait_for", "domcontentloaded")  # or "networkidle"

    # --- Playwright path ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                )
            )
            page.goto(url, wait_until=wait_for, timeout=30000)
            title = page.title()
            text = page.inner_text("body")[:max_chars]
            # Collect visible links
            links = []
            for a in page.query_selector_all("a[href]")[:30]:
                href = a.get_attribute("href") or ""
                label = (a.inner_text() or "").strip()[:60]
                if href.startswith("http") and label:
                    links.append(f"[{label}]({href})")
            browser.close()
        return {
            "success": True,
            "data": {
                "url": url,
                "title": title,
                "text": text,
                "links": links[:20],
                "length": len(text),
                "engine": "playwright",
            },
        }
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("playwright navigate failed: %s — falling back to requests", exc)

    # --- requests fallback ---
    try:
        import re
        import requests
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 Chrome/124.0"},
            timeout=15,
            verify=False,
        )
        r.raise_for_status()
        raw = r.text
        text = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s{3,}", "\n", text).strip()
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.DOTALL)
        title = title_m.group(1).strip() if title_m else url
        return {
            "success": True,
            "data": {
                "url": url,
                "title": title,
                "text": text[:max_chars],
                "links": [],
                "length": len(text),
                "engine": "requests",
            },
        }
    except Exception as exc:
        return {"success": False, "error": f"Navigation failed: {exc}"}


# ── Browser screenshot ────────────────────────────────────────────────────────

def _tool_browser_screenshot(params: dict) -> dict:
    """
    Navigate to a URL and capture a full-page screenshot.
    Stores the image in _PENDING_VISION_IMAGE for vision-model injection.
    """
    url = params.get("url", "").strip()
    if not url:
        return {"success": False, "error": "Missing 'url' parameter"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    wait_for = params.get("wait_for", "networkidle")

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until=wait_for, timeout=30000)
            title = page.title()
            png_bytes = page.screenshot(full_page=False)
            browser.close()

        b64 = base64.b64encode(png_bytes).decode()
        _store_screenshot(b64)
        size_kb = len(png_bytes) // 1024
        return {
            "success": True,
            "data": {
                "url": url,
                "title": title,
                "size_kb": size_kb,
                "width": 1280,
                "height": 900,
                "note": f"Screenshot captured ({size_kb} KB). Image attached to next message.",
            },
        }
    except ImportError:
        return {
            "success": False,
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }
    except Exception as exc:
        return {"success": False, "error": f"Browser screenshot failed: {exc}"}


# ── Desktop screenshot ────────────────────────────────────────────────────────

def _tool_computer_screenshot(params: dict) -> dict:
    """
    Capture a screenshot of the current desktop screen.
    Stores the image in _PENDING_VISION_IMAGE for vision-model injection.
    """
    monitor_idx = int(params.get("monitor", 1))  # 0 = all screens, 1 = primary
    max_dim = int(params.get("max_dim", 1920))    # resize if larger

    # --- mss path (fast, cross-platform) ---
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            monitors = sct.monitors
            mon = monitors[monitor_idx] if monitor_idx < len(monitors) else monitors[1]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

        # Resize if too large (keep aspect ratio)
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        _store_screenshot(b64)
        size_kb = len(buf.getvalue()) // 1024
        return {
            "success": True,
            "data": {
                "width": img.width,
                "height": img.height,
                "size_kb": size_kb,
                "note": f"Desktop screenshot captured ({img.width}×{img.height}, {size_kb} KB). Image attached.",
            },
        }
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("mss screenshot failed: %s", exc)

    # --- screencapture fallback (macOS) ---
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        subprocess.run(["screencapture", "-x", tmp], check=True, timeout=10)
        from PIL import Image
        img = Image.open(tmp)
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        _store_screenshot(b64)
        os.unlink(tmp)
        return {
            "success": True,
            "data": {
                "width": img.width,
                "height": img.height,
                "size_kb": len(buf.getvalue()) // 1024,
                "note": f"Screenshot captured ({img.width}×{img.height}). Image attached.",
            },
        }
    except Exception as exc:
        return {"success": False, "error": f"Screenshot failed: {exc}. Install: pip install mss pillow"}


# ── Computer action ───────────────────────────────────────────────────────────

def _tool_computer_action(params: dict) -> dict:
    """
    Control the mouse or keyboard. Supported actions:
      click (x, y)         — left-click at screen coordinates
      right_click (x, y)   — right-click
      double_click (x, y)  — double-click
      move (x, y)          — move mouse without clicking
      type (text)          — type text via keyboard
      key (key)            — press a key or combo: "enter", "ctrl+c", "cmd+space"
      scroll (x, y, dy)    — scroll wheel: dy > 0 = down, dy < 0 = up
      drag (x, y, ex, ey)  — drag from (x,y) to (ex,ey)
    """
    action = params.get("action", "").lower()
    x = int(params.get("x", 0))
    y = int(params.get("y", 0))
    text = params.get("text", "")
    key = params.get("key", "")
    dy = int(params.get("dy", 3))
    ex = int(params.get("ex", x))
    ey = int(params.get("ey", y))

    if not action:
        return {"success": False, "error": "Missing 'action' parameter"}

    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05

        if action == "click":
            pyautogui.click(x, y)
            return {"success": True, "data": {"action": "click", "x": x, "y": y}}

        elif action == "right_click":
            pyautogui.rightClick(x, y)
            return {"success": True, "data": {"action": "right_click", "x": x, "y": y}}

        elif action == "double_click":
            pyautogui.doubleClick(x, y)
            return {"success": True, "data": {"action": "double_click", "x": x, "y": y}}

        elif action == "move":
            pyautogui.moveTo(x, y, duration=0.2)
            return {"success": True, "data": {"action": "move", "x": x, "y": y}}

        elif action == "type":
            if not text:
                return {"success": False, "error": "Missing 'text' for type action"}
            pyautogui.typewrite(text, interval=0.03)
            return {"success": True, "data": {"action": "type", "chars": len(text)}}

        elif action == "key":
            if not key:
                return {"success": False, "error": "Missing 'key' for key action"}
            # Handle combos like "ctrl+c", "cmd+space"
            parts = [k.strip() for k in key.replace("+", " ").split() if k.strip()]
            if len(parts) > 1:
                pyautogui.hotkey(*parts)
            else:
                pyautogui.press(parts[0])
            return {"success": True, "data": {"action": "key", "key": key}}

        elif action == "scroll":
            pyautogui.scroll(dy, x=x, y=y)
            return {"success": True, "data": {"action": "scroll", "x": x, "y": y, "dy": dy}}

        elif action == "drag":
            pyautogui.moveTo(x, y)
            pyautogui.dragTo(ex, ey, duration=0.5, button="left")
            return {"success": True, "data": {"action": "drag", "from": [x, y], "to": [ex, ey]}}

        else:
            return {
                "success": False,
                "error": (
                    f"Unknown action '{action}'. "
                    "Valid: click, right_click, double_click, move, type, key, scroll, drag"
                ),
            }
    except ImportError:
        return {
            "success": False,
            "error": "pyautogui not installed. Run: pip install pyautogui",
        }
    except pyautogui.FailSafeException:
        return {
            "success": False,
            "error": "Fail-safe triggered: mouse moved to screen corner. Move away and retry.",
        }
    except Exception as exc:
        return {"success": False, "error": f"computer_action failed: {exc}"}


# ── Tool schemas (Ollama / OpenAI function-calling format) ────────────────────

COMPUTER_USE_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": (
                "Open a URL in a headless browser and return the full page text content and links. "
                "Use this to read web pages, documentation, news articles, financial data, or any URL. "
                "Supports JavaScript-rendered pages (via Playwright) with fallback to requests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to navigate to, e.g. https://example.com",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters of page text to return (default 12000, max 40000)",
                    },
                    "wait_for": {
                        "type": "string",
                        "enum": ["domcontentloaded", "networkidle", "load"],
                        "description": "When to consider navigation done (default: domcontentloaded)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": (
                "Navigate to a URL and capture a visual screenshot of the page. "
                "The screenshot is automatically attached to the next message for visual analysis. "
                "Use when you need to SEE the visual layout, charts, images, or UI of a web page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to screenshot",
                    },
                    "wait_for": {
                        "type": "string",
                        "enum": ["domcontentloaded", "networkidle", "load"],
                        "description": "Wait condition before capturing (default: networkidle)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_screenshot",
            "description": (
                "Capture a screenshot of the current desktop screen. "
                "The image is automatically attached to the next message so you can see what's on screen. "
                "Use this before computer_action to understand where to click."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "integer",
                        "description": "Monitor index: 1 = primary (default), 0 = all monitors",
                    },
                    "max_dim": {
                        "type": "integer",
                        "description": "Max pixel dimension for resize (default 1920)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_action",
            "description": (
                "Control the mouse and keyboard to interact with the desktop. "
                "Always take a computer_screenshot first to see the current screen state. "
                "Actions: click, right_click, double_click, move, type, key, scroll, drag."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "click", "right_click", "double_click", "move",
                            "type", "key", "scroll", "drag",
                        ],
                        "description": "Action to perform",
                    },
                    "x": {
                        "type": "integer",
                        "description": "Screen X coordinate (pixels from left)",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Screen Y coordinate (pixels from top)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type (for 'type' action)",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key or combo to press, e.g. 'enter', 'ctrl+c', 'cmd+space'",
                    },
                    "dy": {
                        "type": "integer",
                        "description": "Scroll amount: positive = down, negative = up (for 'scroll' action)",
                    },
                    "ex": {
                        "type": "integer",
                        "description": "Drag end X coordinate (for 'drag' action)",
                    },
                    "ey": {
                        "type": "integer",
                        "description": "Drag end Y coordinate (for 'drag' action)",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


# ── Tool registry for LOCAL_TOOLS ─────────────────────────────────────────────

COMPUTER_USE_TOOLS = {
    "browser_navigate":   (_tool_browser_navigate,   "Open a URL and return page text content"),
    "browser_screenshot": (_tool_browser_screenshot, "Navigate to URL and capture a screenshot"),
    "computer_screenshot":(_tool_computer_screenshot,"Capture a screenshot of the current desktop"),
    "computer_action":    (_tool_computer_action,    "Control mouse/keyboard (click, type, scroll)"),
}
