import io
import zipfile

from frontieror.layout import check_layout, pack


def test_layout_and_archive(tmp_path):
    algorithms = tmp_path / "algorithms"
    (algorithms / "ok" / "__pycache__").mkdir(parents=True)
    (algorithms / "ok" / "solve.py").write_text("print('hi')")
    (algorithms / "ok" / "helpers.py").write_text("")
    (algorithms / "ok" / "__pycache__" / "solve.cpython-314.pyc").write_bytes(b"")
    (algorithms / "binary").mkdir()
    (algorithms / "binary" / "solve").write_text("")  # An executable solve is not an entry point.
    (algorithms / "empty").mkdir()
    (algorithms / "stray.txt").write_text("not a problem")

    coverage = {c.slug: c.status for c in check_layout(algorithms, ["ok", "binary", "empty", "absent"])}
    assert coverage == {"ok": "ok", "binary": "no_entrypoint", "empty": "no_entrypoint", "absent": "missing"}

    names = zipfile.ZipFile(io.BytesIO(pack(algorithms, ["ok"]))).namelist()
    assert sorted(names) == ["ok/helpers.py", "ok/solve.py"]
