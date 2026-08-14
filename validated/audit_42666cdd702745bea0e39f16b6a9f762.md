### Title
Hookify blocking rules silently fail-open on unmatched/empty fields, letting dangerous Bash/Write/Edit operations bypass user-defined `action: block` rules - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
The `hookify` plugin implements a user-defined `PreToolUse`/`Stop` guard system (rules with `action: block`/`warn`) that is supposed to deny dangerous tool calls (e.g. destructive Bash commands, unsafe file writes) before they execute. The analog of the Putty bug is structural: just as a put seller could specify a zero-amount or non-existent-token asset to make `_transferERC20sIn` revert and silently defeat the buyer's exercise right, an attacker-influenceable tool call whose `tool_input` field is missing, empty, or falls outside the small set of hard-coded `tool_name`/`field` branches makes `_extract_field` return `None`, which makes `_check_condition` return `False`, which makes the entire rule silently fail to match — even though the rule was supposed to actively block the operation. There is no fail-closed/"unknown field" handling; the guard just quietly does nothing.

### Finding Description
`RuleEngine.evaluate_rules` iterates configured rules, calls `_rule_matches`, and only produces a `permissionDecision: "deny"` if at least one rule with `action == 'block'` matched; otherwise it returns `{}` (allow) [1](#0-0) .

`_rule_matches` requires every condition to match; if `_check_condition` returns `False` for any condition, the rule is dropped entirely (`return False`) [2](#0-1) .

`_check_condition` treats a `None` field value as "does not match" rather than "cannot evaluate, fail closed": [3](#0-2) 

`_extract_field` only resolves a value in three ways: (1) an exact key match in `tool_input`, (2) a small hard-coded set of `field == 'reason'/'transcript'/'user_prompt'` cases for non-tool events, or (3) a hard-coded per-`tool_name` table limited to `Bash`, `Write`/`Edit`, and `MultiEdit` for specific field aliases (`content`, `new_text`/`new_string`, `old_text`/`old_string`, `file_path`) [4](#0-3) . Anything else — any other tool name (e.g. `Read`, `Glob`, `Grep`, `WebFetch`, MCP tools like `mcp__server__tool`), any renamed/aliased field a rule author configures, or a tool call whose `tool_input` simply omits the expected key with a value that isn't caught by the aliasing (`or`) fallback — causes `_extract_field` to fall through to `return None` at the end of the function.

The practical effect: a rule such as
```yaml
name: block-dangerous-write
event: file
action: block
conditions:
  - field: file_path
    operator: contains
    pattern: ".env"
```
targeting a tool other than `Write`/`Edit`/`MultiEdit` (or one whose `tool_input` key doesn't literally match `field`) never fires, and the operation is silently allowed with `{}` — identical in spirit to the Putty case where a legitimately-configured protection (the option/rule) is defeated by a value shape the enforcement code doesn't handle (zero amount / no-code address vs. missing/aliased field), and the failure is silent rather than surfaced as an error to the user who configured the block rule.

### Impact Explanation
This is a fail-open trust-boundary defect in a user-authored protection mechanism (`.claude/hookify.*.local.md`) that a project owner relies on to block destructive Bash commands or unsafe file edits from being executed by Claude (or by any agent/tool acting on tool_input the user doesn't fully control, e.g. content coming from a prompt-injected source). Because unmatched/absent fields are treated as "rule doesn't apply" instead of "cannot verify, deny or warn," an operation the user explicitly intended to be blocked can execute unimpeded whenever the tool name or field naming falls outside the three hard-coded cases, with no warning emitted to the user (the `{}` return path is a fully silent allow, unlike the `warning_rules` path which at least surfaces a `systemMessage`). This directly undermines the confirmed guarantee of the hook (deny dangerous tool use) with no observable error, which is analogous in class to the referenced report ("seller can silently defeat the exercise mechanism the buyer paid a premium/relied on").

### Likelihood Explanation
Likelihood is limited by the fact that `hookify` is an optional, user-installed plugin, not core `claude-code` policy enforcement, and the bypass requires a rule author to configure conditions against a field/tool combination outside the hard-coded set (a very likely occurrence given the plugin explicitly documents generic `field`/`operator`/`pattern` conditions with no restriction to the three special-cased tools). Any tool other than `Bash`/`Write`/`Edit`/`MultiEdit` — including all MCP tools and `Read`/`Grep`/`Glob`/`WebFetch` — with a rule referencing any field not present verbatim as a `tool_input` key will silently no-op. No special privileges are needed to trigger it; it is simply the default behavior of the shipped rule engine.

### Recommendation
Change the field-resolution/condition-evaluation contract from "unmatched → allow" to "unmatched → fail safe":
1. In `_extract_field`, distinguish "field truly not applicable to this event" from "field expected but absent" and log/warn when a configured field cannot be resolved for the given `tool_name`.
2. In `_check_condition`, when `field_value is None` and the rule's `action == 'block'`, consider defaulting to a conservative outcome (e.g., emit a warning surfaced to the user, or treat unresolved required fields as a match for `block` rules) rather than silently treating it as "no match."
3. Extend the per-tool field table to cover generic tools (arbitrary `tool_input` keys, MCP tool inputs) instead of hard-coding only `Bash`/`Write`/`Edit`/`MultiEdit`, and add a fallback that raises a clear diagnostic to the user (via `systemMessage`) when a rule's configured `field` never resolves for any observed event, so misconfigured/ineffective block rules are not silently inert.

### Proof of Concept
1. Install the `hookify` plugin and add `.claude/hookify.block-mcp-write.local.md`:
```markdown
---
name: block-mcp-secret-write
enabled: true
event: all
tool_matcher: "mcp__myserver__writeFile"
action: block
conditions:
  - field: path
    operator: contains
    pattern: "/secrets/"
---
Blocked: writing to secrets directory via MCP tool.
```
2. Trigger a `PreToolUse` call for `mcp__myserver__writeFile` with `tool_input: {"path": "/secrets/creds.json", "data": "..."}`.
3. Trace: `_rule_matches` → `tool_matcher` matches → `_check_condition(field="path", ...)` → `_extract_field("path", "mcp__myserver__writeFile", {"path": "...", ...}, ...)`.
4. Because `tool_name` is not `Bash`/`Write`/`Edit`/`MultiEdit`, and `field` ("path") is a key literally present in `tool_input`, this specific example would actually match via the first branch — but any rule author who names the condition field differently from the exact JSON key (e.g. `field: file_path` for a tool whose schema uses `path`, or `field: destination` for a tool whose schema uses `target`), or targets a tool with no key at all matching, hits the terminal `return None` in `_extract_field` at line 254 [5](#0-4) , causing `_check_condition` to return `False` and the block rule to never fire, so `evaluate_rules` returns `{}` (silent allow) even though the intended protection was `action: block` [6](#0-5) .

### Citations

**File:** plugins/hookify/core/rule_engine.py (L53-94)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)

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
            else:
                # For other events, just show message
                return {
                    "systemMessage": combined_message
                }

        # If only warnings, show them but allow operation
        if warning_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }

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

**File:** plugins/hookify/core/rule_engine.py (L157-161)
```python
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False

```

**File:** plugins/hookify/core/rule_engine.py (L195-254)
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
