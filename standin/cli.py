"""The standin command: keygen, scan, mask and check."""

import argparse
import os
import secrets
import stat
import sys
from collections import Counter
from pathlib import Path

from . import Problem, __version__
from .check import check
from .csvfile import publish, sniff, table, write
from .rules import BadCell, load, plan
from .scan import SAMPLE_ROWS, scan

KEY_BYTES = 32
PROGRESS_EVERY = 100_000  # rows between progress updates on a terminal


def main(argv: list[str] | None = None) -> int:
    # Rules files are UTF-8, and on Windows a redirected stdout would
    # otherwise use the ANSI code page and mangle non-English column names.
    sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except Problem as e:
        print(f"standin: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nstandin: stopped. Nothing half-written was left behind.", file=sys.stderr)
        return 130
    except OSError as e:
        print(f"standin: {e.strerror or e}" + (f": {e.filename}" if e.filename else ""), file=sys.stderr)
    return 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="standin",
        description="Mask personal data in CSV files with keyed stand-ins that keep their shape.",
        epilog="Exit status: 0 done, 1 check found something, 2 couldn't run.",
    )
    p.add_argument("--version", action="version", version=f"standin {__version__}")
    sub = p.add_subparsers(required=True, metavar="command")

    csv_options = argparse.ArgumentParser(add_help=False)
    csv_options.add_argument("--encoding", help="text encoding of the input (default: UTF-8, or UTF-16 if the file starts with its mark)")
    csv_options.add_argument("--delimiter", help="field delimiter (default: worked out from the header)")

    k = sub.add_parser("keygen", help="write a new random key to a file")
    k.add_argument("key", type=Path, help="where to write the key; it must not exist yet")
    k.set_defaults(run=keygen)

    s = sub.add_parser("scan", parents=[csv_options], help="print suggested rules for some CSV files")
    s.add_argument("files", nargs="+", type=Path)
    s.add_argument("--rows", type=int, default=SAMPLE_ROWS, help=f"rows to sample from each file (default {SAMPLE_ROWS:,})")
    s.set_defaults(run=suggest)

    m = sub.add_parser("mask", parents=[csv_options], help="write masked copies of CSV files")
    m.add_argument("files", nargs="+", type=Path)
    m.add_argument("-r", "--rules", type=Path, required=True, help="rules file")
    m.add_argument("-k", "--key", type=Path, required=True, help="key file from standin keygen")
    m.add_argument("-o", "--out", type=Path, required=True, help="folder for the masked files, which keep their names")
    m.set_defaults(run=mask)

    c = sub.add_parser("check", parents=[csv_options], help="look for anything a masked file still gives away")
    c.add_argument("original", type=Path)
    c.add_argument("masked", type=Path)
    c.add_argument("-r", "--rules", type=Path, required=True, help="the rules file it was masked with")
    c.set_defaults(run=verify)
    return p


def keygen(args: argparse.Namespace) -> int:
    try:
        fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Problem(
            f"{args.key} already exists, and standin never replaces a file. A new key gives every value a "
            f"different stand-in, so keep using the old one if masked files need to keep matching"
        ) from None
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(KEY_BYTES) + "\n")
    print(f"Wrote a new key to {args.key}. With it, stand-ins can be traced back to real values, so store it like a password.", file=sys.stderr)
    return 0


def load_key(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="ascii").strip()
        key = bytes.fromhex(text)
    except OSError as e:
        raise Problem(f"can't read the key file {path}: {e.strerror}") from None
    except (UnicodeDecodeError, ValueError):
        key = b""
    if len(key) != KEY_BYTES:
        raise Problem(f"{path} doesn't hold a standin key (64 hex digits). Make one with: standin keygen NEW-KEY-FILE")
    mode = os.stat(path).st_mode
    if os.name == "posix" and stat.S_ISREG(mode) and mode & 0o077:
        print(f"standin: warning: other users can read {path}. chmod 600 {path} to stop that.", file=sys.stderr)
    return key


def suggest(args: argparse.Namespace) -> int:
    formats = [sniff(p, args.encoding, args.delimiter) for p in args.files]
    sys.stdout.write(scan(args.files, formats, args.rows))
    return 0


def mask(args: argparse.Namespace) -> int:
    rules = load(args.rules)
    key = load_key(args.key)
    names = Counter(p.name for p in args.files)
    if dupes := [n for n, c in names.items() if c > 1]:
        raise Problem(f"more than one input is called {dupes[0]}, and they'd all be written to {args.out / dupes[0]}")
    # Everything that can be checked before reading data is checked first, so a
    # mistake shows up in a second rather than an hour into a big file.
    formats = [sniff(p, args.encoding, args.delimiter) for p in args.files]
    for p, fmt in zip(args.files, formats):
        header, _ = table(p, fmt)
        plan(rules, header, key, str(p))
    dests = [args.out / p.name for p in args.files]
    for d in dests:
        if os.path.lexists(d):
            raise Problem(f"{d} already exists, and standin never replaces a file. Move it, or choose another --out")
    args.out.mkdir(parents=True, exist_ok=True)

    counts = Counter()
    staged = []
    try:
        # Every file is written before any is published, so a failure in the
        # third file doesn't leave two masked files that look like a full set.
        for src, fmt, dest in zip(args.files, formats, dests):
            staged.append((write(dest, fmt, masked_rows(src, fmt, rules, key, counts)), dest))
        for tmp, dest in staged:
            try:
                publish(tmp, dest)
            except FileExistsError:
                raise Problem(f"{dest} was created by something else while standin ran. It was left alone, and the files after it weren't written") from None
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
    for src, dest in zip(args.files, dests):
        print(f"{src} -> {dest} ({counts[src]:,} rows)", file=sys.stderr)
    return 0


def masked_rows(src: Path, fmt, rules: dict, key: bytes, counts: Counter):
    header, rows = table(src, fmt)
    out_header, mask_row = plan(rules, header, key, str(src))
    yield out_header
    show = sys.stderr.isatty()
    for line, row in rows:
        try:
            yield mask_row(row)
        except BadCell as e:
            raise Problem(f"{src} line {line}: {e}") from None
        counts[src] += 1
        if show and counts[src] % PROGRESS_EVERY == 0:
            print(f"\r{src}: {counts[src]:,} rows", end="", file=sys.stderr)
    if show and counts[src] >= PROGRESS_EVERY:
        print(file=sys.stderr)


def verify(args: argparse.Namespace) -> int:
    rules = load(args.rules)
    fmt_o = sniff(args.original, args.encoding, args.delimiter)
    fmt_m = sniff(args.masked, args.encoding, args.delimiter)
    rows, findings = check(rules, args.original, args.masked, fmt_o, fmt_m)
    for f in findings:
        print(f)
    if findings:
        print(f"{len(findings)} problem{'s' if len(findings) > 1 else ''} in {args.masked} ({rows:,} rows compared)")
        return 1
    print(f"OK: {rows:,} rows compared, nothing from the masked columns of {args.original} found in {args.masked}")
    return 0
