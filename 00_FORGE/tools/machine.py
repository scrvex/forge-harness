"""Machine probe & auto-tune (K28, 0.7.0): Forge checks the PC it is running on and configures itself for it.

    python <plugin>/tools/machine.py             # print the cached profile (probe if stale/missing)
    python <plugin>/tools/machine.py --refresh   # force a new probe

Cached at <state>/machine.json (TTL 6 h; the SessionStart brief calls ensure() so every session starts from a fresh
picture of THIS machine). Everything is measured, nothing hardcoded to one PC:
- CPU: logical threads + physical cores (PowerShell Win32_Processor; fallback = logical/2);
- RAM free/total (GlobalMemoryStatusEx on Windows; psutil-free);
- GPU: nvidia-smi name/VRAM/util (absent -> no local tier);
- Ollama: reachable, loaded models, OLLAMA_NUM_PARALLEL / MAX_LOADED_MODELS.
Tuning rules (sources: bench_threads.py runs 2026-09-20 on i9-12900F + RTX 4070 Ti; docs.ollama.com/faq; llama.cpp #572):
- num_thread is NOT set on GPU runs: forcing it reloads the model and slows prompt eval ~4x (8450 -> ~2000 tok/s
  measured); generation speed was equal (76 tok/s at 8 and at 22 threads).
- CPU-only inference is ~11x slower (6.6-7.0 tok/s measured, memory-bound: 8 vs 22 threads within noise) ->
  never CPU-offload a model; if no VRAM fits, the local tier is OFF, not slow.
- Local models by free VRAM: >=10 GB -> extract 8B + review 12B(16k ctx); >=6 -> 8B for both; >=3 -> 4B; else none.
- Owner budget (M18): total machine load stays < 90% - slots are capped, one heavy local run at a time,
  Claude sub-agents <= 2; the lead runs scripts on the CPU in parallel with GPU model runs.

Live load (K28): `machine.py load [--json]` is a snapshot taken NOW (no sudo, no admin): CPU load ratio, memory pressure,
thermal state, GPU utilization, battery. The verdict ok / busy / hot / critical gives the recommended slots for heavy
parallel work; the codex wave, the Ollama runs and the brief read it. Sources, per platform:
- macOS: loadavg, `memory_pressure`, `vm_stat` swapouts, thermal pressure from `notifyutil -g com.apple.system.thermalpressurelevel`
  (the only thermal signal on Apple silicon; `pmset -g therm` prints nothing there), `pmset -g therm` for Intel
  (CPU_Speed_Limit, Thermal Warning Level), `ioreg` GPU, `pmset -g batt`;
- Linux: loadavg, /proc/meminfo, thermal zones, /sys/class/power_supply;
- Windows: ctypes only (GetSystemTimes, GlobalMemoryStatusEx, GetSystemPowerStatus), no PowerShell on the hot path.
  Windows has NO thermal or throttling signal (the WMI thermal zone needs admin): there only CPU and memory can alarm.
A machine with a battery is a laptop without active cooling and is capped at 2 heavy actors. A signal that cannot be read is
unknown and never alarms (the verdict lists it in `unknown`). All thresholds are assumptions, not measured on the owner's PCs.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import shutil
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg  # noqa: E402

TTL_H = 6

# Live-load thresholds (K28). Reason: throttling and memory pressure slow every actor and wear a laptop without fans.
# All values are assumptions (not measured on the owner's PCs); change them here only.
CPU_BUSY = 0.6          # 1-min load / logical CPUs: above this the machine is busy; the verdict says so, slots stay
CPU_HOT = 0.8           # above this the owner's own work starts to lag: one heavy actor
MEM_FREE_HOT_PCT = 20   # free memory under this: the next model load starts swapping
MEM_FREE_CRIT_PCT = 10  # free memory under this: swapping is certain
GPU_BUSY_PCT = 85       # a saturated GPU is reported, slots stay: a Codex wave and CPU scripts do not use the GPU (K28)
TEMP_HOT_C = 95         # hottest thermal zone (Linux): near the usual 100 C limit of a CPU
SWAP_GROWTH_MB = 256    # swapped out since the previous snapshot: below this a few pages are noise, not memory pressure
SWAP_WINDOW_S = 600     # a snapshot older than this says nothing about the present
SWAP_KEEP_S = 30        # a call this soon after the previous one compares with the same baseline (no dilution)
LAPTOP_SLOTS = 2        # owner M18 on a laptop without active cooling: at most 2 heavy actors
NO_PROFILE_CAP = 16     # base for a caller that names no count of its own: only the load and the laptop cap lower it
THERMAL_PRESSURE_KEY = "com.apple.system.thermalpressurelevel"
THERMAL_PRESSURE_NAMES = {1: "moderate", 2: "heavy", 3: "trapping", 4: "sleeping"}  # OSThermalNotification.h, macOS: 0 nominal
THERMAL_LEVEL_UNKNOWN = 255  # IOPM.h: 0 normal, 5 danger, 10 critical, 100 warning, 110 trap, 255 unknown
PROBE_TIMEOUT = 3


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def probe() -> dict:
    cap: dict = {"probed": time.strftime("%Y-%m-%d %H:%M"), "os": os.name}
    cap["cpu_threads"] = os.cpu_count() or 1
    cores = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"], 20)
    cap["cpu_cores"] = int(cores) if cores.isdigit() else max(1, cap["cpu_threads"] // 2)
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                        ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
        m = MS(); m.dwLength = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
        cap["ram_total_gb"] = round(m.ullTotalPhys / 2**30, 1); cap["ram_free_gb"] = round(m.ullAvailPhys / 2**30, 1)
    except Exception:
        cap["ram_total_gb"] = cap["ram_free_gb"] = None
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"]) if shutil.which("nvidia-smi") else ""
    if smi:
        name, total, used, util, temp = [x.strip() for x in smi.splitlines()[0].split(",")]
        cap["gpu"] = {"name": name, "vram_total_gb": round(int(total) / 1024, 1), "vram_free_gb": round((int(total) - int(used)) / 1024, 1),
                      "util_pct": int(util), "temp_c": int(temp)}
    else:
        cap["gpu"] = None
    ps = _run(["ollama", "ps"]) if shutil.which("ollama") else ""
    cap["ollama"] = {"available": bool(ps or shutil.which("ollama")), "reachable": bool(ps != "" or not shutil.which("ollama")),
                     "loaded": [l.split()[0] for l in ps.splitlines()[1:] if l.strip()],
                     "num_parallel": int(os.environ.get("OLLAMA_NUM_PARALLEL", "1") or 1),
                     "max_loaded": int(os.environ.get("OLLAMA_MAX_LOADED_MODELS", "1") or 1)}
    return cap


def tune(cap: dict) -> dict:
    vram = (cap.get("gpu") or {}).get("vram_free_gb") or 0
    ollama = cap.get("ollama", {}).get("available", False)
    if ollama and vram >= 10:
        models = {"extract": "qwen3:8b", "review": "gemma4:12b", "classify": "gemma3:4b", "review_ctx_max": 16384}
    elif ollama and vram >= 6:
        models = {"extract": "qwen3:8b", "review": "qwen3:8b", "classify": "gemma3:4b"}
    elif ollama and vram >= 3:
        models = {"extract": "gemma3:4b", "review": "gemma3:4b", "classify": "gemma3:4b"}
    else:
        models = {}
    return {
        "slots": {
            "local_heavy": 1 if models else 0,             # one heavy local run at a time (owner)
            "local_light": 1 if (ollama and vram >= 3) else 0,
            "claude_subagents": 2,                          # owner cap
            "scripts": max(1, cap.get("cpu_cores", 2) // 2),
        },
        "local_models": models,
        "num_thread": None,   # never set on GPU (bench 2026-09-20: model reload + ~4x slower prompt eval)
        "cpu_offload": False,  # measured ~11x slower and memory-bound - the local tier turns OFF instead
        "load_budget_pct": 90,  # owner M18: leave headroom for the person's own work
        "notes": ["research agents (web) only after an explicit owner yes",
                  "run CPU scripts in parallel with GPU model runs; lead controls via manifest + acceptance report"],
    }


def ensure(root: Path | None = None, refresh: bool = False) -> dict:
    root = root or find_root()
    state = load_cfg(root)["_layout"]["state"]
    f = state / "machine.json"
    if not refresh and f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - d.get("_ts", 0) < TTL_H * 3600:
                return d
        except (json.JSONDecodeError, OSError):
            pass
    cap = probe()
    d = {"_ts": time.time(), "capacity": cap, "tuned": tune(cap)}
    state.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return d


def line(d: dict) -> str:
    c, t = d.get("capacity", {}), d.get("tuned", {})
    gpu = c.get("gpu")
    g = f"GPU {gpu['vram_free_gb']}/{gpu['vram_total_gb']}GB free" if gpu else "no GPU"
    s = t.get("slots", {})
    lm = t.get("local_models", {})
    return (f"machine: {c.get('cpu_cores', '?')}C/{c.get('cpu_threads', '?')}T · RAM {c.get('ram_free_gb', '?')}/{c.get('ram_total_gb', '?')}GB · {g}"
            f" · slots {s.get('claude_subagents', 0)} Claude / {s.get('local_heavy', 0)} local ({lm.get('extract', '-')}) / {s.get('scripts', 0)} scripts · budget <{t.get('load_budget_pct', 90)}%")


# ---------------------------------------------------------------- live load
def _num(m) -> float | None:
    try:
        return float(m.group(1)) if m else None
    except (ValueError, IndexError):
        return None


def parse_memory_pressure(text: str) -> float | None:
    """macOS `memory_pressure`: 'System-wide memory free percentage: 63%'."""
    return _num(re.search(r"free percentage:\s*([\d.]+)\s*%", text or "", re.I))


def parse_vm_stat_swapouts(text: str) -> int | None:
    """macOS `vm_stat`: 'Swapouts:  123.' (pages, cumulative since boot)."""
    m = re.search(r"^Swapouts:\s*(\d+)", text or "", re.M)
    return int(m.group(1)) if m else None


def parse_vm_stat_page_size(text: str) -> int:
    """macOS `vm_stat` header: '(page size of 16384 bytes)'; 16384 (Apple silicon) when the header is missing."""
    m = re.search(r"page size of (\d+) bytes", text or "", re.I)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else 16384


def parse_thermal_pressure(text: str) -> int | None:
    """macOS `notifyutil -g com.apple.system.thermalpressurelevel`: 'com.apple.system.thermalpressurelevel 0'.
    Levels (libkern/OSThermalNotification.h, macOS): 0 nominal, 1 moderate, 2 heavy, 3 trapping, 4 sleeping.
    The last integer of the last line; None when there is no output or no number (unknown, never an alarm)."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    m = re.search(r"(\d+)\s*$", lines[-1]) if lines else None
    return int(m.group(1)) if m else None


