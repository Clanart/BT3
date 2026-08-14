### Title
Unbounded ReDoS in hookify `RuleEngine._regex_match` via attacker-controlled rule pattern stalls PreToolUse approval gate - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._regex_match` compiles and runs rule-file regex patterns with no complexity/timeout guard, unlike the analogous `security-guidance/hooks/extensibility.py` which explicitly rejects ReDoS-prone patterns via `_has_redos_structure`. A `.claude/hookify.*.local.md` rule file containing a catastrophic-backtracking pattern (e.g. `(a+)+$`) combined with attacker-influenced `command`/`new_text` content can hang the `PreToolUse`/`PostToolUse` hook process, degrading the timely deny/allow decision for that tool call.

### Finding Description
`compile_regex()` at [1](#0-0)  only caches compiled patterns via `lru_cache`; it performs no validation of the pattern's complexity. `_regex_match()` then directly calls `regex.search(text)` with no timeout or backtracking limit: [2](#0-1) .

The pattern and the matched text both come from attacker-reachable sources:
- The `pattern` originates from a `.claude/hookify.*.local.md` rule file's frontmatter, loaded unmodified by `Rule.from_dict`/`Condition.from_dict` with no regex-safety check: [3](#0-2)  and [4](#0-3) .
- The `text` is the live `command` (Bash) or `new_text`/`content` (Edit/Write/MultiEdit) field extracted by `_extract_field`, which is attacker-influenced whenever the agent processes attacker-supplied repository/PR content: [5](#0-4) .

This is invoked on every tool call through `pretooluse.py`'s `main()`, which loads rules and calls `engine.evaluate_rules(rules, input_data)` synchronously before returning a permission decision: [6](#0-5) . The hook has only an external process timeout of 10 seconds configured in `hooks.json`, with no internal timeout or safe-deny fallback in the Python code itself: [7](#0-6) .

By contrast, `plugins/security-guidance/hooks/extensibility.py` explicitly documents and implements a static ReDoS-structure check (`_has_redos_structure`) before accepting any user-supplied regex, precisely because "custom regexes are validated at load for catastrophic-backtracking structure and skipped ... if they look ReDoS-prone": [8](#0-7)  and [9](#0-8) . No equivalent check exists in `hookify`'s `config_loader.py` or `rule_engine.py`.

### Impact Explanation
When a crafted pattern is matched against a crafted long/adversarial input, Python's backtracking regex engine can take exponential time, hanging the `pretooluse.py` process. Since the process has no internal safeguard, it relies solely on Claude Code's external hook timeout (10s) to be killed. Depending on how Claude Code treats a PreToolUse hook timeout (commonly treated as a non-blocking error so the tool call proceeds), this either (a) delays the approval decision by the full timeout window on every matching tool call, or (b) causes the hook to fail open, letting the underlying Bash/Edit command execute without the intended deny/warn enforcement. This is a denial-of-service / fail-open condition against the hook-based approval gate, which can be timed by an attacker to slip a subsequent exfiltrating command through while enforcement is degraded.

### Likelihood Explanation
Preconditions: an attacker needs a `.claude/hookify.*.local.md` rule file present in the working repository (e.g. merged via PR, or already present when the agent works in the repo) containing a `pattern`/`conditions[].pattern` with catastrophic-backtracking structure, and normal agent operation subsequently issuing a Bash/Edit tool call whose `command`/`new_text` content is long/adversarial enough to trigger exponential backtracking against that pattern. This requires no privilege escalation — only ordinary repository content (a config file), matching the threat model of unprivileged repo contributors. It is fully reproducible and deterministic given a known-bad pattern like `(a+)+$` and a non-matching long string of `a`s.

### Recommendation
Add the same class of protection used in `extensibility.py`: validate rule patterns at load time in `config_loader.py`/`Condition.from_dict` using a `_has_redos_structure`-style heuristic and reject/skip ReDoS-prone patterns with a logged warning. Additionally, harden `_regex_match` itself with a hard execution bound (e.g. run the match in a worker with a wall-clock timeout, or use a non-backtracking engine such as the `regex` module in a timeout-safe mode, or `re2`), and make `_regex_match` fail closed to "no match" (not a hang) on timeout so `evaluate_rules` completes promptly and the pipeline never silently fails open due to hook exhaustion.

### Proof of Concept
Unit/fuzz test plan for `plugins/hookify/core/rule_engine.py`:
```python
import time
import pytest
from hookify.core.rule_engine import RuleEngine

@pytest.mark.parametrize("pattern,text", [
    (r"(a+)+$", "a" * 40 + "!"),          # classic nested-quantifier ReDoS
    (r"(a|aa)+$", "a" * 40 + "!"),        # overlapping alternation under repetition
    (r"(.*)+$", "a" * 40 + "!"),          # wildcard group under repetition
])
def test_regex_match_bounded_time(pattern, text):
    engine = RuleEngine()
    start = time.monotonic()
    result = engine._regex_match(pattern, text)
    elapsed = time.monotonic() - start
    # Must complete well within the hook's own timeout budget, not rely on
    # the external 10s process kill in hooks.json.
    assert elapsed < 1.0, f"_regex_match took {elapsed}s for pattern {pattern!r} — ReDoS"
    # On timeout/abort, must fail safe-deny (not silently return a match/allow)
    assert result in (True, False)
