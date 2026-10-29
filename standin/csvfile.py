"""Reading and writing CSV files without ever damaging one.

Input files are only opened for reading. Output goes to a hidden temporary file
beside its destination, is flushed to disk, and only then is linked into place,
and linking fails if a file of that name is already there. A run that is
stopped, crashes or meets a bad row therefore leaves either the complete output
or nothing, and never touches a file it didn't create.

Real exports come in more than one flavour, so the encoding (UTF-8, with or
without a byte order mark, or UTF-16), the delimiter and the line ending are
read from the file and the output is written the same way.
"""

import codecs
import csv
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import Problem

SNIFF_BYTES = 64 * 1024
DELIMITERS = ",;\t|"  # in order of preference when a header has none of them, or a tie

# A cell can be bigger than csv's 128 KB default as long as it fits in memory.
# 2**31 - 1 is the largest limit every platform's C long can hold.
csv.field_size_limit(2**31 - 1)


@dataclass(frozen=True)
class Format:
    encoding: str
    delimiter: str
    lineterminator: str


def sniff(path: Path, encoding: str | None = None, delimiter: str | None = None) -> Format:
    """Work out how path is written. An encoding or delimiter passed in wins."""
    try:
        with open(path, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError as e:
        raise Problem(f"can't read {path}: {e.strerror}") from None
    if encoding is None:
        if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8"
    else:
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise Problem(f"{encoding} isn't an encoding Python knows. Common ones are utf-8, cp1252 and utf-16") from None
    if codecs.lookup(encoding).name == "utf-8" and head.startswith(codecs.BOM_UTF8):
        # utf-8-sig drops the mark on reading and writes it back, so Excel
        # still opens the output as UTF-8 and the first column keeps its name.
        encoding = "utf-8-sig"

    first = head.decode(encoding, errors="replace").split("\n", 1)[0]
    if delimiter is None:
        bare = re.sub(r'"[^"]*"', "", first)  # a quoted header may contain any delimiter
        delimiter = max(DELIMITERS, key=bare.count) if any(d in bare for d in DELIMITERS) else ","
    elif len(delimiter) != 1:
        raise Problem(f"the delimiter must be one character, not {delimiter!r}")
    return Format(encoding, delimiter, "\r\n" if first.endswith("\r") else "\n")


def read(path: Path, fmt: Format) -> Iterator[tuple[int, list[str]]]:
    """Yield (line number, row) for every record, the header first.

    The line number is the line the record ends on, which is what an editor
    shows. Blank lines come through as empty rows. Raises Problem for bytes that
    aren't in the encoding and for broken quoting.
    """
    try:
        f = open(path, newline="", encoding=fmt.encoding)
    except OSError as e:
        raise Problem(f"can't read {path}: {e.strerror}") from None
    with f:
        # Without strict, a value that opens a quote and never closes it
        # swallows the rest of the file into one cell, and if that column is
        # kept, every row after it comes through unmasked inside that cell.
        reader = csv.reader(f, delimiter=fmt.delimiter, strict=True)
        start = 1
        try:
            for row in reader:
                yield reader.line_num, row
                start = reader.line_num + 1
        except UnicodeDecodeError:
            # Text is decoded a chunk at a time, so the bad bytes are at or
            # shortly after this line, not necessarily on it.
            raise Problem(
                f"{path} has bytes that aren't {fmt.encoding} near line {start}. "
                f"If it came from Excel on Windows, try --encoding cp1252"
            ) from None
        except csv.Error as e:
            raise Problem(
                f"{path} line {start} is badly quoted ({e}). A value that starts with a quote has to end "
                f"with one, followed by the delimiter or the end of the line"
            ) from None


def table(path: Path, fmt: Format) -> tuple[list[str], Iterator[tuple[int, list[str]]]]:
    """Return the header and an iterator of (line number, row) for the data.

    Blank lines are skipped. A row with a different number of fields from the
    header raises Problem, because its cells would land under the wrong
    columns and a name could end up in a column marked keep.
    """
    records = read(path, fmt)
    for _, header in records:
        if header:
            break
    else:
        raise Problem(f"{path} is empty")

    def rows() -> Iterator[tuple[int, list[str]]]:
        for line, row in records:
            if not row:
                continue
            if len(row) != len(header):
                name = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(fmt.delimiter, repr(fmt.delimiter))
                raise Problem(
                    f"{path} line {line} has {len(row)} fields but the header has {len(header)}. "
                    f"That usually means a value with an unquoted {name} in it, or the wrong --delimiter"
                )
            yield line, row

    return header, rows()


def write(dest: Path, fmt: Format, rows: Iterable[list[str]]) -> Path:
    """Write rows to a hidden temporary file beside dest, and return its path.

    The file is on disk when this returns. If rows raises, or anything else goes
    wrong, the temporary file is removed and the error passes through.
    """
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    try:
        with open(fd, "w", newline="", encoding=fmt.encoding) as f:
            csv.writer(f, delimiter=fmt.delimiter, lineterminator=fmt.lineterminator).writerows(rows)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return Path(tmp)


def publish(tmp: Path, dest: Path) -> None:
    """Move tmp to dest, never replacing a file already at dest.

    Raises FileExistsError if dest exists. tmp is gone afterwards either way.
    """
    try:
        os.link(tmp, dest)
    except FileExistsError:
        os.unlink(tmp)
        raise
    except OSError:
        # exFAT, FAT32 and some network shares have no hard links. Claiming the
        # name with O_EXCL and then renaming over that empty claim still never
        # replaces someone else's file.
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        except BaseException:
            os.unlink(tmp)
            raise
        try:
            os.replace(tmp, dest)
        except BaseException:
            os.unlink(dest)
            os.unlink(tmp)
            raise
    else:
        os.unlink(tmp)