def parse_pmset_therm(text: str) -> dict:
    """macOS `pmset -g therm` (Intel Macs; Apple silicon only prints 'Note: No ... recorded').
    Real formats (strings of /usr/bin/pmset): 'Thermal Warning Level = %d', 'Performance Warning Level = %d',
    'CPU_Speed_Limit = %d'. 'Error:Failed to get ...' and 'Note: No ...' lines carry no value: unknown, never throttling.
    Throttled = CPU_Speed_Limit < 100, or a Thermal Warning Level of 1..254 (IOPM.h: 0 normal, 255 unknown).
    The Performance Warning Level is reported but not used: its scale is not checked."""
    t = text or ""
    limit = _num(re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", t))
    lv = {k.lower(): int(v) for k, v in re.findall(r"\b(Thermal|Performance) Warning Level\s*=\s*(\d+)", t, re.I)}
    thermal = lv.get("thermal")
    warn = thermal is not None and 0 < thermal < THERMAL_LEVEL_UNKNOWN
    return {"throttled": warn or (limit is not None and limit < 100), "cpu_speed_limit": int(limit) if limit is not None else None,
            "thermal_warning_level": thermal, "performance_warning_level": lv.get("performance")}


def parse_ioreg_gpu(text: str) -> float | None:
    """macOS `ioreg -r -d 1 -c IOAccelerator`: '"Device Utilization %"=12' (max over devices)."""
    vals = [int(v) for v in re.findall(r'"Device Utilization %"\s*=\s*(\d+)', text or "")]
    return float(max(vals)) if vals else None


def parse_pmset_batt(text: str) -> dict:
    """macOS `pmset -g batt`: power source and presence of an InternalBattery."""
    t = text or ""
    src = re.search(r"Now drawing from '([^']+)'", t)
    pct = re.search(r"(\d+)%", t)
    return {"battery": "InternalBattery" in t, "source": (src.group(1) if src else None), "battery_pct": int(pct.group(1)) if pct else None}


def parse_meminfo_free_pct(text: str) -> float | None:
    """Linux /proc/meminfo: MemAvailable / MemTotal."""
    tot = _num(re.search(r"^MemTotal:\s*(\d+)", text or "", re.M))
    av = _num(re.search(r"^MemAvailable:\s*(\d+)", text or "", re.M))
    return round(100 * av / tot, 1) if tot and av is not None else None


def parse_nvidia_util(text: str) -> float | None:
    """nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits (max over GPUs)."""
    vals = [int(l.strip()) for l in (text or "").splitlines() if l.strip().isdigit()]
    return float(max(vals)) if vals else None


# -- Windows: ctypes only. Each helper returns None when it cannot read (the field stays unknown); the glue is thin
# so the logic around it (cpu_ratio_from_times, win_power) is tested without Windows.
def _win_system_times() -> tuple[int, int, int] | None:
    """GetSystemTimes: (idle, kernel, user) in 100 ns ticks; kernel time includes idle time."""
    try:
        import ctypes
        from ctypes import wintypes
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):  # type: ignore[attr-defined]
            return None
        return tuple((f.dwHighDateTime << 32) | f.dwLowDateTime for f in (idle, kern, user))  # type: ignore[return-value]
    except Exception:
        return None


