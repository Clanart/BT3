### Title
`Rule.from_dict` silently drops legacy `pattern` in favor of unvalidated `conditions`, letting a "block" rule be authored that never matches - (File: plugins/hookify/core/config_loader.py)

### Summary
`Rule.from_dict` treats the presence of a non-empty `conditions:` list in rule frontmatter as fully authoritative and completely discards the legacy `pattern:` field, with no validation that the resulting `Condition` objects are semantically meaningful. Because `Condition.from_dict` silently defaults missing/garbled fields (`field=''`, `operator='regex_match'`, `pattern=''`) and `RuleEngine._extract_field`/`_check_condition` return `False`/`None` for any field it doesn't recognize, a rule file that looks like a working "block" rule (complete with a visible `pattern:` and `action: block`) can be rendered permanently non-matching simply by also including a `conditions:` block, with zero warnings surfaced to the user.

### Finding Description
`Rule.from_dict` (`plugins/hookify/core/config_loader.py:44-84`) implements two parsing paths that are supposed to be equivalent:
- Legacy: `pattern:` → converted into exactly one `Condition` with `field` inferred from `event` (`command`/`new_text`/`content`) and `operator='regex_match'` [1](#0-0) .
- Explicit: `conditions:` list → each item passed through `Condition.from_dict`, which fills in defaults (`field=''`, `operator='regex_match'`, `pattern=''`) for any missing keys without validation [2](#0-1) .

Critically, the branch selection is:
```
if 'conditions' in frontmatter:
    ...
    conditions = [Condition.from_dict(c) for c in cond_list]
simple_pattern = frontmatter.get('pattern')
if simple_pattern and not conditions:
    ...
``` [3](#0-2) 

Once `conditions` is populated (even with a single malformed or semantically empty entry), the `pattern` fallback is unreachable — `pattern` is stored on the `Rule` object (line 79) purely for display/legacy compatibility but has **no effect on matching**. `RuleEngine._rule_matches` only ever iterates `rule.conditions`; it never falls back to `rule.pattern` [4](#0-3) .

Downstream, `_extract_field` returns `None` for any field name it doesn't explicitly special-case (e.g., an unrecognized `field` value or the default empty string from a malformed condition), and `_check_condition` immediately returns `False` when `field_value is None` [5](#0-4) . Since `_rule_matches` requires **all** conditions to match (AND semantics, line 121-123), a single such broken condition makes the whole rule permanently unmatchable — the rule is loaded, marked `enabled`, and shown as a legitimate `action: block` rule in `/hookify list`, but it will never fire.

No part of `config_loader.py` validates `field` names, `operator` values, or that `conditions` entries actually parsed into something useful; parsing errors are only caught at the file-I/O/type level in `load_rule_file` (`plugins/hookify/core/config_loader.py:244-274`), not at the semantic level. There is also no warning emitted when `pattern` is silently ignored because `conditions` was present.

Because `.claude/hookify.*.local.md` files are loaded directly from the working repository via `glob.glob('.claude/hookify.*.local.md')` in `load_rules` [6](#0-5) , any rule file shipped in a cloned repository (or produced by `/hookify` generation, which is documented in `plugins/hookify/commands/hookify.md` and `plugins/hookify/skills/writing-rules/`, both of which I could not fully inspect before running out of investigation budget) is parsed with this same logic. A rule authored or regenerated with an explicit `conditions:` block that references a non-existent field, or a malformed inline dict item (the hand-rolled YAML parser in `extract_frontmatter` is itself fragile around inline comma-separated dicts, lines 163-181), silently becomes a no-op block rule while still displaying a `pattern:` value and `action: block` that suggests it is active.

### Impact Explanation
This breaks the core security invariant that hookify's legacy and explicit rule syntaxes must be functionally equivalent. A rule intended to block a dangerous action (e.g., `rm -rf`, secret exfiltration commands, disallowed file writes) can be silently defanged by the mere presence of a malformed/incomplete `conditions:` entry, without any error, warning, or visible difference in the rule's displayed configuration. This matches the "Security-control bypass that silently disables or routes around blocking" impact category, since the block action configured by the user/maintainer is never enforced, and there is no signal to the user that protection has been lost.

### Likelihood Explanation
Reaching this requires that a rule file with an explicit `conditions:` list (even alongside a `pattern:` field) end up in `.claude/hookify.*.local.md` in the working repository — via a repo-shipped file (e.g., a rule file merged from a contribution, a `git clone` of a project that ships hookify rules) or via `/hookify` generation producing such a file. I was not able to fully verify the exact generation prompt/logic in `plugins/hookify/commands/hookify.md` or `plugins/hookify/skills/writing-rules/` within the available tool budget, so I cannot confirm whether the LLM-driven generation path is easily steered (e.g., via prompt injection from repo content) into emitting a `conditions:` block with a bogus `field`/`operator`. The `Rule.from_dict`/`Condition.from_dict` differential itself is fully confirmed by direct code inspection and is trivially reproducible with a unit test, independent of how the frontmatter is produced.

### Recommendation
- In `Rule.from_dict`, validate that every `Condition` parsed from `conditions:` has a non-empty `field`/`pattern` and a recognized `operator`; reject or fall back to the legacy `pattern` translation (with a loud warning) if any condition is malformed.
- Add an explicit whitelist of valid `field` and `operator` values in `Condition.from_dict`/`config_loader.py`, raising a `ValueError` (caught and logged as a fatal parse warning in `load_rule_file`) instead of silently defaulting to empty strings.
- Never let a rule with `action: block` load as `enabled` if it has zero conditions capable of matching; surface this at load time via `print(..., file=sys.stderr)` and refuse to treat the rule as active protection.
- Consider merging `pattern` and `conditions` semantics (AND them, or explicitly document/enforce that `conditions` fully replaces `pattern` with equivalent test coverage) so the two authoring styles are provably equivalent for the same intended pattern.

### Proof of Concept
Unit test to add to `plugins/hookify/core/` test suite:
```python
from hookify.core.config_loader import Rule
from hookify.core.rule_engine import RuleEngine

# Legacy form: blocks "rm -rf"
legacy_fm = {
    "name": "block-rm-rf",
    "enabled": True,
    "event": "bash",
    "pattern": r"rm\s+-rf",
    "action": "block",
}
legacy_rule = Rule.from_dict(legacy_fm, "Blocked!")

# "Equivalent" explicit form, but with a malformed condition (bad field name)
explicit_fm = {
    "name": "block-rm-rf",
    "enabled": True,
    "event": "bash",
    "pattern": r"rm\s+-rf",       # still present, but ignored
    "conditions": [{"field": "cmd", "operator": "regex_match", "pattern": r"rm\s+-rf"}],  # 'cmd' is not a recognized field
    "action": "block",
}
explicit_rule = Rule.from_dict(explicit_fm, "Blocked!")

engine = RuleEngine()
malicious_input = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /"},
}

legacy_result = engine.evaluate_rules([legacy_rule], malicious_input)
explicit_result = engine.evaluate_rules([explicit_rule], malicious_input)

assert legacy_result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
# Expected to fail: explicit_result should also deny but instead is {} (no match, action allowed)
assert explicit_result == {}, "Explicit-form rule silently failed to block despite action=block and matching pattern present"
```
Expected assertion outcome demonstrating the bug: `legacy_result` blocks the dangerous `rm -rf /` command, while `explicit_result` is `{}` (no block, no warning), proving the two forms diverge in effective security semantics.

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

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
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

**File:** plugins/hookify/core/rule_engine.py (L157-160)
```python
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False
```
