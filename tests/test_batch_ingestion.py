"""Repository-native tests for the batch-ingestion job.

These tests exercise only the normative public interface
``python3 batch_ingestion.py INPUT OUTPUT`` as a subprocess. They do not
import the implementation or depend on any private symbol.
"""

import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "batch_ingestion.py")
HEADER = "id,name,amount\n"


def run_job(args, cwd=None):
    """Run the job with the given argument list; return CompletedProcess."""
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )


def run_case(input_bytes, extra_args=None, keep_output=None):
    """Write input_bytes to a temp INPUT, run the job, return details.

    Returns (proc, output_bytes_or_None, tmpdir). The caller owns tmpdir
    cleanup via TemporaryDirectory context in each test.
    """
    tmpdir = tempfile.TemporaryDirectory()
    input_path = os.path.join(tmpdir.name, "input.csv")
    output_path = os.path.join(tmpdir.name, "sub", "output.csv")
    with open(input_path, "wb") as handle:
        handle.write(input_bytes)
    if keep_output is not None:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as handle:
            handle.write(keep_output)
    argv = [input_path, output_path]
    if extra_args is not None:
        argv = extra_args
    proc = run_job(argv)
    output_bytes = None
    if os.path.exists(output_path):
        with open(output_path, "rb") as handle:
            output_bytes = handle.read()
    return proc, output_bytes, tmpdir


