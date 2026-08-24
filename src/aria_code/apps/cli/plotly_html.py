"""Helpers for generating offline-safe Plotly HTML fragments."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def plotly_script_tag() -> str:
    """Return an inline Plotly <script> tag when the package is available."""
    try:
        from plotly.offline import get_plotlyjs

        return f"<script>{get_plotlyjs()}</script>"
    except Exception:
        return '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
