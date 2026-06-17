"""System-level tools: run_command, web_fetch, github.

All functions are pure (no module-level globals). Console output is
injected via keyword args so aria_cli.py thin wrappers supply the
Rich console and global state defaults.
"""
from __future__ import annotations

import json
import pathlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent  # aria-code/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from safety import evaluate_command_policy  # noqa: E402


def _cprint(msg: str, *, console, has_rich: bool) -> None:
    if has_rich and console is not None:
        console.print(msg)
    else:
        plain = re.sub(r"\[/?[^\]]+\]", "", msg)
        print(plain)


def tool_run_command(
    params: dict,
    *,
    console=None,
    has_rich: bool = True,
) -> dict:
    """Run a shell command and return output.

    ``params`` should contain ``permission_mode`` and ``network_enabled``
    (filled in by the aria_cli.py wrapper from the active globals).
    """
    command = params.get("command", "")
    # LLMs sometimes send command as a list e.g. ['bash', '-lc', '...'] — normalize to string
    if isinstance(command, list):
        import shlex as _shlex
        command = _shlex.join(str(c) for c in command)
        params["command"] = command
    if not command:
        return {"success": False, "error": "Missing 'command' parameter"}

    effective_policy = params.get("policy", "safe")
    if params.get("user_approved") and effective_policy == "safe":
        effective_policy = "balanced"

    decision = evaluate_command_policy(
        command,
        effective_policy,
        mode=params.get("permission_mode", "safe"),
        network_enabled=bool(params.get("network_enabled", True)),
    )
    command = decision.normalized_command

    dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/", ":(){ :", "fork bomb"]
    for d in dangerous:
        if d in command:
            return {"success": False, "error": f"Blocked dangerous command: {command}"}

    # Prevent executing text/doc files as Python — they are analysis reports, not scripts
    import re as _re_cmd
    _py3_file = _re_cmd.search(r'\bpython3?\s+["\']?(\S+\.(?:txt|md|docx|csv|json|log))', command)
    if _py3_file:
        _bad_file = _py3_file.group(1)
        return {
            "success": False,
            "error": (
                f"拒绝执行: '{_bad_file}' 是文本/分析文件，不是 Python 脚本。\n"
                "如需展示分析结果，请直接输出文字，或将分析结论写入 .py 文件后执行。"
            ),
        }

    if params.get("dry_run"):
        return {"success": True, "data": {
            "command": command,
            "risk": decision.risk,
            "policy": decision.policy,
            "requires_approval": getattr(decision, "requires_approval", False),
            "network": getattr(decision, "network", False),
            "dry_run": True,
        }}
    if not decision.allowed:
        return {"success": False, "error": decision.reason}
    try:
        cwd = params.get("cwd", None)
        timeout = min(params.get("timeout", 120), 300)
        use_shell = True
        argv = None
        if decision.risk == "low":
            has_shell_meta = any(ch in command for ch in ["|", "&", ";", "<", ">", "$", "`", "\n"])
            if not has_shell_meta:
                try:
                    argv = shlex.split(command)
                    if argv:
                        use_shell = False
                except ValueError:
                    use_shell = True
                    argv = None

        result = subprocess.run(
            argv if (argv and not use_shell) else command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout
        stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr

        # ── Auto-fix loop (up to 3 rounds for python3 scripts) ──────────────
        MAX_AUTO_FIX_ROUNDS = 3
        _cmd_tail = (
            command.strip().split("python3 ", 1)[-1].strip().split()
            if command.strip().startswith("python3 ") else []
        )
        if result.returncode != 0 and _cmd_tail:
            script_path = _cmd_tail[0]
            script_p = pathlib.Path(script_path).expanduser().resolve()

            for _fix_round in range(MAX_AUTO_FIX_ROUNDS):
                combined_err = (output + " " + stderr).strip()
                auto_fixed = False

                if not (script_p.exists() and script_p.suffix == ".py"):
                    break
                script_content = script_p.read_text(errors="replace")

                name_match = re.search(r"NameError: name ['\"](\w+)['\"] is not defined", combined_err)
                if name_match and not auto_fixed:
                    missing = name_match.group(1)
                    import_map = {
                        "os": "import os", "sys": "import sys", "re": "import re",
                        "json": "import json", "math": "import math", "time": "import time",
                        "np": "import numpy as np", "pd": "import pandas as pd",
                        "yf": "import yfinance as yf", "plt": "import matplotlib.pyplot as plt",
                        "mpf": "import mplfinance as mpf",
                        "datetime": "from datetime import datetime, timedelta",
                        "Path": "from pathlib import Path",
                        "timedelta": "from datetime import datetime, timedelta",
                        "go": "import plotly.graph_objects as go",
                        "px": "import plotly.express as px",
                        "ta": "import pandas_ta as ta", "warnings": "import warnings",
                        "make_subplots": "from plotly.subplots import make_subplots",
                        "bt": "import backtrader as bt", "vbt": "import vectorbt as vbt",
                        "ccxt": "import ccxt", "requests": "import requests",
                        "BeautifulSoup": "from bs4 import BeautifulSoup",
                        "tqdm": "from tqdm import tqdm",
                        "xgb": "import xgboost as xgb",
                        "Prophet": "from prophet import Prophet",
                        "arch": "from arch import arch_model",
                        "statsmodels": "import statsmodels.api as sm",
                        "sm": "import statsmodels.api as sm",
                    }
                    fix_import = import_map.get(missing)
                    if fix_import and fix_import not in script_content:
                        lines = script_content.split("\n")
                        insert_at = 0
                        for i, l in enumerate(lines):
                            if l.strip().startswith("#!") or l.strip().startswith("# -*-"):
                                insert_at = i + 1
                            else:
                                break
                        lines.insert(insert_at, fix_import)
                        if missing == "plt" and "matplotlib.use" not in script_content:
                            lines.insert(insert_at, "import matplotlib; matplotlib.use('Agg')")
                        script_p.write_text("\n".join(lines))
                        auto_fixed = True
                        _cprint(
                            f"  [#C08050]Auto-fix[{_fix_round+1}/{MAX_AUTO_FIX_ROUNDS}]:"
                            f"[/#C08050] [dim]added '{fix_import}'[/dim]",
                            console=console, has_rich=has_rich,
                        )

                if not auto_fixed and (
                    "cannot be resolved at runtime" in combined_err.lower()
                    or ("matplotlib" in combined_err and "backend" in combined_err.lower())
                ):
                    if "matplotlib.use" not in script_content and "matplotlib.pyplot" in script_content:
                        script_content = script_content.replace(
                            "import matplotlib.pyplot as plt",
                            "import matplotlib; matplotlib.use('Agg')\nimport matplotlib.pyplot as plt",
                        )
                        script_p.write_text(script_content)
                        auto_fixed = True
                        _cprint(
                            f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050]"
                            " [dim]added matplotlib.use('Agg')[/dim]",
                            console=console, has_rich=has_rich,
                        )

                key_match = re.search(
                    r"KeyError: ['\"]?(Close|Open|High|Low|Volume|Adj Close)", combined_err
                )
                if key_match and not auto_fixed and "yfinance" in script_content:
                    if "columns.droplevel" not in script_content:
                        fix_line = (
                            "\n# Fix yfinance MultiIndex columns\n"
                            "if isinstance(df.columns, pd.MultiIndex):\n"
                            "    df.columns = df.columns.droplevel(1)\n"
                        )
                        dl_match = re.search(r"(.*=\s*yf\.download\([^)]+\))", script_content)
                        if dl_match:
                            script_content = script_content.replace(
                                dl_match.group(0), dl_match.group(0) + fix_line
                            )
                            script_p.write_text(script_content)
                            auto_fixed = True
                            _cprint(
                                f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050]"
                                " [dim]MultiIndex column fix[/dim]",
                                console=console, has_rich=has_rich,
                            )

                attr_match = re.search(
                    r"AttributeError: '(\w+)' object has no attribute '(\w+)'", combined_err
                )
                if attr_match and not auto_fixed:
                    obj_type, attr_name = attr_match.group(1), attr_match.group(2)
                    if obj_type == "DataFrame" and attr_name == "append":
                        script_content = re.sub(
                            r"(\w+)\.append\(([^)]+)\)",
                            r"pd.concat([\1, \2], ignore_index=True)",
                            script_content,
                        )
                        script_p.write_text(script_content)
                        auto_fixed = True
                        _cprint(
                            f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050]"
                            " [dim]DataFrame.append→pd.concat[/dim]",
                            console=console, has_rich=has_rich,
                        )

                if not auto_fixed and "TypeError" in combined_err:
                    if "auto_adjust" in combined_err and "auto_adjust" in script_content:
                        script_content = re.sub(
                            r",\s*auto_adjust\s*=\s*(True|False)", "", script_content
                        )
                        script_p.write_text(script_content)
                        auto_fixed = True
                        _cprint(
                            f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050]"
                            " [dim]removed deprecated auto_adjust param[/dim]",
                            console=console, has_rich=has_rich,
                        )

                mod_match = re.search(r"No module named ['\"]?(\w+)", combined_err)
                if mod_match and not auto_fixed:
                    missing_mod = mod_match.group(1)
                    pip_map = {
                        "mplfinance": "mplfinance", "plotly": "plotly",
                        "pandas_ta": "pandas_ta", "ta": "ta",
                        "sklearn": "scikit-learn", "cv2": "opencv-python",
                        "bs4": "beautifulsoup4", "PIL": "Pillow",
                        "backtrader": "backtrader", "vectorbt": "vectorbt",
                        "ccxt": "ccxt", "prophet": "prophet",
                        "arch": "arch", "xgboost": "xgboost",
                        "lightgbm": "lightgbm", "statsmodels": "statsmodels",
                        "akshare": "akshare", "tushare": "tushare",
                        "empyrical": "empyrical", "pyfolio": "pyfolio",
                        "seaborn": "seaborn", "openpyxl": "openpyxl",
                    }
                    pip_pkg = pip_map.get(missing_mod, missing_mod)
                    _cprint(
                        f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050]"
                        f" [dim]pip3 install {pip_pkg}[/dim]",
                        console=console, has_rich=has_rich,
                    )
                    pip_result = subprocess.run(
                        f"pip3 install {pip_pkg}", shell=True, capture_output=True,
                        text=True, timeout=60,
                    )
                    if pip_result.returncode == 0:
                        auto_fixed = True

                if auto_fixed:
                    _cprint(
                        f"  [dim]Re-running after auto-fix (round {_fix_round+1}/{MAX_AUTO_FIX_ROUNDS})...[/dim]",
                        console=console, has_rich=has_rich,
                    )
                    result = subprocess.run(
                        command, shell=True, capture_output=True, text=True,
                        timeout=timeout, cwd=cwd,
                    )
                    output = result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout
                    stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
                    if result.returncode == 0:
                        break
                else:
                    break
        # ── End auto-fix ─────────────────────────────────────────────────────

        if has_rich and console is not None:
            if result.returncode == 0:
                console.print(f"  [green]Command completed[/green] [dim](exit {result.returncode})[/dim]")
            else:
                console.print(f"  [dim]Command exited {result.returncode}[/dim]")
            out_preview = output.strip().splitlines()[:6]
            for ol in out_preview:
                console.print(f"    [dim]{ol[:120]}[/dim]")
            if len(output.strip().splitlines()) > 6:
                console.print("    [dim]...truncated[/dim]")
            if stderr.strip() and result.returncode != 0:
                for el in stderr.strip().splitlines()[:3]:
                    console.print(f"    [red]{el[:120]}[/red]")
        else:
            print(f"  Command exit: {result.returncode}")
        return {"success": True, "data": {
            "command": command, "exit_code": result.returncode,
            "stdout": output, "stderr": stderr,
        }}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out ({timeout}s)"}
    except KeyboardInterrupt:
        _cprint("  [dim]Command interrupted[/dim]", console=console, has_rich=has_rich)
        return {"success": False, "error": "Command interrupted by user (Ctrl+C)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_web_fetch(params: dict) -> dict:
    """Fetch the text content of any URL."""
    url = params.get("url", "").strip()
    if not url:
        return {"success": False, "error": "Missing 'url' parameter"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    max_chars = min(int(params.get("max_chars", 4000)), 12000)
    timeout   = min(int(params.get("timeout", 15)), 30)
    try:
        import urllib.request as _ur
        import ssl as _ssl
        _prx = _ur.getproxies()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        }
        _gh_m = re.match(
            r"https://github\.com/([^/]+/[^/]+)/blob/([^?#]+)", url
        )
        if _gh_m:
            url = f"https://raw.githubusercontent.com/{_gh_m.group(1)}/{_gh_m.group(2)}"

        import requests as _req
        s = _req.Session()
        s.proxies = _prx
        s.verify = False
        r = s.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        raw = r.text

        ct = r.headers.get("content-type", "")
        if "json" in ct or raw.lstrip().startswith(("{", "[")):
            return {"success": True, "data": {
                "url": url, "content_type": ct,
                "text": raw[:max_chars], "length": len(raw),
            }}

        text = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>",   " ", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>",     " ", text)
        text = re.sub(r"&nbsp;",      " ", text)
        text = re.sub(r"&amp;",       "&", text)
        text = re.sub(r"&lt;",        "<", text)
        text = re.sub(r"&gt;",        ">", text)
        text = re.sub(r"&quot;",      '"', text)
        text = re.sub(r"\s{3,}",      "\n", text)
        text = text.strip()

        return {"success": True, "data": {
            "url": url, "content_type": ct,
            "text": text[:max_chars], "length": len(text),
            "truncated": len(text) > max_chars,
        }}
    except Exception as e:
        return {"success": False, "error": f"web_fetch failed: {e}"}


