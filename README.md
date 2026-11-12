# Batch-ingestion job

Build “Batch-ingestion job” for a 100-row fixture with one transformation, one validation rule, and one output, exposed as `python3 batch_ingestion.py INPUT OUTPUT`.

## Normative command

```sh
python3 batch_ingestion.py INPUT OUTPUT
```

`INPUT` is an existing input CSV file. `OUTPUT` is the output CSV file to create. Exactly two positional arguments are required.

## Input contract

- `INPUT` is a UTF-8 CSV file with a header row followed by data rows. The acceptance fixture has a header plus exactly 100 data rows.
- CSV parsing reference is the Python `csv` module defaults (comma delimiter, `"` as quote character, `""` as an escaped quote). A comma or newline inside a double-quoted field belongs to that field and does not end the field or record.
- The header must be exactly `id,name,amount`, in that order.
- `id`: opaque string; it may be empty. It is preserved verbatim in the output. Uniqueness is not required.
- `name`: arbitrary string; it is the only transformed field (see below).
- `amount`: string field whose acceptance is decided solely by the validation rule below. Plain decimal notation (for example `12`, `12.50`) is the typical spelling, but any spelling is evaluated by that rule.
- Extra columns, missing columns, or a different header are a hard input error (see Errors).
- A data record with more or fewer than 3 fields (for example a missing `amount` or a 4-field row) is not a hard error: it is a skipped invalid row. A truly empty line (no characters) is likewise a skipped invalid row, not a hard error. A whitespace-only line is a one-field record and is therefore a skipped invalid row under the short-row rule.

## The one transformation

- `name` is transformed as `name.strip().upper()` (Python `str.strip()` then `str.upper()`).
- No other field is normalized: `id` and `amount` are written back exactly as they appear in `INPUT`.
- The transformation applies only to rows accepted by the validation rule.

## The one validation rule

- A row with exactly 3 fields is valid if and only if Python `float(amount)` parses without exception and the result is `>= 0`.
- This is the only rule that accepts or rejects a row. No other field value ever rejects a row: an empty `id` is accepted verbatim subject only to its row's `amount` verdict.
- Spelling verdicts under `float()`: leading/trailing ASCII whitespace is permitted; an explicit `+`/`-` sign prefix is permitted; exponent notation (for example `1e3`) is permitted; case-insensitive `inf`/`infinity` spellings are permitted and are valid when non-negative (`inf` valid, `-inf` invalid); any `nan` spelling is invalid (all comparisons are false). Accepted `amount` values are written back verbatim.
- Invalid rows are skipped: they are excluded from `OUTPUT` and counted. Valid rows keep input order.

## The one output

- Exactly one output file is produced: a UTF-8 CSV at `OUTPUT` (no BOM) with header `id,name,amount`.
- It contains one row per valid input row, in input order, with the transformed `name`.
- Byte shape: every record, including the header, ends with a single LF (`\n`); fields are quoted per Python `csv` `QUOTE_MINIMAL` (a field containing a comma, quote, or newline is wrapped in double quotes and each internal `"` is doubled); the file ends with a trailing newline after the last record (a zero-valid-row file holds exactly `id,name,amount\n`).
- `OUTPUT` is created or truncated on success; missing parent directories are created.
- If zero rows are valid, the job still succeeds and writes a header-only file.
- On success the job prints `valid=<n> invalid=<m>` to stderr, where `n` and `m` are the valid and skipped-row counts. No counters line is printed on a hard failure.

## Determinism

- The job is deterministic: the same `INPUT` bytes always produce byte-identical `OUTPUT` bytes under the byte shape pinned above.
- No timestamps, random values, network access, or extra inputs affect the result. Standard library only (Python 3.12).

## Errors and exit codes

- Exit `0`: success; `OUTPUT` was written (even when some rows were skipped).
- Exit exactly `2`: hard failure; `OUTPUT` is not left behind as a partial file:
  - wrong argument count;
  - `INPUT` missing or unreadable;
  - `INPUT` empty or header is not exactly `id,name,amount`;
  - `INPUT` and `OUTPUT` resolve to the same file;
  - `OUTPUT` cannot be written.
- Atomicity: `INPUT` is fully validated before `OUTPUT` is created or truncated. If `OUTPUT` did not exist, a hard failure creates nothing there; if it already existed, it is left byte-identical (never truncated, removed, or partially replaced). The same-file check runs before any truncation.
- Hard failures print exactly one one-line human-readable message to stderr and no counters line.

## Boundary cases

- Name with surrounding whitespace or lowercase (for example `"  alice "` becomes `"ALICE"`).
- Zero, negative, and non-numeric `amount` values (for example `"0"`, `"-3"`, `"abc"`): only negative and non-numeric are invalid; `"0"` is valid.
- Exponent, signed, padded, and special `amount` spellings: `"1e3"`, `"+5"`, and `"inf"` are valid (written back verbatim); `"nan"` and `"-inf"` are invalid and skipped.
- Duplicate `id` values are accepted and preserved; an empty `id` is accepted verbatim subject only to its row's `amount` verdict.
- Empty lines are skipped invalid rows, not hard errors; short/long data rows (fewer or more than 3 fields) and whitespace-only lines are skipped invalid rows, not hard errors.
- Quoted fields with embedded commas or newlines parse per the `csv` defaults above.
- `OUTPUT` ends with LF (`\n`), uses minimal quoting, and a pre-existing `OUTPUT` survives a hard failure byte-identical.
