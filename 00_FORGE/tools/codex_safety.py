"""Codex must never silently break the owner's Codex desktop app - Forge 1.1.2 (N028; incident of 2026-10-15).

On 2026-10-15 the Codex desktop app (MSIX OpenAI.Codex) could not start - AppModel-Runtime event 208, 0x80070005 - because
%LOCALAPPDATA%\\Packages had lost its ACEs for SYSTEM and Administrators (inheritance off, only the user's ACE). The owner
restored them by hand. The suspect, not proven, is Codex's own Windows sandbox in elevated mode, which Forge asks for
(`-c windows.sandbox="elevated"`): its setup grants ACEs on AppData (~/.codex/.sandbox/*.log). This module makes the next
occurrence visible and attributable, and never touches an ACL itself - only the owner changes permissions:
- `check()`: Windows only - the folder's DACL as SDDL (SIDs and SDDL aliases, never localized account names: the owner's
  Windows is Russian) -> ok when an allow ACE for S-1-5-18 and one for S-1-5-32-544 apply to the folder itself, broken when
  not, unknown when it cannot be read (fail-open), skipped elsewhere; plus the exact fix command for the owner;
- `before_run(label)`: refuses to start Codex while the ACL is broken (plain Russian + the fix); lifts a block once fixed;
- `after_run(before, label, run)`: re-checks after the run; ok before and broken after = the rights vanished DURING this run
  (a coincidence in time, not a proof: the desktop app or another run may have worked at the same time - the incident lists
  the overlapping Forge runs and the sandbox log tails) -> a loud warning naming the run, an incident file, a lesson (applies
  kernel) in the project and a block: no Codex run starts until the owner fixes the ACL.
Every reading is appended to <forge home>/codex_acl.jsonl (with the process id): the history shows whether a break happened
during a Forge run or between runs (the desktop app itself, another tool).
Tests: FORGE_PACKAGES_SDDL = a file whose text stands for the folder's SDDL (any OS); FORGE_HOME moves the block and log.

Usage: python codex_safety.py [--json]     # the check, read only
"""
from __future__ import annotations

import sys
for _stream in (sys.stdout, sys.stderr):  # the owner's messages are Russian; Windows would print them in cp1251
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

SYSTEM, ADMINS = "S-1-5-18", "S-1-5-32-544"
NEEDED = {SYSTEM: "SYSTEM", ADMINS: "Administrators"}
ALIASES = {"SY": SYSTEM, "BA": ADMINS}  # SDDL's own two-letter names: fixed, the same in every Windows language
RU = {"SYSTEM": "SYSTEM", "Administrators": "Администраторов"}  # genitive: «нет прав для ...», «... нет»
LOG_TAIL_LINES, LOG_TAIL_BYTES, LOG_FILES = 80, 16_000, 8


class CodexBlocked(RuntimeError):
    """Codex must not start: the Codex app's folder lost SYSTEM/Administrators, or they vanished during a run and are not restored yet."""


def forge_home() -> Path:
    return Path(os.environ.get("FORGE_HOME") or Path.home() / ".forge")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def on_windows() -> bool:
    return os.name == "nt"


def packages_dir() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Packages" if base else None


def fix_command(path: Path | str) -> str:
    """The owner's one-line repair for PowerShell (well-known SIDs, so it works in any Windows language)."""
    return f'icacls "{path}" /grant "*{SYSTEM}:(OI)(CI)F" "*{ADMINS}:(OI)(CI)F"'


def read_sddl(path: Path) -> str:
    """The folder's DACL as SDDL, read through the Windows API (read only: GetNamedSecurityInfoW)."""
    import ctypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    sd = ctypes.c_void_p()
    rc = advapi.GetNamedSecurityInfoW(str(path), 1, 0x4, None, None, None, None, ctypes.byref(sd))  # SE_FILE_OBJECT, DACL
    if rc != 0:
        raise OSError(rc, f"GetNamedSecurityInfoW failed ({rc})", str(path))
    try:
        text = ctypes.c_wchar_p()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value or ""
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.LocalFree(sd)


def parse_dacl(sddl: str) -> tuple[str, list[list[str]]]:
    """SDDL -> (DACL flags such as 'PAI', the fields of every ACE); a conditional ACE keeps its own parentheses."""
    start = sddl.find("D:")
    if start < 0:
        return "", []
    text = sddl[start + 2:]
    cut = text.find("(")
    flags = text if cut < 0 else text[:cut]
    aces, cur, depth = [], [], 0
    for ch in text[len(flags):]:
        if ch == "(":
            depth += 1
            if depth == 1:
                cur = []
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                aces.append("".join(cur).split(";"))
                continue
        if depth <= 0:
            break  # past the DACL
        cur.append(ch)
    return flags, aces