def cpu_ratio_from_times(a: tuple | None, b: tuple | None) -> float | None:
    """Two GetSystemTimes samples -> busy share 0..1 of all logical CPUs between them."""
    if not a or not b:
        return None
    total = (b[1] - a[1]) + (b[2] - a[2])
    if total <= 0:
        return None
    return round(min(1.0, max(0.0, 1 - (b[0] - a[0]) / total)), 2)


def _win_cpu_ratio(interval: float = 0.4) -> float | None:
    first = _win_system_times()
    if first is None:
        return None
    time.sleep(interval)
    return cpu_ratio_from_times(first, _win_system_times())


def _win_mem_free_pct() -> float | None:
    """GlobalMemoryStatusEx: 100 - dwMemoryLoad."""
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                        ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):  # type: ignore[attr-defined]
            return None
        return float(100 - m.dwMemoryLoad)
    except Exception:
        return None


def _win_power_status() -> tuple[int, int] | None:
    """GetSystemPowerStatus: (ACLineStatus, BatteryFlag)."""
    try:
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]
        p = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)):  # type: ignore[attr-defined]
            return None
        return p.ACLineStatus, p.BatteryFlag
    except Exception:
        return None


def win_power(status: tuple[int, int] | None) -> dict:
    """GetSystemPowerStatus: BatteryFlag 128 = no system battery, 255 = unknown; ACLineStatus 0 = battery, 1 = mains."""
    if not status:
        return {"battery": False, "source": None}
    ac, flag = status
    return {"battery": flag not in (128, 255), "source": {0: "Battery Power", 1: "AC Power"}.get(ac)}


