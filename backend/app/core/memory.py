from __future__ import annotations

import ctypes
import gc
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path


_LOGGER = logging.getLogger("revia.ocr.memory")
_MB = 1024 * 1024
_CGROUP_CURRENT_PATHS = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)
_CGROUP_STAT_PATHS = (
    Path("/sys/fs/cgroup/memory.stat"),
    Path("/sys/fs/cgroup/memory/memory.stat"),
)


@dataclass(frozen=True)
class ContainerMemorySnapshot:
    """Current cgroup usage plus its less misleading reclaimable-cache view."""

    current_mb: float = 0.0
    working_set_mb: float = 0.0
    inactive_file_mb: float = 0.0


def process_rss_mb(pid: int | None = None) -> float:
    """Return resident memory without importing psutil or other native runtimes."""
    target_pid = pid or os.getpid()
    proc_statm = Path(f"/proc/{target_pid}/statm")
    if proc_statm.exists():
        try:
            resident_pages = int(proc_statm.read_text(encoding="ascii").split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE") / _MB
        except (OSError, ValueError, IndexError):
            return 0.0
    if os.name == "nt":
        return _windows_process_rss_mb(target_pid)
    try:
        import resource

        maximum_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return maximum_rss / 1024 if maximum_rss else 0.0
    except (ImportError, OSError, ValueError):
        return 0.0


def container_memory_snapshot() -> ContainerMemorySnapshot:
    """Return raw cgroup memory and working set (raw minus inactive file cache).

    ``memory.current`` includes page cache. Large PDFs downloaded to a temporary
    file can therefore make the container look almost full even when a large
    part of that usage is reclaimable by the kernel. The working-set value is
    the conservative Docker-style view used for OCR admission decisions.
    """
    current_bytes = _read_first_integer(_CGROUP_CURRENT_PATHS)
    if current_bytes <= 0:
        return ContainerMemorySnapshot()
    stat = _read_first_memory_stat(_CGROUP_STAT_PATHS)
    inactive_file_bytes = max(
        int(stat.get("total_inactive_file", 0)),
        int(stat.get("inactive_file", 0)),
    )
    working_set_bytes = max(0, current_bytes - inactive_file_bytes)
    return ContainerMemorySnapshot(
        current_mb=current_bytes / _MB,
        working_set_mb=working_set_bytes / _MB,
        inactive_file_mb=inactive_file_bytes / _MB,
    )


def container_memory_mb() -> float:
    """Return raw current cgroup/container memory usage when Linux exposes it."""
    return container_memory_snapshot().current_mb


def release_process_memory() -> None:
    """Best-effort release of Python and glibc free memory in the current process."""
    gc.collect()
    if not sys.platform.startswith("linux"):
        return
    try:
        malloc_trim = ctypes.CDLL(None).malloc_trim
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        malloc_trim(0)
    except Exception:
        # Non-glibc Linux images may not expose malloc_trim. Cleanup must never
        # change the durable task result.
        return


def drop_file_cache(path: Path) -> bool:
    """Ask Linux to evict cached pages for a closed temporary file.

    This is advisory and must never affect correctness. It does not delete or
    truncate the file; the OCR worker can immediately reopen it normally.
    """
    if not sys.platform.startswith("linux"):
        return False
    posix_fadvise = getattr(os, "posix_fadvise", None)
    dont_need = getattr(os, "POSIX_FADV_DONTNEED", None)
    if posix_fadvise is None or dont_need is None:
        return False
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        posix_fadvise(descriptor, 0, 0, dont_need)
        return True
    except OSError:
        return False
    finally:
        os.close(descriptor)


def log_ocr_memory(stage: str, page_number: int, initialized: bool, *, rss_mb: float | None = None) -> float:
    current = process_rss_mb() if rss_mb is None else rss_mb
    _LOGGER.info(
        "ocr_memory stage=%s rss_mb=%.1f page=%d initialized=%s",
        stage,
        current,
        page_number,
        str(initialized).lower(),
    )
    return current


def _read_first_integer(paths: tuple[Path, ...]) -> int:
    for candidate in paths:
        if not candidate.exists():
            continue
        try:
            return int(candidate.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
    return 0


def _read_first_memory_stat(paths: tuple[Path, ...]) -> dict[str, int]:
    for candidate in paths:
        if not candidate.exists():
            continue
        values: dict[str, int] = {}
        try:
            for raw_line in candidate.read_text(encoding="ascii").splitlines():
                parts = raw_line.split()
                if len(parts) != 2:
                    continue
                try:
                    values[parts[0]] = int(parts[1])
                except ValueError:
                    continue
        except OSError:
            continue
        return values
    return {}


def _windows_process_rss_mb(pid: int) -> float:
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process_query_information = 0x0400
    process_vm_read = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_information | process_vm_read,
        False,
        pid,
    )
    if not handle:
        return 0.0
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            return 0.0
        return counters.WorkingSetSize / _MB
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