def granted(aces: list[list[str]]) -> set[str]:
    """SIDs with an allow ACE that applies to the folder itself (an inherit-only ACE reaches only its children)."""
    out = set()
    for f in aces:
        if len(f) < 6 or f[0].strip().upper() != "A":
            continue
        flags = f[1].strip().upper()
        if "IO" in {flags[i:i + 2] for i in range(0, len(flags), 2)}:
            continue
        sid = f[5].strip().upper()
        out.add(ALIASES.get(sid, sid))
    return out


def check(path: Path | str | None = None) -> dict:
    """{state: ok | broken | unknown | skipped, path, missing, protected, sddl, fix, note} - reads, never writes."""
    hook = os.environ.get("FORGE_PACKAGES_SDDL")
    folder = Path(path) if path else packages_dir()
    res = {"state": "skipped", "path": str(folder or ""), "missing": [], "protected": False, "sddl": "",
           "fix": fix_command(folder) if folder else "", "note": ""}
    if not hook and not on_windows():
        res["note"] = "not Windows"
        return res
    if folder is None:
        res["note"] = "LOCALAPPDATA is not set"
        return res
    try:
        if not hook and not folder.is_dir():
            res["note"] = "no Packages folder (no Microsoft Store apps on this account)"
            return res
        sddl = Path(hook).read_text(encoding="utf-8").strip() if hook else read_sddl(folder)
    except Exception as e:  # noqa: BLE001 - a reader failure never blocks work (fail-open), it is reported
        res.update(state="unknown", note=f"cannot read the ACL: {type(e).__name__}: {e}"[:300])
        return res
    flags, aces = parse_dacl(sddl)
    have = granted(aces)
    missing = [] if "NO_ACCESS_CONTROL" in flags else [name for sid, name in NEEDED.items() if sid not in have]
    res.update(state="broken" if missing else "ok", missing=missing, protected="P" in flags, sddl=sddl,
               note=("missing: " + ", ".join(missing)) if missing else "SYSTEM and Administrators present")
    return res


def summary(r: dict | None = None) -> str:
    r = r or check()
    return f"packages acl: {r['state']} - {r['note'] or r['path']}" + (f" | fix (owner): {r['fix']}" if r["state"] == "broken" else "")


def block_file() -> Path:
    return forge_home() / "codex_blocked.json"


def read_block() -> dict | None:
    try:
        data = json.loads(block_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"run": "?"}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"run": "?", "when": "?", "incident": str(block_file())}  # unreadable = still a block


def log(phase: str, label: str, r: dict, **extra) -> None:
    """One line per reading in <forge home>/codex_acl.jsonl; never fails the run."""
    try:
        f = forge_home() / "codex_acl.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now().isoformat(timespec="seconds"), "phase": phase, "run": label, "pid": os.getpid(),
               "state": r["state"], "missing": r["missing"], "protected": r["protected"], "sddl": r["sddl"], **extra}
        with f.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


OPEN_WINDOW = 3 * 3600  # seconds: a Forge run with a `before` and no `after` this recent may still have been running


