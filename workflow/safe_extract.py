"""One traversal guard, for every caller that writes a file under a directory
whose name somebody else chose.

This check lived inline in `procurement/project.py::unpack_vendor_zip` and
nowhere else. BD-6 needs the same check for the members of an uploaded zip
**and** for the relative paths a folder upload sends, so it was extracted here
rather than copied: a second copy of a security check is a second thing to
forget to fix, and the copy is always the one that does not get fixed.

Two functions, one of which is the other's first half:

- `safe_relative_path` normalises a caller-supplied name and refuses anything
  that is not a plain relative path.
- `safe_destination` resolves that path under a base directory and refuses
  anything that lands outside it.

**Conservative by construction.** A `..` segment is refused on the segment, not
on where it happens to land: `a/../b.txt` stays inside the base and is still
refused, because a name that navigates is a name nobody typed on purpose. Both
separators are normalised before the check, so `..\\..\\evil` is refused on
Linux — where it is a single legal filename — as well as on Windows, where it
is traversal. An archive is portable; the machine that wrote it is not the
machine that reads it.

Imports stdlib only, and nothing from `workflow.*` or `procurement.*`, so both
packages can use it without either importing the other.
"""
import os
import re

__all__ = ["has_drive", "safe_destination", "safe_relative_path"]

#: A Windows drive prefix — `C:`, and `C:foo` too, which is *drive-relative*
#: rather than rooted and is the sneakier of the two.
_DRIVE = re.compile(r"^[A-Za-z]:")


def has_drive(name: str) -> bool:
    """Whether `name` opens with a drive letter, asked the same way everywhere.

    **Not `os.path.splitdrive`**, which is `ntpath` on Windows and `posixpath`
    on Linux — and the POSIX one is a no-op: it answers `('', 'C:/Windows/x')`,
    so a check built on it silently stops checking on the deployment target and
    on CI. That is worse than no check at all, because it reads as one. The
    pattern below is the same answer on every platform, which is what the
    caller's refusal claims.

    This is not where containment is enforced — `safe_destination` resolves and
    compares, and `doc_store` builds paths out of leaves. It is here so that a
    drive letter is refused *by name*, with a sentence saying why, rather than
    quietly becoming an ordinary directory called `C:` inside the destination.
    """
    return bool(_DRIVE.match(name))


def _refuse(what: str, name: str, reason: str) -> None:
    """Every refusal names the offending entry, because a refusal that does not
    say which member was wrong leaves the uploader nothing to act on. It never
    names the destination directory: these sentences reach an HTTP client, and
    the entry is the caller's own text while the directory is ours."""
    raise ValueError(f"Unsafe {what}: {name} — {reason}")


def safe_relative_path(name: str, *, what: str = "path") -> str:
    """The normalised relative path `name` denotes, or `ValueError`.

    Returns forward-slash separated text with redundant segments collapsed, so
    a caller storing it as a record field stores one spelling rather than
    whichever one the uploader's operating system happened to use.
    """
    if not isinstance(name, str) or not name.strip():
        _refuse(what, repr(name) if not isinstance(name, str) else name,
                "the name is empty")
    if "\0" in name:
        _refuse(what, name.replace("\0", "\\0"), "the name contains a null byte")

    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or has_drive(normalised):
        _refuse(what, name, "an absolute path cannot be stored inside the "
                            "destination directory")

    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        _refuse(what, name, "it contains a '..' segment")
    if not parts:
        _refuse(what, name, "the name is empty")
    return "/".join(parts)


def safe_destination(base_dir: str, name: str, *, what: str = "path") -> str:
    """Where `name` may be written under `base_dir`, or `ValueError`.

    The containment test is on the **resolved** path, so a symlink planted
    inside the base cannot be used as a door out of it. Neither path needs to
    exist yet.
    """
    base = os.path.realpath(base_dir)
    relative = safe_relative_path(name, what=what)
    destination = os.path.realpath(os.path.join(base, relative))
    if not destination.startswith(base + os.sep):
        # `destination == base` lands here too, and deliberately: a caller
        # asking to write "the directory itself" has asked for something it
        # cannot do, and the honest answer is the refusal rather than a path
        # that fails at `open`.
        _refuse(what, name, "it resolves outside the destination directory")
    return destination