class BatchIngestionTest(unittest.TestCase):

    def assertSuccess(self, proc, valid, invalid):
        self.assertEqual(proc.returncode, 0, msg=proc.stderr.decode())
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr,
                         "valid={} invalid={}\n".format(valid, invalid).encode())

    def assertHardFailure(self, proc):
        self.assertEqual(proc.returncode, 2, msg=proc.stderr.decode())
        self.assertEqual(proc.stdout, b"")
        lines = proc.stderr.decode().splitlines()
        self.assertEqual(len(lines), 1, msg=proc.stderr.decode())
        self.assertNotIn("valid=", proc.stderr.decode())

    def test_happy_path_transformation(self):
        data = (HEADER + "a1,  alice ,12.50\n"
                + "a2,bob,0\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 2, 0)
            self.assertEqual(out, b"id,name,amount\na1,ALICE,12.50\na2,BOB,0\n")

    def test_100_row_fixture(self):
        rows = []
        for i in range(100):
            if i % 10 == 0:
                rows.append("x{:03d},bad,-{}".format(i, i + 1))  # invalid
            elif i % 10 == 1:
                rows.append("x{:03d},oops,abc".format(i))  # invalid
            else:
                rows.append("x{:03d},user{} ,{}".format(i, i, i))
        data = (HEADER + "\n".join(rows) + "\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 80, 20)
            lines = out.decode().splitlines()
            self.assertEqual(len(lines), 81)  # header + 80 valid
            self.assertEqual(lines[0], "id,name,amount")
            self.assertIn("x002,USER2,2", lines)

    def test_zero_valid_rows_header_only(self):
        data = (HEADER + "a,bad,-1\nc,worse,xyz\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 0, 2)
            self.assertEqual(out, b"id,name,amount\n")

    def test_validation_spellings(self):
        data = (HEADER
                + "e1,n,1e3\n"      # valid, verbatim
                + "e2,n,+5\n"       # valid, verbatim
                + "e3,n,inf\n"      # valid, verbatim
                + "e4,n,Infinity\n"  # valid, verbatim
                + "i1,n,nan\n"      # invalid
                + "i2,n,NaN\n"      # invalid
                + "i3,n,-inf\n"     # invalid
                + "i4,n,-Infinity\n"  # invalid
                + "i5,n,\n"         # invalid (empty amount)
                + "p1,n,  7  \n").encode()  # valid, verbatim with spaces
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 5, 5)
            self.assertEqual(out, b"id,name,amount\n"
                             b"e1,N,1e3\n"
                             b"e2,N,+5\n"
                             b"e3,N,inf\n"
                             b"e4,N,Infinity\n"
                             b"p1,N,  7  \n")

    def test_short_long_rows_skipped(self):
        data = (HEADER
                + "only-two,fields\n"
                + "four,fields,here,extra\n"
                + "good,name,3\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 1, 2)
            self.assertEqual(out, b"id,name,amount\ngood,NAME,3\n")

    def test_empty_and_whitespace_lines_skipped(self):
        data = (HEADER + "a,ok,1\n\n   \n" + "b,fine,2\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 2, 2)
            self.assertEqual(out, b"id,name,amount\na,OK,1\nb,FINE,2\n")

    def test_empty_id_and_duplicate_ids_accepted(self):
        data = (HEADER + ",noname,4\ndup,x,1\ndup,y,2\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 3, 0)
            self.assertEqual(out, b"id,name,amount\n,NONAME,4\ndup,X,1\ndup,Y,2\n")

    def test_quoted_fields_with_comma_and_newline(self):
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["id", "name", "amount"])
        writer.writerow(["q1", "  doe, john  ", "9"])
        writer.writerow(["q2", "line1\nline2", "8"])
        writer.writerow(['q3', 'say "hi"', "7"])
        data = buf.getvalue().encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 3, 0)
            reader = list(csv.reader(io.StringIO(out.decode())))
            self.assertEqual(reader, [["id", "name", "amount"],
                                      ["q1", "DOE, JOHN", "9"],
                                      ["q2", "LINE1\nLINE2", "8"],
                                      ["q3", 'SAY "HI"', "7"]])
            # Minimal quoting: plain fields are unquoted.
            self.assertIn(b"q1,\"DOE, JOHN\",9\n", out)

    def test_output_byte_shape(self):
        data = (HEADER + "a,  zed ,1\n").encode()
        proc, out, tmp = run_case(data)
        with tmp:
            self.assertSuccess(proc, 1, 0)
            self.assertFalse(out.startswith(b"\xef\xbb\xbf"))  # no BOM
            self.assertTrue(out.endswith(b"\n"))
            self.assertNotIn(b"\r", out)
            self.assertEqual(out, b"id,name,amount\na,ZED,1\n")

    def test_missing_parent_directories_created(self):
        tmpdir = tempfile.TemporaryDirectory()
        with tmpdir:
            input_path = os.path.join(tmpdir.name, "in.csv")
            output_path = os.path.join(tmpdir.name, "a", "b", "out.csv")
            with open(input_path, "wb") as handle:
                handle.write((HEADER + "k,v,1\n").encode())
            proc = run_job([input_path, output_path])
            self.assertSuccess(proc, 1, 0)
            with open(output_path, "rb") as handle:
                self.assertEqual(handle.read(), b"id,name,amount\nk,V,1\n")

    def test_determinism(self):
        data = (HEADER + "a,  mix ,1e2\nb,bad,-1\n").encode()
        _, out1, tmp1 = run_case(data)
        _, out2, tmp2 = run_case(data)
        with tmp1, tmp2:
            self.assertEqual(out1, out2)

    def test_wrong_argument_counts(self):
        for argv in ([], ["only-one"], ["a", "b", "c"]):
            proc = run_job(argv)
            self.assertHardFailure(proc)

    def test_missing_input(self):
        tmpdir = tempfile.TemporaryDirectory()
        with tmpdir:
            missing = os.path.join(tmpdir.name, "nope.csv")
            out = os.path.join(tmpdir.name, "out.csv")
            proc = run_job([missing, out])
            self.assertHardFailure(proc)
            self.assertFalse(os.path.exists(out))

    def test_empty_input_file(self):
        proc, out, tmp = run_case(b"")
        with tmp:
            self.assertHardFailure(proc)
            self.assertIsNone(out)

    def test_bad_header(self):
        for header in ("id,name\n", "id,name,amount,extra\n",
                       "ID,NAME,AMOUNT\n", "id, name, amount\n", "\n"):
            proc, out, tmp = run_case(header.encode() + b"a,b,1\n")
            with tmp:
                self.assertHardFailure(proc)
                self.assertIsNone(out)

    def test_same_input_and_output(self):
        tmpdir = tempfile.TemporaryDirectory()
        with tmpdir:
            path = os.path.join(tmpdir.name, "same.csv")
            original = (HEADER + "a,b,1\n").encode()
            with open(path, "wb") as handle:
                handle.write(original)
            proc = run_job([path, path])
            self.assertHardFailure(proc)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original)

    def test_preexisting_output_survives_hard_failure(self):
        sentinel = b"untouched,sentinel\n"
        proc, out, tmp = run_case(b"", keep_output=sentinel)
        with tmp:
            self.assertHardFailure(proc)
            self.assertEqual(out, sentinel)

    def test_output_not_written_when_header_bad_and_output_missing(self):
        proc, out, tmp = run_case(b"nope\n")
        with tmp:
            self.assertHardFailure(proc)
            self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