def overlapping(since: str, label: str) -> list[str]:
    """Other Forge Codex runs that ran during [since, now] by the history log: finished after `since`, or started and not
    finished within the last OPEN_WINDOW seconds. They could have caused the change as well as this run."""
    try:
        rows = [json.loads(x) for x in (forge_home() / "codex_acl.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    me, recent = (label, os.getpid()), datetime.fromtimestamp(datetime.now().timestamp() - OPEN_WINDOW).isoformat(timespec="seconds")
    started, out = {}, []
    for row in rows:
        key = (row.get("run"), row.get("pid"))
        if key == me:
            continue
        if row.get("phase") == "before":
            started[key] = row.get("ts", "")
        elif row.get("phase") == "after" and key in started:
            if row.get("ts", "") >= since:
                out.append(f"{key[0]} (pid {key[1]}, {started[key]} - {row['ts']})")
            del started[key]
    out += [f"{k[0]} (pid {k[1]}, since {ts}, not finished)" for k, ts in started.items() if ts >= recent]
    return out


def who(missing: list[str]) -> str:
    return " и ".join(RU.get(m, m) for m in missing) or "SYSTEM и Администраторов"


def refusal(r: dict, blk: dict | None) -> str:
    if r["state"] == "broken":
        lines = [f"Codex не запущен: у папки {r['path']} нет прав для {who(r['missing'])}.",
                 "Без этих прав Windows не запускает приложения из Microsoft Store — и приложение Codex тоже (ошибка 0x80070005)."]
        if blk:
            lines.append(f"Права пропали во время запуска Codex «{blk.get('run', '?')}» ({blk.get('when', '?')}); "
                         f"доказательства: {blk.get('incident', '?')}.")
        return "\n".join(lines + ["Исправьте один раз сами: откройте PowerShell и выполните",
                                  f"    {r['fix']}",
                                  "Потом повторите команду. Forge права не меняет — это делает только владелец."])
    fix = (blk or {}).get("fix") or r["fix"] or fix_command(r["path"] or "%LOCALAPPDATA%\\Packages")
    return "\n".join([
        f"Codex не запущен: во время запуска «{(blk or {}).get('run', '?')}» ({(blk or {}).get('when', '?')}) пропали права папки "
        f"{(blk or {}).get('path') or r['path']}, а проверить, исправлены ли они, сейчас не удалось ({r['note'] or r['state']}).",
        "Если вы уже выполнили в PowerShell команду",
        f"    {fix}",
        f"удалите файл {block_file()} и повторите. Доказательства: {(blk or {}).get('incident', '?')}."])


def before_run(label: str) -> dict:
    """Preflight of one Codex run -> the reading for `after_run`. Raises CodexBlocked while the ACL is broken, or while a run
    during which they vanished blocked Codex and the fix cannot be confirmed. A reader error alone never blocks (fail-open)."""
    try:
        r = check()
    except Exception as e:  # noqa: BLE001 - the guard's own bug never stops the caller
        r = {"state": "unknown", "path": "", "missing": [], "protected": False, "sddl": "", "fix": "",
             "note": f"check failed: {type(e).__name__}: {e}"[:300]}
    blk = read_block()
    if r["state"] == "broken" or (blk and r["state"] != "ok"):
        log("refused", label, r)
        raise CodexBlocked(refusal(r, blk))
    r["ts"] = datetime.now().isoformat(timespec="seconds")
    log("before", label, r)
    if blk:  # the owner fixed the ACL: the block goes, the incident file and the lesson stay
        try:
            block_file().unlink()
        except OSError:
            pass
        log("lifted", label, r, blocked_by=blk.get("run", "?"))
        print(f"Forge: права папки {r['path']} снова в порядке — блок Codex снят (его поставил запуск «{blk.get('run', '?')}», "
              f"{blk.get('when', '?')}; доказательства: {blk.get('incident', '?')}).", file=sys.stderr, flush=True)
    return r


def sandbox_logs() -> list[dict]:
    """The newest files of ~/.codex/.sandbox with their tails - copied, because Codex rotates its logs."""
    d = codex_home() / ".sandbox"
    try:
        files = sorted((p for p in d.iterdir() if p.is_file() and p.suffix in (".log", ".json")),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:LOG_FILES]
    except OSError:
        return []
    out = []
    for p in files:
        try:
            st = p.stat()
            text = p.read_bytes()[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")
            out.append({"path": str(p), "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        "size": st.st_size, "tail": "\n".join(text.splitlines()[-LOG_TAIL_LINES:])})
        except OSError as e:
            out.append({"path": str(p), "error": str(e)})
    return out


def write_lesson(root: Path | None, label: str, run: dict, evidence: list[str]) -> tuple[str | None, str]:
    """A kernel lesson in the project the run belongs to (lesson.py) -> (lesson file or None, why not)."""
    if root is None:
        return None, "запуск вне проекта Forge"
    oid = run.get("order") or next(iter(re.findall(r"\bN\d{3}\b", label)), "")
    mode = run.get("windows_sandbox") or "(no override)"
    others = run.get("overlapping") or []
    cmd = [sys.executable, str(Path(__file__).with_name("lesson.py")),
           "--failed", f"during the Codex run '{label}' {', '.join(run.get('missing') or ['SYSTEM', 'Administrators'])} vanished "
                       f"from {run.get('path', '%LOCALAPPDATA%/Packages')} - the owner's Codex app cannot start (0x80070005)",
           "--cause", f"measured by codex_safety: the ACL was ok right before this run and broken right after it - a coincidence "
                      f"in time, not a proof (the desktop app's own Codex or another run may have acted in the same window; "
                      f"overlapping Forge runs: {'; '.join(others) or 'none'}); Codex {run.get('codex', '?')}, "
                      f"windows.sandbox={mode}",
           "--fix", "only the owner restores the ACL (icacls grant *S-1-5-18 and *S-1-5-32-544); Forge refuses Codex runs until "
                    "then; compare with the sandbox log tails in the incident; ask the owner whether Forge's Codex runs change "
                    "windows.sandbox (forge.json code.codex_windows_sandbox; env FORGE_GPT_WINDOWS_SANDBOX for gpt_bridge)",
           "--applies", "kernel", "--source", "gate", "--confidence", "medium", "--evidence", "; ".join(evidence)]
    if oid:
        cmd += ["--order", oid]
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"lesson.py не запустился: {e}"
    lines = p.stdout.strip().splitlines()
    if p.returncode != 0 or not lines:
        return None, f"lesson.py: {(p.stderr or p.stdout).strip()[-300:]}"
    return lines[-1].strip(), ""


def alarm(label: str, when: str, r: dict, mode: str, incident: str, lesson: str | None, why_not: str,
          others: list[str]) -> str:
    bar = "=" * 100
    return "\n".join([
        bar, "ВНИМАНИЕ: во время запуска Codex пропали права Windows",
        f"Запуск «{label}» ({when}): до него у папки {r['path']} были права SYSTEM и Администраторов, после — "
        f"{who(r['missing'])} нет.",
        "Из-за этого не запускается приложение Codex (ошибка 0x80070005) — как 15.10.2026.",
        "Причина не доказана: права могли изменить и приложение Codex, и другой запуск, если они работали в это же время.",
        f"Другие запуски Forge в это время: {'; '.join(others) if others else 'не было'}.",
        f"Режим песочницы Codex в этом запуске: windows.sandbox={mode or '(не задан)'}.",
        "Forge больше не запускает Codex, пока права не исправлены. Исправьте один раз сами в PowerShell:",
        f"    {r['fix']}",
        f"Доказательства (права до и после, журналы песочницы Codex): {incident}",
        f"Урок: {lesson or 'не записан — ' + why_not}",
        bar])


def after_run(before: dict, label: str, run: dict, *, root: Path | None = None, evidence: list[str] | None = None) -> dict | None:
    """Postflight of one Codex run. ok before + broken after -> incident, block, lesson, loud warning on stderr; returns
    {incident, lesson, text}. Anything else -> None (a broken ACL whose cause is unknown is refused by the next before_run)."""
    try:
        r = check()
        log("after", label, r)
        if r["state"] != "broken":
            return None
        if before.get("state") != "ok":
            print(f"Forge: у папки {r['path']} нет прав для {who(r['missing'])} (до запуска «{label}» проверить не удалось). "
                  f"Следующий запуск Codex будет отклонён до исправления: {r['fix']}", file=sys.stderr, flush=True)
            return None
        when = datetime.now()
        stamp = when.isoformat(timespec="seconds")
        mode = run.get("windows_sandbox", "")
        others = overlapping(before.get("ts") or stamp, label)
        run = {**run, "overlapping": others}
        d = forge_home() / "codex_acl"
        d.mkdir(parents=True, exist_ok=True)
        incident = d / f"incident_{when:%Y%m%d_%H%M%S}_{os.getpid()}.json"
        data = {"when": stamp, "run": label, "details": run, "path": r["path"], "fix": r["fix"],
                "note": "the rights vanished during this run - a coincidence in time, not a proof of cause",
                "before": {k: before.get(k) for k in ("ts", "state", "missing", "protected", "sddl")},
                "after": {k: r[k] for k in ("state", "missing", "protected", "sddl")},
                "codex_home": str(codex_home()), "sandbox_logs": sandbox_logs()}
        incident.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        blk = {"when": stamp, "run": label, "path": r["path"], "fix": r["fix"], "incident": str(incident),
               "windows_sandbox": mode}
        block_file().write_text(json.dumps(blk, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        ev = [str(incident), *(evidence or []), str(codex_home() / ".sandbox" / "*.log")]
        lesson, why_not = write_lesson(root, label, {**run, "missing": r["missing"], "path": r["path"]}, ev)
        if lesson:
            blk["lesson"] = lesson
            block_file().write_text(json.dumps(blk, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        text = alarm(label, stamp, r, mode, str(incident), lesson, why_not, others)
        log("alert", label, r, incident=str(incident), lesson=lesson or "")
        print(text, file=sys.stderr, flush=True)
        return {"incident": str(incident), "lesson": lesson, "text": text}
    except Exception as e:  # noqa: BLE001 - the guard's own failure never hides the run's result
        print(f"Forge: проверка прав после запуска Codex не удалась ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        return None


def main() -> int:
    r = check()
    if "--json" in sys.argv:
        print(json.dumps({**r, "blocked": read_block()}, ensure_ascii=False, indent=1))
        return 0
    print(summary(r))
    blk = read_block()
    if blk:
        print(f"Codex blocked since {blk.get('when', '?')} by the run '{blk.get('run', '?')}' - evidence {blk.get('incident', '?')}"
              + (" (the ACL is fixed: the next Codex run lifts the block)" if r["state"] == "ok" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
