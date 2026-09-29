#!/usr/bin/env python3
"""Render real Aria Code terminal output captured by asciinema.

No interface text is generated here. The script replays the recorded ANSI stream
into its original terminal grid and captures the output as the four actual CLI
commands run. Idle time between commands is removed.
The source .cast stays local because startup output includes a session URL.

Regenerate with: python -m pip install pyte Pillow
                 python scripts/render_terminal_capture.py --cast session.cast
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
FONTS = (
    Path("/System/Library/Fonts/Menlo.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
)
ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CELL_W, CELL_H = 10, 20
BACKGROUND = "#171717"
FOREGROUND = "#E8E8E8"


def frame(screen: pyte.Screen) -> Image.Image:
    visible = "\n".join(screen.display).lower()
    if "http://" in visible or "https://" in visible or "remote control" in visible:
        raise ValueError("The captured frame contains a URL or session information")
    image = Image.new("RGB", (screen.columns * CELL_W, screen.lines * CELL_H), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font_path = next((path for path in FONTS if path.exists()), None)
    if font_path is None:
        raise FileNotFoundError("Install Menlo or DejaVu Sans Mono to render this capture")
    regular = ImageFont.truetype(str(font_path), 15)
    bold = ImageFont.truetype(str(font_path), 15, index=1)
    for row in range(screen.lines):
        cells = screen.buffer[row]
        for col in range(screen.columns):
            cell = cells[col]
            if cell.data.strip():
                draw.text((col * CELL_W, row * CELL_H), cell.data, font=bold if cell.bold else regular, fill=FOREGROUND)
    return image


def capture(cast: Path) -> tuple[list[Image.Image], list[int]]:
    with cast.open(encoding="utf-8") as handle:
        header = json.loads(next(handle))
        screen = pyte.Screen(header["width"], header["height"])
        stream = pyte.Stream(screen)
        images: list[Image.Image] = []
        durations: list[int] = []
        stage = 0
        recording = False
        previous_display: tuple[str, ...] | None = None
        starts = (
            "/run python3 -m unittest -q",
            "/read calculator.py",
            "/write /private/tmp/aria-demo/calculator.py",
            "/run python3 -m unittest -q",
        )
        ends = (
            "FAILED (failures=2)",
            "return a - b",
            "Updated /private/tmp/aria-demo/calculator.py",
            "Ran 2 tests",
        )
        for line in handle:
            event = json.loads(line)
            if event[1] != "o":
                continue
            clean = ESCAPE.sub("", event[2])
            stream.feed(event[2])
            if stage >= len(starts):
                break
            if not recording and starts[stage] in clean:
                recording = True
            if recording and clean.strip():
                display = tuple(screen.display)
                if display != previous_display:
                    images.append(frame(screen))
                    durations.append(220)
                    previous_display = display
            if recording and ends[stage] in clean:
                durations[-1] = 1700
                recording = False
                stage += 1
    if stage != 4 or len(images) < 8:
        raise ValueError(f"Expected four genuine CLI commands, found {stage} and {len(images)} frames")
    return images, durations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cast", type=Path, required=True, help="Locally recorded asciinema v2 .cast file")
    parser.add_argument("--gif", type=Path, default=ROOT / "docs/assets/demo-coding-workflow.gif")
    parser.add_argument("--png", type=Path, default=ROOT / "docs/assets/demo-coding-workflow.png")
    args = parser.parse_args()
    images, durations = capture(args.cast)
    args.gif.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(args.gif, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
    images[-1].save(args.png)
    print(args.gif)
    print(args.png)


if __name__ == "__main__":
    main()
