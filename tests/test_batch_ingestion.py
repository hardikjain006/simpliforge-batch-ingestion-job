"""Repository-native tests for the batch-ingestion job public contract."""

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch_ingestion import run_job  # noqa: E402


def write_csv(path, rows, lineterminator="\n"):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(
            f,
            delimiter=",",
            quotechar='"',
            doublequote=True,
            lineterminator=lineterminator,
        )
        writer.writerows(rows)


def run_cli(input_path, output_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [sys.executable, os.path.join(repo_root, "batch_ingestion.py"),
         input_path, output_path],
        capture_output=True,
        text=True,
    )


def read_output(output_path):
    with open(output_path, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf"), "output must not have BOM"
    text = raw.decode("utf-8")
    assert text.endswith("\n") and not text.endswith("\n\n"), \
        "exactly one trailing newline"
    assert "\r" not in text, "LF line endings only"
    for line in text.split("\n"):
        assert line == line.rstrip(" \t"), "no trailing whitespace"
        assert "\t" not in line, "no tabs"
    return text, json.loads(text)


class BatchIngestionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.in_path = os.path.join(self.tmp.name, "in.csv")
        self.out_path = os.path.join(self.tmp.name, "out.json")

    def test_t1_trims_name_and_preserves_order(self):
        write_csv(self.in_path, [
            ["id", "name", "amount"],
            ["a1", "  hello  ", "1"],
            ["a2", "\tworld\t", "2"],
        ])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual([r["name"] for r in data["records"]], ["hello", "world"])
        self.assertEqual([r["id"] for r in data["records"]], ["a1", "a2"])
        self.assertEqual(data["summary"], {
            "total_rows": 2, "accepted": 2, "rejected": 0})

    def test_t1_whitespace_only_name_still_accepted(self):
        write_csv(self.in_path, [["id", "name", "amount"], ["a1", "   ", "3"]])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual(data["records"][0]["name"], "")
        self.assertEqual(data["summary"]["accepted"], 1)

    def test_id_trim_and_empty_id_rejected(self):
        write_csv(self.in_path, [
            ["id", "name", "amount"],
            ["  abc  ", "n", "1"],
            ["   ", "n", "1"],
            ["", "n", "1"],
            ["\t", "n", "1"],
        ])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual([r["id"] for r in data["records"]], ["abc"])
        self.assertEqual(data["summary"],
                         {"total_rows": 4, "accepted": 1, "rejected": 3})

    def test_duplicate_ids_accepted_in_input_order(self):
        write_csv(self.in_path, [
            ["id", "name", "amount"],
            ["dup", "first", "1"],
            ["dup", "second", "2"],
        ])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual([r["name"] for r in data["records"]],
                         ["first", "second"])
        self.assertEqual(data["summary"]["accepted"], 2)

    def test_v1_accepted_forms(self):
        cases = [
            ("0", "0"), ("0.0", "0"), ("12.50", "12.5"), (".5", "0.5"),
            ("+5", "5"), ("  12.5  ", "12.5"), ("007", "7"), ("00.50", "0.5"),
        ]
        for raw, canonical in cases:
            with self.subTest(raw=raw):
                write_csv(self.in_path, [["id", "name", "amount"],
                                         ["x", "n", raw]])
                self.assertEqual(run_job(self.in_path, self.out_path), 0)
                text, data = read_output(self.out_path)
                self.assertEqual(data["records"][0]["amount"],
                                 json.loads(canonical))
                self.assertIn(f'"amount": {canonical}', text)

    def test_v1_rejected_forms(self):
        bad = ["", "abc", "NaN", "nan", "inf", "Infinity", "-inf",
               "1e3", "1E3", "0x10", "1_0", "5.", "-5", "-0.5", "--5",
               "+-5", ".", "+.", "12,5"]
        for raw in bad:
            with self.subTest(raw=raw):
                write_csv(self.in_path, [["id", "name", "amount"],
                                         ["x", "n", raw]])
                self.assertEqual(run_job(self.in_path, self.out_path), 0)
                _, data = read_output(self.out_path)
                self.assertEqual(data["summary"],
                                 {"total_rows": 1, "accepted": 0,
                                  "rejected": 1})
                self.assertEqual(data["records"], [])

    def test_amount_zero_variants_pass(self):
        for raw in ["0", "0.0", "+0", "+0.0", "-0", "-0.0"]:
            with self.subTest(raw=raw):
                write_csv(self.in_path, [["id", "name", "amount"],
                                         ["x", "n", raw]])
                self.assertEqual(run_job(self.in_path, self.out_path), 0)
                _, data = read_output(self.out_path)
                self.assertEqual(data["summary"]["accepted"], 1)
                self.assertEqual(data["records"][0]["amount"], 0)

    def test_quoted_fields_with_comma_and_quotes(self):
        write_csv(self.in_path, [
            ["id", "name", "amount"],
            ["a1", 'hello, "world"', "4"],
        ])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual(data["records"][0]["name"], 'hello, "world"')
        self.assertEqual(data["summary"]["accepted"], 1)

    def test_blank_lines_ignored(self):
        with open(self.in_path, "w", encoding="utf-8", newline="") as f:
            f.write("id,name,amount\n")
            f.write("\n")
            f.write("   \n")
            f.write("\t\n")
            f.write("a1,n1,1\n")
            f.write("\n")
            f.write("a2,n2,bad\n")
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, data = read_output(self.out_path)
        self.assertEqual(data["summary"],
                         {"total_rows": 2, "accepted": 1, "rejected": 1})

    def test_quoted_empty_or_whitespace_only_single_field_is_job_failure(self):
        for quoted_line in ['""\n', '"   "\n', '"\t"\n', '" \t "\n']:
            with self.subTest(line=repr(quoted_line)):
                with open(self.in_path, "w", encoding="utf-8", newline="") as f:
                    f.write("id,name,amount\n")
                    f.write(quoted_line)
                    f.write("a1,n1,1\n")
                out = os.path.join(self.tmp.name, "q.json")
                self.assertNotEqual(run_job(self.in_path, out), 0)
                self.assertFalse(os.path.exists(out))
        # CRLF quoted-whitespace is likewise a data row, not an ignored blank.
        with open(self.in_path, "w", encoding="utf-8", newline="") as f:
            f.write("id,name,amount\r\n")
            f.write('"   "\r\n')
            f.write("a1,n1,1\r\n")
        out = os.path.join(self.tmp.name, "q-crlf.json")
        self.assertNotEqual(run_job(self.in_path, out), 0)
        self.assertFalse(os.path.exists(out))

    def test_crlf_accepted_identically_to_lf(self):
        rows = [["id", "name", "amount"], ["a1", "  n1 ", "1.50"],
                ["a2", "n2", "bad"]]
        write_csv(self.in_path, rows, lineterminator="\n")
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        _, lf_data = read_output(self.out_path)
        lf_text = open(self.out_path, "rb").read()
        crlf_in = os.path.join(self.tmp.name, "crlf.csv")
        crlf_out = os.path.join(self.tmp.name, "crlf.json")
        write_csv(crlf_in, rows, lineterminator="\r\n")
        self.assertEqual(run_job(crlf_in, crlf_out), 0)
        crlf_text = open(crlf_out, "rb").read()
        self.assertEqual(lf_text, crlf_text)
        self.assertEqual(lf_data["summary"]["total_rows"], 2)

    def test_header_mismatch_is_job_failure(self):
        for header in [["id", "name"], ["id", "name", "amount", "extra"],
                       ["name", "id", "amount"], [" id", "name", "amount"],
                       ["ID", "name", "amount"]]:
            with self.subTest(header=header):
                out = os.path.join(self.tmp.name, "h.json")
                write_csv(self.in_path, [header, ["a", "b", "1"]])
                self.assertNotEqual(run_job(self.in_path, out), 0)
                self.assertFalse(os.path.exists(out))

    def test_missing_header_row_is_job_failure(self):
        for content in ["", "\n", "   \n\t\n"]:
            with self.subTest(content=repr(content)):
                with open(self.in_path, "w", encoding="utf-8") as f:
                    f.write(content)
                out = os.path.join(self.tmp.name, "e.json")
                self.assertNotEqual(run_job(self.in_path, out), 0)
                self.assertFalse(os.path.exists(out))

    def test_data_column_count_mismatch_is_job_failure(self):
        write_csv(self.in_path, [["id", "name", "amount"],
                                 ["a1", "n1", "1"],
                                 ["a2", "n2"]])
        self.assertNotEqual(run_job(self.in_path, self.out_path), 0)
        self.assertFalse(os.path.exists(self.out_path))
        write_csv(self.in_path, [["id", "name", "amount"],
                                 ["a1", "n1", "1", "extra"]])
        self.assertNotEqual(run_job(self.in_path, self.out_path), 0)
        self.assertFalse(os.path.exists(self.out_path))

    def test_missing_input_is_job_failure(self):
        out = os.path.join(self.tmp.name, "m.json")
        self.assertNotEqual(
            run_job(os.path.join(self.tmp.name, "nope.csv"), out), 0)
        self.assertFalse(os.path.exists(out))

    def test_unwritable_output_is_job_failure(self):
        write_csv(self.in_path, [["id", "name", "amount"], ["a", "b", "1"]])
        bad_out = os.path.join(self.tmp.name, "no-dir", "out.json")
        self.assertNotEqual(run_job(self.in_path, bad_out), 0)
        self.assertFalse(os.path.exists(bad_out))

    def test_output_canonical_serialization(self):
        write_csv(self.in_path, [["id", "name", "amount"],
                                 ["b", "ünïcodé ✓", "12.50"],
                                 ["a", "x", "0.0"]])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        text, data = read_output(self.out_path)
        self.assertEqual(list(data.keys()), ["records", "summary"])
        self.assertEqual(list(data["records"][0].keys()),
                         ["id", "name", "amount"])
        self.assertEqual(list(data["summary"].keys()),
                         ["total_rows", "accepted", "rejected"])
        # 2-space indentation: nested record keys have 6 spaces.
        self.assertIn('      "id": "b"', text)
        self.assertIn('"amount": 12.5', text)
        self.assertIn('"amount": 0', text)
        # Non-ASCII emitted directly, not \u-escaped.
        self.assertIn("ünïcodé ✓", text)
        self.assertNotIn("\\u", text.lower().replace("\\u000", ""))
        reparsed = json.loads(text)
        self.assertEqual(reparsed, data)

    def test_determinism_byte_identical(self):
        write_csv(self.in_path, [["id", "name", "amount"],
                                 ["a1", "  x ", "12.50"],
                                 ["a2", "y", "bad"],
                                 ["a1", "z", ".5"]])
        self.assertEqual(run_job(self.in_path, self.out_path), 0)
        first = open(self.out_path, "rb").read()
        out2 = os.path.join(self.tmp.name, "out2.json")
        self.assertEqual(run_job(self.in_path, out2), 0)
        self.assertEqual(first, open(out2, "rb").read())

    def test_cli_exit_zero_and_acceptance_shape(self):
        rows = [["id", "name", "amount"]]
        for i in range(100):
            if i % 10 == 9:
                rows.append([f"id{i}", f" name{i} ", "bad"])
            else:
                rows.append([f"id{i}", f" name{i} ", str(i % 17)])
        write_csv(self.in_path, rows)
        proc = run_cli(self.in_path, self.out_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, data = read_output(self.out_path)
        self.assertEqual(data["summary"]["total_rows"], 100)
        self.assertEqual(data["summary"]["total_rows"],
                         data["summary"]["accepted"]
                         + data["summary"]["rejected"])
        self.assertEqual(len(data["records"]),
                         data["summary"]["accepted"])
        for rec in data["records"]:
            self.assertEqual(rec["name"], rec["name"].strip(" \t"))
            self.assertGreaterEqual(rec["amount"], 0)
            self.assertIsInstance(rec["amount"], (int, float))


if __name__ == "__main__":
    unittest.main()
