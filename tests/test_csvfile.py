import codecs
import errno
import os

import pytest

from standin import Problem
from standin.csvfile import Format, publish, sniff, table, write

UTF8 = Format("utf-8", ",", "\n")


def roundtrip(tmp_path, raw: bytes, **sniff_args) -> tuple[bytes, list[str], list[list[str]]]:
    """Write raw bytes, read them as a table and write the table back out."""
    src = tmp_path / "in.csv"
    src.write_bytes(raw)
    fmt = sniff(src, **sniff_args)
    header, rows = table(src, fmt)
    body = [row for _, row in rows]
    out = tmp_path / "out.csv"
    publish(write(out, fmt, [header, *body]), out)
    return out.read_bytes(), header, body


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_delimiter_is_worked_out_from_the_header(tmp_path, delimiter):
    raw = delimiter.join(["id", "name", "note"]).encode() + b"\n1" + delimiter.encode() + b"a" + delimiter.encode() + b'"x,y;z"\n'
    out, header, body = roundtrip(tmp_path, raw)
    assert header == ["id", "name", "note"]
    assert body == [["1", "a", "x,y;z"]]


def test_delimiters_inside_a_quoted_header_name_dont_count(tmp_path):
    _, header, _ = roundtrip(tmp_path, b'"Name, full";"Email; work";id\na;b;c\n')
    assert header == ["Name, full", "Email; work", "id"]


def test_line_endings_bom_and_quoted_newlines_come_back_as_they_were(tmp_path):
    raw = codecs.BOM_UTF8 + 'id,note\r\n1,"two\r\nlines"\r\n2,Zoë\r\n'.encode()
    out, header, body = roundtrip(tmp_path, raw)
    assert out == raw
    assert header == ["id", "note"]  # the byte order mark isn't part of the first name


def test_utf16_from_excel_is_read_and_written_as_utf16(tmp_path):
    raw = "id\tname\r\n1\t王小明\r\n".encode("utf-16")
    out, header, body = roundtrip(tmp_path, raw)
    assert body == [["1", "王小明"]]
    assert out == raw


def test_other_encodings_work_when_named(tmp_path):
    raw = "id,name\n1,Zoë\n".encode("cp1252")
    out, _, body = roundtrip(tmp_path, raw, encoding="cp1252")
    assert body == [["1", "Zoë"]] and out == raw


def test_wrong_encoding_says_roughly_where_and_what_to_try(tmp_path):
    src = tmp_path / "in.csv"
    src.write_bytes(b"id,name\n" + b"1,ok\n" * 3 + "4,Zoë\n".encode("cp1252"))
    with pytest.raises(Problem, match=r"near line \d+.*--encoding cp1252"):
        list(table(src, sniff(src))[1])


def test_an_unknown_encoding_is_refused_up_front(tmp_path):
    (tmp_path / "in.csv").write_text("a\n1\n")
    with pytest.raises(Problem, match="isn't an encoding"):
        sniff(tmp_path / "in.csv", encoding="utf-9")


def test_an_unclosed_quote_stops_instead_of_swallowing_the_rest_of_the_file(tmp_path):
    # Read loosely, line 3 would open a quote and every later row would end up
    # inside one cell of a column that might be marked keep.
    src = tmp_path / "in.csv"
    src.write_text('id,note\n1,fine\n2,"oops\n3,Jane Citizen\n4,jane@example.com\n')
    with pytest.raises(Problem, match="line 3 is badly quoted"):
        list(table(src, sniff(src))[1])


def test_a_row_with_the_wrong_number_of_fields_is_refused_with_its_line(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text('id,name\n1,"multi\nline"\n2,Jane,Citizen\n')
    with pytest.raises(Problem, match="line 4 has 3 fields but the header has 2.*unquoted comma"):
        list(table(src, sniff(src))[1])


def test_blank_lines_are_skipped_and_an_empty_file_is_refused(tmp_path):
    _, header, body = roundtrip(tmp_path, b"\n\nid\n1\n\n2\n")
    assert header == ["id"] and body == [["1"], ["2"]]
    (tmp_path / "empty.csv").write_bytes(b"")
    with pytest.raises(Problem, match="is empty"):
        table(tmp_path / "empty.csv", UTF8)


def test_cells_bigger_than_the_csv_default_limit_are_fine(tmp_path):
    big = "x" * 1_000_000
    _, _, body = roundtrip(tmp_path, f"id,blob\n1,{big}\n".encode())
    assert body == [["1", big]]


def test_a_missing_input_is_a_problem_not_a_traceback(tmp_path):
    with pytest.raises(Problem, match="can't read"):
        sniff(tmp_path / "nope.csv")


def test_publish_never_replaces_an_existing_file(tmp_path):
    dest = tmp_path / "out.csv"
    dest.write_text("precious")
    tmp = write(dest, UTF8, [["a"], ["1"]])
    with pytest.raises(FileExistsError):
        publish(tmp, dest)
    assert dest.read_text() == "precious"
    assert os.listdir(tmp_path) == ["out.csv"]


def test_publish_works_where_hard_links_dont(tmp_path, monkeypatch):
    # exFAT and FAT32 drives refuse hard links, so the fallback has to keep
    # the same promise: the file appears whole, or not at all.
    def no_links(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    dest = tmp_path / "out.csv"
    publish(write(dest, UTF8, [["a"], ["1"]]), dest)
    assert dest.read_text() == "a\n1\n"
    tmp = write(dest, UTF8, [["b"]])
    with pytest.raises(FileExistsError):
        publish(tmp, dest)
    assert dest.read_text() == "a\n1\n"
    assert os.listdir(tmp_path) == ["out.csv"]


def test_a_failure_while_writing_leaves_nothing_behind(tmp_path):
    def rows():
        yield ["a"]
        yield ["1"]
        raise Problem("bad row")

    with pytest.raises(Problem):
        write(tmp_path / "out.csv", UTF8, rows())
    assert os.listdir(tmp_path) == []
