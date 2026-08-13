### Title
Hookify Guard Rule Engine Field Extraction is Hardcoded to Specific Tool Names, Allowing Blocking Rules to be Silently Bypassed via Any Other Tool - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
The `hookify` plugin lets users define blocking guardrails (e.g. "block `rm -rf`", "deny writes to `.env`") that are evaluated by `RuleEngine._rule_matches()` on every `PreToolUse`/`PostToolUse` event. Just like `GuardCM.checkTransaction()` only inspected calls where `to == owner` (the timelock) and let every other `delegatecall` target through unchecked, hookify's condition evaluator only knows how to extract a matchable value from a small, hardcoded set of tool names (`Bash`, `Write`, `Edit`, `MultiEdit`). Any other tool — including MCP tools, `Task`/subagent dispatch, `NotebookEdit`, or any tool whose input schema uses different field names — causes `_extract_field()` to return `None`, which makes every condition evaluate to `False` and the rule silently not fire, regardless of how dangerous the underlying action is.

### Finding Description
`RuleEngine._rule_matches()` short-circuits to "no match" whenever the extracted field value is `None`: [1](#0-0) 

`_extract_field()` only has explicit handling for `tool_name == 'Bash'` (field `command`), `tool_name in ['Write', 'Edit']` (fields `content`/`new_text`/`old_text`/`file_path`), and `tool_name == 'MultiEdit'`: [2](#0-1) 

The only generic fallback is a direct `tool_input[field]` lookup, which only helps if the alternate tool happens to expose a key with the exact same name the rule's condition targets: [3](#0-2) 

On top of that, the hook executor itself pre-filters which rules are even loaded, based on the same narrow tool-name classification, before the engine runs: [4](#0-3) 

`load_rules()` further filters by `event` (`"bash"`, `"file"`, etc.), and for any tool that isn't Bash/Edit/Write/MultiEdit, `event` is `None`, at which point event-based filtering is skipped entirely, but this doesn't matter — the condition evaluation still fails because of `_extract_field`: [5](#0-4) 

This is the same class of bug as the GuardCM finding: a guard is written to check a specific, enumerated set of targets/tool-shapes rather than checking the *operation* generically, so anything outside that enumerated set sails through unchecked — exactly like `delegatecall` being blocked only when `to == owner` while every other `to` address was left wide open.

### Impact Explanation
A user who relies on hookify's `block` rules to enforce local guardrails (e.g. "never allow writing to `.env`", "never run `rm -rf`", "block credential exfiltration patterns") gets a false sense of protection. The exact same effect — deleting a file, running a destructive shell command, exfiltrating secrets — can be achieved by directing Claude to use a different tool path that isn't Bash/Write/Edit/MultiEdit (for example an MCP filesystem/shell tool, a subagent dispatched via `Task`, or any plugin-provided tool with differently named input fields such as `script`, `cmd`, or `code`). Since `_rule_matches` returns `False` silently — with no warning, no error, no log entry indicating the rule was skipped due to an unsupported tool shape — the operation is allowed to proceed as if no rule existed at all. This directly maps to "hook bypass" in the trust-boundary categories: unprivileged-user-authored protections around dangerous local actions can be defeated with attacker-influenced routing of tool calls (e.g., prompt injection instructing Claude to "use the terminal MCP tool instead of Bash").

### Likelihood Explanation
High likelihood in any environment that mixes hookify with MCP servers, subagents, or any plugin tool beyond the four hardcoded names — which is extremely common given Claude Code's broad plugin/MCP ecosystem. No special privileges are required to trigger the bypass; it only requires steering the agent (via prompt content, including injected/untrusted content in a file or tool output) toward an alternate tool that performs an equivalent action to the blocked one.

### Recommendation
Rework `_extract_field()` (and the `event`-based pre-filtering in `pretooluse.py`/`config_loader.py`) to be schema-agnostic rather than hardcoded to specific tool names:
- When no exact `tool_input[field]` key exists, fall back to scanning all string-valued fields in `tool_input` for the specified pattern instead of returning `None`.
- Stop pre-filtering rule loading by a coarse `event` derived from a fixed tool-name allowlist; load all enabled rules and let `tool_matcher` (which supports full tool-name matching, including `*`) be the single source of truth for tool scoping.
- Fail closed (warn/block) when a `block` rule cannot determine an evaluable field for an in-scope tool, rather than silently treating it as "not matched."

### Proof of Concept
1. Author `.claude/hookify.rm-guard.local.md`:
```yaml
---
name: rm-guard
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: "rm\\s+-rf"
---
Blocked: destructive rm -rf command.
```
2. Verify the guard fires normally for the `Bash` tool with `tool_input.command = "rm -rf /tmp/x"` — `pretooluse.py` sets `event='bash'`, `_extract_field` returns the command string via the `tool_name == 'Bash'` branch [6](#0-5) , and the rule blocks as expected.
3. Now trigger the equivalent destructive action through a tool that isn't `Bash`/`Write`/`Edit`/`MultiEdit` — e.g. an MCP tool named `mcp__shell__exec` whose `tool_input` is `{"script": "rm -rf /tmp/x"}`. `pretooluse.py` computes `event=None` (tool_name doesn't match any branch) [7](#0-6) . `_extract_field` finds no `command` key in `tool_input` and `tool_name` doesn't match any hardcoded branch, so it returns `None` [8](#0-7) , `_check_condition` immediately returns `False` [9](#0-8) , `_rule_matches` returns `False`, and `evaluate_rules` returns `{}` — the destructive command runs with zero warning or blocking, despite an identical action being blocked one step earlier via the `Bash` tool.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L156-160)
```python
        """
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False
```

**File:** plugins/hookify/core/rule_engine.py (L182-254)
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

**File:** plugins/hookify/hooks/pretooluse.py (L41-52)
```python
        # Determine event type for filtering
        # For PreToolUse, we use tool_name to determine "bash" vs "file" event
        tool_name = input_data.get('tool_name', '')

        event = None
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'

        # Load rules
        rules = load_rules(event=event)
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
