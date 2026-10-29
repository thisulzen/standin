# standin

standin replaces the personal data in CSV extracts with stand-ins that look like the real thing, so a copy of production data can go to a test environment or a vendor without the people in it.

```bash
standin keygen masking.key
standin scan customers.csv transactions.csv > rules.toml    # a first draft: read it and fix it
standin mask -r rules.toml -k masking.key -o masked customers.csv transactions.csv
standin check -r rules.toml customers.csv masked/customers.csv
```

Two rows of `examples/customers.csv`, before and after:

```
customer_id,full_name,email,phone,date_of_birth,card_number,balance,notes,status
C000101,Jane Citizen,jane.citizen@example.com,+61 412 345 678,1987-03-04,4111 1111 1111 1111,1520.40,Called about card fees,active
C000103,王小明,xiaoming.wang@example.net,0423 111 222,1975-06-15,3782 822463 10005,98000.00,Prefers Mandarin,active

customer_id,full_name,email,phone,date_of_birth,card_number,balance,notes,status
Q432618,Jose Russo,leur.jofcjtz@lwbwjyj.xqx,+58 654 948 945,1986-09-26,4111 1197 3885 5580,1520.40,,active
K549083,Ravi,qlcbjxkc.zdve@omzznna.usi,6507 911 066,1975-01-11,3782 821296 34216,98000.00,,active
```

The customer ID becomes another ID of the same shape. It's the same stand-in in every file masked with the same key, so `transactions.csv` still joins to `customers.csv`. Two different IDs can never get the same stand-in. The card keeps its first six digits and still passes the Luhn check, the birth date moves by a few months, the name becomes a made-up one and the notes are emptied.

## Status

Done: the four commands (`keygen`, `scan`, `mask`, `check`) and seven rules. Files of any size are streamed, in UTF-8, UTF-16 or any other encoding you name, with any common delimiter. It never overwrites a file and never leaves a half-written one. CI runs the tests on Linux, macOS and Windows with Python 3.11 and 3.13, then installs it with pipx, runs the walkthrough below and uninstalls it.

