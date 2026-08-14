### Title
Field-extraction gap for MultiEdit `old_string`/`new_string` allows equivalent dangerous edits to bypass block rules - ([File: plugins/hookify/core/rule_engine.py])

### Finding Description
`RuleEngine._extract_field` (plugins/hookify/core/rule_engine.py) resolves a condition's `field` value differently per tool. For `Edit`/`Write` it explicitly aliases `new_text`→`new_string` and `old_text`→`old_string`: [1](#0-0) 

For `MultiEdit`, however, only `file_path` and `new_text`/`content` (concatenated across `edits`) are handled — there is no alias for `old_text`/`old_string`, and no handling of a bare `field == "new_string"`/`"old_string"` at all: [2](#0-1) 

Because the top-level lookup `if field in tool_input` (line 196) only checks literal top-level keys of `tool_input`, and `MultiEdit`'s `tool_input` nests the actual strings inside a list under `edits`, a condition with `field: "new_string"` or `field: "old_string"` (naming conventions natural for a rule author targeting `Edit`) falls through every branch and returns `None`: [3](#0-2) 

`_check_condition` treats a `None` field value as "condition does not match" and returns `False` immediately: [4](#0-3) 

So a block rule with `tool_matcher: "Edit|MultiEdit"` and `field: "new_string"` (or `old_string`/`old_text`) correctly denies a dangerous `Edit` call but silently never matches (and never blocks) the semantically identical dangerous content delivered via `MultiEdit`'s `edits` list, because that one tool representation is not normalized into the extraction logic. `_matches_tool` (lines 127-142) happily matches `MultiEdit` against the same matcher pattern, so the tool-matching step is not what fails — the field extraction fails silently afterward, defeating the rule without any error being raised.

### Impact Explanation
This is a Security-control bypass: a rule intended to block a dangerous edit (e.g., matching secrets, dangerous code patterns, or protected file modifications) can be routed around simply by the agent/tool choosing `MultiEdit` instead of `Edit` to perform the same textual change, since `evaluate_rules` returns `{}` (no block, no warning) for that call while it would have returned a `deny` decision for `Edit`. This silently disables the protection for one of several equivalent tool representations, matching the "Security-control bypass that silently disables or routes around blocking" impact category.

### Likelihood Explanation
Preconditions: a hookify rule must target `field: "old_string"`, `"new_string"`, or `"old_text"` (rather than the safer `content`/`new_text` aliases) with a `tool_matcher` that includes `MultiEdit` (or relies on `*`). This is a plausible authoring pattern since `old_string`/`new_string` are the literal Claude Code `Edit` tool parameter names. Any workflow where Claude is induced (e.g., via prompt injection from repository content) to prefer `MultiEdit` over `Edit` for a change will reach this gap deterministically and repeatably — no special privilege beyond normal tool invocation is needed.

### Recommendation
Normalize field extraction for `MultiEdit` to mirror `Edit`/`Write`: support `old_text`/`old_string` (concatenated across `edits[].old_string`) in addition to the existing `new_text`/`content` handling, and add explicit `new_string`/`old_string` field name handling (not just `new_text`) for every tool branch in `_extract_field`, or better, centralize field aliasing so the same logical field name is resolved identically regardless of tool representation.

### Proof of Concept
Unit test added to a rule-engine test file:
```python
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Rule, Condition

rule = Rule(
    name="block-secret",
    enabled=True,
    event="file",
    tool_matcher="Edit|MultiEdit",
    conditions=[Condition(field="new_string", operator="contains", pattern="SECRET_KEY")],
    action="block",
    message="Blocked dangerous edit",
)
engine = RuleEngine()

edit_input = {
    "tool_name": "Edit",
    "tool_input": {"file_path": "a.py", "old_string": "x", "new_string": "SECRET_KEY=1"},
}
multiedit_input = {
    "tool_name": "MultiEdit",
    "tool_input": {
        "file_path": "a.py",
        "edits": [{"old_string": "x", "new_string": "SECRET_KEY=1"}],
    },
}

assert engine.evaluate_rules([rule], edit_input).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
# Bug: MultiEdit with identical dangerous content is not blocked
assert engine.evaluate_rules([rule], multiedit_input) == {}
```
Expected (buggy) behavior: the `Edit` call is denied while the `MultiEdit` call with identical dangerous content returns `{}` (allowed), demonstrating the bypass.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L157-161)
```python
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False

```

**File:** plugins/hookify/core/rule_engine.py (L182-200)
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

**File:** plugins/hookify/core/rule_engine.py (L246-254)
```python
        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)

        return None
```
