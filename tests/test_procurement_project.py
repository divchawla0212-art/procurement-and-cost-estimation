import io
import os
import zipfile
import pytest
from procurement.project import (
    slugify, create_project, load_project, list_projects, unpack_vendor_zip, vendor_files,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_slugify():
    assert slugify("ADNOC Gas P5 !!") == "adnoc-gas-p5"


def test_create_and_load_project(tmp_path):
    p = create_project(str(tmp_path), "Gas Gensets", target_currency="USD")
    assert p.slug == "gas-gensets"
    assert os.path.isdir(tmp_path / "gas-gensets" / "vendors")
    assert load_project(str(tmp_path), "gas-gensets").name == "Gas Gensets"
    assert [x.slug for x in list_projects(str(tmp_path))] == ["gas-gensets"]


def test_unpack_vendor_zip_creates_vendors(tmp_path):
    create_project(str(tmp_path), "Proj")
    zpath = tmp_path / "v.zip"
    zpath.write_bytes(_zip_bytes({
        "KERUI/quote.pdf": b"x",
        "KERUI/bom.pdf": b"x",
        "ADPOWER/ADP-935.pdf": b"x",
        "__MACOSX/junk": b"x",
        "ADPOWER/~$temp.docx": b"x",
    }))
    vendors = unpack_vendor_zip(str(tmp_path), "proj", str(zpath))
    assert sorted(vendors) == ["ADPOWER", "KERUI"]
    assert len(vendor_files(str(tmp_path), "proj", "KERUI")) == 2
    # hidden/temp file skipped
    assert all("~$" not in f for f in vendor_files(str(tmp_path), "proj", "ADPOWER"))
    assert load_project(str(tmp_path), "proj").vendors == sorted(vendors)


def test_unpack_rejects_zip_slip(tmp_path):
    create_project(str(tmp_path), "Proj")
    zpath = tmp_path / "evil.zip"
    zpath.write_bytes(_zip_bytes({"../../evil.txt": b"pwned"}))
    with pytest.raises(ValueError):
        unpack_vendor_zip(str(tmp_path), "proj", str(zpath))
    assert not (tmp_path.parent / "evil.txt").exists()
