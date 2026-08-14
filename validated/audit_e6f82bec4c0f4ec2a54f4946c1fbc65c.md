### Title
Unbounded `re.search()` in hookify's `RuleEngine._regex_match` allows an attacker-supplied catastrophic-backtracking pattern to hang the PreToolUse hook past its 10s timeout - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
`RuleEngine._regex_match` compiles and runs user-supplied regex patterns from `.claude/hookify.*.local.md` rule files with no execution-time bound, and `compile_regex` only caches compiled patterns without any complexity/safety validation. Because `pretooluse.py` runs under a fixed 10-second hook timeout (`plugins/hookify/hooks/hooks.json`), a catastrophic-backtracking pattern evaluated against an attacker-influenced `command`/`new_text` field can hang the hook process past that timeout.

### Finding Description
`load_rules()` in `plugins/hookify/core/config_loader.py:198-241` globs `.claude/hookify.*.local.md` from the project's working directory and parses arbitrary YAML-like frontmatter, including a free-form `pattern` string, with no sanitization of the regex itself [1](#0-0) . `Rule.from_dict` and `Condition.from_dict` pass this string straight through as a `regex_match` operator pattern [2](#0-1) .

`pretooluse.py` reads the tool call from stdin, resolves the event type for `Bash`/`Edit`/`Write`/`MultiEdit`, loads applicable rules, and calls `RuleEngine.evaluate_rules` [3](#0-2) . That flows into `_rule_matches` → `_check_condition` → `_regex_match`, which calls `compile_regex(pattern).search(text)` with no timeout, no length cap, and no static ReDoS detection [4](#0-3) . `compile_regex` is only an `lru_cache`-wrapped `re.compile`, providing no execution-time protection [5](#0-4) .

`hooks.json` sets a fixed 10-second timeout for the PreToolUse (and other) hooks [6](#0-5) . A pattern like `(a+)+$` matched against a moderately long non-matching `command` string (an attacker can influence the `command`/`new_text` text the rule evaluates against, e.g. via file content or command construction that Claude is induced to run) causes exponential backtracking in Python's `re` engine, which has no built-in timeout. This makes the CPython process hang, and only the outer hook-invocation timeout (10s) can terminate it.

Where this claim can't be substantiated from this repo alone: the actual behavior of Claude Code's core CLI when a hook process is killed for exceeding its timeout (i.e., whether the tool call is then denied — fail closed — or allowed to proceed — fail open) is not implemented in this plugin repo; it lives in the closed-source Claude Code core, which is not present in this codebase. `pretooluse.py`'s own `except Exception`/`finally: sys.exit(0)` fail-open logic only covers exceptions raised inside the Python process itself (e.g., import errors, parsing errors) [7](#0-6) ; a hang that gets externally killed by the host at the 10s timeout never reaches that `except`/`finally` block, so whether the operation is allowed or denied afterward depends entirely on Claude Code's own timeout-handling logic outside this repository, which I could not locate or verify in the indexed content.

### Impact Explanation
If Claude Code's host-side timeout handling for PreToolUse hooks is fail-open (allows the tool call when the hook doesn't return in time), then a `block`-action rule intended to prevent dangerous commands (e.g. `rm -rf`, credential exfiltration patterns) could be silently bypassed once its regex is replaced or supplemented with a catastrophic-backtracking pattern, defeating the security control the rule was meant to enforce. This is a security-hook-bypass class of impact. However, this repo does not contain the code that determines fail-open vs fail-closed behavior on hook timeout, so the actual end-to-end bypass cannot be confirmed from the available codebase.

### Likelihood Explanation
Exploitation requires an attacker to get a `.claude/hookify.*.local.md` file with an appropriately crafted `pattern`/`conditions` field into the victim's project (e.g., via a malicious PR contribution, since the plugin's own README explicitly invites "sharing example files via PR") [8](#0-7) , and requires the victim to already be relying on that hookify rule as a security control, and requires the attacker (or an agent instructed by attacker-controlled content) to trigger a Bash/Edit/Write tool call whose `command`/`new_text` is long/complex enough to trip the catastrophic backtracking. This is a fairly narrow, multi-step precondition chain, and the decisive question (host fail-open vs fail-closed on hook timeout) is outside this repo's control.

### Recommendation
- Impose a length cap and complexity screening on user-supplied `pattern` values in `config_loader.py`/`rule_engine.py` before compiling.
- Bound regex execution time (e.g., run `regex.search` in a subprocess/thread with a hard wall-clock timeout well under the hook's 10s budget, or use a linear-time regex engine such as Google's `re2` via a bound package) inside `_regex_match`.
- On any timeout/failure during rule evaluation, `pretooluse.py`/`RuleEngine.evaluate_rules` should not just print an error and exit 0 unconditionally for `block`-action rules — instead surface a systemMessage indicating rule evaluation failed, so blocking intent is not silently dropped.

### Proof of Concept
Fuzz/unit test plan for `plugins/hookify/core/rule_engine.py`:
1. Construct a `Rule` with `conditions=[Condition(field="command", operator="regex_match", pattern="(a+)+$")]`, `action="block"`.
2. Build a `command` string of increasing length of `"a" * n + "!"` (n = 20, 25, 30, 35) that does not match at the end, forcing worst-case backtracking.
3. Call `RuleEngine()._regex_match(pattern, command)` (or `evaluate_rules`) wrapped with a wall-clock timer; assert that execution time grows exponentially with `n` and exceeds a few seconds by n≈30, well within/exceeding the 10s hook timeout budget defined in `hooks/hooks.json`.
4. Integration test: invoke `pretooluse.py` as a subprocess with `timeout 10 python3 pretooluse.py` and the crafted `tool_input.command`, and assert whether the process is killed by the OS `timeout` (exit 124) rather than returning a JSON deny/allow decision — demonstrating no fail-closed behavior exists at the plugin layer for this case.
5. Note: to fully validate the exploitable end-to-end impact (bypassed block), a further test against Claude Code's actual hook-timeout handling logic would be required, which is outside this repository's contents.

### Citations

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

**File:** plugins/hookify/hooks/pretooluse.py (L35-59)
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

**File:** plugins/hookify/hooks/hooks.json (L4-14)
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
    ],
```

**File:** plugins/hookify/README.md (L326-329)
```markdown
## Contributing

Found a useful rule pattern? Consider sharing example files via PR!

```
