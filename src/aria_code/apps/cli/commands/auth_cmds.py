"""AuthCommandsMixin — Arthera backend auth: /login, /logout, /whoami.

Method bodies use aria_cli module globals (self.context.console, self.context.has_rich, _esc_watcher,
self.context.save_config, _print_error, asyncio, datetime) which are bound at import time by
aria_cli._rebind_mixin_globals(AuthCommandsMixin). getpass/aiohttp are imported
locally inside cmd_login, so this module needs no module-level imports.
"""

from __future__ import annotations


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional

def _esc_watcher(*args, **kwargs):
    from aria_code.aria_cli import _esc_watcher as fn
    return fn(*args, **kwargs)
def _print_error(*args, **kwargs):
    from aria_code.aria_cli import _print_error as fn
    return fn(*args, **kwargs)

import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class AuthCommandsMixin:
    """Mixin: authentication commands (/login, /logout, /whoami)."""

    async def cmd_login(self, args: str):
        """Login to Arthera backend.

        Usage: /login <email>           — prompts for password securely
               /login                   — prompts for both email and password
        """
        import getpass as _getpass
        import aiohttp

        parts = args.split()
        if parts:
            email = parts[0]
        else:
            try:
                prompt_fn = self.context.console.input if self.context.has_rich else input
                email = prompt_fn("  Email: ").strip()
            except (EOFError, KeyboardInterrupt):
                self.context.console.print("[dim]Cancelled[/dim]" if self.context.has_rich else "Cancelled")
                return
        if not email:
            self.context.console.print("[dim]Usage: /login <email>[/dim]" if self.context.has_rich else "Usage: /login <email>")
            return

        # Always prompt for password — never accept it as a CLI argument (security)
        try:
            _esc_watcher.pause()
            password = _getpass.getpass("  Password: ")
        except (EOFError, KeyboardInterrupt):
            self.context.console.print("[dim]Cancelled[/dim]" if self.context.has_rich else "Cancelled")
            return
        finally:
            _esc_watcher.resume()

        if not password:
            self.context.console.print("[red]Password cannot be empty[/red]" if self.context.has_rich else "Password cannot be empty")
            return

        if self.context.has_rich:
            self.context.console.print("[dim]Authenticating...[/dim]")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.terminal.api_url}/auth/login",
                    json={"email": email, "password": password},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    if resp.status == 200 and data.get("token"):
                        self.terminal.config["auth_token"] = data["token"]
                        user_id = data.get("user_id", data.get("uid", email))
                        self.terminal.config["user_id"] = user_id
                        # Store token expiry if provided
                        if data.get("expires_at"):
                            self.terminal.config["token_expires_at"] = data["expires_at"]
                        self.context.save_config(self.terminal.config)
                        self.context.console.print(f"[green]✓ Logged in as {user_id}[/green]" if self.context.has_rich
                                      else f"Logged in as {user_id}")
                    elif resp.status == 401:
                        _print_error("Invalid email or password", "login")
                    elif resp.status == 429:
                        _print_error("Too many login attempts — please wait before retrying", "login")
                    else:
                        err = data.get("error", data.get("message", f"Login failed (HTTP {resp.status})"))
                        _print_error(err, "login")
        except aiohttp.ClientConnectorError:
            _print_error(
                f"Cannot reach {self.terminal.api_url} — check your network connection or use /local on",
                "login"
            )
        except asyncio.TimeoutError:
            _print_error("Login request timed out (15s) — server may be unavailable", "login")
        except Exception as e:
            _print_error(f"Login error: {e}", "login")

    def cmd_logout(self, args: str):
        self.terminal.config["auth_token"] = None
        self.terminal.config["user_id"] = None
        self.terminal.config.pop("token_expires_at", None)
        self.context.save_config(self.terminal.config)
        self.context.console.print("[dim]Logged out[/dim]" if self.context.has_rich else "Logged out")

    def cmd_whoami(self, args: str):
        """Show current authentication status."""
        cfg = self.terminal.config
        user_id = cfg.get("user_id")
        token = cfg.get("auth_token")
        expires = cfg.get("token_expires_at")

        if not token:
            self.context.console.print("[dim]Not logged in — use /login <email>[/dim]" if self.context.has_rich
                          else "Not logged in")
            return

        if self.context.has_rich:
            self.context.console.print()
            self.context.console.print(f"  [dim]User:[/dim]    {user_id or 'unknown'}")
            self.context.console.print(f"  [dim]Token:[/dim]   {token[:12]}...")
            if expires:
                # Check expiry
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    now = datetime.now(exp_dt.tzinfo)
                    if now > exp_dt:
                        self.context.console.print(f"  [dim]Expires:[/dim] [red]EXPIRED ({expires[:10]})[/red]")
                        self.context.console.print("  [dim]Run /login to refresh your session[/dim]")
                    else:
                        delta = exp_dt - now
                        hours = int(delta.total_seconds() // 3600)
                        self.context.console.print(f"  [dim]Expires:[/dim] {expires[:10]} [dim](in {hours}h)[/dim]")
                except Exception:
                    self.context.console.print(f"  [dim]Expires:[/dim] {expires}")
            self.context.console.print()
        else:
            print(f"User: {user_id or 'unknown'}")
            print(f"Token: {token[:12]}...")
            if expires:
                print(f"Expires: {expires}")
