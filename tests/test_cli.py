import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import standin.cli
from standin.cli import main
from synth import CUSTOMER_HEADER, CUSTOMER_RULES, TRANSACTION_HEADER, customers, read_csv, transactions, write_csv

ROOT = Path(__file__).resolve().parent.parent


def extract(tmp_path: Path, n_customers: int = 200, n_transactions: int = 1000) -> tuple[Path, Path, Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    cust = write_csv(data / "customers.csv", CUSTOMER_HEADER, customers(n_customers))
    txns = write_csv(data / "transactions.csv", TRANSACTION_HEADER, transactions(n_transactions, n_customers))
    rules = tmp_path / "rules.toml"
    rules.write_text(CUSTOMER_RULES)
    # A fixed key keeps every run identical. keygen has its own test.
    key = tmp_path / "key"
    key.write_text(bytes(range(32)).hex())
    if os.name == "posix":
        key.chmod(0o600)
    return cust, txns, rules, key


def run_mask(rules, key, out, *files) -> int:
    return main(["mask", "-r", str(rules), "-k", str(key), "-o", str(out), *map(str, files)])


def test_masked_files_still_join_and_pass_check(tmp_path):
    cust, txns, rules, key = extract(tmp_path)
    out = tmp_path / "out"
    assert run_mask(rules, key, out, cust, txns) == 0

    before_c, after_c = read_csv(cust), read_csv(out / "customers.csv")
    before_t, after_t = read_csv(txns), read_csv(out / "transactions.csv")
    assert len(after_c) == len(before_c) and len(after_t) == len(before_t)
    # Every transaction's customer is the same stand-in in both files.
    stand_in = {b[0]: a[0] for b, a in zip(before_c[1:], after_c[1:])}
    assert all(stand_in[b[1]] == a[1] for b, a in zip(before_t[1:], after_t[1:]))
    assert len(set(stand_in.values())) == len(stand_in)

    assert main(["check", "-r", str(rules), str(cust), str(out / "customers.csv")]) == 0
    assert main(["check", "-r", str(rules), str(txns), str(out / "transactions.csv")]) == 0


def test_same_key_same_output_and_a_new_key_changes_everything(tmp_path):
    cust, _, rules, key = extract(tmp_path, 50, 0)
    assert run_mask(rules, key, tmp_path / "a", cust) == 0
    assert run_mask(rules, key, tmp_path / "b", cust) == 0
    assert (tmp_path / "a/customers.csv").read_bytes() == (tmp_path / "b/customers.csv").read_bytes()
    assert main(["keygen", str(tmp_path / "key2")]) == 0
    assert run_mask(rules, tmp_path / "key2", tmp_path / "c", cust) == 0
    a, c = read_csv(tmp_path / "a/customers.csv"), read_csv(tmp_path / "c/customers.csv")
    assert all(x[0] != y[0] for x, y in zip(a[1:], c[1:]))


def test_check_catches_an_unmasked_copy(tmp_path, capsys):
    cust, _, rules, _ = extract(tmp_path, 20, 0)
    assert main(["check", "-r", str(rules), str(cust), str(cust)]) == 1
    out = capsys.readouterr().out
    assert '"email" (token): 20 cells unchanged' in out
    assert "@example.com" not in out  # findings name columns and lines, never values


def test_existing_output_is_never_replaced(tmp_path, capsys):
    cust, _, rules, key = extract(tmp_path, 5, 0)
    out = tmp_path / "out"
    out.mkdir()
    (out / "customers.csv").write_text("precious")
    assert run_mask(rules, key, out, cust) == 2
    assert "already exists" in capsys.readouterr().err
    assert (out / "customers.csv").read_text() == "precious"
    # Masking into the input's own folder is the same case.
    assert run_mask(rules, key, cust.parent, cust) == 2


def test_a_bad_cell_in_the_second_file_publishes_neither(tmp_path, capsys):
    cust, txns, rules, key = extract(tmp_path, 5, 50)
    rows = read_csv(txns)
    rows[-1][2] = "31/12/2025"
    write_csv(txns, rows[0], rows[1:])
    out = tmp_path / "out"
    assert run_mask(rules, key, out, cust, txns) == 2
    err = capsys.readouterr().err
    assert "line 51: column \"posted\" isn't a date in the format %Y-%m-%d" in err
    assert "31/12/2025" not in err
    assert os.listdir(out) == []


def test_a_column_with_no_rule_stops_before_anything_is_written(tmp_path, capsys):
    cust, _, rules, key = extract(tmp_path, 5, 0)
    rules.write_text(CUSTOMER_RULES.replace('phone = "token"\n', ""))
    assert run_mask(rules, key, tmp_path / "out", cust) == 2
    assert 'no rule: "phone"' in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_full_disk_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    cust, _, rules, key = extract(tmp_path, 5, 0)

    def full(fd):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(os, "fsync", full)
    assert run_mask(rules, key, tmp_path / "out", cust) == 2
    assert os.strerror(errno.ENOSPC) in capsys.readouterr().err
    assert os.listdir(tmp_path / "out") == []


def test_out_must_be_a_folder_and_rows_must_be_positive(tmp_path, capsys):
    cust, _, rules, key = extract(tmp_path, 5, 0)
    out = tmp_path / "out"
    out.write_text("precious")
    assert run_mask(rules, key, out, cust) == 2
    assert "--out needs a folder" in capsys.readouterr().err
    assert out.read_text() == "precious"
    assert main(["scan", "--rows", "0", str(cust)]) == 2
    assert "--rows must be at least 1" in capsys.readouterr().err


def test_keygen_never_replaces_a_key(tmp_path, capsys):
    key = tmp_path / "key"
    assert main(["keygen", str(key)]) == 0
    first = key.read_text()
    assert len(first.strip()) == 64
    assert main(["keygen", str(key)]) == 2
    assert key.read_text() == first
    if os.name == "posix":
        assert key.stat().st_mode & 0o777 == 0o600


def test_a_bad_key_file_is_refused_and_a_readable_one_is_warned_about(tmp_path, capsys):
    cust, _, rules, key = extract(tmp_path, 5, 0)
    bad = tmp_path / "bad"
    bad.write_text("not a key\n")
    assert run_mask(rules, bad, tmp_path / "out", cust) == 2
    assert "doesn't hold a standin key" in capsys.readouterr().err
    if os.name == "posix":
        key.chmod(0o644)
        assert run_mask(rules, key, tmp_path / "out", cust) == 0
        assert "other users can read" in capsys.readouterr().err


def test_scan_writes_rules_that_mask_runs_with(tmp_path, capsys):
    cust, txns, _, key = extract(tmp_path, 300, 300)
    assert main(["scan", str(cust), str(txns)]) == 0
    rules = tmp_path / "suggested.toml"
    rules.write_text(capsys.readouterr().out)
    assert run_mask(rules, key, tmp_path / "out", cust, txns) == 0
    assert main(["check", "-r", str(rules), str(cust), str(tmp_path / "out/customers.csv")]) == 0


def test_stopping_with_ctrl_c_leaves_no_files(tmp_path, monkeypatch, capsys):
    cust, _, rules, key = extract(tmp_path, 50, 0)
    real_plan = standin.cli.plan

    def plan_then_stop(*args):
        header, mask_row = real_plan(*args)
        rows = iter(range(10))

        def interrupted(row):
            if next(rows, None) is None:
                raise KeyboardInterrupt
            return mask_row(row)

        return header, interrupted

    monkeypatch.setattr(standin.cli, "plan", plan_then_stop)
    assert run_mask(rules, key, tmp_path / "out", cust) == 130
    assert "stopped" in capsys.readouterr().err
    assert os.listdir(tmp_path / "out") == []


@pytest.mark.skipif(os.name != "posix", reason="sends a real SIGINT")
def test_a_real_interrupt_mid_file_leaves_no_files(tmp_path):
    cust, _, rules, key = extract(tmp_path, 1, 0)
    write_csv(cust, CUSTOMER_HEADER, customers(200_000))
    out = tmp_path / "out"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "standin", "mask", "-r", str(rules), "-k", str(key), "-o", str(out), str(cust)],
        env=env, stderr=subprocess.PIPE, text=True,
    )
    # Wait for the temporary file to exist, so the signal lands mid-write.
    # The limits are generous because CI runners can be slow and busy.
    deadline = time.monotonic() + 60
    while not (out.exists() and os.listdir(out)) and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert proc.poll() is None, "finished before it could be interrupted"
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert os.listdir(out) == []


def test_runs_as_a_module_and_reports_its_version():
    result = subprocess.run([sys.executable, "-m", "standin", "--version"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0 and result.stdout.startswith("standin ")
