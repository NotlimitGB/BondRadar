from __future__ import annotations

import os
import subprocess
import tempfile
import time
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath

import rarfile

from .contracts import (
    ArchiveMember,
    CbrBankArtifact,
    CbrSourceError,
    CbrSourceStatus,
)


MAX_ARCHIVE_MEMBERS = 16
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
EXTRACTION_TIMEOUT_SECONDS = 20
RAR_SIGNATURES = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")


def resolve_libarchive_executable(explicit: str | None = None) -> str:
    if explicit is not None:
        candidate = Path(explicit).resolve()
    elif os.name == "nt":
        candidate = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe").resolve()
    else:
        candidate = Path("/usr/bin/bsdtar")
    if not candidate.is_file():
        raise CbrSourceError(
            CbrSourceStatus.RAR_RUNTIME_UNAVAILABLE,
            "verified libarchive runtime is unavailable",
        )
    allowed = (
        candidate == Path("/usr/bin/bsdtar")
        if os.name != "nt"
        else candidate.name.casefold() == "tar.exe"
        and candidate.parent.name.casefold() == "system32"
    )
    if not allowed:
        raise CbrSourceError(
            CbrSourceStatus.RAR_RUNTIME_UNAVAILABLE,
            "unapproved archive runtime",
        )
    try:
        result = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CbrSourceError(
            CbrSourceStatus.RAR_RUNTIME_UNAVAILABLE,
            "archive runtime validation failed",
        ) from exc
    version = (result.stdout + result.stderr).decode("utf-8", errors="replace").casefold()
    if result.returncode != 0 or "bsdtar" not in version or "libarchive" not in version:
        raise CbrSourceError(
            CbrSourceStatus.RAR_RUNTIME_UNAVAILABLE,
            "archive runtime is not libarchive bsdtar",
        )
    return str(candidate)


def inspect_archive_bytes(content: bytes) -> tuple[ArchiveMember, ...]:
    if not content.startswith(RAR_SIGNATURES):
        raise CbrSourceError(CbrSourceStatus.INVALID_ARCHIVE, "invalid RAR signature")
    with tempfile.TemporaryDirectory(prefix="bondradar-task251-rar-") as directory:
        archive_path = Path(directory) / "source.rar"
        archive_path.write_bytes(content)
        try:
            with rarfile.RarFile(archive_path, errors="strict") as archive:
                if archive.is_solid():
                    raise CbrSourceError(
                        CbrSourceStatus.UNSUPPORTED_ARCHIVE_FEATURE,
                        "solid archives are unsupported",
                    )
                if len(archive.volumelist()) != 1:
                    raise CbrSourceError(
                        CbrSourceStatus.UNSUPPORTED_ARCHIVE_FEATURE,
                        "multi-volume archives are unsupported",
                    )
                infos = archive.infolist()
        except CbrSourceError:
            raise
        except (rarfile.Error, OSError, ValueError) as exc:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_ARCHIVE, "invalid RAR archive"
            ) from exc
    if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
        code = (
            CbrSourceStatus.ARCHIVE_TOO_MANY_MEMBERS
            if infos
            else CbrSourceStatus.INVALID_ARCHIVE
        )
        raise CbrSourceError(code, "invalid archive member count")
    result: list[ArchiveMember] = []
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = str(info.filename)
        posix = PurePosixPath(name.replace("\\", "/"))
        windows = PureWindowsPath(name)
        if (
            not name
            or name != posix.name
            or posix.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or any(part in {"", ".", ".."} for part in posix.parts)
        ):
            raise CbrSourceError(
                CbrSourceStatus.ARCHIVE_PATH_TRAVERSAL,
                "archive member path is not allowed",
            )
        normalized = name.casefold()
        if normalized in seen:
            raise CbrSourceError(
                CbrSourceStatus.ARCHIVE_DUPLICATE_MEMBER,
                "duplicate archive member",
            )
        seen.add(normalized)
        is_file = bool(getattr(info, "is_file", lambda: True)())
        is_symlink = bool(getattr(info, "is_symlink", lambda: False)())
        if (
            not is_file
            or is_symlink
            or getattr(info, "needs_password", lambda: False)()
            or getattr(info, "volume", 0) not in {0, None}
            or getattr(info, "file_redir", None) is not None
            or not name.casefold().endswith(".dbf")
        ):
            raise CbrSourceError(
                CbrSourceStatus.UNSUPPORTED_ARCHIVE_FEATURE,
                "unsupported archive member",
            )
        size = int(info.file_size)
        compressed_size = int(info.compress_size)
        if size < 0 or compressed_size < 0 or size > MAX_MEMBER_BYTES:
            raise CbrSourceError(
                CbrSourceStatus.ARCHIVE_MEMBER_TOO_LARGE,
                "archive member exceeds size limit",
            )
        total += size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise CbrSourceError(
                CbrSourceStatus.ARCHIVE_TOTAL_TOO_LARGE,
                "archive exceeds total size limit",
            )
        result.append(
            ArchiveMember(
                name=name,
                normalized_name=name.upper(),
                compressed_size=compressed_size,
                uncompressed_size=size,
                crc32=int(info.CRC) if info.CRC is not None else None,
            )
        )
    return tuple(result)


def extract_archive_members(
    artifact: CbrBankArtifact,
    *,
    executable: str | None = None,
) -> tuple[tuple[ArchiveMember, bytes], ...]:
    members = inspect_archive_bytes(artifact.content)
    tool = resolve_libarchive_executable(executable)
    with tempfile.TemporaryDirectory(prefix="bondradar-task251-extract-") as directory:
        archive_path = Path(directory) / "source.rar"
        archive_path.write_bytes(artifact.content)
        extracted: list[tuple[ArchiveMember, bytes]] = []
        for member in members:
            with tempfile.TemporaryFile() as output:
                try:
                    process = subprocess.Popen(
                        [tool, "-xOf", str(archive_path), member.name],
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError as exc:
                    raise CbrSourceError(
                        CbrSourceStatus.INVALID_ARCHIVE,
                        "archive member extraction failed",
                    ) from exc
                deadline = time.monotonic() + EXTRACTION_TIMEOUT_SECONDS
                while process.poll() is None:
                    if time.monotonic() >= deadline or output.tell() > MAX_MEMBER_BYTES:
                        process.kill()
                        process.wait(timeout=5)
                        raise CbrSourceError(
                            CbrSourceStatus.INVALID_ARCHIVE,
                            "archive member extraction exceeded limits",
                        )
                    time.sleep(0.01)
                if process.returncode != 0 or output.tell() > MAX_MEMBER_BYTES:
                    raise CbrSourceError(
                        CbrSourceStatus.INVALID_ARCHIVE,
                        "archive member extraction failed",
                    )
                output.seek(0)
                payload = output.read(MAX_MEMBER_BYTES + 1)
                if len(payload) != member.uncompressed_size:
                    raise CbrSourceError(
                        CbrSourceStatus.INVALID_ARCHIVE,
                        "archive member extraction failed",
                    )
            if member.crc32 is not None and zlib.crc32(payload) & 0xFFFFFFFF != member.crc32:
                raise CbrSourceError(
                    CbrSourceStatus.INVALID_ARCHIVE,
                    "archive member checksum mismatch",
                )
            extracted.append((member, payload))
    return tuple(extracted)
