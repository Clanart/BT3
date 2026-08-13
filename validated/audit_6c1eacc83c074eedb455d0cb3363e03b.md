### Title
ReDoS in hookify `_regex_match` via attacker-supplied `stop` event regex causes Stop-hook hang, bypassing the "always exit 0" fallback - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._regex_match` compiles and executes an attacker-controlled `pattern` from a `.claude/hookify.*.local.md` rule file directly against attacker-influenced text (`command`, `transcript`, etc.) with no complexity limits or timeout. A catastrophic-backtracking pattern (e.g. `(a+)+$`) with `event: stop` causes `re.search` to hang the `stop.py` hook process on every Stop event, and because the hang is a runaway computation (not a raised exception), the `try/except Exception` / `finally: sys.exit(0)` fallback in `stop.py` never executes — the process must instead be killed by the external per-hook `"timeout": 10` in `hooks.json`.

### Finding Description
`compile_regex` (`plugins/hookify/core/rule_engine.py:14-24`) compiles `pattern` with no length/complexity validation, and `_regex_match` (`plugins/hookify/core/rule_engine.py:256-273`) calls `regex.search(text)` with only a `try/except re.error` guard — this only catches *invalid* regex syntax, not slow-but-valid regex like `(a+)+$`.

A rule with `event: stop` is picked up by `load_rules(event='stop')` (`plugins/hookify/core/config_loader.py:198-241`), which globs `.claude/hookify.*.local.md` — any file present in the working tree, including one shipped as ordinary (non-gitignored) repository content in a cloned/malicious repo. `stop.py` (`plugins/hookify/hooks/stop.py:30-55`) then calls `engine.evaluate_rules(rules, input_data)`, which reaches `_check_condition` → `_regex_match(pattern, field_value)` (`plugins/hookify/core/rule_engine.py:166-167`) for fields like `command`, `reason`, or `transcript` (the transcript is read directly from the session's transcript file, `plugins/hookify/core/rule_engine.py:207-225`, and can contain assistant/tool output partially influenced by the attacker's crafted repo instructions).

Because `re.search` with `(a+)+$` against a crafted string (e.g., `"a"*40 + "!"`) exhibits exponential backtracking, this call blocks the Python interpreter indefinitely for realistic input sizes. The surrounding `try: ... except Exception ... finally: sys.exit(0)` in `stop.py` (`plugins/hookify/hooks/stop.py:32-55`) cannot mitigate this: a hang is not an exception, so control never reaches the `except`/`finally` block from within the hung call. The only backstop is the external per-hook `"timeout": 10` declared in `plugins/hookify/hooks/hooks.json:26-35`, enforced by the Claude Code hook runner outside this process — i.e., the "always exit 0" invariant documented in the code is not actually what prevents the DoS; it is the external timeout kill, and it happens on every single Stop event for as long as the malicious rule file remains enabled.

### Impact Explanation
Every attempt to stop the agent session incurs a ~10 second stall (bounded by the hook's configured `timeout`) before the Stop hook is externally killed, repeating on every Stop event for the lifetime of the malicious rule file in `.claude/`. This is a genuine availability/DoS impact on ordinary Claude Code usage triggered purely by attacker-controlled repository content (a hookify rule file), matching a "hook execution becomes an uncontrolled availability sink" class of bug — it degrades responsiveness of the agent loop repeatedly rather than a one-time crash, and defeats the plugin's own stated fail-safe design ("Always exit 0 — never block operations due to hook errors") since that safeguard is architecturally unreachable for a hang.

### Likelihood Explanation
Preconditions: the victim must have the `hookify` plugin installed/enabled and must work in/clone a repository containing an attacker-supplied `.claude/hookify.*.local.md` file with `enabled: true`, `event: stop` (or `all`), and a catastrophic-backtracking `pattern`. Given the plugin's design explicitly supports arbitrary user-authored regex rules loaded from plain repo files (`plugins/hookify/core/config_loader.py:198-241`), and the README only *recommends* gitignoring these files rather than enforcing it, a malicious/compromised repository can ship such a rule as ordinary tracked content. The exploit is fully deterministic and repeats on every Stop event once the plugin is active, making it highly reproducible.

### Recommendation
Add a regex complexity/time budget in `compile_regex`/`_regex_match` (e.g., enforce a bounded execution via a separate process/thread with `signal.alarm`/`multiprocessing` timeout, or use the `regex` module's timeout parameter, or a safe-regex validator that rejects patterns with nested quantifiers over user-controlled text) before evaluating any rule pattern loaded from `.claude/*.local.md`. At minimum, wrap `regex.search` calls with a hard wall-clock timeout independent of the outer hook process timeout, so a slow pattern degrades to "rule skipped" rather than hanging the whole hook invocation.

### Proof of Concept
Fuzz/invariant test using `hypothesis` and a wall-clock guard:

```python
import re, time, signal
import pytest
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Rule, Condition

def test_redos_hangs_regex_match():
    engine = RuleEngine()
    rule = Rule(
        name="evil-stop-rule", enabled=True, event="stop", action="warn",
        conditions=[Condition(field="reason", operator="regex_match", pattern=r"(a+)+$")],
        message="x",
    )
    input_data = {
        "hook_event_name": "Stop",
        "reason": "a" * 35 + "!",  # crafted attacker-influenced text
    }

    def handler(signum, frame):
        raise TimeoutError("regex_match exceeded time budget")

    signal.signal(signal.SIGALRM, handler)
    signal.alarm(2)  # 2s budget, well under the hooks.json 10s kill
    start = time.time()
    with pytest.raises(TimeoutError):
        engine.evaluate_rules([rule], input_data)
    signal.alarm(0)
    assert time.time() - start >= 2  # confirms hang, not fast exception path
```

Expected result on current code: the test times out / hangs past the 2s budget, demonstrating `_regex_match` has no internal bound and relies entirely on the external hook `timeout` (10s in `hooks.json`) to be killed — confirming the `try/except`/`finally: sys.exit(0)` fallback in `stop.py` is never reached for this input class. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** plugins/hookify/core/rule_engine.py (L13-24)
```python
# Cache compiled regexes (max 128 patterns)
@lru_cache(maxsize=128)
def compile_regex(pattern: str) -> re.Pattern:
    """Compile regex pattern with caching.

    Args:
        pattern: Regex pattern string

    Returns:
        Compiled regex pattern
    """
    return re.compile(pattern, re.IGNORECASE)
```

**File:** plugins/hookify/core/rule_engine.py (L256-273)
```python
    def _regex_match(self, pattern: str, text: str) -> bool:
        """Check if pattern matches text using regex.

        Args:
            pattern: Regex pattern
            text: Text to match against

        Returns:
            True if pattern matches
        """
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            return bool(regex.search(text))

        except re.error as e:
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False
```

**File:** plugins/hookify/core/config_loader.py (L198-226)
```python
def load_rules(event: Optional[str] = None) -> List[Rule]:
    """Load all hookify rules from .claude directory.

    Args:
        event: Optional event filter ("bash", "file", "stop", etc.)

    Returns:
        List of enabled Rule objects matching the event.
    """
    rules = []

    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)

    for file_path in files:
        try:
            rule = load_rule_file(file_path)
            if not rule:
                continue

            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)
```

**File:** plugins/hookify/hooks/stop.py (L30-55)
```python
def main():
    """Main entry point for Stop hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Load stop rules
        rules = load_rules(event='stop')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        # On any error, allow the operation
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/hooks/hooks.json (L26-35)
```json
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/stop.py",
            "timeout": 10
          }
        ]
      }
```
