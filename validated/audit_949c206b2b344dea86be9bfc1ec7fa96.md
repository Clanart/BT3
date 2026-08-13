Important finding: `hooks.json` sets a `"timeout": 10` on the `PreToolUse` command hook, so `pretooluse.py` process itself is externally killed after 10 seconds if a `re.search` call hangs. This means the hang is bounded per-invocation (fail-safe cutoff), but the question's core claim — that an attacker-influenced regex pattern can cause `_regex_match` to hang the hook process on every subsequent Bash/Edit/Write call — is still substantively valid within that 10-second window, and it is unclear from available code whether Claude Code's runtime treats a hook timeout as fail-open (allow) or fail-closed (deny) for `PreToolUse`, which affects whether this becomes an actual enforcement bypass or just a 10s latency tax per tool call.

### Title
ReDoS in `RuleEngine._regex_match` allows attacker-influenced rule patterns to cause unbounded regex backtracking on every subsequent tool call - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
`_regex_match` compiles and executes user/attacker-influenced regex patterns from `.claude/hookify.*.local.md` files with no timeout, no complexity validation, and no bound on the size of `text` being matched. A pattern with nested quantifiers (e.g. `(a+)+$`) combined with adversarial input (e.g. a long Bash command or file content ending in a non-matching character) triggers catastrophic backtracking in Python's `re` engine, causing `regex.search(text)` to consume CPU for an extremely long time on every subsequent `Bash`/`Edit`/`Write` tool call that reaches this rule.

