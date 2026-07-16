from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path


_LOGGER = logging.getLogger("revia.ocr.memory")
_MB = 1024 * 1024


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
