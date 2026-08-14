### Title
Hookify PreToolUse Block Rules Silently Never Match `old_text`/`old_string` Conditions on `MultiEdit`, Letting Unauthorized Edits Execute Unblocked - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
The `hookify` plugin implements a local security-gate for tool calls: a `PreToolUse` hook (`plugins/hookify/hooks/pretooluse.py`) loads user-authored rules and, via `RuleEngine.evaluate_rules`, returns `permissionDecision: "deny"` when a rule's conditions match the incoming tool call, blocking the action before it executes [1](#0-0) . Just as the Nayms Diamond upgrade authorized a `diamondCut` based only on a hash of the `_diamondCut` parameter while leaving `_init`/`_calldata` — the parameters that actually perform the state change — unchecked, `hookify`'s rule matcher validates conditions against only a subset of the fields that determine a tool call's actual effect. For the `MultiEdit` tool specifically, the field extractor never resolves `old_text`/`old_string`, so any rule written to gate on that field silently fails to match, and the edit executes with no block, regardless of how dangerous its content is.

### Finding Description
`RuleEngine._extract_field` resolves the field a `Condition` checks against, dispatching on `tool_name`. For `MultiEdit`, only `file_path` and `new_text`/`content` (concatenated `new_string` values) are handled [2](#0-1) . There is no branch handling `old_text` or `old_string` for `MultiEdit` (contrast with `Edit`/`Write`, which do resolve `old_text`/`old_string` [3](#0-2) ). When no branch matches, execution falls through to `return None` [4](#0-3) .

`_check_condition` treats a `None` field value as "no match" and returns `False` immediately, without raising or warning [5](#0-4) . `_rule_matches` requires **all** conditions of a rule to be true; a single `False` condition makes the entire rule not fire [6](#0-5) . Consequently, a rule such as:

```yaml
event: file
tool_matcher: MultiEdit
conditions:
  - field: old_string
    operator: contains
    pattern: "SECURITY_CHECK"
action: block
```

authored via the documented `writing-rules` skill [7](#0-6)  — intended to block a `MultiEdit` that removes a security check by inspecting `old_string` — never matches, because `_extract_field` returns `None` for that field/tool combination, and the rule is silently disabled. The `PreToolUse` hook then emits an empty result (`{}`), which is treated as "no matches — allow operation" [8](#0-7) , and the tool executes with `permissionDecision` left unset (implicit allow).

This mirrors the Diamond bug's root cause: the guard mechanism is scoped to validate only part of the parameters that fully determine the action's effect (here, `new_string`/`file_path` are checked but `old_string` — often the field carrying the sensitive content being removed or bypassed — is not), so an attacker (or the model, if prompt-injected) can craft a `MultiEdit` call whose dangerous content lives exclusively in the unchecked `old_string`/`old_text` field and sail through the block rule.

### Impact Explanation
Any user or team relying on `hookify` block rules that target `old_text`/`old_string` conditions on `MultiEdit` operations gets a false sense of security: the intended guardrail (e.g., "block edits that remove `SECURITY_CHECK`/credential-masking/dangerous-flag-removal code") never fires for `MultiEdit`, silently permitting the exact class of edit the rule was written to stop. Because this is a locally-installed, unprivileged plugin acting as a `PreToolUse` gate (not a remote/multi-party trust boundary), impact is a local guard/hook bypass allowing an unauthorized file modification to proceed unblocked in the user's own project — consistent with the "hook bypass" trust-boundary category.

### Likelihood Explanation
This triggers deterministically and silently whenever a hookify rule author scopes a condition's `field` to `old_text`/`old_string` for the `MultiEdit` tool matcher — a documented, supported field name per the plugin's own skill docs — with no error or warning surfaced anywhere in the pipeline (`pretooluse.py` swallows exceptions and always exits 0 regardless) [9](#0-8) . Any rule author who assumes parity between `Edit`/`Write` and `MultiEdit` field coverage (a reasonable assumption given the shared `event: file` grouping) will unknowingly ship a dead rule.

### Recommendation
Add explicit `old_text`/`old_string` handling for `MultiEdit` in `_extract_field`, concatenating each edit's `old_string` analogous to the existing `new_string` concatenation, and add unit/regression tests asserting that every field name documented in the `writing-rules` skill resolves to a non-`None` value for every tool type it claims to support. Additionally, consider having `_check_condition` distinguish "field intentionally absent" from "field not implemented for this tool" (e.g., log a warning) so silently-dead rules are surfaced rather than failing open.

### Proof of Concept
1. Author `.claude/hookify.block-security-removal.local.md`:
```markdown
---
name: block-security-removal
enabled: true
event: file
tool_matcher: MultiEdit
action: block
conditions:
  - field: old_string
    operator: contains
    pattern: "verify_signature"
---
Blocks edits removing signature verification.
```
2. Invoke `MultiEdit` with `edits: [{old_string: "if not verify_signature(x): raise", new_string: ""}]` on a file — content that should be blocked.
3. `RuleEngine._extract_field('old_string', 'MultiEdit', tool_input)` returns `None` (no matching branch), `_check_condition` returns `False`, `_rule_matches` returns `False`, `evaluate_rules` returns `{}`, and `pretooluse.py` outputs `{}` — the `MultiEdit` proceeds unblocked despite the rule's intent, confirmed by tracing `plugins/hookify/core/rule_engine.py` lines 246–254 and 96–125 against `plugins/hookify/hooks/pretooluse.py` lines 35–59.

### Citations

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

**File:** plugins/hookify/core/rule_engine.py (L93-94)
```python
        # No matches - allow operation
        return {}
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

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L41-56)
```markdown
**event** (required): Which hook event to trigger on
- `bash`: Bash tool commands
- `file`: Edit, Write, MultiEdit tools
- `stop`: When agent wants to stop
- `prompt`: When user submits a prompt
- `all`: All events

**action** (optional): What to do when rule matches
- `warn`: Show message but allow operation (default)
- `block`: Prevent operation (PreToolUse) or stop session (Stop events)
- If omitted, defaults to `warn`

**pattern** (simple format): Regex pattern to match
- Used for simple single-condition rules
- Matches against command (bash) or new_text (file)
- Python regex syntax
```

**File:** plugins/hookify/hooks/pretooluse.py (L61-70)
```python
    except Exception as e:
        # On any error, allow the operation and log
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0 - never block operations due to hook errors
        sys.exit(0)
```
