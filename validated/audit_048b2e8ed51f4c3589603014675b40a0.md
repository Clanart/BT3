## Title
Hookify security rules using the `old_text` field silently never match for `MultiEdit`, letting the identical edit bypass a configured block rule - (`plugins/hookify/core/rule_engine.py`)

### Summary
Hookify lets users write `PreToolUse` rules that block dangerous edits by matching on a field of the tool call (e.g. `old_text`, the content being removed). The rule engine's field extractor supports `old_text`/`old_string` for the `Edit`/`Write` tools but has no equivalent case for the `MultiEdit` tool, even though the skill's own documentation lists `old_text` as a valid field for the `file` event, which explicitly covers `Edit, Write, MultiEdit`. This mirrors the Superform bug class: an authorization/blocking gate inspects only a subset of the call's arguments, and the excluded argument is exactly the one an attacker (or a prompt-injected instruction) needs to control to defeat the gate — performing the exact same operation through a different code path that isn't inspected.

### Finding Description
`RuleEngine._extract_field` special-cases each tool to pull out the field a condition checks: [1](#0-0) 

For `Write`/`Edit` it returns `old_text`/`old_string` via `tool_input.get('old_string', '')`, but the `MultiEdit` branch only extracts `file_path` and `new_text`/`content` (concatenated from `edits[].new_string`). There is no case for `old_text`/`old_string` on `MultiEdit`. Since `MultiEdit`'s `tool_input` doesn't have a top-level `old_string` key (it's nested per-edit under `edits[].old_string`), the earlier "direct tool_input fields" check at line 196 also fails to find it. Consequently `_extract_field` falls through and returns `None`.

Back in `_check_condition`, a `None` field value causes an immediate `return False`: [2](#0-1) 

And `_rule_matches` requires every condition to match: [3](#0-2) 

So any rule with a condition on `old_text`/`old_string` for the `file` event (which is documented to cover `Edit, Write, MultiEdit`) will always evaluate to `False` for `MultiEdit` calls, regardless of what content is actually being replaced. The skill documentation explicitly tells users `old_text` is a valid field for `file` events without carving out an exception for `MultiEdit`: [4](#0-3) [5](#0-4) 

This is the same root-cause shape as the Superform report: a whitelisting/authorization check that is supposed to gate an operation based on the operation's arguments silently omits an argument for one variant of the operation, and that omitted argument is precisely the one that determines whether the operation should be blocked.

### Impact Explanation
A user (or an org via `.claude/hookify.*.local.md`) may write a `block` rule intended to prevent the agent from removing security-relevant code — e.g. blocking any edit whose `old_text` matches `verify_signature\(|authenticate\(|check_permission\(` to stop a prompt-injected or compromised session from silently stripping auth checks. Because `MultiEdit` bypasses the `old_text` condition entirely, the exact same removal succeeds unblocked as long as it's issued via `MultiEdit` instead of `Edit`. Since Claude routinely chooses `MultiEdit` over `Edit` for multi-location changes (and can be steered to prefer it via prompt injection in untrusted file content), this converts an intended hard block into a rule that is trivially bypassed without any error, warning, or indication to the user that the guardrail was skipped — a silent hook-bypass in a security-relevant control path.

### Likelihood Explanation
No special privileges are needed: any project or user that defines an `old_text`-based hookify block rule is affected, and the bypass requires only that the tool used for the edit be `MultiEdit` rather than `Edit`/`Write` — something entirely within the agent's normal tool-choice discretion (and thus influenceable by malicious content the agent reads, e.g., prompt injection in a file or fetched content instructing it to "use MultiEdit"). This makes the bypass easy to trigger, likely to occur without any adversarial effort at all (Claude often prefers `MultiEdit` for efficiency), and undetectable to the user since no error or log entry is produced.

### Recommendation
Add an `old_text`/`old_string` case to the `MultiEdit` branch of `_extract_field`, concatenating `old_string` across all `edits[]` entries the same way `new_text`/`content` is already handled:
```python
elif tool_name == 'MultiEdit':
    if field == 'file_path':
        return tool_input.get('file_path', '')
    elif field in ['new_text', 'content']:
        edits = tool_input.get('edits', [])
        return ' '.join(e.get('new_string', '') for e in edits)
    elif field in ['old_text', 'old_string']:
        edits = tool_input.get('edits', [])
        return ' '.join(e.get('old_string', '') for e in edits)
```
More generally, treat any field documented as valid for an `event` type as required to be extracted for every tool that event type claims to cover, and add a test asserting parity between `Edit`/`Write` and `MultiEdit` field extraction so future tool additions can't silently reopen this gap.

### Proof of Concept
1. Add `.claude/hookify.block-auth-removal.local.md`:
```markdown
---
name: block-auth-removal
enabled: true
event: file
action: block
conditions:
  - field: old_text
    operator: contains
    pattern: verify_signature(
---
Blocked: removal of signature verification code.
```
2. Ask Claude to remove a `verify_signature(...)` call from a file using the `Edit` tool — the rule triggers, `RuleEngine.evaluate_rules` returns `permissionDecision: deny`, and the edit is blocked (`plugins/hookify/core/rule_engine.py:72-79`).
3. Ask Claude (or have injected content instruct it) to perform the identical removal via `MultiEdit` with one entry `{"old_string": "verify_signature(user, sig)\n", "new_string": ""}`. `_extract_field` returns `None` for `old_text` on `MultiEdit` (`plugins/hookify/core/rule_engine.py:246-252`), `_check_condition` short-circuits to `False` (`rule_engine.py:159-160`), `_rule_matches` returns `False`, and the edit proceeds unblocked — demonstrating the same security-relevant change silently bypasses the configured hook depending solely on which tool variant is used.

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

**File:** plugins/hookify/core/rule_engine.py (L230-252)
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
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L85-98)
```markdown
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

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L368-371)
```markdown
**Field options:**
- Bash: `command`
- File: `file_path`, `new_text`, `old_text`, `content`
- Prompt: `user_prompt`
```