def tool_github(
    params: dict,
    *,
    console=None,
    has_rich: bool = True,
) -> dict:
    """GitHub API / gh CLI integration.

    actions: list_prs, list_issues, view_pr, view_issue, create_pr,
             list_commits, read_file, search, pr_diff, pr_checks
    """
    action = params.get("action", "list_prs").lower().replace("-", "_")
    cwd    = params.get("cwd") or None
    policy = "safe"

    def _gh(cmd: str, timeout: int = 20) -> dict:
        import shutil
        if not shutil.which("gh"):
            return {"success": False,
                    "error": "gh CLI not found. Install: brew install gh && gh auth login"}
        return tool_run_command(
            {"command": cmd, "cwd": cwd, "timeout": timeout, "policy": policy},
            console=console,
            has_rich=has_rich,
        )

    if action in ("list_prs", "prs", "pull_requests"):
        state = params.get("state", "open")
        limit = int(params.get("limit", 20))
        return _gh(f"gh pr list --state {state} --limit {limit} "
                   "--json number,title,author,state,headRefName,url")

    if action in ("list_issues", "issues"):
        state = params.get("state", "open")
        limit = int(params.get("limit", 20))
        label = f' --label "{params["label"]}"' if params.get("label") else ""
        return _gh(f"gh issue list --state {state} --limit {limit}{label} "
                   "--json number,title,author,state,labels,url")

    if action in ("view_pr", "pr"):
        number = params.get("number") or params.get("pr")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        return _gh(f"gh pr view {number} "
                   "--json number,title,body,state,headRefName,baseRefName,additions,deletions,files,url")

    if action in ("view_issue", "issue"):
        number = params.get("number") or params.get("issue")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        return _gh(f"gh issue view {number} "
                   "--json number,title,body,state,labels,comments,url")

    if action == "create_pr":
        title  = params.get("title", "")
        body   = params.get("body", "")
        branch = params.get("branch", "")
        base   = params.get("base", "main")
        if not title:
            return {"success": False, "error": "Missing 'title' for create_pr"}
        b_flag = f"--head {shlex.quote(branch)}" if branch else ""
        cmd = (
            f"gh pr create --title {shlex.quote(title)} "
            f"--body {shlex.quote(body)} "
            f"--base {shlex.quote(base)} {b_flag}"
        )
        return _gh(cmd, timeout=30)

    if action in ("list_commits", "commits", "log"):
        limit = int(params.get("limit", 10))
        return _gh(
            f"gh api repos/{{owner}}/{{repo}}/commits?per_page={limit} "
            "--jq '[.[] | {sha: .sha[:7], "
            'message: .commit.message | split("\\n")[0], '
            "author: .commit.author.name, date: .commit.author.date}]'"
        )

    if action == "search":
        q    = params.get("q") or params.get("query", "")
        kind = params.get("kind", "code")
        if not q:
            return {"success": False, "error": "Missing 'q' parameter"}
        return _gh(f"gh search {kind} {shlex.quote(q)} --limit 10 "
                   "--json url,path,textMatches", timeout=15)

    if action in ("read_file", "file"):
        ref       = params.get("ref", "")
        file_path = params.get("path", "")
        if ref:
            m = re.match(r"([^@:]+)@([^:]+):(.+)", ref)
            if m:
                repo, branch, fp = m.groups()
                url = f"https://raw.githubusercontent.com/{repo}/{branch}/{fp}"
                return tool_web_fetch({"url": url, "max_chars": 20000})
        if file_path:
            return _gh(f"gh api repos/{{owner}}/{{repo}}/contents/{file_path} "
                       "--jq '.content' | base64 -d")
        return {"success": False, "error": "Provide 'ref' (owner/repo@branch:path) or 'path'"}

    if action in ("pr_diff", "diff"):
        number = params.get("number") or params.get("pr")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        return _gh(f"gh pr diff {number}", timeout=30)

    if action in ("pr_checks", "checks", "ci"):
        number = params.get("number") or params.get("pr")
        return _gh(f"gh pr checks {number or ''}")

    return {
        "success": False,
        "error": (
            f"Unknown GitHub action: '{action}'. "
            "Use: list_prs, list_issues, view_pr, view_issue, create_pr, "
            "list_commits, search, read_file, pr_diff, pr_checks"
        ),
    }
