"""Path helpers for generated code and strategy artifacts."""

from __future__ import annotations

from pathlib import Path


def resolve_user_code_path(
    description: str,
    save_path: str | None = None,
    *,
    user_generated_dir: Path,
) -> Path:
    """Resolve a code output path into the user's generated workspace."""
    from artifacts import slugify_topic

    if save_path:
        p = Path(save_path).expanduser()
        if not p.is_absolute():
            p = user_generated_dir / p
        if p.suffix != ".py":
            p = p.with_suffix(".py")
        return p

    desc_slug = slugify_topic(description, fallback="generated_code")
    if any(k in description.lower() for k in ("backtest", "strategy", "quant", "portfolio")):
        desc_slug = f"strategy_{desc_slug}"
    return user_generated_dir / f"{desc_slug}.py"

