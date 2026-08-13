### Title
Presence of a `conditions` frontmatter key silently discards a legacy `pattern` block rule, allowing untrusted rule content to neutralize enforcement - (File: `plugins/hookify/core/config_loader.py`)

### Finding Description
`Rule.from_dict` decides which matching semantics to use with a simple precedence rule: if the frontmatter key `conditions` is present and is a list, it is used verbatim via `Condition.from_dict`; the legacy `pattern` field is converted into a condition only `if simple_pattern and not conditions:` [1](#0-0) . This means the mere *presence* of a `conditions` list — regardless of whether its entries are well-formed or semantically meaningful — completely disables the legacy `pattern` fallback, even though `pattern` is still stored on the `Rule` object and displayed/interpreted by humans (e.g. in `/hookify:configure` descriptions) as if it were the enforced rule [2](#0-1) .

`Condition.from_dict` performs no validation on `field` or `operator`: unknown/misspelled `field` values are accepted as-is [3](#0-2) . In `RuleEngine._rule_matches`, **all** conditions must match (AND semantics), and a rule with zero valid/matching conditions can never fire [4](#0-3) . `_extract_field` returns `None` for any field name it does not explicitly recognize (e.g. a typo'd field, or a field not applicable to the current tool), and `_check_condition` immediately returns `False` when the field value is `None` [5](#0-4) [6](#0-5) .

The documented rule-authoring workflow (`/hookify` command, loaded via the `hookify:writing-rules` skill) explicitly shows the Claude-generated rule file format for both the "legacy" `pattern:` form and the "complex" `conditions:` form [7](#0-6) . Rule content — including which style is used — is derived from `/hookify` generation (an LLM-driven process influenced by conversation context and, in `$ARGUMENTS`-driven flows, by attacker-influenced text) or can be a repo-shipped `.claude/hookify.*.local.md` file that a victim clones and whose rules get auto-loaded by `load_rules()`/`load_rule_file()` with no schema/consistency validation between the human-readable `pattern`/message and the actual enforced `conditions` [8](#0-7) .

As a result, a `block` rule that is nominally documented/labeled as blocking a dangerous pattern (e.g. `rm -rf`, writes to `.env`, etc.) can be made a permanent no-op simply by including a `conditions:` block whose entries reference a field/operator combination that will structurally never evaluate to `True` (e.g., a `field` value not recognized by `_extract_field`, or a second ANDed condition on an unrelated field that is virtually never satisfied). Since `rule.action == 'block'` only ever triggers via `_rule_matches` returning `True`, and `_rule_matches` requires every condition in the list to match, the block silently degrades to "always allow" while everything else about the rule (name, message, `action: block`) still reads as an active protection. This breaks the stated invariant that legacy (`pattern`) and explicit (`conditions`) rule forms provide equivalent effective security semantics, and lets `Write`/`Edit`/`Bash` operations that a user believes are hard-blocked proceed unimpeded.

### Impact Explanation
This enables unauthorized file writes (or command execution) that the user explicitly configured a `block` rule to prevent — e.g. bypassing a rule intended to stop edits to `.env`/credential files or dangerous `rm -rf` commands — because the enforcement point (`plugins/hookify/hooks/pretooluse.py` → `RuleEngine.evaluate_rules`) never sees the block fire [9](#0-8) . This matches "Unauthorized file read or write outside the user-approved workspace or target scope," since the whole purpose of a hookify block rule is to constrain what Claude Code is permitted to touch.

### Likelihood Explanation
Preconditions are low: any repo-shipped `.claude/hookify.*.local.md` file (cloned by a victim) or any `/hookify` rule-generation output that includes a `conditions:` block is auto-loaded with zero schema validation via `load_rules()` [10](#0-9) . No privileges beyond normal repository content or normal `/hookify` usage are required, and the bypass is deterministic and fully reproducible: the flaw is a direct logic property of `from_dict`'s precedence rule and `_rule_matches`'s strict AND semantics, not a timing or race condition.

### Recommendation
1. In `Rule.from_dict`, do not let an arbitrary/malformed `conditions` list silently suppress a `pattern`-derived condition; instead validate each condition's `field`/`operator` against an explicit allow-list and raise/skip invalid entries rather than accepting them as legitimate rule content.
2. Change `RuleEngine._rule_matches`/`_check_condition` so that a condition referencing an unrecognized `field` is treated as a configuration error (log + treat the whole rule as invalid to be safe, especially for `action: block` rules) rather than as a silently-false condition that only ever weakens enforcement.
3. Add a `Rule` consistency check when both `pattern` and `conditions` exist: warn (and fail closed) when they appear semantically related but the condition list will never be satisfiable against the tool inputs matching the rule's `event`.
4. Add a unit/invariant test asserting that for every `action: block` rule generated from legacy `pattern` semantics, an equivalent `conditions`-based rewrite produces identical `_rule_matches` results across a fuzzed corpus of tool inputs.

### Proof of Concept
```python
# test_rule_semantics_diff.py
from hookify.core.config_loader import Rule
from hookify.core.rule_engine import RuleEngine

# "Legacy" block rule: blocks any bash command containing rm -rf
legacy_fm = {
    "name": "block-dangerous-rm",
    "enabled": True,
    "event": "bash",
    "pattern": r"rm\s+-rf",
    "action": "block",
}
legacy_rule = Rule.from_dict(legacy_fm, "Blocked: dangerous rm -rf")

# "Explicit" form generated for the same declared intent, but with an
# extra ANDed condition on a field that _extract_field never resolves
# for Bash tool_input (e.g. "new_text"), making the rule unsatisfiable.
explicit_fm = {
    "name": "block-dangerous-rm",
    "enabled": True,
    "event": "bash",
    "pattern": r"rm\s+-rf",   # still present, but ignored because conditions exists
    "conditions": [
        {"field": "command", "operator": "regex_match", "pattern": r"rm\s+-rf"},
        {"field": "new_text", "operator": "contains", "pattern": "x"},  # never present for Bash
    ],
    "action": "block",
}
explicit_rule = Rule.from_dict(explicit_fm, "Blocked: dangerous rm -rf")

engine = RuleEngine()
dangerous_input = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /important/data"},
}

legacy_result = engine.evaluate_rules([legacy_rule], dangerous_input)
explicit_result = engine.evaluate_rules([explicit_rule], dangerous_input)

# Invariant: legacy and explicit forms of the "same" rule intent must
# both block the dangerous command.
assert legacy_result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
assert explicit_result == legacy_result, (
    "Explicit conditions form silently bypassed the block rule: "
    f"{explicit_result!r} != {legacy_result!r}"
)
```
Expected (current, buggy) behavior: `legacy_result` denies the operation, but `explicit_result` is `{}` (no match, operation allowed) — demonstrating the exploitable differential and the assertion failure that proves the invariant violation.

### Citations

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

**File:** plugins/hookify/core/config_loader.py (L50-73)
```python
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

**File:** plugins/hookify/core/config_loader.py (L75-84)
```python
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

**File:** plugins/hookify/core/config_loader.py (L198-274)
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


def load_rule_file(file_path: str) -> Optional[Rule]:
    """Load a single rule file.

    Returns:
        Rule object or None if file is invalid.
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()

        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None

        rule = Rule.from_dict(frontmatter, message)
        return rule

    except (IOError, OSError, PermissionError) as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return None
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        print(f"Error: Malformed rule file {file_path}: {e}", file=sys.stderr)
        return None
    except UnicodeDecodeError as e:
        print(f"Error: Invalid encoding in {file_path}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: Unexpected error parsing {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
        return None
```

**File:** plugins/hookify/core/rule_engine.py (L115-125)
```python
        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            return False

        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True
```

**File:** plugins/hookify/core/rule_engine.py (L156-161)
```python
        """
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False

```

**File:** plugins/hookify/core/rule_engine.py (L230-254)
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

        return None
```

**File:** plugins/hookify/commands/hookify.md (L91-124)
```markdown
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

**Action values:**
- `warn`: Show message but allow operation (default)
- `block`: Prevent operation or stop session

**For more complex rules (multiple conditions):**
```markdown
---
name: {rule-name}
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$
  - field: new_text
    operator: contains
    pattern: API_KEY
---

{Warning message}
```
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
