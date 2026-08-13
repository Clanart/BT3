This confirms the exploit path is fully reachable without any privileged operation.

`load_rules` globs `.claude/hookify.*.local.md` relative to CWD ` [1](#0-0) `, so any file matching that name pattern committed to a cloned repository is automatically picked up — no explicit user opt-in beyond the hookify plugin being enabled and the rule file existing. The rule's `pattern` (or `conditions[].pattern`) flows straight into `compile_regex` via `_regex_match` without any sanitization, length bound, complexity check, or execution-time cap: ` [2](#0-1) `. `compile_regex` itself just calls `re.compile(pattern, re.IGNORECASE)` with no safety wrapper: ` [3](#0-2) `.

The call chain from the hook entrypoint is direct: `pretooluse.py main()` → `load_rules(event=event)` → `RuleEngine.evaluate_rules` → `_rule_matches` → `_check_condition` → `_regex_match` → `compile_regex(pattern).search(text)` ` [4](#0-3) ` ` [5](#0-4) `. Because rule conditions are matched against attacker-influenced fields like `command`, `new_text`, `file_path` on every `Bash`/`Write`/`Edit`/`MultiEdit` call ` [6](#0-5) `, a catastrophic-backtracking pattern (e.g. `(a+)+b`) combined with an adversarial-but-plausible-length string in one of those fields (e.g. a long shell command or file content containing many `a` characters and no trailing `b`) causes `re.search` to take exponential time.

The hook is configured with a `timeout: 10` in `hooks.json` for `PreToolUse` ` [7](#0-6) `. Note this timeout is enforced by the core Claude Code hook-execution runtime (which is not part of this repository's source — it's the closed-source CLI), so I cannot verify from this codebase alone whether a killed/timed-out hook results in fail-open (tool proceeds unchecked) or fail-closed behavior. The plugin's own `pretooluse.py` code catches only Python-level exceptions and always exits 0 in its `finally` block, which does establish that *within* this plugin's design, "no crash" always means "allow" — but that path is not what's hit on an external OS-level timeout kill; that outcome is decided by the outer Claude Code runtime, whose source is outside this repository.

### Title
ReDoS in hookify's `compile_regex`/`_regex_match` via attacker-controlled rule patterns in committed `.claude/hookify.*.local.md` files - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._regex_match` compiles and executes user-supplied regex patterns from `.claude/hookify.*.local.md` rule files with no complexity/length/time bound, and these files are auto-discovered by `load_rules` glob on every `Bash`/`Write`/`Edit`/`MultiEdit` tool call. A catastrophic-backtracking pattern committed to such a file, paired with a matching adversarial input in the tool call's own fields, causes `re.search` to hang, tying up the `PreToolUse`/`PostToolUse`/`Stop`/`UserPromptSubmit` hook subprocess.

### Finding Description
`load_rules()` globs `.claude/hookify.*.local.md` relative to the current working directory with no filtering of pattern complexity ` [1](#0-0) `. The frontmatter `pattern`/`conditions[].pattern` string is stored verbatim on the `Rule`/`Condition` objects ` [8](#0-7) `. `RuleEngine._regex_match` passes that pattern straight to `compile_regex`, a bare `re.compile` wrapped only in an `lru_cache`, then calls `.search(text)` with no timeout, thread, or length guard ` [2](#0-1) `. `text` is extracted from ordinary tool-call fields such as `command` (Bash), `new_text`/`content` (Write/Edit), or `file_path` — all attacker-influenceable during normal agent operation ` [6](#0-5) `. Because Python's `re` module backtracking engine is vulnerable to classic exponential patterns like `(a+)+b`, a rule with such a pattern combined with a long non-matching string of `a`s in the checked field triggers unbounded CPU time in the hook subprocess spawned per `hooks.json` (`timeout: 10`) ` [7](#0-6) `. No existing validation rejects malformed/dangerous regex patterns at load time — only `re.error` (syntax errors) is caught, not runtime cost ` [9](#0-8) `.

