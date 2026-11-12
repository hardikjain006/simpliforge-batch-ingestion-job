# Batch-ingestion job

A batch job that ingests a 100-row CSV fixture, applies one transformation and
one validation rule, and writes one output file.

## Scope

- Ingest exactly one input fixture per run.
- Apply exactly one transformation (T1) and exactly one validation rule (V1)
  as defined below.
- Write exactly one output file (O1) per run.
- No other transformations, validations, outputs, or side effects are part of
  this goal.

## Inputs

- **Fixture (I1):** one UTF-8 CSV file without BOM, with a header row plus
  data rows.
  - CSV field syntax: fields are separated by commas. A field containing a
    comma, double-quote, or line break must be enclosed in double quotes,
    with an embedded double-quote encoded as two double-quotes (`""`),
    per RFC 4180-style quoting. Otherwise a row is a plain comma split.
    The parser supports both forms. The acceptance fixture contains no
    embedded newlines inside fields.
  - Header must be exactly `id,name,amount` (three fields, exact order and
    case, no surrounding whitespace).
  - The acceptance fixture contains exactly 100 data rows.
  - Columns:
    - `id`: opaque string that must be non-empty after removing
      leading/trailing ASCII space (`0x20`) and tab (`0x09`). Uniqueness is
      not required; duplicates are accepted as distinct rows. A row whose
      `id` trims to empty is rejected at row level (see V1). The stored and
      emitted `id` is the trimmed form.
    - `name`: arbitrary string; may contain leading/trailing whitespace
      (handled by T1). Commas or quotes inside `name` are encoded with the
      quoted-field rule above.
    - `amount`: decimal number in plain notation (handled by V1).
  - Line endings `LF` or `CRLF` are accepted and treated identically.
  - Blank lines (zero characters, or only space/tab, excluding the line
    ending) are ignored and count as neither accepted nor rejected.
  - Every non-blank data row must parse to exactly three fields. A data row
    with fewer or more than three fields is a job-level failure (see
    Observable behavior), not a row-level rejection.

## Transformation (T1)

- **T1 — trim `name`:** remove leading and trailing ASCII whitespace
  (space, tab) from the `name` field of every non-blank row.
- T1 is applied before V1.
- No other field is modified and no rows are added, removed, or reordered
  by T1.

## Validation rule (V1)

- **V1 — `amount` must be a finite number >= 0:** remove leading/trailing
  ASCII space and tab (the same set as T1) from the `amount` field, then the
  trimmed value must match plain decimal notation
  `^[+-]?([0-9]+(\.[0-9]+)?|\.[0-9]+)$`: an optional `+`/`-` sign followed by
  either digits with an optional `.` fraction, or a leading-dot form such as
  `.5`. At least one digit is required. Exponent form (for example `1e3`),
  hexadecimal, underscores, empty string, `NaN`, and any infinity spelling
  fail V1. `5.` (trailing dot with no fraction digits) fails V1;
  `.5` passes as `0.5`. A leading `+` (for example `+5`) is allowed and
  passes when the value is `>= 0`; a leading `-` parses and then fails the
  range check.
- The parsed value must be finite with numeric value `>= 0`.
- Rows passing V1 **and** whose `id` is non-empty after trimming are
  **accepted**; rows failing V1 **or** whose `id` trims to empty are
  **rejected**.
- Rejected rows are excluded from the output `records` and counted in the
  output `summary` (see O1).
- Row-level rejections are limited to V1 failure or empty-after-trim `id`
  only. Header, column-count, file-access, and output-write problems are
  job-level failures, not row-level rejections.

## Output (O1)

- **O1:** one UTF-8 JSON file without BOM written per run.
- Canonical serialization (required for byte-identical determinism):
  - Top-level keys in order `records`, then `summary`.
  - Record keys in order `id`, `name`, `amount`.
  - `summary` keys in order `total_rows`, `accepted`, `rejected`.
  - 2-space indentation, `LF` line endings, exactly one trailing newline,
    no tabs or trailing spaces.
  - Non-ASCII characters are emitted directly as UTF-8; only mandatory
    JSON control escapes are used.
  - `amount` is a JSON number in minimal plain-decimal form: no exponent,
    no leading `+`, no leading zeros except a single `0` before the point,
    no trailing zeros in the fraction (for example `12.50` is written as
    `12.5`, `0.0` is written as `0`, `.5` is written as `0.5`).
- Top-level shape:
  - `records`: array with one object per accepted row, in input order.
    Each object has exactly the keys `id`, `name`, `amount`, where `id` is
    the trimmed `id` field, `name` is the T1-trimmed value, and `amount` is
    the parsed number in the canonical form above.
  - `summary`: object with exactly the integer keys `total_rows`,
    `accepted`, `rejected`, where `total_rows` counts non-blank data rows
    in runs that write output and
    `total_rows == accepted + rejected`.
- For the 100-row acceptance fixture, `summary.total_rows` must equal 100.

## Observable behavior

- The job takes an input fixture path and an output file path and, on
  success, writes O1 and exits with status 0.
- The following are job-level failures: the job exits non-zero and writes
  no output file (removing a partially written output if one was created):
  input file missing or unreadable, no header row, header other than
  `id,name,amount`, any non-blank data row with other than three parsed
  fields, or unwritable output location.
- If the output location is not writable, the job exits non-zero.
- Successful runs are deterministic: the same input bytes always produce
  byte-identical output under the canonical O1 serialization above (fixed
  key order, canonical number form, input order preserved, no timestamps,
  no randomness).

## Boundary cases

- Whitespace-only `name` trims to an empty string under T1 and is still
  accepted if V1 passes and `id` is non-empty (T1 never rejects).
- `id` that is empty or only space/tab trims to empty and the row is
  rejected and counted in `summary.rejected`; the `id` trim set is space/tab,
  and the emitted `id` is the trimmed value.
- `amount` values such as empty string, non-numeric text, `NaN`,
  infinities, hexadecimal, underscores, exponent form such as `1e3`, `5.`,
  and negative numbers fail V1 and are rejected.
- `amount` of `0` (including `0.0`, written canonically as `0`) passes V1.
  `+5` passes; `.5` passes as `0.5`.
- Surrounding space/tab around `amount` (for example `" 12.5 "`) is trimmed
  before parsing and passes V1 when the trimmed value is valid. The `amount`
  trim set is space/tab, the same set as T1.
- A header with extra columns beyond the three header fields, missing
  columns, reordered columns, or surrounding whitespace causes a
  job-level header-mismatch failure. A non-blank data row with other than
  three parsed fields likewise causes a job-level failure with no output;
  it is not counted per-row in `summary`.
- Duplicate `id` values are accepted as distinct rows and appear once each
  in `records` in input order.

## Non-goals

- Streaming, scheduling, retries, partial outputs, logging formats, and
  performance targets are out of scope.
- Additional transformations, validation rules, outputs, or fixture formats
  are out of scope.

## Acceptance

- Given a 100-row fixture with the header above, a successful run writes one
  JSON output whose `records.length` equals `summary.accepted`, whose
  `summary` counts satisfy `total_rows == 100 == accepted + rejected`, and
  whose every accepted record shows the trimmed `name` and numeric
  `amount >= 0`.
