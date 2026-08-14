### Title
Attacker-controlled rule `pattern` field enables ReDoS in `RuleEngine._regex_match`, causing PreToolUse hook to fail open - ([File: plugins/hookify/core/rule_engine.py])

### Finding Description
`compile_regex()` compiles the `pattern` field from a `.claude/hookify.*.local.md` rule file with no validation, length limit, or safety check [1](#0-0) . `_regex_match()` then runs `regex.search(text)` where `text` is the attacker-influenced tool input (e.g. the `Bash` `command` string), with `re.error` being the only handled exception — there is no timeout, signal alarm, or backtracking-safe engine wrapping this call [2](#0-1) .

`config_loader.load_rule_file()` / `Rule.from_dict()` pass the `pattern` field straight from YAML frontmatter into a `Condition` with zero sanitization of the regex content itself, so any user- or agent-authored rule file checked into `.claude/` can embed a catastrophic-backtracking pattern (e.g. `(a+)+$`, `(a|a)+$`) [3](#0-2) [4](#0-3) .

The call chain `pretooluse.py:main()` → `load_rules()` → `RuleEngine.evaluate_rules()` → `_rule_matches()` → `_check_condition()` → `_regex_match()` → `compile_regex(pattern).search(text)` runs on every `Bash`/`Edit`/`Write`/`MultiEdit` tool call [5](#0-4) . `pretooluse.py`'s only safety net is a Python-level `try/except Exception` with a `finally: sys.exit(0)` [6](#0-5) , but a ReDoS hang never raises an exception — the interpreter spins in `re.search` and the `finally` block never runs. The only thing that stops the hung process is the framework-level `"timeout": 10` configured in `hooks.json` for the `PreToolUse` hook [7](#0-6) , which externally kills the script rather than letting it return a deny decision.

### Impact Explanation
If Claude Code's hook runner treats an externally-killed/timed-out `PreToolUse` hook the same way it treats the script's own explicit fail-open path (`exit 0` with empty/allow JSON, consistent with the codebase's existing fail-open pattern for import errors and generic exceptions), then a single attacker-authored rule with a catastrophic regex silently disables all rule enforcement for that tool call — including any legitimate `block` rules meant to stop dangerous `Bash`/file operations. This breaks the `DENY_MEANS_DENY` invariant: hook enforcement can be forced into an allow state purely by attacker-controlled repository content (a rule file), enabling unauthorized command execution or file mutation that the hookify guard was meant to prevent.

### Likelihood Explanation
Precondition is simply having an attacker-authored `.claude/hookify.*.local.md` rule file present (as described, via `/hookify` Step 3 user-supplied or agent-suggested patterns) — no elevated privilege or secret is needed. The exploit is deterministic and repeatable: any subsequent `Bash` command whose text triggers exponential backtracking against the malicious pattern (or is simply long enough) reproduces the hang every time it's evaluated, since `compile_regex` caches the compiled pattern via `lru_cache` and reruns it on every relevant tool call.

### Recommendation
- Enforce a hard per-match timeout for regex evaluation in `_regex_match` (e.g. via a worker process/thread with `SIGALRM` or a max-step regex engine like Python's `regex` module with `timeout=`), and treat a timeout as a "block"/deny outcome rather than allow.
- Validate/lint `pattern` strings at rule-load time (e.g. static ReDoS detection, disallow nested quantifiers, or cap pattern complexity) and reject unsafe patterns with a warning instead of loading them.
- Change the fail-safe default: on any internal error, timeout, or unexpected hook termination, the PreToolUse hook should default to `deny`/block for high-risk tool types rather than silently allowing the operation, to preserve `DENY_MEANS_DENY`.

### Proof of Concept
```python
# test_redos_hookify.py
import time
from hookify.core.config_loader import Rule, Condition
from hookify.core.rule_engine import RuleEngine

def test_redos_pattern_hangs_evaluation():
    rule = Rule(
        name="malicious-rule",
        enabled=True,
        event="bash",
        conditions=[
            Condition(field="command", operator="regex_match", pattern=r"(a+)+$")
        ],
        action="block",
        message="should block dangerous command"
    )
    engine = RuleEngine()
    malicious_command = "echo " + "a" * 40 + "!"  # no trailing match -> catastrophic backtrack
    input_data = {
        "tool_name": "Bash",
        "tool_input": {"command": malicious_command}
    }

    start = time.time()
    result = engine.evaluate_rules([rule], input_data)
    elapsed = time.time() - start

    # Expect: engine either times out fast and still returns a deny decision,
    # or evaluation completes within a bounded time (e.g. < 1s).
    assert elapsed < 1.0, f"ReDoS pattern caused hang: {elapsed:.2f}s"
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
```
Run this against the current implementation: `evaluate_rules` will hang far past 1 second (approaching/exceeding the framework's 10s `PreToolUse` timeout in `hooks.json`), demonstrating the ReDoS and the absence of any internal deny-on-timeout safeguard.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L14-24)
```python
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

**File:** plugins/hookify/core/config_loader.py (L22-29)
```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )
```

**File:** plugins/hookify/core/config_loader.py (L56-73)
```python
        # Legacy style: simple pattern field
        simple_pattern = frontmatter.get('pattern')
        if simple_pattern and not conditions:
            # Convert simple pattern to condition
            # Infer field from event
            event = frontmatter.get('event', 'all')
            if event == 'bash':
                field = 'command'
            elif event == 'file':
                field = 'new_text'
            else:
                field = 'content'

            conditions = [Condition(
                field=field,
                operator='regex_match',
                pattern=simple_pattern
            )]
```

**File:** plugins/hookify/hooks/pretooluse.py (L44-56)
```python

        event = None
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'

        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
```

**File:** plugins/hookify/hooks/pretooluse.py (L61-70)
```python
    except Exception as e:
        # On any error, allow the operation and log
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0 - never block operations due to hook errors
        sys.exit(0)
```

**File:** plugins/hookify/hooks/hooks.json (L4-13)
```json
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
            "timeout": 10
          }
        ]
      }
```
