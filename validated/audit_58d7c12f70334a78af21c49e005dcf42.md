### Title
Legacy `pattern:` rules with `event: all` silently bind to a non-existent `content` field on Bash tool_input, causing dangerous-command block/warn rules to never fire - ([File: plugins/hookify/core/config_loader.py])

### Summary
`Rule.from_dict` infers the target field for legacy `pattern:` rules purely from the `event` string, mapping `bash`→`command`, `file`→`new_text`, and anything else (including the documented `event: all`, or any typo/case mismatch like `Bash`)→`content`. Because `config_loader.load_rules` treats `event: all` as matching every hook event (including PreToolUse for Bash), such a rule is loaded and evaluated against Bash tool calls, but `RuleEngine._extract_field` has no `content` mapping for the `Bash` tool (only `command`), so the condition always evaluates to `None`/`False` and the rule can never match, block, or warn - even for commands that exactly match the stated dangerous pattern.

### Finding Description
- `Rule.from_dict` (config_loader.py:61-67) infers `field` from `event` for legacy `pattern:` rules: only `event == 'bash'` maps to `command`; everything else, including the fully documented and supported `event: all` (skills/writing-rules/SKILL.md:21,46,366 explicitly documents `all` as a valid event that applies to "All events"), falls into the `else` branch and is bound to `content`. [1](#0-0) 
- `load_rules` treats `rule.event == 'all'` as matching any requested event filter, so a rule with `event: all` and a Bash-targeted `pattern:` is loaded for the `bash` event (PreToolUse hook for the `Bash` tool). [2](#0-1) 
- `pretooluse.py` sets `event='bash'` when `tool_name == 'Bash'` and calls `load_rules(event='bash')`, then `evaluate_rules`. [3](#0-2) 
- `RuleEngine._rule_matches` requires all conditions to match via `_check_condition`, which calls `_extract_field(condition.field, tool_name, tool_input, input_data)`. For `tool_name == 'Bash'`, `tool_input` only ever contains a `command` key, never `content`. [4](#0-3) [5](#0-4) 
- `_extract_field`'s direct lookup (`if field in tool_input`) fails because `'content'` is not a key in Bash's `tool_input`, the Stop/prompt special-case block doesn't handle `'content'`, and the Bash-specific branch only recognizes `field == 'command'` - so the function falls through to `return None` at the end. [6](#0-5) [7](#0-6) 
- Because `field_value is None`, `_check_condition` immediately returns `False` regardless of the regex pattern, so the rule can never contribute to `blocking_rules` or `warning_rules` in `evaluate_rules`. [8](#0-7) 

The rule object still reports `enabled: True` and is loaded without error/warning - there is no validation step that cross-checks the inferred field against the actual tool schema, so an author (or an attacker submitting a rule file via PR/plugin content) sees an apparently valid, "enabled" security rule that never actually enforces anything.

### Impact Explanation
This is a silent security-control failure: a `.claude/hookify.*.local.md` rule intended to `block` dangerous Bash invocations (e.g. `rm -rf`, `curl | sh`, `sudo`) using the legacy `pattern:` shorthand with `event: all` (a documented, valid value) never triggers, so dangerous commands execute without any block or warning. This matches a "hook enforcement bypass" / false sense of protection impact: a reviewer or user who added this rule believes destructive-command protection is active, while in fact no enforcement occurs, undermining the integrity of the hookify approval/block guard for Bash tool calls.

### Likelihood Explanation
Fully deterministic and trivially reproducible: any rule file using the legacy `pattern:` field with `event: all` (or any value other than exactly `'bash'`, e.g. a case typo like `Bash`) targeting Bash commands will always fail to fire, on every load. Since `all` is explicitly documented as a supported event for cross-cutting rules, this is a realistic authoring pattern, not a contrived edge case.

### Recommendation
In `Rule.from_dict`, do not infer the condition field solely from `event`; either require an explicit `field` (or `pattern` + `event`) mapping table that also handles `all`/`prompt`/`stop`, or generate one condition per relevant tool/field pair when `event == 'all'` (e.g., `command` for Bash and `new_text` for file tools), and emit a load-time warning when a legacy-pattern rule's inferred field cannot resolve to any known tool_input key for the events it will be evaluated against.

### Proof of Concept
```python
# test_legacy_pattern_all_event.py
from hookify.core.config_loader import Rule
from hookify.core.rule_engine import RuleEngine

frontmatter = {
    "name": "block-dangerous-rm",
    "enabled": True,
    "event": "all",          # documented valid value, but triggers 'content' inference
    "pattern": r"rm\s+-rf",
    "action": "block",
}
rule = Rule.from_dict(frontmatter, "Dangerous rm command blocked!")

# Confirm the inferred field is wrong for Bash tool_input
assert rule.conditions[0].field == "content"

engine = RuleEngine()
input_data = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /"},
}

result = engine.evaluate_rules([rule], input_data)

# Bug: expected a block response, but rule silently never matches
assert result == {}, f"Expected no enforcement due to field mismatch, got {result}"
```
Expected (buggy) behavior: `evaluate_rules` returns `{}` (no block, no warning) even though the Bash command `rm -rf /` exactly matches the rule's dangerous pattern, confirming the block rule is permanently inert while appearing `enabled: true` and correctly configured.

### Citations

**File:** plugins/hookify/core/config_loader.py (L60-73)
```python
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

**File:** plugins/hookify/core/config_loader.py (L219-226)
```python
            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)
```

**File:** plugins/hookify/hooks/pretooluse.py (L43-56)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L144-160)
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
```

**File:** plugins/hookify/core/rule_engine.py (L195-233)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L252-254)
```python
                return ' '.join(e.get('new_string', '') for e in edits)

        return None
```
