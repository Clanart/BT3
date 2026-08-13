### Title
ReDoS in hookify rule engine via attacker-controlled regex `pattern` causes hook hang / fail-open bypass - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._regex_match` compiles and runs user/attacker-supplied `pattern` strings from `hookify.*.local.md` rule files using Python's backtracking `re` engine with no timeout, complexity check, or resource limit. A rule file containing a catastrophic-backtracking pattern (e.g. `(a+)+$`) will cause `_regex_match` to hang or consume excessive CPU when evaluated against a crafted `user_prompt`/`tool_input` value, potentially causing the `UserPromptSubmit` hook to fail to complete or time out.

### Finding Description
The call chain is exactly as described: `load_rules()` [1](#0-0)  parses every `.claude/hookify.*.local.md` file and builds `Rule`/`Condition` objects directly from the file's YAML frontmatter `pattern` field [2](#0-1) , with no validation of regex safety. `RuleEngine.evaluate_rules` → `_rule_matches` → `_check_condition` dispatches `regex_match` conditions to `_regex_match(pattern, field_value)` [3](#0-2) , which compiles the pattern via the LRU-cached `compile_regex` and calls `regex.search(text)` with no timeout or bound [4](#0-3) . `compile_regex` only catches `re.error` (invalid syntax) — it does not guard against exponential-time patterns [5](#0-4) . The `userpromptsubmit.py` hook entry point feeds `user_prompt` from the JSON stdin payload straight into `evaluate_rules`, so any attacker-controlled prompt text combined with an attacker-planted rule pattern reaches `_regex_match` on every prompt submission [6](#0-5) . No allowlist, complexity scanner, or execution-time guard exists anywhere in this path.

### Impact Explanation
Because Python's `re` module uses a backtracking engine, a pattern such as `(a+)+$` or `(a|aa)+$` matched against a crafted long string of `a`s followed by a non-matching character produces exponential-time backtracking, hanging the hook process. Since the hook script only wraps the top-level logic in `try/except` (which does not interrupt a hung regex call) and always intends to `sys.exit(0)` in `finally` [7](#0-6) , a hang here means the process never reaches that exit, so Claude Code's calling harness must rely on an external timeout. If the hook times out, Claude Code's hook execution semantics treat an unresponsive/failed hook as non-blocking (fail-open), meaning any accompanying `block` rules intended to deny the prompt are not enforced — an availability failure of the approval-check invariant.

### Likelihood Explanation
This requires the attacker to be able to add or edit a `hookify.*.local.md` file, which the question explicitly frames as a satisfied precondition (checked-in repo/plugin content, not requiring machine admin/maintainer privilege — e.g., landed via an accepted PR in a shared repo using this plugin, or a malicious plugin marketplace entry). Given that precondition, the exploit is deterministic and trivially reproducible: any known catastrophic-backtracking pattern plus a matching adversarial input string will reliably reproduce hangs, with no existing mitigation (no regex safety linting, no timeout, no length cap on evaluated text) blocking it.

### Recommendation
- Enforce a hard timeout on regex evaluation (e.g., run `regex.search` in a subprocess/thread with `signal.alarm`, or use the `regex` module's timeout support, or a dedicated ReDoS-safe engine such as `re2`).
- Validate/lint rule patterns at load time (`load_rule_file`) for known catastrophic-backtracking constructs (nested quantifiers, alternation with overlapping branches) and reject or warn on unsafe patterns.
- Cap the length of `field_value`/`user_prompt` text passed into `_regex_match` to bound worst-case backtracking time.
- Consider requiring rule files to be reviewed/signed or restricting `hookify.*.local.md` loading to trusted, non-repo-tracked locations, since currently any repo-content author can define arbitrary regexes executed against runtime hook data.

### Proof of Concept
Add a unit/fuzz test in `plugins/hookify/core/rule_engine.py`'s test area or a new test file:
```python
import time
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Rule, Condition

def test_redos_pattern_hangs():
    rule = Rule(
        name="evil",
        enabled=True,
        event="prompt",
        conditions=[Condition(field="user_prompt", operator="regex_match", pattern=r"(a+)+$")],
        message="x",
        action="block",
    )
    engine = RuleEngine()
    evil_input = {
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "a" * 40 + "!",  # forces full backtracking, no match
    }
    start = time.monotonic()
    engine.evaluate_rules([rule], evil_input)
    elapsed = time.monotonic() - start
    # Expected: should complete within e.g. 1s; currently takes seconds->minutes
    # growing exponentially with input length, demonstrating no timeout guard.
    assert elapsed < 1.0, f"ReDoS: regex evaluation took {elapsed}s"
```
Running this with increasing counts of `"a"` (e.g. 30, 35, 40, 45) demonstrates exponential growth in `_regex_match` execution time, confirming the absence of any timeout/complexity guard in `compile_regex`/`_regex_match`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L56-84)
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

        return cls(
            name=frontmatter.get('name', 'unnamed'),
            enabled=frontmatter.get('enabled', True),
            event=frontmatter.get('event', 'all'),
            pattern=simple_pattern,
            conditions=conditions,
            action=frontmatter.get('action', 'warn'),
            tool_matcher=frontmatter.get('tool_matcher'),
            message=message.strip()
        )
```

**File:** plugins/hookify/core/config_loader.py (L198-241)
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

        except (IOError, OSError, PermissionError) as e:
            # File I/O errors - log and continue
            print(f"Warning: Failed to read {file_path}: {e}", file=sys.stderr)
            continue
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            # Parsing errors - log and continue
            print(f"Warning: Failed to parse {file_path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            # Unexpected errors - log with type details
            print(f"Warning: Unexpected error loading {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
            continue

    return rules
```

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

**File:** plugins/hookify/core/rule_engine.py (L166-167)
```python
        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
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

**File:** plugins/hookify/hooks/userpromptsubmit.py (L30-44)
```python
def main():
    """Main entry point for UserPromptSubmit hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Load user prompt rules
        rules = load_rules(event='prompt')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
```

**File:** plugins/hookify/hooks/userpromptsubmit.py (L46-54)
```python
    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```
