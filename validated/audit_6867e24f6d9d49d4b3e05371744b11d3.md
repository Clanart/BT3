### Title
Rule engine field extraction misses `old_text`/`old_string` for `MultiEdit`, allowing block rules to be bypassed - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
`RuleEngine._extract_field` only resolves the `old_text`/`old_string` field for `Edit`/`Write` tool calls, but never for `MultiEdit`, even though `MultiEdit` is a fully supported, attacker-selectable alternate representation of the same file-editing operation. Any block rule written against `old_text`/`old_string` (e.g. "don't allow removal of a security check line") silently never matches when the same edit is performed with `MultiEdit` instead of `Edit`, because the extracted field value is `None` and `_check_condition` short-circuits to `False`.

### Finding Description
`RuleEngine._rule_matches` requires `_check_condition` to return `True` for every condition in a rule before treating the rule as matched [1](#0-0) . `_check_condition` extracts the field value via `_extract_field` and immediately returns `False` if that value is `None` [2](#0-1) .

`_extract_field` handles `old_text`/`old_string` only inside the `tool_name in ['Write', 'Edit']` branch: [3](#0-2) 

The `MultiEdit` branch that follows handles only `file_path` and `new_text`/`content` (by concatenating each sub-edit's `new_string`), but has no case for `old_text`/`old_string`: [4](#0-3) 

`MultiEdit` is a normal, unprivileged Claude Code tool whose `tool_input['edits']` is a list of `{old_string, new_string, ...}` objects — semantically identical to `Edit`'s single `old_string`/`new_string` pair, just batched. `pretooluse.py` treats `Edit`, `Write`, and `MultiEdit` as the same `event = 'file'` category for rule loading [5](#0-4) , so a maintainer who writes a rule with `tool_matcher: "Edit|MultiEdit"` (or `"*"`) and a condition on `field: old_text` reasonably expects it to guard both tools equally. Because the engine only implements the `old_text` extraction for one of the two equivalent representations (`Edit`), any attacker-controlled change routed through `MultiEdit` with an identical `old_string` value that would trigger a block under `Edit` instead produces `field_value = None`, causing `_check_condition` to return `False`, `_rule_matches` to return `False`, and the operation to proceed unblocked — with no warning that the rule was skipped.

### Impact Explanation
This is a Security-control bypass: a rule author's intended blocking/review control based on the pre-edit content (`old_text`/`old_string`) can be silently routed around simply by performing the same file modification through `MultiEdit` rather than `Edit`. Since hookify rules are commonly used to enforce guardrails (e.g., "do not remove this authorization check", "do not delete this safety comment"), an attacker who can choose which tool call performs an edit (which is normal, unprivileged tool usage) can defeat the intended block without any credential or privilege escalation.

### Likelihood Explanation
No special privileges are required — only the ability to trigger a `MultiEdit` tool call instead of `Edit` for the targeted file change, which is standard behavior available to any Claude Code session. The bypass is deterministic and repeatable: any rule with `field: old_text` or `field: old_string` is affected whenever the actual edit is performed via `MultiEdit`.

### Recommendation
Add `old_text`/`old_string` handling to the `MultiEdit` branch of `_extract_field`, aggregating `old_string` across all entries in `tool_input['edits']` (mirroring the existing `new_text`/`content` aggregation), so conditions on pre-edit content are evaluated consistently regardless of which file-editing tool representation is used.

### Proof of Concept
Unit test added to a test module exercising `RuleEngine` directly:
```python
from hookify.core.config_loader import Condition, Rule
from hookify.core.rule_engine import RuleEngine

rule = Rule(
    name="protect-auth-check",
    enabled=True,
    event="file",
    tool_matcher="Edit|MultiEdit",
    conditions=[Condition(field="old_text", operator="contains", pattern="if not authorized")],
    action="block",
    message="Do not remove authorization check",
)
engine = RuleEngine()

# Edit tool: rule correctly blocks
edit_input = {
    "tool_name": "Edit",
    "tool_input": {"old_string": "if not authorized: raise", "new_string": ""},
}
assert engine.evaluate_rules([rule], edit_input).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

# MultiEdit tool: identical removal, but rule fails to match
multiedit_input = {
    "tool_name": "MultiEdit",
    "tool_input": {"edits": [{"old_string": "if not authorized: raise", "new_string": ""}]},
}
result = engine.evaluate_rules([rule], multiedit_input)
assert result == {}, "MultiEdit bypasses the old_text block rule that Edit correctly enforces"
```
Expected: the `Edit` case is denied, while the `MultiEdit` case with the same removed content returns an empty dict (allowed), demonstrating the bypass.

### Citations

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

**File:** plugins/hookify/core/rule_engine.py (L235-244)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L246-252)
```python
        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)
```

**File:** plugins/hookify/hooks/pretooluse.py (L43-49)
```python
        tool_name = input_data.get('tool_name', '')

        event = None
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'
```
