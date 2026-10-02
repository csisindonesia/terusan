"""Reading a member out of a ZIP without trusting its header.

Several sources publish archives — Bank Indonesia's surveys, OJK's banking
statistics, GDELT's exports, UCDP's releases — and each extractor reads one
member into memory. A ZIP is a few bytes that claim to unpack to some size, and
the claim is the archive's own: a crafted one can say a kilobyte and unpack to
tens of gigabytes. On the NAS, with 8 GB shared between the pipelines, DuckDB
and the API, one such file landed from a compromised or mistaken portal would
take everything down with it rather than fail as one bad document.

So the declared size is checked, and the read itself is bounded too, because
the declared size is exactly the part an attacker writes.
"""

from __future__ import annotations

import zipfile
import zlib

from .base import ExtractionError

#: The most one archive member may unpack to.
#:
#: The largest member in the lake today is an OJK banking-statistics workbook
#: of about 56 MB, so this leaves close to ten times that for growth. It is set
#: well below the NAS's memory, not at it, because the member is then decoded
#: and parsed, and a workbook in openpyxl or a CSV as a Python string takes
#: several times its size on disk.
MAX_MEMBER_BYTES = 512 * 1024 * 1024


def read_member(
    archive: zipfile.ZipFile,
    member: str | zipfile.ZipInfo,
    raw_path: str,
    *,
    limit: int | None = None,
) -> bytes:
    """The bytes of one archive member, refused if it unpacks past `limit`.

    Raises `ExtractionError`, which every extractor already reports as a
    refused document, so an oversized member fails its own artifact and the
    run carries on with the rest.
    """
    # Read at call time rather than bound as a default, so a test can lower it.
    limit = MAX_MEMBER_BYTES if limit is None else limit
    info = member if isinstance(member, zipfile.ZipInfo) else archive.getinfo(member)
    if info.file_size > limit:
        raise ExtractionError(
            raw_path,
            f"archive member {info.filename} declares {info.file_size} bytes, "
            f"over the {limit}-byte limit",
        )
    # One byte past the limit is enough to know the header lied, and reading
    # no further is what keeps a bomb from being unpacked in full first.
    #
    # Recent CPython also stops at the declared size and fails the CRC check
    # when there is more, which surfaces as `BadZipFile`. Either way the
    # member is refused; it is reported in the same terms as any other.
    try:
        with archive.open(info) as stream:
            data = stream.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ExtractionError(
            raw_path, f"archive member {info.filename} is corrupt: {exc}"
        ) from exc
    if len(data) > limit:
        raise ExtractionError(
            raw_path,
            f"archive member {info.filename} unpacks past the {limit}-byte limit "
            f"although its header declares {info.file_size}",
        )
    return data
