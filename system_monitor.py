"""FileMind - read-only live system monitor.

Collects CPU / RAM / Disk / Battery / Network and (if present) NVIDIA GPU
metrics. 100% read-only: it only *reads* sensors, never changes anything on
the system - no overclocking, no fan control, no hardware modification.

Every sensor call is wrapped so a missing library or unsupported sensor
returns None / "Not available" instead of raising. The app must never crash
because a metric is unavailable.
"""

import os
import shutil
import subprocess

# Optional dependencies — degrade gracefully if absent.
try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    psutil = None
    HAS_PSUTIL = False

try:
    import GPUtil
    HAS_GPUTIL = True
except Exception:
    GPUtil = None
    HAS_GPUTIL = False

# Hide the console window when shelling out to nvidia-smi on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Shown on the CPU/RAM/Network/Battery cards when psutil cannot be imported.
INSTALL_HINT = "pip install psutil"


def psutil_available() -> bool:
    return HAS_PSUTIL


# ── individual sensors (each returns None on any failure) ────────────────────

def prime_cpu():
    """Prime psutil's CPU counter once (call after launch).

    The first psutil.cpu_percent() always returns 0.0; priming it means the
    later sampled reads are meaningful from the very first poll."""
    if not HAS_PSUTIL:
        return
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass


def cpu_percent(interval=0.1):
    """CPU load % sampled over a short window (default 100 ms).

    Sampling over a small interval gives a Task-Manager-like figure. This is
    called from a background thread, so the brief block never touches the UI."""
    if not HAS_PSUTIL:
        return None
    try:
        return float(psutil.cpu_percent(interval=interval))
    except Exception:
        return None


def cpu_cores():
    """{'physical': N, 'logical': M} or None."""
    if not HAS_PSUTIL:
        return None
    try:
        return {"physical": psutil.cpu_count(logical=False),
                "logical":  psutil.cpu_count(logical=True)}
    except Exception:
        return None


def ram():
    if not HAS_PSUTIL:
        return None
    try:
        m = psutil.virtual_memory()
        return {"percent": float(m.percent), "used": int(m.used),
                "total": int(m.total)}
    except Exception:
        return None


def disk_for(path):
    """Usage for a single drive/path, or None if it does not exist.

    Read-only: only queries free/used space — it never scans the disk."""
    # prefer psutil.disk_usage; fall back to shutil so it always works
    if HAS_PSUTIL:
        try:
            u = psutil.disk_usage(path)
            return {"percent": float(u.percent), "used": int(u.used),
                    "total": int(u.total)}
        except Exception:
            pass
    try:
        u = shutil.disk_usage(path)
        pct = (u.used / u.total * 100.0) if u.total else 0.0
        return {"percent": pct, "used": int(u.used), "total": int(u.total)}
    except Exception:
        return None


def disk(path=None):
    """Single-drive usage (kept for compatibility)."""
    return disk_for(path or ("C:\\" if os.name == "nt" else "/"))


def disks(letters=("C", "D", "E")):
    """Per-drive usage keyed by letter, e.g. {'C': {...}, 'D': None, ...}.

    A drive that does not exist maps to None (shown as 'Unavailable')."""
    out = {}
    for L in letters:
        path = f"{L}:\\" if os.name == "nt" else (
            "/" if L == "C" else f"/__nodrive_{L}__")
        out[L] = disk_for(path)
    return out


def battery():
    """Battery state.

    Returns:
      None                      -> psutil missing / sensor error
      {"present": False}        -> no battery (desktop / AC only)
      {"present": True, ...}    -> percent + plugged
    """
    if not HAS_PSUTIL:
        return None
    try:
        fn = getattr(psutil, "sensors_battery", None)
        if fn is None:
            return {"present": False}
        b = fn()
        if b is None:                       # desktop / no battery present
            return {"present": False}
        return {"present": True, "percent": float(b.percent),
                "plugged": bool(b.power_plugged)}
    except Exception:
        return None


def network():
    if not HAS_PSUTIL:
        return None
    try:
        stats = psutil.net_if_stats()
        up = []
        for name, s in stats.items():
            low = name.lower()
            if not s.isup:
                continue
            if low.startswith("lo") or "loopback" in low:
                continue
            up.append(name)
        return {"online": bool(up), "interfaces": up}
    except Exception:
        return None


def gpu():
    """First NVIDIA GPU's load / temp / memory, or None if unavailable."""
    # 1 — GPUtil
    if HAS_GPUTIL:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                g = gpus[0]
                temp = getattr(g, "temperature", None)
                return {
                    "name":      getattr(g, "name", "GPU"),
                    "load":      float(g.load) * 100.0,
                    "mem_used":  float(getattr(g, "memoryUsed", 0) or 0),
                    "mem_total": float(getattr(g, "memoryTotal", 0) or 0),
                    "temp":      float(temp) if temp is not None else None,
                }
        except Exception:
            pass

    # 2 — nvidia-smi fallback (read-only query)
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,temperature.gpu,"
             "memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=_NO_WINDOW)
        line = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and line:
            p = [x.strip() for x in line[0].split(",")]

            def _f(i):
                try:
                    return float(p[i])
                except Exception:
                    return None
            return {
                "name":      p[4] if len(p) > 4 else "GPU",
                "load":      _f(0),
                "temp":      _f(1),
                "mem_used":  _f(2),
                "mem_total": _f(3),
            }
    except Exception:
        pass

    return None


def snapshot(check_gpu=True):
    """Read every sensor once. Safe to call from a background thread.

    Returns a dict; any unavailable metric is None. `check_gpu=False` skips
    the GPU probe entirely (used once we know there is no NVIDIA GPU, so we
    don't keep spawning nvidia-smi).
    """
    return {
        "cpu":     cpu_percent(0.1),
        "cores":   cpu_cores(),
        "ram":     ram(),
        "disks":   disks(("C", "D", "E")),
        "battery": battery(),
        "network": network(),
        "gpu":     gpu() if check_gpu else None,
    }