def _swap_state_file() -> Path:
    return Path(tempfile.gettempdir()) / "forge_machine_swap.json"


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _swap_growing(swapouts: int | None, page_size: int = 16384, state: Path | None = None) -> bool:
    """True when the cumulative swapouts grew by SWAP_GROWTH_MB or more since the snapshot kept in the temp dir
    (not older than SWAP_WINDOW_S). A call within SWAP_KEEP_S of the snapshot compares with it without replacing it, so
    back-to-back calls (brief, wave, Ollama) do not dilute the growth. A counter below the snapshot (reboot), a stale,
    missing or broken state file = no growth. Never raises."""
    if swapouts is None:
        return False
    growing = False
    try:
        f = state or _swap_state_file()
        now = time.time()
        keep = False
        try:
            prev = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = None
        if isinstance(prev, dict) and _is_num(prev.get("t")) and _is_num(prev.get("n")):
            age = now - prev["t"]
            if 0 <= age < SWAP_WINDOW_S and swapouts >= prev["n"]:
                growing = (swapouts - prev["n"]) * page_size >= SWAP_GROWTH_MB * 2**20
                keep = age < SWAP_KEEP_S
        if not keep:
            f.write_text(json.dumps({"t": now, "n": swapouts}), encoding="utf-8")
    except Exception:
        pass
    return growing