### Impact Explanation
If the outer hook runtime treats a timed-out/killed `PreToolUse` command hook as non-blocking (allow), this becomes a deny-bypass: a dangerous `Bash`/`Write` call proceeds without hookify's `block` decision being evaluated, defeating any `action: block` rules the user relies on. At minimum, this is a reliable, repeatable local denial-of-service against the hook subprocess/session responsiveness on every matching tool call, degrading the security posture the hookify plugin is meant to provide. This repository does not contain the core Claude Code hook-timeout-handling code, so the fail-open behavior on timeout cannot be confirmed from this codebase alone.

### Likelihood Explanation
Preconditions are low-effort and fully attacker-controlled: clone/checkout a repository containing a `.claude/hookify.<name>.local.md` file with a catastrophic-backtracking pattern (this file is ordinary repo content, not gitignored by default unless the user adds it), then trigger any `Bash`/`Write`/`Edit` call whose relevant field is adversarial-length and non-matching. `load_rules` requires no special privilege — it just globs the working directory ` [10](#0-9) `. This is deterministic and repeatable on every subsequent matching tool call for the lifetime of the rule file.

### Recommendation
Bound regex evaluation cost in `_regex_match`/`compile_regex`: enforce a maximum pattern length, run a static ReDoS-pattern detector (e.g. reject nested quantifiers) at `load_rule_file` time, or execute `regex.search` in a subprocess/thread with a hard wall-clock timeout (e.g. via `signal.alarm`, `multiprocessing`, or the `regex` module's timeout support / `re2`-style linear engine) and treat a timeout as "rule does not match" rather than hanging the whole hook process.

### Proof of Concept
Add a unit/fuzz test in `plugins/hookify/core/` that:
1. Constructs a `Rule` with `conditions=[Condition(field="command", operator="regex_match", pattern="(a+)+b")]` and `event="bash"`.
2. Calls `RuleEngine()._check_condition(condition, "Bash", {"command": "a"*40}, {})` (or `evaluate_rules` with a `Bash` `tool_input.command` of `"a"*40`, no trailing `b`).
3. Wraps the call with a wall-clock assertion, e.g. `start=time.time(); result=engine._regex_match(pattern, text); assert time.time()-start < 1.0`.
4. Expect the assertion to fail (execution time grows exponentially with input length ~30-45 chars), demonstrating unbounded hang; parametrize with increasing lengths (30, 35, 40, 45) to show exponential growth confirming ReDoS in `compile_regex`/`_regex_match`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L15-29)
```python
@dataclass
class Condition:
    """A single condition for matching."""
    field: str  # "command", "new_text", "old_text", "file_path", etc.
    operator: str  # "regex_match", "contains", "equals", etc.
    pattern: str  # Pattern to match

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )
```

**File:** plugins/hookify/core/config_loader.py (L207-220)
```python
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
```

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

**File:** plugins/hookify/core/rule_engine.py (L96-167)
```python
    def _rule_matches(self, rule: Rule, input_data: Dict[str, Any]) -> bool:
        """Check if rule matches input data.

        Args:
            rule: Rule to evaluate
            input_data: Hook input data

        Returns:
            True if rule matches, False otherwise
        """
        # Extract tool information
        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})

        # Check tool matcher if specified
        if rule.tool_matcher:
            if not self._matches_tool(rule.tool_matcher, tool_name):
                return False

        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            return False

        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True

    def _matches_tool(self, matcher: str, tool_name: str) -> bool:
        """Check if tool_name matches the matcher pattern.

        Args:
            matcher: Pattern like "Bash", "Edit|Write", "*"
            tool_name: Actual tool name

        Returns:
            True if matches
        """
        if matcher == '*':
            return True

        # Split on | for OR matching
        patterns = matcher.split('|')
        return tool_name in patterns

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

**File:** plugins/hookify/core/rule_engine.py (L230-252)
```python
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

**File:** plugins/hookify/hooks/pretooluse.py (L35-60)
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
