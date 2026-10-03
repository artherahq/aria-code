#!/usr/bin/env python3
"""Pack and unpack the PyInstaller --onedir build that every release ships.

Why --onedir: a --onefile binary unpacks ~400 native libraries into a new
temporary directory on *every* launch, and macOS scans each newly seen library
before it may load. Measured on an M-series Mac with the v0.57.0 build:

    --onefile   every launch               88-96 s   (6 s of it CPU)
    --onedir    first launch after install  84 s
                every launch after that     2.2 s

The scan is per file path, so --onefile pays it forever; --onedir pays it once.

The directory has to travel as one archive: actions/upload-artifact drops
symlinks and execute bits, and PyInstaller's macOS output has 22 symlinks
(Python.framework's Versions/Current, PIL's relocated dylibs). tar keeps both.
Windows output has neither, and zip is what PowerShell can open natively.

    package_onedir.py pack   <dist>/<name>  <out-dir>    -> <out-dir>/<name>.tar.gz | .zip
    package_onedir.py unpack <archive>      <dest-dir>   -> <dest-dir>/<name>/

unpack --dereference replaces every symlink with a copy of its target. npm
tarballs cannot be trusted to carry symlinks, so the npm platform packages are
built from a dereferenced tree; a symlink pointing outside the tree is refused
rather than followed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile


def pack(source: pathlib.Path, out_dir: pathlib.Path, *, use_zip: bool | None = None) -> pathlib.Path:
    """Archive the directory `source` so it unpacks to `<source.name>/`."""
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"not a --onedir build directory: {source}")
    if use_zip is None:
        use_zip = sys.platform == "win32"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{source.name}{'.zip' if use_zip else '.tar.gz'}"
    if use_zip:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise SystemExit(f"zip cannot carry the symlink {path}; use tar.gz")
                if path.is_file():
                    zf.write(path, path.relative_to(source.parent).as_posix())
    else:
        with tarfile.open(target, "w:gz") as tf:
            tf.add(source, arcname=source.name)
    return target


def _extract(archive: pathlib.Path, dest: pathlib.Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                resolved = (dest / member).resolve()
                if not resolved.is_relative_to(dest.resolve()):
                    raise SystemExit(f"{archive.name}: {member} would extract outside the target")
            zf.extractall(dest)
        return
    with tarfile.open(archive, "r:gz") as tf:
        # "data" refuses absolute paths, links that escape dest, and device
        # files, and keeps the execute bits that the binaries need.
        try:
            tf.extractall(dest, filter="data")
        except tarfile.FilterError as exc:
            raise SystemExit(f"{archive.name}: refusing to unpack: {exc}") from exc


def _dereference(root: pathlib.Path) -> None:
    """Replace every symlink under root with a copy of what it points to."""
    root = root.resolve()
    # Deepest first, so a link inside a linked directory is gone before its
    # parent is copied over it.
    links = sorted((p for p in root.rglob("*") if p.is_symlink()),
                   key=lambda p: len(p.parts), reverse=True)
    for link in links:
        target = link.resolve()
        if not target.is_relative_to(root):
            raise SystemExit(f"{link.relative_to(root)} points outside the build ({target}); refusing")
        if not target.exists():
            raise SystemExit(f"{link.relative_to(root)} is a dangling symlink")
        link.unlink()
        if target.is_dir():
            shutil.copytree(target, link, symlinks=False)
        else:
            shutil.copy2(target, link)


def unpack(archive: pathlib.Path, dest: pathlib.Path, *, dereference: bool = False) -> pathlib.Path:
    """Unpack into dest and return the single top-level directory."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest) as staging:
        staging_path = pathlib.Path(staging)
        _extract(archive, staging_path)
        tops = list(staging_path.iterdir())
        if len(tops) != 1 or not tops[0].is_dir():
            raise SystemExit(f"{archive.name} must contain exactly one directory, found "
                             f"{sorted(t.name for t in tops)}")
        if dereference:
            _dereference(tops[0])
        final = dest / tops[0].name
        if final.exists():
            shutil.rmtree(final)
        tops[0].rename(final)
    return final


def ensure_executable(path: pathlib.Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("source", type=pathlib.Path)
    p.add_argument("out_dir", type=pathlib.Path)
    u = sub.add_parser("unpack")
    u.add_argument("archive", type=pathlib.Path)
    u.add_argument("dest", type=pathlib.Path)
    u.add_argument("--dereference", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.cmd == "pack":
        print(os.fspath(pack(args.source, args.out_dir)))
    else:
        print(os.fspath(unpack(args.archive, args.dest, dereference=args.dereference)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
