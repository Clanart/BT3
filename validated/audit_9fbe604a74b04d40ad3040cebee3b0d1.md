### Title
Hookify PreToolUse Rule Engine Trusts Tool-Specific Field Mapping Instead of the Actually-Modified Content, Letting `MultiEdit` Bypass `old_text`/`new_text`-Based Blocking Rules - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
The Abra NFT bug involved a value declared by an untrusted party (the borrower's chosen oracle) never being validated against the value the counterparty (the lender) actually relied on, letting the untrusted party route around the intended check. The analogous pattern in `hookify` is that the plugin's PreToolUse rule engine documents `old_text`/`new_text`/`content` as valid guard fields for any file-modifying tool [1](#0-0) , but `RuleEngine._extract_field` only wires those field names up for `Write`/`Edit`, not for `MultiEdit` [2](#0-1) . A rule author "agrees" to be protected by a field-based guard, but the engine silently fails to check that field for one of the three tools the guard claims to cover.

### Finding Description
`writing-rules/SKILL.md` explicitly tells rule authors that `file` events (covering `Edit`, `Write`, `MultiEdit` per the same doc [3](#0-2) ) can be guarded using the fields `file_path`, `new_text`, `old_text`, `content` [4](#0-3) .

`RuleEngine._rule_matches` requires every condition to resolve to a real value; if `_extract_field` returns `None`, `_check_condition` returns `False` and the rule silently does not match, i.e. does not block [5](#0-4) .

Looking at `_extract_field`'s per-tool branches:
- `Write`/`Edit` handles `content`, `new_text`/`new_string`, `old_text`/`old_string`, and `file_path` [6](#0-5) .
- `MultiEdit` only handles `file_path` and `new_text`/`content` (concatenated across `edits`) — there is **no** handling of `old_text`/`old_string` for `MultiEdit` at all [7](#0-6) .

So a rule such as:
```yaml
event: file
conditions:
  - field: old_text
    operator: contains
    pattern: "DO NOT REMOVE"
```
correctly blocks an `Edit` that deletes the protected marker (matched via `old_string`), but the exact same removal performed through `MultiEdit` never resolves `old_text`, `_extract_field` returns `None`, and the condition — and therefore the whole rule — never matches. The operation is silently allowed with no warning, no error, and no indication to the rule author that the guard field they were told exists doesn't apply to `MultiEdit`.

This mirrors the oracle report's root cause exactly: the guard's protection is scoped by a value (`old_text`) that the enforcement point never actually validates for one of the code paths that reaches the guarded resource, so any actor able to choose which of the "equivalent" tools to invoke (here: Claude itself, potentially steered via prompt injection in file content, or a user simply asking for `MultiEdit`) can route around the intended block, just as the borrower in the report could route around the lender's expected oracle by supplying a different accepted value at the entry point.

### Impact Explanation
`hookify` is explicitly documented as a security/guardrail mechanism — its rules exist to block dangerous or unwanted edits before they execute (e.g., protecting markers, secrets, or invariants in files) [8](#0-7) . Because `MultiEdit` silently bypasses `old_text`-based conditions, any content-removal guard written against `old_text` is ineffective the moment the invoking agent (which could be following prompt-injected instructions from untrusted file/tool content) uses `MultiEdit` instead of `Edit` to perform the same edit. This is a concrete hook/guardrail bypass in the local, project-owned trust boundary (the PreToolUse enforcement point a user configured to restrict Claude's file edits), not a purely cosmetic gap — a blocking rule that a user relies on to prevent specific destructive edits can be defeated without any error signal.

### Likelihood Explanation
The bypass requires no special privilege — it triggers whenever Claude (potentially guided by adversarial content it is instructed to act on, e.g. prompt injection embedded in a file being edited) chooses `MultiEdit` rather than `Edit`/`Write` to perform the same content change. Since `MultiEdit` is a normal, commonly-used built-in tool and nothing hints to the model that it should avoid it, this is easily and unintentionally triggered, and just as easily exploitable deliberately by an attacker who can influence which tool gets called (e.g., via a malicious file whose contents instruct the agent to "use MultiEdit to remove the marker").

### Recommendation
In `RuleEngine._extract_field`, add `old_text`/`old_string` handling for the `MultiEdit` branch, mirroring the `new_text`/`content` aggregation already done for that tool (e.g., concatenate `edit.get('old_string', '')` across `tool_input.get('edits', [])`), so that guard rules referencing `old_text` are enforced consistently across `Edit`, `Write`, and `MultiEdit`. More generally, the engine should either (a) explicitly enumerate every field it supports per tool and treat unsupported-but-documented fields as a configuration error surfaced to the user (fail loud), or (b) unify field extraction so any field applicable to a tool family (all file-editing tools) is derived from a single normalized representation of "old content" / "new content" / "path", eliminating per-tool ad-hoc branches that can drift out of sync with documentation.

### Proof of Concept
1. Create `.claude/hookify.protect-marker.local.md`:
```markdown
---
name: protect-marker
enabled: true
event: file
action: block
conditions:
  - field: old_text
    operator: contains
    pattern: "DO_NOT_REMOVE_MARKER"
---
Blocked: attempted removal of protected marker.
```
2. Ask Claude to edit a file containing `DO_NOT_REMOVE_MARKER` using the `Edit` tool to delete that line — the `PreToolUse` hook correctly returns `permissionDecision: deny` because `_extract_field` resolves `old_text` from `tool_input['old_string']` for `Edit` [6](#0-5) .
3. Ask Claude to perform the identical deletion using the `MultiEdit` tool instead (a single edit entry with `old_string` containing the marker and `new_string` removing it). `_extract_field` has no `old_text`/`old_string` case for `MultiEdit` [7](#0-6) , so it returns `None`, `_check_condition` returns `False` [9](#0-8) , the rule doesn't match, `evaluate_rules` returns `{}` [10](#0-9) , and the edit proceeds unblocked despite the guard rule explicitly targeting that exact content pattern.

### Citations

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L41-43)
```markdown
**event** (required): Which hook event to trigger on
- `bash`: Bash tool commands
- `file`: Edit, Write, MultiEdit tools
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L48-51)
```markdown
**action** (optional): What to do when rule matches
- `warn`: Show message but allow operation (default)
- `block`: Prevent operation (PreToolUse) or stop session (Stop events)
- If omitted, defaults to `warn`
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L85-88)
```markdown
**Condition fields:**
- `field`: Which field to check
  - For bash: `command`
  - For file: `file_path`, `new_text`, `old_text`, `content`
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L368-371)
```markdown
**Field options:**
- Bash: `command`
- File: `file_path`, `new_text`, `old_text`, `content`
- Prompt: `user_prompt`
```

**File:** plugins/hookify/core/rule_engine.py (L93-94)
```python
        # No matches - allow operation
        return {}
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