Not done: listed under [Not done](#not-done).

## Install

You need Python 3.11 or later and [pipx](https://pipx.pypa.io/). Getting pipx:

- macOS: `brew install pipx`
- Ubuntu or Debian: `sudo apt install pipx`
- Windows: `py -m pip install --user pipx`, then use `py -m pipx` wherever this README says `pipx`

Then:

```bash
pipx install git+https://github.com/thisulzen/standin
standin --version
```

Without git, download the repository as a zip from GitHub, unzip it and run `pipx install ./standin-main`. If you'd rather use a virtual environment of your own, `pip install` into it works the same way.

At run time it uses nothing outside the Python standard library.

## Remove

```bash
pipx uninstall standin
```

That removes the program and everything pipx installed for it. Your key, rules and masked files are yours and stay where you put them.

## Walkthrough

`examples/` holds a small made-up extract (five customers and their transactions) and a rules file for it.

```bash
cd examples
standin keygen ../demo.key
standin mask -r rules.toml -k ../demo.key -o ../masked customers.csv transactions.csv
standin check -r rules.toml customers.csv ../masked/customers.csv
```

```
Wrote a new key to ../demo.key. With it, stand-ins can be traced back to real values, so store it like a password.
customers.csv -> ../masked/customers.csv (5 rows)
transactions.csv -> ../masked/transactions.csv (8 rows)
OK: 5 rows compared, nothing from the masked columns of customers.csv found in ../masked/customers.csv
```

Your output will differ from the table above, because your key is different.

For your own data, start with `standin scan`. It reads the header and the first 1,000 rows of each file and writes a rules file with a reason beside every guess:

```
customer_id = "token"   # header looks like an identifier, contact detail or address
card_number = "card"    # values look like card numbers (they pass the Luhn check)
status = "blank"        # nothing recognised, so blanked to be safe. Change it to keep if it isn't personal
```

They are guesses. Read every line before you use it. A column scan can't place is blanked, so a rules file used unread errs towards losing a column, not leaking one.

## The rules file

A [TOML](https://toml.io) file with one line per column:

```toml
customer_id = "token"
full_name = "name"
card_number = "card"
notes = "blank"
balance = "keep"
date_of_birth = { rule = "date", by = "customer_id", days = 180 }
"Date opened" = { rule = "date", format = "%d/%m/%Y" }
```

| Rule | What it does | For |
|---|---|---|
| `keep` | Leaves the value as it is. | amounts, statuses, anything that isn't personal |
| `drop` | Removes the column. | columns nobody downstream needs |
| `blank` | Empties every cell. | notes, comments, free text |
| `token` | Replaces each letter with a letter and each digit with a digit, keeping the case, the punctuation and the length. Two different values never get the same token. | IDs, account numbers, email addresses, phone numbers, postcodes |
| `card` | Keeps the first six digits (the issuer), replaces the rest and recomputes the check digit. | card numbers, with or without spaces or dashes |
| `date` | Moves the date by 1 to `days` days either way (30 by default), never by zero. With `by`, every date belonging to one value of that column moves by the same amount. `format` is a [strftime format](https://docs.python.org/3/library/datetime.html#format-codes), `%Y-%m-%d` by default. | birth dates, event dates |
| `name` | Replaces the value with a made-up name of the same number of words, never the person's own. Works for names in any script. | people's names |

A few things apply to every rule:

- An empty cell stays empty.
- One rule covers a column name in every file, which is what keeps `customer_id` the same stand-in everywhere. If two files use one name for different things, give it the stricter rule.
- A column name with spaces, dots or anything but letters, digits, `_` and `-` goes in quotes, as `"Date opened"` does above.
- A column that's in a file but not in the rules stops the run, before anything is written. That's deliberate: the risky column is the one someone adds to the export next month, and it should fail loudly instead of leaking quietly.
- `token` stops on a value with letters or digits outside A to Z and 0 to 9 (`Zoë`, `王小明`), because it can't replace them and would leave them readable. Use `name` or `blank` for those columns.

Error messages name the file, the line and the column, but never the value, so nothing personal lands in a terminal or a CI log.

## The key

`standin keygen` writes 32 random bytes as hex, readable only by you. The same key and the same input always give the same output, on any machine, so masking next month's extract with the same key keeps every stand-in the same. A new key gives everything new stand-ins.

Anyone with the key can work backwards from `token` and `card` stand-ins to the real values, and from those to how far each date moved. Keep it where you keep passwords, and never next to the masked files. standin warns if other users can read the key file. To take it from a secrets manager without writing it to disk, use your shell's process substitution: `-k <(your-secrets-command)`.

## What check looks for

`standin check -r RULES ORIGINAL MASKED` reads both files side by side, without the key, and reports:

- masked cells that are still the same as the original;
- `blank` columns with something in them;
- `keep` columns that changed, which usually means the file was masked with different rules;
- `keep` columns holding a value from a masked column of the original, such as an email address copied into a contact column;
- `keep` columns with anything that looks like an email address or a card number.

It exits 0 when it finds nothing, 1 when it finds something, and 2 when it can't compare the files at all. Like every error, its findings name columns and lines, not values. Finding nothing isn't proof that nothing leaked (see [what masking can't do](#what-masking-cant-do)).

## Design

### Stand-ins that can't collide

A hash looks like the obvious way to make a stand-in, but a hash cut down to the original's shape collides. Hash 100,000 customer IDs of the form `C` plus six digits back into that million-value space and about 5,000 pairs of customers end up sharing an ID, so joins and primary keys quietly break.

So `token` is a permutation, the same construction as NIST's format-preserving encryption mode FF1. The letters and digits of a value are read as one number (base 10 for each digit, 26 for each letter), the number goes through a 10-round Feistel network keyed by your key, and the result is written back in the same pattern. A Feistel network is a permutation whatever its round function is, so two inputs can't share an output. When the network's square domain is bigger than the value's, the result is fed back in until it lands inside, which is called cycle walking and still gives a permutation. Punctuation and the pattern of letters and digits go into the round function as a tweak, so `AB-12` and `AB/12` are permuted independently.

The round function is keyed BLAKE2b. FF1 uses AES, and the standard library has none. So this isn't FF1 and hasn't been reviewed as a cipher. It's meant for masking test data, not for encrypting anything. It also costs something: anyone with the key can run it backwards, and in a dense ID space a stand-in is often another real customer's ID (`C000123` might become `C004567`, which belongs to someone else in the original). That's a meaningless pairing, not a leak, but don't load masked and real data into one database and assume the two sets of IDs can't meet.

Short values get little protection. A two-digit code has 100 possible tokens, and a permutation that small can leave a value unchanged by chance, which `check` will report.

### Dates

Moving every date by a random amount would hide the most, but it would also break every interval: a customer's account would close before it opened. With `by = "customer_id"`, all of one customer's dates move by the same keyed amount, so intervals and ages stay true to within the shift. The cost is that anyone who knows one real date for a customer can work out that customer's shift, and from it all their other dates.

### Nothing half-written, nothing overwritten

Input files are only ever opened for reading. Output goes to a hidden temporary file beside its destination, is flushed to disk with `fsync`, and is then hard-linked into place. A hard link fails rather than replace an existing file, so there's no window in which standin could clobber one. exFAT, FAT32 and some network shares have no hard links, and there standin claims the name with `O_EXCL` first, then renames over its own empty claim.

When you mask several files at once, every file is written before any is published, so a bad row in the third file doesn't leave two masked files that look like a complete set. Stopping it with Ctrl-C has the same effect.

### Stopping instead of guessing

Besides unknown columns, standin stops on:

- a row with a different number of fields from the header, since its cells would land under the wrong columns and a name could end up under one marked `keep`;
- a quote that opens a value and never closes, which would otherwise swallow the rest of the file into one cell;
- bytes that aren't in the file's encoding.

Each message says what to change.

### What masking can't do

Masked data is pseudonymised, not anonymous. With the key, it can be linked back. Without the key, people can sometimes still be picked out from what you kept: a rare combination of postcode, birth year and balance can be enough. Free text can hold anything, so blank it. Treat masked extracts with the care your privacy obligations ask for, and decide what to keep with that in mind.

## Performance

Measured on a 2018 laptop (Intel i5-8259U, macOS 15.7.9, Python 3.12.7), on synthetic files from `tests/synth.py`, using one core:

| Job | Size | Time | Rate | Peak memory |
|---|---|---|---|---|
| `mask` customers, six masked columns | 1,000,000 rows, 119 MB | 190 s | 5,250 rows/s | 80 MB |
| `mask` transactions, three masked columns | 1,000,000 rows, 54 MB | 87 s | 11,500 rows/s | 59 MB |
| `check` the masked customers | 1,000,000 rows | 11 s | 88,000 rows/s | 500 MB |

Masking takes memory that doesn't grow with the file. Almost all its time goes on the Feistel rounds in pure Python, about 18 µs a value. `check` keeps every distinct masked value in memory, which is where its 500 MB goes.

## What's tested

87 tests, `python -m pytest`:

- `test_shape.py`: the permutation is one to one on every domain size tried (including sizes either side of a perfect square, where cycle walking works hardest), it depends on the key and the tweak, it keeps length, case and punctuation, and changing the last letter of a 300-letter value changes the whole stand-in.
- `test_rules.py`: every rule, every way a rules file can be wrong, columns with no rule, 2,000 cards with no repeated stand-in, Luhn digits against published test numbers, `by` keeping a customer's gaps, error messages that don't quote values, and names never coming back as themselves.
- `test_csvfile.py`: every delimiter, a quoted header containing delimiters, CRLF and byte order marks coming back exactly, UTF-16 from Excel, cp1252, an unclosed quote, ragged rows, blank lines, a 1 MB cell, never overwriting, and the exFAT fallback.
- `test_scan.py` and `test_check.py`: guesses for a synthetic bank extract, column names TOML finds awkward, the stricter guess winning, and each kind of leak `check` reports.
- `test_cli.py`: two files masked together still join and pass `check`; the same key gives byte-identical output; a bad row in the second file publishes neither; a full disk, Ctrl-C and a real `SIGINT` halfway through 200,000 rows all leave no files behind.

To check the tests could fail, I broke the code on purpose in 23 ways (Luhn digit not recomputed, zero-day date shifts, loose CSV quoting, a publish that overwrites, temporary files left behind, and so on). Each one made a test fail. One got through at first: `check` could stop looking for masked values in kept columns without any test noticing. `test_check.py` now covers it.

On top of the tests, I masked onto a real exFAT disk image, where `os.link` fails with "Operation not supported", and the fallback published the file whole and refused to replace it. I also ran the million-row files above through `mask` and `check`.

## What testing turned up

- Python's `csv` module, by default, reads a value that opens a quote and never closes it as one cell running to the end of the file. If that cell was in a `keep` column, every row after it would have come through unmasked, inside it. standin reads with `strict=True` now, and stops with the line where the bad value starts.
- The fake-name lists hold common real names, so now and then a customer drew their own name. `check` caught it in one run of sixty. `name` now draws again when that happens.
- `scan` read ISO dates such as `2025-07-01` as phone numbers (eight digits and some dashes) and would have tokenised them. It tests for dates first now.
- The first round function was HMAC-SHA256, which took 425 seconds for the million customers. Keyed BLAKE2b is six times faster per call on this machine, and the same run took 190 seconds.
- On Windows, `standin scan > rules.toml` would have written the file in the ANSI code page, and PowerShell 5 writes redirected output as UTF-16. Either would break a non-English column name. Output is now always UTF-8, and rules files load as UTF-8 with or without a byte order mark, or as UTF-16. I found this by reading the code, not on a Windows machine.

## Not done

- CSV only. No Excel, Parquet or JSON, no database connections, no gzip, and no stdin or stdout.
- One rule per column name across all files, with no per-file overrides.
- `token` can't replace letters outside A to Z. `name` and `blank` cover those columns.
- Nothing finds names or numbers inside free text. Blank free-text columns, or run a tool built for it, such as [Presidio](https://microsoft.github.io/presidio/), first.
- No measure of how identifiable the masked data still is (k-anonymity and similar).
- One core. Splitting rows across processes is the obvious next step if a million rows in three minutes is too slow.
- There's no command to reverse masking, on purpose.
- On Windows it's been tested in CI (tests, install, walkthrough, uninstall), but not by hand, and not under PowerShell 5.

## Synthetic data only

Every name, number, email address and card in the tests and examples is made up. The example cards are well-known published test numbers, and the tests generate random ones under the `411111` test prefix.

## Layout

```
standin/
    shape.py     The keyed permutation that keeps a value's shape
    rules.py     The rules file and what each rule does to a cell
    csvfile.py   Reading CSV files, and writing them without overwriting or half-writing
    scan.py      Guessing rules from headers and values
    check.py     Comparing a masked file with its original
    cli.py       The standin command
tests/
examples/        A made-up extract and its rules file
```

## References

- NIST SP 800-38G, [Methods for Format-Preserving Encryption](https://csrc.nist.gov/pubs/sp/800/38/g/final) (FF1)
- Bellare, Rogaway and Spies, *The FFX Mode of Operation for Format-Preserving Encryption* (2010)
- Black and Rogaway, *Ciphers with Arbitrary Finite Domains* (2002), for cycle walking
- Luby and Rackoff, *How to Construct Pseudorandom Permutations from Pseudorandom Functions* (1988)
- RFC 7693, [The BLAKE2 Cryptographic Hash and Message Authentication Code](https://www.rfc-editor.org/rfc/rfc7693)

## Licence

MIT. See `LICENSE`.
