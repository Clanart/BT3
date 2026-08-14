### Title
ReDoS in `hookify` rule engine via attacker-controlled regex + attacker-influenced text stalls PreToolUse/PostToolUse/Stop hooks - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`compile_regex()` compiles rule `pattern` strings with plain `re.compile(pattern, re.IGNORECASE)` and `_regex_match()` calls `regex.search(text)` with no timeout, length cap, or complexity check. Since `pattern` comes from `.claude/hookify.*.local.md` rule files (which can be committed into an ordinary repository and picked up automatically by `load_rules()`) and `text` comes from attacker-influenceable Bash `command` / `new_text` tool-input fields, a catastrophic-backtracking pattern paired with adversarial text causes the hook process to hang indefinitely.

### Finding Description
`compile_regex` [1](#0-0)  compiles any `pattern` string supplied via `Condition.pattern`, which is populated straight from rule-file frontmatter fields (`pattern`/`conditions[].pattern`) by `Condition.from_dict` and `Rule.from_dict` [2](#0-1) . Rule files are auto-discovered from the project directory via `glob.glob('.claude/hookify.*.local.md')` in `load_rules()` [3](#0-2) , so any repository content (or a rule file checked into a shared/cloned repo) that a victim opens with the hookify plugin enabled will be loaded and evaluated with no sanitization of the regex pattern.

At evaluation time, `_check_condition` extracts the field value (e.g. Bash `command`, Edit/Write `new_string`) and calls `_regex_match(pattern, field_value)` [4](#0-3) , which does `compile_regex(pattern).search(text)` inside a bare `try/except re.error` [5](#0-4) . Python's `re` engine has no built-in backtracking limit, so a pattern like `(a+)+$` matched against a crafted/adversarial string causes exponential-time matching; `re.error` only catches compile-time syntax errors, not runtime blowup, so nothing bounds execution.

This is reached from every `PreToolUse`/`PostToolUse` invocation via `pretooluse.py`/`posttooluse.py`, which call `RuleEngine.evaluate_rules(rules, input_data)` synchronously before printing JSON and exiting [6](#0-5) . Because the hang occurs inside the `try` block before the `finally: sys.exit(0)` is reached, the process does not terminate, and Claude Code's tool-approval flow (which waits on the hook's stdout/exit) stalls.

### Impact Explanation
A malicious rule pattern combined with attacker-influenced command/file text stalls the PreToolUse/PostToolUse/Stop hook process indefinitely, blocking the approval pipeline for that tool call and degrading or freezing the entire Claude Code session — an availability/DoS impact on the hookify enforcement mechanism itself.

### Likelihood Explanation
Requires: (1) a `.claude/hookify.*.local.md` rule file with `enabled: true` and a catastrophic-backtracking `pattern` (e.g. from a malicious/compromised repository the victim opens with hookify active), and (2) a matching field value (Bash `command` or file `new_text`/`old_text`) that triggers exponential backtracking — achievable with generic patterns matching broadly on ordinary command text. No special privileges beyond providing repo content are needed, and the trigger is deterministic and repeatable once the rule file is loaded.

### Recommendation
Enforce bounded regex execution in `rule_engine.py`: validate/reject regex patterns with known ReDoS-prone structures at rule-load time (similar to `_has_redos_structure` used in `security-guidance/hooks/extensibility.py`), cap the length of text passed to `regex.search`, and/or run matching with an enforced timeout (e.g. via a worker process/signal-based timeout or a safer regex engine such as `re2`/`regex` module's timeout support) so a single pathological pattern cannot stall the hook indefinitely.

### Proof of Concept
Add a unit/fuzz test in the hookify test suite:
```python
import time
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Rule, Condition

rule = Rule(name="evil", enabled=True, event="bash",
            conditions=[Condition(field="command", operator="regex_match", pattern=r"(a+)+$")])
engine = RuleEngine()
text = "a" * 40 + "!"  # no trailing match -> forces full backtracking
input_data = {"tool_name": "Bash", "tool_input": {"command": text}}

start = time.monotonic()
engine.evaluate_rules([rule], input_data)
elapsed = time.monotonic() - start
assert elapsed < 1.0, f"regex match took {elapsed}s, no bound enforced (ReDoS)"
```
Expected today: the assertion fails / test hangs, demonstrating unbounded compute time proportional to input length, confirming the missing timeout/complexity bound.

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

**File:** plugins/hookify/core/rule_engine.py (L144-167)
```python
    def _check_condition(self, condition: Condition, tool_name: str,
                        tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> bool:
        """Check if a single condition matches.

        Args:
            condition: Condition to check
            tool_name: Tool being used
            tool_input: Tool input dict
            input_data: Full hook input data (for Stop events, etc.)

        Returns:
            True if condition matches
        """
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False

        # Apply operator
        operator = condition.operator
        pattern = condition.pattern

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

**File:** plugins/hookify/core/config_loader.py (L22-73)
```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )


@dataclass
class Rule:
    """A hookify rule."""
    name: str
    enabled: bool
    event: str  # "bash", "file", "stop", "all", etc.
    pattern: Optional[str] = None  # Simple pattern (legacy)
    conditions: List[Condition] = field(default_factory=list)
    action: str = "warn"  # "warn" or "block" (future)
    tool_matcher: Optional[str] = None  # Override tool matching
    message: str = ""  # Message body from markdown

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
