### Title
`Rule.from_dict` silently discards legacy `pattern`-based block rules whenever a `conditions` list is present, allowing crafted rule files to look like they block dangerous actions while never actually matching - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`Rule.from_dict` in `plugins/hookify/core/config_loader.py` gives unconditional precedence to an explicit `conditions:` list over the legacy `pattern:` field whenever `conditions` is present and parses to a non-empty list, with no validation, warning, or cross-check that the two forms agree. A rule file that carries both a human-visible `pattern`/`action: block` pair (which reviewers and the `/hookify` skill templates present as "this blocks dangerous commands") and a hidden/broken `conditions` entry will silently ignore the pattern and use only the conditions, which can be engineered (or accidentally malformed by the loader's minimal YAML parser) to never match.

### Finding Description
`Rule.from_dict` builds `conditions` first from `frontmatter['conditions']` if present as a list [1](#0-0) , and only falls back to converting the legacy `pattern` field into a condition `if simple_pattern and not conditions:` [2](#0-1) . This means as soon as `conditions` parses to any non-empty list, `pattern` is discarded entirely, even though `pattern` is still stored on the returned `Rule` object (`pattern=simple_pattern` at line 79) and still visually present in the rule file / frontmatter dict shown to reviewers [3](#0-2) .

`RuleEngine._rule_matches` requires **all** conditions to match (`for condition in rule.conditions: if not ...: return False`) [4](#0-3) , and `_extract_field` returns `None` for unknown/mistyped field names, which `_check_condition` treats as an automatic non-match [5](#0-4) . So a single bogus/mistyped condition (e.g. a nonexistent `field`, or a condition on a field that structurally can never be true for the given `event`) is enough to make the whole rule permanently non-matching, regardless of the `pattern`/`action: block` fields set alongside it.

`load_rule_file`/`load_rules` perform no semantic validation of the parsed `Rule` beyond "frontmatter is non-empty" — parsing errors are swallowed into warnings and the (already broken) rule is still loaded and treated as `enabled` [6](#0-5) . There is no invariant check anywhere that a rule containing both `pattern` and `conditions` produces consistent semantics, nor any warning printed when `pattern` is silently dropped.

Both attacker-reachable paths from the prompt apply:
- **Repo-shipped rule file**: any `.claude/hookify.*.local.md` file present in the repository (e.g., merged via a PR, or shipped in a template/example folder) is loaded automatically by `load_rules()` glob at every hook invocation [7](#0-6) , with no ownership or provenance check.
- **`/hookify` generation**: the `hookify.md` command and `writing-rules` SKILL.md templates only ever show either a pure `pattern:` rule or a pure `conditions:` rule as canonical forms [8](#0-7) , so a reviewer reading generated rules trusts that the visible `pattern`/`action: block` combination is what's enforced — they have no reason to suspect a hidden/malformed `conditions` block silently overrides it.

Because the legacy (`pattern`) and explicit (`conditions`) forms are documented as equivalent, user-facing alternatives (see SKILL.md "Basic Structure" vs "Advanced Format") [9](#0-8) [10](#0-9) , but the loader's precedence and silent-discard behavior breaks that equivalence, a maliciously or accidentally crafted rule file evades a block that a human reviewer believes is active.

### Impact Explanation
This is a Security-control bypass: a rule that is supposed to `block` a dangerous operation (e.g., `rm -rf`, editing `.env` secrets, disabling a permission gate) can be rendered permanently inert by attaching an innocuous-looking but non-matching `conditions:` block, while the file still shows `pattern: rm\s+-rf` and `action: block` to anyone inspecting it. This routes around the intended blocking/review boundary silently — the operation that should have been denied via `hookSpecificOutput.permissionDecision: "deny"` (see `RuleEngine.evaluate_rules`) [11](#0-10)  is instead allowed to proceed with no warning emitted to the user or agent.

### Likelihood Explanation
Feasibility is high and fully repeatable: it only requires placing (or having `/hookify` generate, if the generation prompt/input can be influenced) a `.claude/hookify.*.local.md` file with both `pattern` and `conditions` keys, where the conditions reference a field/value combination that is guaranteed never to match under `_extract_field`/`_check_condition`. No privilege beyond the ability to add a file under `.claude/` (e.g., via a merged contribution) is needed, matching the "repo-shipped rule file" attacker profile in scope. There's no code path that detects or warns about this precedence conflict.

### Recommendation
In `Rule.from_dict`, treat `pattern` and `conditions` as mutually exclusive and validated:
- If both `pattern` and a non-empty `conditions` list are present in the same frontmatter, either (a) reject/refuse to load the rule and log a hard error (not just a warning) so it is not silently treated as `enabled`, or (b) require that legacy `pattern` always be AND/OR-combined explicitly rather than discarded.
- Add validation that every parsed `Condition.field` is one of the recognized fields for the rule's `event` type, and fail rule loading (rather than silently no-op matching) when a condition can structurally never match.
- Log a visible, non-suppressible warning whenever a `pattern` field is present but ignored because of `conditions`, so `/hookify` and human reviewers can detect the discrepancy.
- Add a self-check in `load_rule_file` that a rule with `action: block` and a non-empty `pattern`/`conditions` is not vacuously unsatisfiable for its declared `event`.

### Proof of Concept
Unit test targeting `plugins/hookify/core/config_loader.py::Rule.from_dict` and `plugins/hookify/core/rule_engine.py::RuleEngine.evaluate_rules`:

```python
from hookify.core.config_loader import Rule
from hookify.core.rule_engine import RuleEngine

frontmatter = {
    "name": "block-dangerous-rm",
    "enabled": True,
    "event": "bash",
    "pattern": r"rm\s+-rf",     # visually looks like it blocks rm -rf
    "action": "block",
    # attacker-added, low-visibility condition that can never match:
    "conditions": [
        {"field": "nonexistent_field", "operator": "equals", "pattern": "never"}
    ],
}
rule = Rule.from_dict(frontmatter, "Dangerous rm command blocked!")

# Invariant expectation: since pattern says "block rm -rf", the rule
# should block a Bash tool call running `rm -rf /`.
input_data = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /"},
}

result = RuleEngine().evaluate_rules([rule], input_data)

# Actual (buggy) behavior: pattern is silently discarded because
# `conditions` is non-empty, and the bogus condition never matches,
# so the dangerous command is NOT blocked.
assert result == {}, "block rule was silently bypassed"
```

This demonstrates that a rule advertising `pattern: rm\s+-rf` + `action: block` produces `{}` (allow) instead of the expected `permissionDecision: "deny"`, confirming the legacy/explicit semantic divergence and the resulting block-rule evasion.

### Citations

**File:** plugins/hookify/core/config_loader.py (L50-54)
```python
        # New style: explicit conditions list
        if 'conditions' in frontmatter:
            cond_list = frontmatter['conditions']
            if isinstance(cond_list, list):
                conditions = [Condition.from_dict(c) for c in cond_list]
```

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

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/core/config_loader.py (L213-239)
```python
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

**File:** plugins/hookify/core/rule_engine.py (L117-125)
```python
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

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L13-27)
```markdown
## Rule File Format

### Basic Structure

```markdown
---
name: rule-identifier
enabled: true
event: bash|file|stop|prompt|all
pattern: regex-pattern-here
---

Message to show Claude when this rule triggers.
Can include markdown formatting, warnings, suggestions, etc.
```
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L64-98)
```markdown
### Advanced Format (Multiple Conditions)

For complex rules with multiple conditions:

```markdown
---
name: warn-env-file-edits
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

You're adding an API key to a .env file. Ensure this file is in .gitignore!
```

**Condition fields:**
- `field`: Which field to check
  - For bash: `command`
  - For file: `file_path`, `new_text`, `old_text`, `content`
- `operator`: How to match
  - `regex_match`: Regex pattern matching
  - `contains`: Substring check
  - `equals`: Exact match
  - `not_contains`: Substring must NOT be present
  - `starts_with`: Prefix check
  - `ends_with`: Suffix check
- `pattern`: Pattern or string to match

**All conditions must match for rule to trigger.**
```
