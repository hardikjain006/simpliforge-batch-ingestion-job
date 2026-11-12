"""Batch-ingestion job.

Reads a UTF-8 CSV file, applies one transformation (``name.strip().upper()``)
to rows accepted by one validation rule (``float(amount) >= 0``), and writes
exactly one UTF-8 CSV output file.

Usage: ``python3 batch_ingestion.py INPUT OUTPUT``
"""

import csv
import os
import sys
import tempfile

EXPECTED_HEADER = ["id", "name", "amount"]

EXIT_HARD_FAILURE = 2


def _fail(message):
    """Print exactly one human-readable line to stderr for a hard failure."""
    print("batch_ingestion.py: error: {}".format(message), file=sys.stderr)
    return EXIT_HARD_FAILURE


def _is_valid_amount(amount):
    try:
        return float(amount) >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def main(argv):
    if len(argv) != 3:
        return _fail("expected exactly 2 arguments: INPUT OUTPUT")
    input_path, output_path = argv[1], argv[2]

    # Same-file check runs before any truncation or creation of OUTPUT.
    try:
        same_file = os.path.realpath(input_path) == os.path.realpath(output_path)
    except OSError:
        same_file = False
    if same_file:
        return _fail("INPUT and OUTPUT resolve to the same file")

    # Fully read and validate INPUT before touching OUTPUT.
    try:
        with open(input_path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                header = None
            if header != EXPECTED_HEADER:
                if header is None:
                    return _fail("INPUT is empty")
                return _fail("INPUT header must be exactly 'id,name,amount'")
            valid_rows = []
            invalid_count = 0
            try:
                for record in reader:
                    if len(record) != 3:
                        invalid_count += 1
                        continue
                    row_id, name, amount = record
                    if not _is_valid_amount(amount):
                        invalid_count += 1
                        continue
                    valid_rows.append([row_id, name.strip().upper(), amount])
            except csv.Error as exc:
                return _fail("INPUT is not a readable CSV file ({})".format(exc))
    except FileNotFoundError:
        return _fail("INPUT '{}' does not exist".format(input_path))
    except IsADirectoryError:
        return _fail("INPUT '{}' is not a readable file".format(input_path))
    except UnicodeDecodeError:
        return _fail("INPUT '{}' is not valid UTF-8".format(input_path))
    except OSError as exc:
        return _fail("cannot read INPUT '{}' ({})".format(input_path, exc))

    valid_count = len(valid_rows)

    # Write OUTPUT atomically: temp file in the target directory, then replace,
    # so a write failure never leaves a partial OUTPUT behind.
    try:
        parent = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(parent, exist_ok=True)
        tmp_handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=parent,
            prefix=".batch_ingestion-",
            suffix=".tmp",
            delete=False,
        )
        try:
            writer = csv.writer(tmp_handle, lineterminator="\n")
            writer.writerow(EXPECTED_HEADER)
            writer.writerows(valid_rows)
            tmp_handle.close()
            os.replace(tmp_handle.name, output_path)
        except BaseException:
            try:
                tmp_handle.close()
            except OSError:
                pass
            try:
                os.unlink(tmp_handle.name)
            except OSError:
                pass
            raise
    except OSError as exc:
        return _fail("cannot write OUTPUT '{}' ({})".format(output_path, exc))

    print("valid={} invalid={}".format(valid_count, invalid_count), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
