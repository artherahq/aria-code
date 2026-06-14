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

from typing import Iterator, List, Tuple

from ui.console import HAS_PT

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
    (("/help", "/clear", "/exit", "/quit", "/history", "/session"),     "系统"),
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
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style as PTStyle

    class AriaPTCompleter(Completer):
        """
        Slash-command completer with instant popup + fuzzy search.

        Activates the moment the user types "/" (complete_while_typing=True
        is already set in PromptSession, so no extra keypress is needed).

        Matching:
          /          → show ALL commands sorted by category order
          /ch        → fuzzy-match "ch" against command names, highlight hits
          /stat      → matches /stat-arb even though it's not a prefix
          /team AAPL → only complete the command part (stop after first space)
        """

        def __init__(self, commands_dict: dict, skills: list, watchlist: list):
            self.commands = commands_dict
            self.skills   = skills
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
                meta_str = desc[:45] + ("…" if len(desc) > 45 else "")

                # start_position: replace the entire typed prefix
                start = -len(pattern)

                yield Completion(
                    text            = name,
                    start_position  = start,
                    display         = FormattedText(display_parts),
                    display_meta    = meta_str,
                )

    # ── Style ────────────────────────────────────────────────────────────────
    # Matches the GitHub dark palette used in input_box.py.
    # fz-hi = fuzzy-matched character highlight (amber, visible in all rows)
    # fz-cat = category badge (dim muted)
    ARIA_PT_STYLE = PTStyle.from_dict({
        "":                  "#c9d1d9 bg:#0d1117",
        "prompt":            "#8b949e",
        "placeholder":       "#484f58",
        "input-bg":          "#c9d1d9 bg:#0d1117",
        "bottom-toolbar":    "noreverse #8b949e bg:#161b22",
        "bottom-toolbar.text":"noreverse #8b949e bg:#161b22",

        # Completion menu — GitHub dark
        "completion-menu":                     "bg:#161b22 #c9d1d9",
        "completion-menu.completion":          "bg:#161b22 #c9d1d9",
        "completion-menu.completion.current":  "bg:#1f2937 #e6edf3 bold",
        "completion-menu.meta":                "bg:#161b22 #484f58",
        "completion-menu.meta.current":        "bg:#1f2937 #8b949e",
        "scrollbar.background":                "bg:#161b22",
        "scrollbar.button":                    "bg:#30363d",

        # Fuzzy highlight classes
        "fz-hi":   "bold #f0883e",   # amber — matched chars, visible in any row
        "fz-cat":  "#484f58",        # dim — category badge (mkt/ana/qnt…)
    })

else:
    class AriaPTCompleter:  # type: ignore
        def __init__(self, *a, **kw): pass
        def get_completions(self, *a, **kw): return iter([])

    ARIA_PT_STYLE = None