def _blank_sample() -> dict:
    return {"os": sys.platform, "cpus": os.cpu_count() or 1, "cpu_ratio": None, "cpu_pct": None, "mem_free_pct": None,
            "swap_growing": False, "throttled": False, "cpu_speed_limit": None, "thermal_pressure": None, "thermal_c": None,
            "gpu_pct": None, "battery": False, "power_source": None}


def _loadavg_ratio(cpus: int) -> float | None:
    try:
        return round(os.getloadavg()[0] / cpus, 2)
    except (OSError, AttributeError):
        return None


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _sample_windows(s: dict) -> None:
    ratio = _win_cpu_ratio()
    if ratio is not None:
        s["cpu_ratio"], s["cpu_pct"] = ratio, round(ratio * 100, 1)
    s["mem_free_pct"] = _win_mem_free_pct()
    p = win_power(_win_power_status())
    s["battery"], s["power_source"] = p["battery"], p["source"]


def _sample_macos(s: dict) -> None:
    s["cpu_ratio"] = _loadavg_ratio(s["cpus"])
    s["mem_free_pct"] = parse_memory_pressure(_run(["memory_pressure"], PROBE_TIMEOUT))
    vm = _run(["vm_stat"], PROBE_TIMEOUT)
    s["swap_growing"] = _swap_growing(parse_vm_stat_swapouts(vm), parse_vm_stat_page_size(vm))
    s["thermal_pressure"] = parse_thermal_pressure(_run(["notifyutil", "-g", THERMAL_PRESSURE_KEY], PROBE_TIMEOUT))
    th = parse_pmset_therm(_run(["pmset", "-g", "therm"], PROBE_TIMEOUT))
    s["throttled"], s["cpu_speed_limit"] = th["throttled"], th["cpu_speed_limit"]
    s["gpu_pct"] = parse_ioreg_gpu(_run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], PROBE_TIMEOUT))
    b = parse_pmset_batt(_run(["pmset", "-g", "batt"], PROBE_TIMEOUT))
    s["battery"], s["power_source"] = b["battery"], b["source"]


def _sample_linux(s: dict) -> None:
    s["cpu_ratio"] = _loadavg_ratio(s["cpus"])
    s["mem_free_pct"] = parse_meminfo_free_pct(_read_text("/proc/meminfo"))
    temps = []
    for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            temps.append(int(z.read_text().strip()) / 1000)
        except (OSError, ValueError):
            pass
    s["thermal_c"] = max(temps) if temps else None
    s["battery"] = any(Path("/sys/class/power_supply").glob("BAT*")) if Path("/sys/class/power_supply").is_dir() else False


def _sample_nvidia(s: dict) -> None:
    if s["gpu_pct"] is None and shutil.which("nvidia-smi"):
        s["gpu_pct"] = parse_nvidia_util(_run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], PROBE_TIMEOUT))


def sample() -> dict:
    """One live snapshot of THIS machine. A probe that fails leaves its fields unknown (None / False); never raises."""
    s = _blank_sample()
    plat = _sample_windows if sys.platform == "win32" else _sample_macos if sys.platform == "darwin" else _sample_linux
    for probe_fn in (plat, _sample_nvidia):
        try:
            probe_fn(s)
        except Exception:  # one broken probe must not take the other (or the caller) down
            pass
    return s


