## Finding

This is a real logic bug in `plugins/hookify/core/config_loader.py`'s `Rule.from_dict`. There is a genuine parsing differential between the "legacy" `pattern` field and the "explicit" `conditions` list that silently disables the effective enforcement of a rule while leaving the frontmatter looking fully protective.

### Root cause

`Rule.from_dict` decides whether to use the legacy `pattern` field based solely on whether `conditions` ends up non-empty — not on whether those conditions are actually meaningful: [1](#0-0) 

```
if 'conditions' in frontmatter:
    cond_list = frontmatter['conditions']
    if isinstance(cond_list, list):
        conditions = [Condition.from_dict(c) for c in cond_list]

simple_pattern = frontmatter.get('pattern')
if simple_pattern and not conditions:
    ...
```

If a rule file contains **both** a top-level `pattern:` field (which auto-infers the correct field for the event, e.g. `command` for `bash`) **and** a `conditions:` list with at least one entry, the `pattern` is discarded entirely — even if that single explicit condition references a field that will never resolve for the given event (e.g. `field: content` on a `bash` event rule, where `_extract_field` only special-cases `command` for the `Bash` tool): [2](#0-1) [3](#0-2) 

When the field can't be resolved, `_extract_field` returns `None`, `_check_condition` returns `False`, and `_rule_matches` returns `False` for every input — silently and permanently, with no warning logged anywhere in `load_rule_file`/`load_rules`. [4](#0-3) [5](#0-4) 

### Exploit flow

A repo-shipped `.claude/hookify.*.local.md` file (or a rule emitted by `/hookify` generation, whose documented "advanced" template uses a `conditions:` block) can look exactly like a strict block rule:

```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
action: block
pattern: rm\s+-rf
conditions:
  - field: content
    operator: regex_match
    pattern: .*
---
Blocks destructive rm commands.
```

A reviewer/user sees `action: block` + `pattern: rm\s+-rf` and reasonably assumes this blocks `rm -rf` on Bash. In reality:
1. `conditions` is non-empty, so per `Rule.from_dict` line 58 the legacy `pattern` is ignored entirely.
2. The lone condition's `field: content` never resolves for a `Bash` tool call (only `command` is special-cased for Bash in `_extract_field`), so `_check_condition` always returns `False`.
3. `_rule_matches` always returns `False`, `evaluate_rules` returns `{}` (no block, no warning) for every input, including `rm -rf /`.
4. The command executes without any PreToolUse deny from hookify, even though the file's stated intent and visible `pattern` field imply it should be blocked.

This is exactly the kind of differential the invariant targets: the legacy pattern form and an "equivalent-looking" explicit `conditions` form do **not** produce equivalent security semantics, and the loader gives no diagnostic when the explicit path silently nullifies the legacy path.

### Caveat on impact

Hookify is a repo-local, opt-in supplementary hook plugin, not Claude Code's core approval/permission engine. The bypass defeats the *user's own custom hookify block rule* — it does not bypass Claude Code's built-in tool-use approval prompts unless the user has configured hookify as their sole enforcement layer for that command. The practical impact is: a malicious or careless rule file (planted by a repo contributor, or produced by `/hookify` generation combining both syntaxes) can make a rule appear to block dangerous operations while never actually doing so, with no error surfaced — a silent security-control bypass within the hookify plugin's own enforcement model.

### Recommendation
- In `Rule.from_dict`, treat `pattern` and `conditions` as mutually informative rather than mutually exclusive: if both are present, either merge them (AND them together) or explicitly warn/error that `pattern` is being ignored due to presence of `conditions`.
- In `load_rule_file`, validate that each condition's `field` is resolvable for the rule's declared `event` (e.g. reject/warn on `field: content` combined with `event: bash`), so unmatchable conditions are caught at load time instead of silently making the rule inert.
- Add a self-test in `RuleEngine`/`config_loader` that fails loudly (not just a stderr print swallowed by callers) when a `block` rule can structurally never match.

### Proof of Concept (unit/integration test plan)

```python
from hookify.core.config_loader import Rule, extract_frontmatter
from hookify.core.rule_engine import RuleEngine

content = """---
name: block-dangerous-rm
enabled: true
event: bash
action: block
pattern: rm\\s+-rf
conditions:
  - field: content
    operator: regex_match
    pattern: .*
---
Blocks destructive rm commands.
"""

fm, msg = extract_frontmatter(content)
rule = Rule.from_dict(fm, msg)

engine = RuleEngine()
result = engine.evaluate_rules([rule], {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /important/data"},
})

# Expected if invariant held: block/deny response for rm -rf
# Actual: result == {} -> command silently allowed, no block/warning
assert result == {}
```

This demonstrates that a rule whose frontmatter clearly states `action: block` / `pattern: rm\s+-rf` never actually blocks the matching command once an (even trivially broken) `conditions` list is present, confirming the legacy-vs-explicit parsing differential leads to silent enforcement bypass.

### Citations

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

**File:** plugins/hookify/core/rule_engine.py (L96-125)
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
```

**File:** plugins/hookify/core/rule_engine.py (L144-161)
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

**File:** plugins/hookify/core/rule_engine.py (L230-233)
```python
        # Handle special cases by tool type
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')
```