### Finding Description
`pretooluse.py` calls `load_rules()` then `RuleEngine.evaluate_rules()` → `_rule_matches()` → `_check_condition()` → `_regex_match(pattern, field_value)` for every `PreToolUse` event [1](#0-0) . `_regex_match` compiles the pattern via `compile_regex` (an `lru_cache`-wrapped `re.compile`) and calls `regex.search(text)` with no timeout or complexity guard [2](#0-1) . `pattern` originates directly from YAML frontmatter (`pattern:` field or `conditions[].pattern`) in `.claude/hookify.*.local.md`, parsed as a raw string with no length or complexity restriction in `config_loader.py`'s `extract_frontmatter`/`Rule.from_dict` [3](#0-2) . The `hookify.md` slash command's documented workflow explicitly instructs the model to derive "pattern" values from conversation analysis (including a `conversation-analyzer` sub-agent scanning recent user messages) and write them verbatim into the rule file with only a coarse `AskUserQuestion` behavior/action approval step — the literal regex text itself is not reviewed for safety [4](#0-3) [5](#0-4) . There is no allowlist, regex-safety check, or sandboxing anywhere in the load/evaluate path.

Once such a rule file exists, every subsequent `Bash` command or `Edit`/`Write` `new_string`/`content` value is matched against the pattern via `_extract_field` → `_regex_match` [6](#0-5) . Python's backtracking regex engine has no built-in catastrophic-backtracking protection, so a pattern like `(a+)+$` against a string like `"a"*40 + "!"` causes exponential-time evaluation.

Mitigating factor: `hooks/hooks.json` sets `"timeout": 10` on the `PreToolUse` command hook [7](#0-6) , which externally kills the `pretooluse.py` process after 10 seconds, bounding the worst case per invocation rather than allowing an indefinite hang. However, this repo does not contain the Claude Code core runtime source, so it cannot be verified here whether a `PreToolUse` hook timeout is treated as fail-open (tool proceeds) or fail-closed (tool denied) — this is an important unresolved detail for assessing whether the impact is "enforcement bypass" versus "repeated 10s latency/DoS" on every matching tool call.

### Impact Explanation
If a hook timeout on `PreToolUse` is fail-open in the Claude Code runtime, this becomes a persistent denial of the hook enforcement path: any `Bash`/`Edit`/`Write` operation whose input reaches the vulnerable condition's field would bypass all hookify rule checks (including `block` rules) after the timeout elapses, enabling unchecked tool execution. Even if fail-closed, it degrades every matching tool call to a forced ~10s delay, which is a real availability/DoS impact on the hook enforcement path, and can be triggered repeatedly for the lifetime of the malicious rule file. This is scoped to the local project/session where the malicious `.local.md` rule file exists.

### Likelihood Explanation
The attacker cannot directly write the rule file themselves (no direct filesystem write tool available to an unprivileged remote actor per constraints), but the `hookify.md` command's designed flow explicitly ingests conversation content and repo/user-message context to derive `pattern` values, with no regex-safety review before writing the pattern into `.claude/hookify.*.local.md` [4](#0-3) . If attacker-controlled text (e.g. injected instructions in repo content, issue text, or transcript content analyzed by the `conversation-analyzer` agent) proposes a ReDoS-prone pattern and the user approves the general behavior via `AskUserQuestion` (approving intent, not auditing regex safety), the vulnerable pattern gets persisted and executed on every future matching tool call. This requires a non-trivial but plausible prompt-injection + user-approval chain, and is not directly attacker-write-controlled without that chain.

### Recommendation
- Enforce a hard wall-clock timeout around `regex.search()` in `_regex_match` (e.g., via a worker process/thread with `signal.alarm` on POSIX, or a subprocess with its own timeout) and treat timeout as a non-match plus a logged warning, never as a raw hang.
- Validate/sanitize patterns before caching: reject patterns exceeding a length limit, reject or warn on known ReDoS-prone constructs (nested quantifiers like `(a+)+`, `(a*)*`, alternation with overlapping branches), or switch to a linear-time regex engine (e.g. Google's `re2` via the `google-re2` package) for user-authored patterns.
- Cap the size of `text` passed to `_regex_match` in `_extract_field` (e.g., truncate very large `command`/`content`/`transcript` values before regex evaluation).
- In `hookify.md`'s Step 3 workflow, add an explicit regex-safety check step (e.g., test each candidate pattern against a few large synthetic inputs with a timeout before writing the rule file).

### Proof of Concept
```python
# test_redos_regex_match.py
import time
import pytest
from hookify.core.rule_engine import RuleEngine

def test_regex_match_redos_hangs():
    engine = RuleEngine()
    pattern = r"(a+)+$"
    # Adversarial input: matches quantifier prefix but fails overall match,
    # forcing catastrophic backtracking.
    text = "a" * 35 + "!"

    start = time.monotonic()
    # Wrap with a wall-clock timeout assertion; in current code this call
    # has no internal timeout so it will exceed a reasonable bound (e.g. 2s).
    result = engine._regex_match(pattern, text)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"_regex_match took {elapsed:.2f}s for a small ReDoS payload; "
        "no timeout/complexity guard present in rule_engine.py"
    )
```
Expected result on current code: the assertion fails (elapsed time grows exponentially with payload length, e.g. seconds to minutes), demonstrating the missing timeout/complexity guard in `_regex_match`/`compile_regex`. A companion integration test should additionally invoke `pretooluse.py` end-to-end with a `.claude/hookify.evil.local.md` file containing `pattern: (a+)+$` and a `Bash` `tool_input.command` payload like `"a"*40 + "!"`, asserting the subprocess completes (or is killed by the 10s `hooks.json` timeout) and documenting/asserting whether the resulting tool call is allowed (fail-open) or denied (fail-closed) by the surrounding Claude Code runtime — this last assertion requires the core runtime, which is outside this repo's indexed contents.

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L51-56)
```python
        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
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

**File:** plugins/hookify/commands/hookify.md (L42-58)
```markdown

For each issue found, extract:
- What tool was used (Bash, Edit, Write, etc.)
- Specific pattern or command
- Why it was problematic
- User's stated reason

Return findings as a structured list with:
- category: Type of issue
- tool: Which tool was involved
- pattern: Regex or literal pattern to match
- context: What happened
- severity: high/medium/low

Focus on the most recent issues (last 20-30 messages). Don't go back further unless explicitly asked."
}
```
```

**File:** plugins/hookify/commands/hookify.md (L77-102)
```markdown
**Question 3: Ask for example patterns:**
- "What patterns should trigger this rule?"
- Show detected patterns
- Allow user to refine or add more

### Step 3: Generate Rule Files

For each confirmed behavior, create a `.claude/hookify.{rule-name}.local.md` file:

**Rule naming convention:**
- Use kebab-case
- Be descriptive: `block-dangerous-rm`, `warn-console-log`, `require-tests-before-stop`
- Start with action verb: block, warn, prevent, require

**File format:**
```markdown
---
name: {rule-name}
enabled: true
event: {bash|file|stop|prompt|all}
pattern: {regex pattern}
action: {warn|block}
---

{Message to show Claude when rule triggers}
```
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