def verdict(s: dict, profile_slots: int = NO_PROFILE_CAP) -> dict:
    """ok / busy / hot / critical with reasons and the recommended slots for heavy parallel work.
    critical = 0 slots; hot = 1; busy and ok = profile_slots (a laptop without active cooling: at most LAPTOP_SLOTS).
    `unknown` lists the signals that could not be read (they never alarm)."""
    laptop = bool(s.get("battery"))
    crit, hot, busy = [], [], []
    if s.get("throttled"):
        crit.append("thermal throttling" + (f" (CPU speed limit {s['cpu_speed_limit']}%)" if s.get("cpu_speed_limit") is not None else ""))
    tp = s.get("thermal_pressure")
    if _is_num(tp) and tp >= 2:
        crit.append(f"thermal pressure {THERMAL_PRESSURE_NAMES.get(int(tp), f'level {int(tp)}')}")
    elif _is_num(tp) and tp == 1:
        hot.append(f"thermal pressure {THERMAL_PRESSURE_NAMES[1]}")
    mem = s.get("mem_free_pct")
    if mem is not None and mem < MEM_FREE_CRIT_PCT:
        crit.append(f"memory free {mem:.0f}%")
    if s.get("swap_growing"):
        crit.append("swap growing")
    cpu = s.get("cpu_ratio")
    if cpu is not None and cpu > CPU_HOT:
        hot.append(f"CPU load {cpu:.2f} per core")
    elif cpu is not None and cpu > CPU_BUSY:
        busy.append(f"CPU load {cpu:.2f} per core")
    if mem is not None and MEM_FREE_CRIT_PCT <= mem < MEM_FREE_HOT_PCT:
        hot.append(f"memory free {mem:.0f}%")
    temp = s.get("thermal_c")
    if temp is not None and temp >= TEMP_HOT_C:
        hot.append(f"temperature {temp:.0f} C")
    gpu = s.get("gpu_pct")
    if gpu is not None and gpu > GPU_BUSY_PCT:
        busy.append(f"GPU {gpu:.0f}%")
    base = max(0, int(profile_slots))
    if laptop:
        base = min(base, LAPTOP_SLOTS)
    if crit:
        level, slots, reasons = "critical", 0, crit + hot + busy
    elif hot:
        level, slots, reasons = "hot", min(1, base), hot + busy
    elif busy:
        level, slots, reasons = "busy", base, busy
    else:
        level, slots, reasons = "ok", base, []
    return {"verdict": level, "reasons": reasons, "slots": slots, "laptop": laptop, "unknown": ["cpu"] if cpu is None else [], "sample": s}


def load(profile_slots: int | None = None) -> dict:
    """Live check: sample + verdict. Never raises (an unreadable probe is unknown and the verdict stays ok).
    profile_slots is the count the caller wants to run in parallel; None = the caller has no count (NO_PROFILE_CAP), so the
    recommended slots come from the live load and the laptop cap only. The caller takes min(its own count, slots)."""
    try:
        s = sample()
    except Exception:
        s = _blank_sample()
    return verdict(s, NO_PROFILE_CAP if profile_slots is None else profile_slots)


def load_line(v: dict) -> str:
    parts = list(v["reasons"]) + [f"{u} unknown" for u in v.get("unknown") or []]
    return f"machine: {v['verdict']} ({'; '.join(parts)})" if parts else f"machine: {v['verdict']}"


def load_text(v: dict) -> str:
    s = v["sample"]

    def f(x, suf=""):
        return "?" if x is None else f"{x}{suf}"
    tp = s.get("thermal_pressure")
    slots = ("not limited by the load (the caller keeps its own count)" if v["slots"] >= NO_PROFILE_CAP else str(v["slots"])) \
        + (" (laptop without active cooling: capped at 2)" if v["laptop"] else "")
    return "\n".join([
        f"verdict: {v['verdict']}" + (f" - {'; '.join(v['reasons'])}" if v["reasons"] else ""),
        f"slots for heavy parallel work: {slots}",
        f"cpu: load {f(s['cpu_ratio'])} per core ({s['cpus']} logical)", f"memory free: {f(s['mem_free_pct'], '%')}" + (", swap growing" if s["swap_growing"] else ""),
        f"thermal: {'THROTTLED' if s['throttled'] else 'normal'}" + (f", CPU speed limit {s['cpu_speed_limit']}%" if s["cpu_speed_limit"] is not None else "")
        + (f", pressure {THERMAL_PRESSURE_NAMES.get(int(tp), 'nominal' if tp == 0 else tp)}" if _is_num(tp) else "")
        + (f", {s['thermal_c']} C" if s["thermal_c"] is not None else ""),
        f"gpu: {f(s['gpu_pct'], '%')}", f"power: {'battery machine' if s['battery'] else 'no battery'}" + (f", {s['power_source']}" if s["power_source"] else "")])


def refuse_reason(heavy: int = 1) -> str | None:
    """None when a heavy run may start now, else the reason (critical = 0 slots). Never raises."""
    try:
        v = load()
    except Exception:
        return None
    if v["slots"] >= heavy:
        return None
    return f"machine is {v['verdict']} ({'; '.join(v['reasons']) or 'no free slots'}); re-check with: python {Path(__file__).resolve().as_posix()} load"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "load":
        _v = load()
        print(json.dumps(_v, ensure_ascii=False, indent=2) if "--json" in sys.argv else load_text(_v))
        sys.exit(0)
    d = ensure(refresh="--refresh" in sys.argv)
    print(line(d))
    if "--json" in sys.argv or "--refresh" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
