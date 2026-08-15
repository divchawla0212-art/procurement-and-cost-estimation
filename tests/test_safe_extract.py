"""The one traversal guard, tested directly.

This check used to live inline in `procurement/project.py::unpack_vendor_zip`
and nowhere else. BD-6 needs the same check for zip members *and* for the
relative paths a folder upload sends, so it was extracted rather than copied —
a second copy of a security check is a second thing to forget to fix.

Every refusal names the offending entry, because a refusal that does not say
which member was wrong leaves the uploader nothing to act on.
"""
import os

import pytest

from workflow.safe_extract import safe_destination, safe_relative_path


def test_a_normal_relative_path_resolves_inside_the_base(tmp_path):
    base = str(tmp_path)

    dest = safe_destination(base, "VENDOR/quote.pdf")

    assert dest == os.path.realpath(os.path.join(base, "VENDOR", "quote.pdf"))
    assert dest.startswith(os.path.realpath(base) + os.sep)


def test_a_dot_dot_path_is_refused(tmp_path):
    with pytest.raises(ValueError, match=r"\.\./\.\./evil\.txt"):
        safe_destination(str(tmp_path), "../../evil.txt")


def test_a_dot_dot_buried_mid_path_is_refused(tmp_path):
    """Refused on the segment, not on where it happens to land. `a/../b` stays
    inside the base and is still refused: a name that navigates is a name
    nobody typed on purpose."""
    with pytest.raises(ValueError, match="a/../b.txt"):
        safe_destination(str(tmp_path), "a/../b.txt")


def test_an_absolute_path_is_refused(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        safe_destination(str(tmp_path), "/etc/passwd")


def test_a_windows_absolute_path_is_refused(tmp_path):
    """Refused on every platform, not only the one whose separator it is: an
    archive is portable and the machine that wrote it is not the machine that
    reads it."""
    with pytest.raises(ValueError, match="absolute"):
        safe_destination(str(tmp_path), r"C:\Windows\System32\evil.dll")


def test_a_backslash_escape_is_refused_on_every_platform(tmp_path):
    """`..\\..\\evil` is a single legal filename on Linux and traversal on
    Windows. Both separators are normalised before the check, so the answer
    does not depend on where the server happens to run."""
    with pytest.raises(ValueError):
        safe_destination(str(tmp_path), r"..\..\evil.txt")


def test_an_empty_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        safe_destination(str(tmp_path), "")


def test_a_whitespace_only_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        safe_destination(str(tmp_path), "   ")


def test_a_name_that_resolves_to_the_base_itself_is_refused(tmp_path):
    """"." is not a file. Allowing it would hand a caller a destination it
    cannot write to, and the honest answer is the refusal."""
    with pytest.raises(ValueError):
        safe_destination(str(tmp_path), ".")


# A symlink planted inside the base and pointing out of it is refused too —
# the containment test is on the *resolved* path, which is why
# `safe_destination` calls `os.path.realpath` rather than `os.path.abspath`.
# There is deliberately no test for it: creating a symlink on Windows needs
# privileges a workstation run does not have, so the test would skip there and
# pass on CI, and a platform-conditional skip is a fifth environmental gate for
# `CLAUDE.md`'s baseline table to carry for one assertion.


def test_the_refusal_names_the_entry(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        safe_destination(str(tmp_path), "../evil.txt", what="path in archive")

    assert "../evil.txt" in str(excinfo.value)
    assert "path in archive" in str(excinfo.value)


def test_the_refusal_does_not_leak_the_server_path(tmp_path):
    """These sentences reach an HTTP client. The offending entry is the
    uploader's own text; the destination directory is ours."""
    with pytest.raises(ValueError) as excinfo:
        safe_destination(str(tmp_path / "secret-root"), "../evil.txt")

    assert "secret-root" not in str(excinfo.value)


def test_a_relative_path_is_normalised_to_forward_slashes():
    assert safe_relative_path(r"folder\sub\file.pdf") == "folder/sub/file.pdf"


def test_redundant_segments_are_collapsed():
    assert safe_relative_path("folder//./file.pdf") == "folder/file.pdf"


def test_a_null_byte_is_refused():
    with pytest.raises(ValueError):
        safe_relative_path("evil\0.txt")
