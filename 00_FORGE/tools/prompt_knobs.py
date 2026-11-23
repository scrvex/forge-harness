"""Owner's short knobs as a UserPromptSubmit hook - Forge 1.0 (K36; from agent-playbook's conversation handles).

Turns a few plain words of the owner into a one-line instruction for the Lead, so the knob works the same in every session:
- `ss` at the start of the message      -> short answer, read only: no edits, no orders, no memory writes;
- "без тестов / без DoD / без ревью ..." -> the owner lifts a rule: record the waiver in the order (`--waive`, K19);
- "кодекс ревью" / "codex review"       -> a Codex review (verify_gpt.py N for an order, /codex:review for uncommitted code);
- "работай без меня" / "решай сам"      -> autonomous mode (K20): the start question first (skill forge-autonomous), then autorun.py start;
- the whole message "стоп" / "остановись" -> the autonomous run stops (autorun.py stop).
Whole words only, and a negation just before the knob ("не", "нельзя", "not", "don't") cancels it - "не работай без меня"
is the opposite of the knob (N024 Codex audit). Prints nothing when no knob matches. Never blocks a prompt.
"""
from __future__ import annotations

import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):  # 1.1.1: the knob texts carry «...»; Windows would print them in cp1251, not UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NEGATION = re.compile(r"(?i)(?:^|[^\w])(не|нельзя|никогда|not|don't|do not|never)\s+(?:\w+\s+){0,2}$")
KNOBS = [
    (re.compile(r"^\s*ss(?=\s|[:,.!?]|$)", re.I),
     "Owner knob `ss`: a short answer, read only - no file edits, no orders, no commits, no memory writes. Short means no "
     "padding, never fewer facts: warnings and risks stay in."),
    (re.compile(r"(?i)(?<!\w)без\s+(тестов|теста|dod|ревью|review|проверк[аиу]|verifier2?|плана|кодекса)(?!\w)"),
     "Owner waiver words: the owner lifts a Forge rule for this work. Record it word for word in the order: "
     "`order.py new|amend ... --waive \"<rule>: <owner's words>\"` (K19); a waiver never covers money, deletion or sending outside (K1)."),
    (re.compile(r"(?i)(?<!\w)(кодекс|codex)\s+(ревью|review)(?!\w)"),
     "Owner asks for a Codex review: for an order - `python <plugin>/tools/verify_gpt.py N` (rung 3, triage every finding); "
     "for uncommitted code - `/codex:review` (or `/codex:adversarial-review`). Codex reviews Claude's work; never Codex's own (K8)."),
    (re.compile(r"(?i)(?<!\w)(работай\s+(дальше\s+)?без\s+меня|решай\s+сам|как\s+считаешь\s+нужным)(?!\w)"),
     "Owner switches to autonomous mode (K20): load skill forge-autonomous - ask the start question (orders in scope and their "
     "DoD in the preview, answer «Запустить»), then `autorun.py start --words \"<owner's words>\"`; decide within the scope and "
     "report; ask only on money, deletion, sending outside or strategy - other questions go to `autorun.py ask`."),
    (re.compile(r"(?i)^\s*(стоп|остановись|хватит|stop)\s*[.!]*\s*$"),
     "Owner says stop: if an autonomous run is on, `python <plugin>/tools/autorun.py stop --why \"owner: stop\"`; finish the "
     "tool step in hand, leave the work in a resumable state (order file, handoff) and report where it stands."),
]


def matches(rx: re.Pattern, text: str) -> bool:
    """A knob counts only when no negation stands right before it (up to two words between)."""
    return any(not NEGATION.search(text[:m.start()]) for m in rx.finditer(text))


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")  # 1.1.1: UTF-8, not the Windows locale
        text = str(data.get("prompt", ""))[:4000]
        notes = [note for rx, note in KNOBS if matches(rx, text)]
        if notes:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": "Forge: " + " | ".join(notes)}}, ensure_ascii=False))
    except Exception as e:  # never blocks a prompt
        print(f"prompt_knobs: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
