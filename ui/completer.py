"""prompt_toolkit completer and base style for the Aria REPL.

Improvements over the original:
  - Instant popup: triggers as soon as "/" is typed (no extra keypress needed)
  - Fuzzy match: "/ch" matches /chart, /check, /watch; "/ta" matches /stat-arb
  - Matched chars highlighted in amber so they're visible in all rows
  - Results ranked: exact-prefix > fuzzy-command > description-fuzzy
  - Category tag shown in display_meta (市场 / 分析 / 量化 / 数据源 …)

    from ui.completer import AriaPTCompleter, ARIA_PT_STYLE
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Tuple

from ui.console import HAS_PT
from packages.aria_services.references import REFERENCE_KINDS, reference_search_roots

_REFERENCE_KIND_NAMES = frozenset(item.name for item in REFERENCE_KINDS)

# ── Category map ────────────────────────────────────────────────────────────
# Keyed on command name fragments; first match wins.
_CATS: List[Tuple[Tuple[str, ...], str]] = [
    (("/quote", "/market", "/macro", "/watch", "/alert", "/hot", "/indices",
      "/cn", "/hk", "/crypto", "/forex", "/commodity", "/funding", "/feargreed",
      "/edgar", "/datasource"),                                          "市场"),
    (("/team", "/analyze", "/options", "/factor", "/ta", "/ichimoku",
      "/peer", "/quality", "/risk", "/signal", "/predict", "/earnings",
      "/insights", "/deep", "/morning", "/trade-idea", "/research"),    "分析"),
    (("/backtest", "/wf", "/compare", "/execution", "/stat-arb",
      "/ptbt", "/corr", "/optimize", "/stress", "/auto-strategy",
      "/portfolio", "/journal"),                                         "量化"),
    (("/chart", "/report", "/shortterm", "/longterm", "/cloudbt"),       "图表"),
    (("/project", "/file", "/run", "/code", "/scaffold", "/init",
      "/review", "/vision", "/browser", "/web"),                        "工具"),
    (("/config", "/model", "/apikey", "/setup", "/local", "/mcp",
      "/memory", "/cost", "/version"),                                   "设置"),
    (("/help", "/clear", "/btw", "/recap", "/exit", "/quit", "/history", "/session",
      "/bug", "/feedback", "/privacy"), "系统"),
]

_CAT_BADGE: dict[str, str] = {
    "市场": "mkt",
    "分析": "ana",
    "量化": "qnt",
    "图表": "viz",
    "工具": "dev",
    "设置": "cfg",
    "系统": "sys",
}


def _get_cat(name: str) -> str:
    for prefixes, cat in _CATS:
        for p in prefixes:
            if name.startswith(p):
                return cat
    return ""


# ── Fuzzy matching ───────────────────────────────────────────────────────────

def _fuzzy(pattern: str, text: str) -> Tuple[bool, List[int]]:
    """
    Sequential fuzzy match — pattern chars must appear in order in text.
    Returns (matched, indices_of_matched_chars_in_text).
    Consecutive matches score higher because they produce a compact index list.
    """
    if not pattern:
        return True, []
    pi = 0
    indices: List[int] = []
    for i, ch in enumerate(text):
        if ch.lower() == pattern[pi].lower():
            indices.append(i)
            pi += 1
            if pi == len(pattern):
                return True, indices
    return False, []


def _score(name: str, pattern: str, indices: List[int]) -> int:
    """
    Lower score = better.
      0   exact match
      1   exact prefix  (/ch → /chart)
      5   word-segment exact match  (/arb → /stat-arb because '-arb' segment)
     10   consecutive run from position 0
     12+  consecutive run from a word boundary
     15+  consecutive run from elsewhere
     20+  non-consecutive from a word boundary
     30+  scattered fuzzy match
    """
    if name == pattern:
        return 0
    if name.startswith(pattern):
        return 1
    if not indices:
        return 99

    # Word-segment exact match: pattern matches a whole segment after "-" or "_"
    bare = name.lstrip("/")
    pat_bare = pattern.lstrip("/")
    for sep in ("-", "_"):
        for seg in bare.split(sep)[1:]:   # skip first segment (already caught by prefix)
            if seg == pat_bare or seg.startswith(pat_bare):
                return 5 + len(sep)       # score 6 for "-", 6 for "_"

    start = indices[0]
    consecutive = (indices == list(range(start, start + len(indices))))
    at_boundary = start == 0 or (start > 0 and bare[start - 1] in "-_")

    if consecutive:
        if start == 0:
            return 10
        if at_boundary:
            return 12 + start
        return 15 + start
    if at_boundary:
        return 20 + start
    return 30 + start


# ── FormattedText display builder ────────────────────────────────────────────

def _highlighted(name: str, matched_indices: List[int]) -> List[Tuple[str, str]]:
    """
    Build a FormattedText list: matched chars get 'class:fz-hi', rest are plain.
    """
    idx_set = set(matched_indices)
    parts: List[Tuple[str, str]] = []
    for i, ch in enumerate(name):
        if i in idx_set:
            parts.append(("class:fz-hi", ch))
        else:
            parts.append(("", ch))
    return parts


if HAS_PT:
    import os as _os
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style as PTStyle

    class AriaPTCompleter(Completer):
        """
        Slash-command completer with instant popup + fuzzy search.

        Activates the moment the user types "/" (complete_while_typing=True
        is already set in PromptSession, so no extra keypress is needed).

        Triggers:
          /      → show ALL slash commands (fuzzy matched)
          @      → file/directory path autocomplete (@ anywhere in input)
          !      → shell history autocomplete (first word after !)

        Matching:
          /          → show ALL commands sorted by category order
          /ch        → fuzzy-match "ch" against command names, highlight hits
          /stat      → matches /stat-arb even though it's not a prefix
          /team AAPL → only complete the command part (stop after first space)
        """

        def __init__(
            self,
            commands_dict: dict,
            skills: list,
            watchlist: list,
            *,
            workspace: Path | str | None = None,
            output_root: Path | str | None = None,
            lang: str = "en",
        ):
            self.commands = commands_dict
            self.skills   = skills
            self.workspace = Path(workspace or Path.cwd()).expanduser().resolve()
            self.output_root = Path(output_root).expanduser().resolve() if output_root else None
            self.lang = "zh" if str(lang).lower().startswith("zh") else "en"
            self._shell_history: list[str] = []  # populated by REPL after ! commands
            self.symbols  = sorted(set([
                "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META",
                "NFLX", "AMD", "INTC", "SPY", "QQQ", "DIA", "IWM",
                "BTC-USD", "ETH-USD", "SOL-USD",
                "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "XOM",
                "GLD", "SLV", "USO", "TLT", "HYG",
            ] + list(watchlist)))

            # Pre-compute category for each command
            self._cmd_cat: dict[str, str] = {}
            for name in self.commands:
                self._cmd_cat[name] = _get_cat(name)
            for s in self.skills:
                self._cmd_cat[s["command"]] = _get_cat(s["command"])

        def get_completions(self, document, complete_event) -> Iterator[Completion]:
            text = document.text_before_cursor
            ltext = text.lstrip()

            # ── @ context reference autocomplete ───────────────────────────
            # References work anywhere in a prompt. Email addresses do not.
            ref_match = re.search(r"(?<![\w@])@([^\s@]*)$", text)
            if ref_match:
                yield from self._reference_completions(ref_match.group(1))
                return

            # ── ! shell command autocomplete ────────────────────────────────
            if ltext.startswith("!"):
                shell_frag = ltext[1:].lstrip()
                if shell_frag:
                    for hist_cmd in reversed(self._shell_history):
                        if hist_cmd.startswith(shell_frag) and hist_cmd != shell_frag:
                            yield Completion(
                                hist_cmd,
                                start_position=-(len(ltext) - 1),
                                display=FormattedText([("class:fz-hi", hist_cmd)]),
                                display_meta="shell history",
                            )
                return

            # Only activate for slash commands
            if not ltext.startswith("/"):
                return

            # Don't complete after first space — user is typing arguments
            if " " in ltext:
                return

            # The typed prefix after "/"
            pattern = ltext  # includes leading "/"

            # --- Build candidate list with scores ---
            candidates: list[tuple[int, str, list[int], str, str]] = []
            # (score, name, matched_indices, desc, category)

            all_cmds = list(self.commands.items())
            for name, (_, desc) in all_cmds:
                cmd_part = name  # e.g. "/chart"
                matched, indices = _fuzzy(pattern.lstrip("/"), cmd_part.lstrip("/"))
                if not matched and pattern != "/":
                    # Also try matching with the slash included
                    matched2, indices2 = _fuzzy(pattern, cmd_part)
                    if not matched2:
                        continue
                    indices = indices2
                score = _score(cmd_part, pattern, indices)
                cat   = self._cmd_cat.get(name, "")
                candidates.append((score, name, indices, desc, cat))

            for s in self.skills:
                cmd  = s["command"]
                desc = s.get("description", "")
                matched, indices = _fuzzy(pattern.lstrip("/"), cmd.lstrip("/"))
                if not matched and pattern != "/":
                    continue
                score = _score(cmd, pattern, indices)
                cat   = self._cmd_cat.get(cmd, "")
                candidates.append((score, cmd, indices, desc, cat))

            # Sort: primary = score, secondary = name
            candidates.sort(key=lambda x: (x[0], x[1]))

            # Emit Completions
            for score, name, indices, desc, cat in candidates:
                # Build highlighted display (amber on matched chars)
                display_parts = _highlighted(name, indices if pattern != "/" else [])

                # Category badge as short suffix in display
                badge = _CAT_BADGE.get(cat, "")
                if badge:
                    display_parts += [("class:fz-cat", f"  {badge}")]

                # Truncate description to ~45 chars for meta column
                cat_label = {
                    "市场": "市场" if self.lang == "zh" else "Market",
                    "分析": "研究" if self.lang == "zh" else "Research",
                    "量化": "量化" if self.lang == "zh" else "Quant",
                    "图表": "可视化" if self.lang == "zh" else "Visualize",
                    "工具": "开发工具" if self.lang == "zh" else "Developer",
                    "设置": "运行设置" if self.lang == "zh" else "Runtime",
                    "系统": "会话" if self.lang == "zh" else "Session",
                }.get(cat, "")
                meta_body = desc[:38] + ("…" if len(desc) > 38 else "")
                meta_str = f"{cat_label} · {meta_body}" if cat_label else meta_body

                # start_position: replace the entire typed prefix
                start = -len(pattern)

                yield Completion(
                    text            = name,
                    start_position  = start,
                    display         = FormattedText(display_parts),
                    display_meta    = meta_str,
                )

        def _reference_completions(self, frag: str) -> Iterator[Completion]:
            """Complete typed reference namespaces and their values."""
            if ":" not in frag:
                lowered = frag.lower()
                for kind in REFERENCE_KINDS:
                    if kind.name.startswith(lowered):
                        label = kind.label_zh if self.lang == "zh" else kind.label_en
                        yield Completion(
                            f"{kind.name}:",
                            start_position=-len(frag),
                            display=FormattedText([
                                ("class:fz-hi", f"@{kind.name}"),
                                ("class:fz-cat", ":"),
                            ]),
                            display_meta=label,
                        )
                # Keep the first-level @ menu semantic and compact. Plain-path
                # completion remains available only after an explicit path
                # prefix; normal discovery goes through @file:/@folder:.
                if frag and (frag.startswith((".", "/", "~")) or "/" in frag):
                    yield from self._file_completions(frag)
                return

            kind, value_frag = frag.split(":", 1)
            kind = kind.lower()
            if kind == "asset":
                for symbol in self.symbols:
                    if symbol.lower().startswith(value_frag.lower()):
                        yield Completion(
                            f"asset:{symbol}",
                            start_position=-len(frag),
                            display=FormattedText([("class:fz-hi", symbol)]),
                            display_meta="市场资产" if self.lang == "zh" else "market asset",
                        )
                return
            if kind in {"file", "folder"}:
                yield from self._file_completions(
                    value_frag,
                    value_prefix=f"{kind}:",
                    directories_only=(kind == "folder"),
                )
                return
            if kind in _REFERENCE_KIND_NAMES:
                yield from self._named_resource_completions(kind, value_frag)

        def _file_completions(
            self,
            frag: str,
            *,
            value_prefix: str = "",
            directories_only: bool = False,
        ) -> Iterator[Completion]:
            """Yield workspace-relative file/directory reference completions."""
            try:
                expanded = _os.path.expanduser(frag) if frag.startswith("~") else frag
                base_fragment = _os.path.dirname(expanded) or "."
                base_dir = Path(base_fragment)
                if not base_dir.is_absolute():
                    base_dir = self.workspace / base_dir
                prefix   = _os.path.basename(frag)
                if not base_dir.is_dir():
                    return
                for entry in sorted(_os.listdir(base_dir))[:60]:
                    if entry.startswith("."):
                        continue
                    if not entry.lower().startswith(prefix.lower()):
                        continue
                    candidate = base_dir / entry
                    is_dir = candidate.is_dir()
                    if directories_only and not is_dir:
                        continue
                    if Path(expanded).is_absolute():
                        full = str(candidate)
                    else:
                        parent = _os.path.dirname(frag)
                        full = _os.path.join(parent, entry) if parent else entry
                    display_parts = [("class:fz-hi", prefix), ("", entry[len(prefix):])]
                    if is_dir:
                        display_parts.append(("class:fz-cat", "/"))
                    yield Completion(
                        value_prefix + full + ("/" if is_dir else ""),
                        start_position=-(len(value_prefix) + len(frag)),
                        display=FormattedText(display_parts),
                        display_meta="dir" if is_dir else "file",
                    )
            except Exception:
                pass

        def _named_resource_completions(self, kind: str, frag: str) -> Iterator[Completion]:
            seen: set[str] = set()
            emitted = 0
            for root in reference_search_roots(kind, self.workspace, self.output_root):
                if not root.is_dir():
                    continue
                try:
                    for path in root.rglob("*"):
                        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
                            continue
                        name = path.stem
                        if name in seen or not name.lower().startswith(frag.lower()):
                            continue
                        seen.add(name)
                        yield Completion(
                            f"{kind}:{name}",
                            start_position=-(len(kind) + 1 + len(frag)),
                            display=FormattedText([("class:fz-hi", name)]),
                            display_meta=kind,
                        )
                        emitted += 1
                        if emitted >= 40:
                            return
                except OSError:
                    continue

        def add_shell_history(self, cmd: str) -> None:
            """Called by REPL after each ! command to update shell autocomplete."""
            cmd = cmd.strip()
            if cmd and cmd not in self._shell_history:
                self._shell_history.append(cmd)
                if len(self._shell_history) > 200:
                    self._shell_history = self._shell_history[-200:]

    # ── Style ────────────────────────────────────────────────────────────────
    # Theme-aware completion menu in the 5-color palette.
    # Selection + fuzzy highlight use copper (brand); the menu surface
    # matches the terminal theme so it never floats as a dark popup on a
    # light terminal (or vice-versa).

    # Copper-palette menu colors per theme.
    #   bg     = menu surface (sits clearly above terminal bg)
    #   fg     = row text
    #   sel_bg = selected row (copper tint)
    #   sel_fg = selected row text (copper, bold-applied at use site)
    #   meta   = description column (dim)
    #   hi     = fuzzy-matched chars (copper)
    _MENU_THEMES = {
        "dark": dict(
            bg="#161b22", fg="#c9d1d9",
            sel_bg="#3a2e20", sel_fg="#e8c9a6",
            meta="#6e7681", meta_cur="#c0a585",
            scroll_bg="#161b22", scroll_btn="#C08050",   # copper position handle
            hi="#C08050", cat="#6e7681",
            base_bg="#0d1117", prompt="#8b949e", ph="#484f58",
            tb_fg="#8b949e", tb_bg="#161b22",
        ),
        "light": dict(
            bg="#f2eee4", fg="#24292f",                   # warm surface, high contrast
            sel_bg="#e7e1d3", sel_fg="#8a5a00",           # stronger copper selection
            meta="#6e7781", meta_cur="#8a5a00",
            scroll_bg="#e7e1d3", scroll_btn="#9a6700",    # copper position handle
            hi="#9a6700", cat="#6e7781",
            base_bg="default", prompt="#57606a", ph="#6e7781",
            tb_fg="#57606a", tb_bg="#e7e1d3",
        ),
    }

    def _detect_theme() -> str:
        try:
            from ui.input_box import detect_terminal_theme
            return detect_terminal_theme()
        except Exception:
            return "dark"

    def build_aria_pt_style(theme: str = "auto") -> "PTStyle":
        """Build a theme-aware PromptSession style in the copper palette."""
        if theme == "auto":
            theme = _detect_theme()
        c = _MENU_THEMES.get(theme, _MENU_THEMES["dark"])
        base = f"{c['fg']} bg:{c['base_bg']}" if c["base_bg"] != "default" else c["fg"]
        return PTStyle.from_dict({
            "":                   base,
            "prompt":             c["prompt"],
            "placeholder":        c["ph"],
            "input-bg":           base,
            "bottom-toolbar":     f"noreverse {c['tb_fg']} bg:{c['tb_bg']}",
            "bottom-toolbar.text":f"noreverse {c['tb_fg']} bg:{c['tb_bg']}",

            # Completion menu — theme-aware surface, copper selection
            "completion-menu":                    f"bg:{c['bg']} {c['fg']}",
            "completion-menu.completion":         f"bg:{c['bg']} {c['fg']}",
            "completion-menu.completion.current": f"bg:{c['sel_bg']} {c['sel_fg']} bold",
            "completion-menu.meta.completion":         f"bg:{c['bg']} {c['meta']}",
            "completion-menu.meta.completion.current": f"bg:{c['sel_bg']} {c['meta_cur']}",
            "completion-menu.multi-column-meta":       f"bg:{c['bg']} {c['meta']}",
            "scrollbar.background":               f"bg:{c['scroll_bg']}",
            "scrollbar.button":                   f"bg:{c['scroll_btn']}",

            # Fuzzy highlight classes — copper
            "fz-hi":   f"bold {c['hi']}",
            "fz-cat":  c["cat"],
        })

    # Back-compat default (dark). Prefer build_aria_pt_style(theme) at call sites.
    ARIA_PT_STYLE = build_aria_pt_style("dark")

else:
    def build_aria_pt_style(theme: str = "auto"):  # type: ignore
        return None
    class AriaPTCompleter:  # type: ignore
        def __init__(self, *a, **kw): pass
        def get_completions(self, *a, **kw): return iter([])

    ARIA_PT_STYLE = None
