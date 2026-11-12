"""Batch-ingestion job (I1 + T1 + V1 -> O1).

Reads one UTF-8 CSV fixture, applies exactly one transformation (T1: trim
``name``) and exactly one validation rule (V1: ``amount`` finite decimal
``>= 0`` plus non-empty ``id``), and writes exactly one canonical JSON
output file (O1). See README.md for the authoritative requirements contract.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

AMOUNT_RE = re.compile(r"^[+-]?([0-9]+(\.[0-9]+)?|\.[0-9]+)$")
EXPECTED_HEADER = ["id", "name", "amount"]
_TRIM_CHARS = " \t"


def _is_blank_physical_line(text_without_ending: str) -> bool:
    """Blank physical lines: zero chars or only space/tab (ending excluded)."""
    return text_without_ending.strip(_TRIM_CHARS) == ""


def _physical_line_texts(raw_text: str) -> list[str]:
    """Raw physical line texts excluding LF/CRLF endings, for blank checks."""
    lines_with_endings = io.StringIO(raw_text, newline="").readlines()
    texts: list[str] = []
    for line in lines_with_endings:
        if line.endswith("\r\n"):
            texts.append(line[:-2])
        elif line.endswith("\n") or line.endswith("\r"):
            texts.append(line[:-1])
        else:
            texts.append(line)
    return texts


def _canonical_amount_str(trimmed: str) -> str:
    """Minimal plain-decimal form: no exponent, no `+`, no extra zeros."""
    s = trimmed
    if s.startswith("+"):
        s = s[1:]
        neg = False
    elif s.startswith("-"):
        s = s[1:]
        if s.strip("0.").strip() == "":
            return "0"
        # Negative non-zero values never pass V1, but keep the mapping total.
        neg = True
    else:
        neg = False
    if s.startswith("."):
        s = "0" + s
    if "." in s:
        int_part, frac_part = s.split(".", 1)
        int_part = int_part.lstrip("0") or "0"
        frac_part = frac_part.rstrip("0")
        cand = int_part if frac_part == "" else int_part + "." + frac_part
    else:
        cand = s.lstrip("0") or "0"
    if cand.replace(".", "").strip("0") == "":
        return "0"
    return ("-" + cand) if neg else cand


def _parse_amount_value(trimmed: str) -> Decimal | None:
    """Return Decimal value when the trimmed text matches V1 lexical form."""
    if not AMOUNT_RE.match(trimmed):
        return None
    try:
        value = Decimal(trimmed)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def run_job(input_path: str, output_path: str) -> int:
    """Run the job. Returns process exit status (0 on success)."""
    # Read and parse input fully before touching the output path.
    # Blank lines are classified from raw physical line text (empty or only
    # space/tab excluding the line ending) before CSV field parsing, so quoted
    # empty/whitespace-only lines (e.g. '""' or '"   "') are data rows subject
    # to the three-field job-failure rule.
    try:
        with open(input_path, "r", encoding="utf-8", newline="") as infile:
            raw_text = infile.read()
    except UnicodeDecodeError as exc:
        print(f"error: input is not valid UTF-8: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 1
    try:
        reader = csv.reader(
            io.StringIO(raw_text, newline=""),
            delimiter=",",
            quotechar='"',
            doublequote=True,
            skipinitialspace=False,
            strict=True,
        )
        try:
            physical_texts = _physical_line_texts(raw_text)
            rows_with_blank: list[tuple[list[str], bool]] = []
            prev_line = 0
            for row in reader:
                cur_line = reader.line_num
                span = physical_texts[prev_line:cur_line]
                is_blank = len(span) == 1 and _is_blank_physical_line(span[0])
                rows_with_blank.append((row, is_blank))
                prev_line = cur_line
        except csv.Error as exc:
            print(f"error: malformed CSV: {exc}", file=sys.stderr)
            return 1
    except UnicodeDecodeError as exc:
        print(f"error: input is not valid UTF-8: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 1

    # Header is the first non-blank row; a file with none has no header.
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row, is_blank in rows_with_blank:
        if is_blank:
            continue
        if header is None:
            header = row
        else:
            data_rows.append(row)

    if header is None:
        print("error: missing header row", file=sys.stderr)
        return 1
    if header != EXPECTED_HEADER:
        print(f"error: header mismatch: {header!r}", file=sys.stderr)
        return 1

    for row in data_rows:
        if len(row) != 3:
            print(
                f"error: data row has {len(row)} fields, expected 3",
                file=sys.stderr,
            )
            return 1

    records: list[dict] = []
    rejected = 0
    for row in data_rows:
        raw_id, raw_name, raw_amount = row
        id_trimmed = raw_id.strip(_TRIM_CHARS)
        name_trimmed = raw_name.strip(_TRIM_CHARS)
        amount_trimmed = raw_amount.strip(_TRIM_CHARS)
        amount_value = _parse_amount_value(amount_trimmed)
        id_ok = id_trimmed != ""
        amount_ok = amount_value is not None and amount_value >= 0
        if id_ok and amount_ok:
            assert amount_value is not None
            records.append(
                {
                    "id": id_trimmed,
                    "name": name_trimmed,
                    "amount_str": _canonical_amount_str(amount_trimmed),
                }
            )
        else:
            rejected += 1

    total_rows = len(data_rows)
    accepted = len(records)

    # Canonical O1 serialization: fixed key order, 2-space indent, LF,
    # exactly one trailing newline, UTF-8 without BOM, mandatory escapes
    # only, minimal plain-decimal amounts.
    lines: list[str] = []
    lines.append("{")
    if records:
        lines.append('  "records": [')
        for i, rec in enumerate(records):
            lines.append("    {")
            lines.append(
                f'      "id": {json.dumps(rec["id"], ensure_ascii=False)},'
            )
            lines.append(
                f'      "name": {json.dumps(rec["name"], ensure_ascii=False)},'
            )
            lines.append(f'      "amount": {rec["amount_str"]}')
            lines.append("    }" + ("," if i < len(records) - 1 else ""))
        lines.append("  ],")
    else:
        lines.append('  "records": [],')
    lines.append('  "summary": {')
    lines.append(f'    "total_rows": {total_rows},')
    lines.append(f'    "accepted": {accepted},')
    lines.append(f'    "rejected": {rejected}')
    lines.append("  }")
    lines.append("}")
    payload = "\n".join(lines) + "\n"

    # Atomic write: temp file in the destination directory, then replace.
    # On any failure, leave no partial file at the requested output path.
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    tmp_path = output_path + f".tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as out:
            out.write(payload)
        os.replace(tmp_path, output_path)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch-ingestion job: CSV fixture -> canonical JSON output."
    )
    parser.add_argument("input_path", help="input CSV fixture path (I1)")
    parser.add_argument("output_path", help="output JSON file path (O1)")
    args = parser.parse_args(argv)
    return run_job(args.input_path, args.output_path)


if __name__ == "__main__":
    sys.exit(main())
