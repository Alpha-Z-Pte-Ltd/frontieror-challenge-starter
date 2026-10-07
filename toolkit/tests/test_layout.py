import io
import zipfile

from frontieror.layout import check_layout, pack


def test_layout_and_archive(tmp_path):
    solvers = tmp_path / "solvers"
    (solvers / "ok" / "__pycache__").mkdir(parents=True)
    (solvers / "ok" / "solve.py").write_text("print('hi')")
    (solvers / "ok" / "helpers.py").write_text("")
    (solvers / "ok" / "__pycache__" / "solve.cpython-314.pyc").write_bytes(b"")
    (solvers / "binary").mkdir()
    (solvers / "binary" / "solve").write_text("")  # An executable solve is not an entry point.
    (solvers / "empty").mkdir()
    (solvers / "stray.txt").write_text("not a problem")

    coverage = {c.slug: c.status for c in check_layout(solvers, ["ok", "binary", "empty", "absent"])}
    assert coverage == {"ok": "ok", "binary": "no_entrypoint", "empty": "no_entrypoint", "absent": "missing"}

    names = zipfile.ZipFile(io.BytesIO(pack(solvers, ["ok"]))).namelist()
    assert sorted(names) == ["ok/helpers.py", "ok/solve.py"]
