"""Run the original workbook builder with its current and one additional feature."""
import contextlib
import io
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

build = runpy.run_path("tools/create_feature_tracker.py")["build"]
globals_ = build.__globals__
original_rows = globals_["ROWS"]
with TemporaryDirectory() as directory:
    output = Path(directory) / "tracker.xlsx"
    globals_["OUTPUT"] = output
    for added in (False, True):
        globals_["ROWS"] = [*original_rows, original_rows[-1]] if added else original_rows
        with contextlib.redirect_stdout(io.StringIO()):
            build()
        workbook = load_workbook(output)
        try:
            last = workbook["Features"].max_row
            assert str(workbook["Features"].data_validations.dataValidation[0].sqref) == f"F2:F{last}"
            covered = int(workbook["Summary"]["B2"].value.split(":A")[1].split(")")[0])
            assert covered == last - int(added), (covered, last)
        finally:
            workbook.close()
print("The current workbook covers its rows. Adding a feature extends validation but not summary formulas.")