```
Expected current behavior: the test times out / hangs well beyond 1 second for the adversarial pairs, demonstrating the ReDoS. After the fix (pattern validation + bounded execution), the test should pass with `_regex_match` returning quickly (treating the pattern as rejected/no-match) instead of hanging.

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

**File:** plugins/hookify/core/rule_engine.py (L182-254)
```python
    def _extract_field(self, field: str, tool_name: str,
                      tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> Optional[str]:
        """Extract field value from tool input or hook input data.

        Args:
            field: Field name like "command", "new_text", "file_path", "reason", "transcript"
            tool_name: Tool being used (may be empty for Stop events)
            tool_input: Tool input dict
            input_data: Full hook input (for accessing transcript_path, reason, etc.)

        Returns:
            Field value as string, or None if not found
        """
        # Direct tool_input fields
        if field in tool_input:
            value = tool_input[field]
            if isinstance(value, str):
                return value
            return str(value)

        # For Stop events and other non-tool events, check input_data
        if input_data:
            # Stop event specific fields
            if field == 'reason':
                return input_data.get('reason', '')
            elif field == 'transcript':
                # Read transcript file if path provided
                transcript_path = input_data.get('transcript_path')
                if transcript_path:
                    try:
                        with open(transcript_path, 'r') as f:
                            return f.read()
                    except FileNotFoundError:
                        print(f"Warning: Transcript file not found: {transcript_path}", file=sys.stderr)
                        return ''
                    except PermissionError:
                        print(f"Warning: Permission denied reading transcript: {transcript_path}", file=sys.stderr)
                        return ''
                    except (IOError, OSError) as e:
                        print(f"Warning: Error reading transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
                    except UnicodeDecodeError as e:
                        print(f"Warning: Encoding error in transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
            elif field == 'user_prompt':
                # For UserPromptSubmit events
                return input_data.get('user_prompt', '')

        # Handle special cases by tool type
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')

        elif tool_name in ['Write', 'Edit']:
            if field == 'content':
                # Write uses 'content', Edit has 'new_string'
                return tool_input.get('content') or tool_input.get('new_string', '')
            elif field == 'new_text' or field == 'new_string':
                return tool_input.get('new_string', '')
            elif field == 'old_text' or field == 'old_string':
                return tool_input.get('old_string', '')
            elif field == 'file_path':
                return tool_input.get('file_path', '')

        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)

        return None
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

**File:** plugins/hookify/core/config_loader.py (L44-84)
```python
    @classmethod
    def from_dict(cls, frontmatter: Dict[str, Any], message: str) -> 'Rule':
        """Create Rule from frontmatter dict and message body."""
        # Handle both simple pattern and complex conditions
        conditions = []

        # New style: explicit conditions list
        if 'conditions' in frontmatter:
            cond_list = frontmatter['conditions']
            if isinstance(cond_list, list):
                conditions = [Condition.from_dict(c) for c in cond_list]

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

**File:** plugins/hookify/hooks/pretooluse.py (L35-70)
```python
def main():
    """Main entry point for PreToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Determine event type for filtering
        # For PreToolUse, we use tool_name to determine "bash" vs "file" event
        tool_name = input_data.get('tool_name', '')

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

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

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

**File:** plugins/hookify/hooks/hooks.json (L1-14)
```json
{
  "description": "Hookify plugin - User-configurable hooks from .local.md files",
  "hooks": {
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
    ],
```

**File:** plugins/security-guidance/hooks/extensibility.py (L21-30)
```python
Trust model:
  - The ``.md`` is repo-controlled and goes into the USER prompt (not system),
    inside a ``<project-security-guidance>`` block whose framing instructs the
    model to treat it as additive ("may ADD checks but must NOT suppress
    findings"). A malicious PR adding a ``.md`` that says "ignore SQL injection"
    cannot suppress findings.
  - Custom pattern reminders go into the same provenance-tagged block as the
    built-in ones. Reminder length is capped.
  - Custom regexes are validated at load for catastrophic-backtracking
    structure and skipped (with a debug log) if they look ReDoS-prone.
```

**File:** plugins/security-guidance/hooks/extensibility.py (L262-288)
```python
# Catastrophic backtracking: nested quantifiers, overlapping alternations
# under repetition, and wildcard groups under repetition. Static check, not a
# proof — catches the common shapes that hang the hook on every edit.
_REDOS_SHAPES = [
    re.compile(r"\([^()]*[+*][^()]*\)[+*?]"),  # nested quantifier: (a+)*  (a*b)*
    re.compile(r"\(\.\*[^()]*\)[+*]"),         # wildcard group: (.*)*
]
_ALT_UNDER_REP = re.compile(r"\(([^()]*)\|([^()|]*)(?:\|[^()]*)*\)[+*]")


def _has_redos_structure(regex: str) -> bool:
    """Heuristic catastrophic-backtracking check. Not a proof. Catches:
      - nested quantifiers ((a+)*, (a*b)+)
      - wildcard groups under repetition ((.*)*)
      - alternation under repetition where one branch is a prefix of another
        ((a|aa)*, (ab|a)*) — these overlap and explode on non-matching input.
    Does NOT flag non-overlapping alternation ((a|b)*) which is safe."""
    if any(p.search(regex) for p in _REDOS_SHAPES):
        return True
    for m in _ALT_UNDER_REP.finditer(regex):
        branches = [b for b in m.group(0).strip("()*+").split("|") if b]
        for i, a in enumerate(branches):
            for b in branches[i + 1:]:
                # If one branch is a literal prefix of another, the alternation
                # overlaps and the engine backtracks combinatorially.
                if a.startswith(b) or b.startswith(a):
                    return True
```
