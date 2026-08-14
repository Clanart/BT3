### Title
Silent block-rule bypass via unvalidated field/event mismatch in explicit `conditions` vs. auto-inferred legacy `pattern` - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`Rule.from_dict` auto-infers a correct extraction `field` (`command`/`new_text`/`content`) when a rule is written in the legacy `pattern:` form, but blindly trusts an attacker/LLM-supplied `field` name when the rule uses the explicit `conditions:` form, with no cross-validation against `event`. Because `RuleEngine` uses strict AND semantics and returns "no match" the moment any field lookup fails, a `conditions`-based rule with a field name that doesn't exist for its declared `event`/tool becomes a permanently inert no-op — while still looking like a fully functional `action: block` rule.

### Finding Description
`Rule.from_dict` [1](#0-0)  handles two rule authoring styles:

- Legacy: `pattern:` — the field to check is derived deterministically from `event` (`bash`→`command`, `file`→`new_text`, else→`content`), so it is always internally consistent.
- Explicit: `conditions:` — each item's `field`, `operator`, and `pattern` are taken verbatim from attacker/LLM-controlled frontmatter via `Condition.from_dict` [2](#0-1) , with no check that `field` is actually a valid/extractable field for the rule's `event`/tool type.

At evaluation time, `RuleEngine._extract_field` [3](#0-2)  returns `None` whenever the requested `field` doesn't correspond to a key `_extract_field` knows about for the current `tool_name` (e.g. `field: command` on a `Write`/`Edit` file event, or `field: new_text` on a `Bash` event). `_check_condition` treats a `None` field value as "does not match" [4](#0-3) , and `_rule_matches` requires ALL conditions to match (`for condition in rule.conditions: if not ...: return False`) [5](#0-4) . A single mismatched field therefore makes the rule permanently unmatchable for its intended event — silently, with no error surfaced anywhere in `load_rule_file`/`load_rules` [6](#0-5) , since the failure is normal control flow, not an exception.

This breaks the stated invariant that legacy and explicit rule forms must yield the same effective security semantics: a legacy `pattern:` rule for a given `event` is guaranteed to check the right field, while an equivalent-looking `conditions:` rule (as produced by `/hookify rule creation`, per the multi-condition examples in `plugins/hookify/commands/hookify.md` and `plugins/hookify/skills/writing-rules/SKILL.md`) can carry a subtly wrong `field` and thereby never trigger, even though it is `enabled: true` and `action: block`.

### Impact Explanation
A `.claude/hookify.*.local.md` rule with `action: block` is Claude Code's mechanism to hard-stop dangerous Bash commands or sensitive file edits (PreToolUse deny / Stop decision) [7](#0-6) . If such a rule is generated (via `/hookify rule creation`, potentially influenced by repository content that steers the generation, i.e. prompt injection) or shipped directly in a repository with a field/event mismatch in its `conditions`, the rule silently never fires. The user/operator believes a specific dangerous action class (e.g., `rm -rf`, writing secrets to `.env`) is blocked, but it is not — subsequent dangerous Bash commands or sensitive file writes proceed unblocked across the entire session and in every future session that loads that rule file, until a maintainer notices the rule is dead. This is a real bypass of a security control (hookify block rule), matching "unauthorized command or file action" / "trust-boundary bypass" style impact.

### Likelihood Explanation
Preconditions: the victim must have (or generate via `/hookify:create`) a `conditions:`-based block rule whose `field` doesn't match the actual tool_input keys for its `event`/tool. This is entirely plausible because:
- The documentation itself (`plugins/hookify/README.md`, `SKILL.md`) shows multiple field names (`command`, `new_text`, `old_text`, `content`, `file_path`, `user_prompt`) without warning that mismatching one to the wrong event silently disables the rule.
- No validation anywhere (`Rule.from_dict`, `load_rule_file`) checks field/event compatibility or warns that a rule has zero possible matches.
- A repo-shipped rule file or LLM-generated frontmatter (steered by adversarial repository content read during `/hookify rule creation`) can introduce this mismatch without any visible error, and it is fully reproducible/deterministic once present.

### Recommendation
- In `Rule.from_dict`/`Condition.from_dict`, validate that each condition's `field` is a recognized, extractable field for the rule's declared `event` (mirroring the same table used for legacy pattern inference), and reject/warn on unknown or event-incompatible fields instead of silently accepting them.
- In `load_rule_file`, after building a `Rule`, perform a sanity check (e.g., a "self-test" using representative sample field values) or at minimum log a warning when a `block`/`warn` rule's conditions reference fields not valid for its event, so a dead rule doesn't pass silently as "loaded successfully."
- Treat legacy-to-condition conversion and explicit-condition construction through a single shared field-resolution helper to guarantee behavioral parity between the two rule forms.

### Proof of Concept
```python
from hookify.core.config_loader import Rule
from hookify.core.rule_engine import RuleEngine

# Explicit "conditions" form generated by /hookify rule creation for a file-write block rule,
# but with a field name copy-pasted from a bash-rule template ("command" instead of "new_text"/"content").
frontmatter_explicit = {
    "name": "block-hardcoded-api-key",
    "enabled": True,
    "event": "file",
    "action": "block",
    "conditions": [
        {"field": "command", "operator": "regex_match", "pattern": r"API_KEY\s*="}
    ],
}
rule_explicit = Rule.from_dict(frontmatter_explicit, "Blocked: hardcoded API key")

engine = RuleEngine()
dangerous_write = {
    "tool_name": "Write",
    "tool_input": {"file_path": "secrets.env", "new_string": "API_KEY=abcd1234"},
}
result = engine.evaluate_rules([rule_explicit], dangerous_write)
assert result == {}, "Block rule silently failed to trigger (evasion confirmed)"

# Equivalent legacy-form rule for the same policy correctly infers field='new_text'
# and DOES block, proving the semantic divergence between the two rule forms.
frontmatter_legacy = {
    "name": "block-hardcoded-api-key-legacy",
    "enabled": True,
    "event": "file",
    "action": "block",
    "pattern": r"API_KEY\s*=",
}
rule_legacy = Rule.from_dict(frontmatter_legacy, "Blocked: hardcoded API key")
result_legacy = engine.evaluate_rules([rule_legacy], dangerous_write)
assert result_legacy.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
```
Expected assertions: `rule_explicit` never matches the dangerous write (empty `{}` result — evasion), while `rule_legacy`, encoding the identical intended policy, correctly denies it — demonstrating the legacy/explicit semantic divergence and the resulting silent block-rule bypass.

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

**File:** plugins/hookify/core/config_loader.py (L244-274)
```python
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

**File:** plugins/hookify/core/rule_engine.py (L60-79)
```python
        # If any blocking rules matched, block the operation
        if blocking_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in blocking_rules]
            combined_message = "\n\n".join(messages)

            # Use appropriate blocking format based on event type
            if hook_event == 'Stop':
                return {
                    "decision": "block",
                    "reason": combined_message,
                    "systemMessage": combined_message
                }
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"
                    },
                    "systemMessage": combined_message
                }
```

**File:** plugins/hookify/core/rule_engine.py (L120-125)
```python
        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True
```

**File:** plugins/hookify/core/rule_engine.py (L157-160)
```python
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False
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
