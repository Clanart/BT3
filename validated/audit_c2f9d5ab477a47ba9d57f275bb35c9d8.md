### Title
Hookify's PreToolUse/PostToolUse hooks fail open on any rule-engine exception, silently allowing operations the user configured to be blocked - (File: plugins/hookify/hooks/pretooluse.py)

### Summary
The `hookify` plugin lets a user define `action: block` rules (e.g. "block-dangerous-rm", "block-chmod-777") that are supposed to prevent Claude Code from executing dangerous Bash commands or file edits. The executor scripts wrap the entire rule-loading/evaluation path in a blanket `try/except Exception`, and on *any* failure they emit only a `systemMessage` and unconditionally `sys.exit(0)`. Since Claude Code interprets a `PreToolUse` hook exit of `0` with no `permissionDecision: "deny"` as "allow," any exception in rule loading or evaluation causes the configured block rule to be silently skipped — the tool call proceeds even though the user's guardrail said it should be denied. This is directly analogous to the Gnosis wallet's `external_call`, which returns success even when the destination call actually failed, misleading the caller about the true outcome.

### Finding Description
`plugins/hookify/hooks/pretooluse.py` is registered for every `PreToolUse` event [1](#0-0) . It imports `load_rules` and `RuleEngine`, and on `ImportError` it already fails open, printing a message and exiting 0 without ever considering the user's rules [2](#0-1) .

Inside `main()`, all of stdin parsing, `load_rules(event=event)`, and `engine.evaluate_rules(rules, input_data)` are wrapped by a single `except Exception` that, on any error, produces `{"systemMessage": f"Hookify error: {str(e)}"}` — never a `hookSpecificOutput.permissionDecision: "deny"` — and the `finally` block force-exits with code 0 regardless of what happened, explicitly commented "ALWAYS exit 0 - never block operations due to hook errors" [3](#0-2) .

The only mechanism by which hookify can actually deny a tool call is `RuleEngine.evaluate_rules` returning `hookSpecificOutput.permissionDecision: "deny"` when a `block` rule matches [4](#0-3) . Because this return value is only produced along the success path, any exception thrown before reaching that `return` statement (e.g., a malformed `.claude/hookify.*.local.md` rule file causing a YAML/frontmatter parse error in `config_loader.load_rules`, a `UnicodeDecodeError` on read, or any other unexpected exception in `_check_condition`/`_extract_field`) causes the hook to degrade to "allow" while telling the user only a generic `systemMessage`, not that enforcement was skipped. This mirrors the smart-contract bug class precisely: a low-level failure path is not distinguished from success, so the caller is misled into believing normal (enforced) behavior occurred.

Note: I could not fully inspect `plugins/hookify/core/config_loader.py` in this session (file read failed due to a tool parameter error), so the exact set of exception-triggering inputs (e.g. malformed frontmatter, bad YAML, non-UTF8 files) is inferred from the documented rule-file format rather than confirmed line-by-line against `config_loader.py`'s implementation.

### Impact Explanation
A user who has explicitly configured hookify to `block` a dangerous action (per the plugin's own documented use case: "Block Dangerous Commands" like `rm -rf`, `dd if=`, `mkfs`, `chmod 777`, or edits to `.env`/credentials files) can have that guardrail silently bypassed whenever the rule engine hits any exception. The command still executes with no enforcement, and the user is not informed enforcement failed — only a generic "Hookify error: ..." system message appears alongside proceeding (in `PreToolUse`, even that message is attached to an implicit-allow response). This is a real unauthorized-action/approval-bypass condition within Claude Code's own hook/guardrail trust boundary, not a hypothetical: this plugin is explicitly designed by the vendor to prevent exactly this class of destructive Bash command.

### Likelihood Explanation
Likelihood is moderate: it requires a rule file or evaluation path that throws an exception not otherwise caught (e.g., a hand-edited or malformed `.claude/hookify.*.local.md`, an environment issue affecting `config_loader`, or any bug in condition extraction). Given hookify rules are simple user-edited markdown/YAML files (per `plugins/hookify/README.md` format) [5](#0-4) , malformed frontmatter is a plausible and easy-to-hit scenario, especially since the plugin's own `/hookify` and `/hookify:configure` commands write and toggle these files programmatically.

### Recommendation
- **Short term:** Change the exception handler in `pretooluse.py` (and `posttooluse.py`) to fail closed for `block`-capable events: if rule loading/evaluation cannot be completed reliably, return `hookSpecificOutput.permissionDecision: "ask"` (or `"deny"` with a clear reason) instead of silently allowing, and surface a prominent, unambiguous message that guardrail enforcement failed rather than a generic "Hookify error."
- **Long term:** Validate `.claude/hookify.*.local.md` rule files at write time (`/hookify`, `/hookify:configure`) and at load time, isolate parsing of each rule file so a single malformed file can't disable evaluation of all other rules, and document clearly (in `README.md`/`writing-rules/SKILL.md`) that hookify enforcement can silently no-op on internal errors, so users relying on it for safety-critical blocking rules are aware of the limitation.

### Proof of Concept
1. Create `.claude/hookify.block-rm.local.md` with `action: block` targeting `rm\s+-rf` (per documented format) [6](#0-5) .
2. Introduce a condition/input that causes `load_rules` or `RuleEngine._rule_matches`/`_extract_field` to raise an uncaught exception (e.g., a rule file with malformed YAML frontmatter, or a `None`/non-string field value not defensively handled outside the explicitly caught `re.error` in `_regex_match`) [7](#0-6) .
3. Run `rm -rf /tmp/test` via the Bash tool.
4. Observe that `pretooluse.py`'s `except Exception` branch fires, prints only `{"systemMessage": "Hookify error: ..."}`, and `sys.exit(0)` is reached in `finally` [8](#0-7)  — no `permissionDecision: "deny"` is emitted, so Claude Code proceeds to execute the destructive command the user explicitly configured to be blocked.

### Citations

**File:** plugins/hookify/hooks/hooks.json (L4-13)
```json
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
            "timeout": 10
          }
        ]
      }
```

**File:** plugins/hookify/hooks/pretooluse.py (L25-32)
```python
try:
    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine
except ImportError as e:
    # If imports fail, allow operation and log error
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)
```

**File:** plugins/hookify/hooks/pretooluse.py (L35-70)
```python
def main():
    """Main entry point for PreToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

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

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

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

**File:** plugins/hookify/core/rule_engine.py (L256-273)
```python
    def _regex_match(self, pattern: str, text: str) -> bool:
        """Check if pattern matches text using regex.

        Args:
            pattern: Regex pattern
            text: Text to match against

        Returns:
            True if pattern matches
        """
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            return bool(regex.search(text))

        except re.error as e:
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False
```

**File:** plugins/hookify/README.md (L73-120)
```markdown
### Simple Rule (Single Pattern)

`.claude/hookify.dangerous-rm.local.md`:
```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---

⚠️ **Dangerous rm command detected!**

This command could delete important files. Please:
- Verify the path is correct
- Consider using a safer approach
- Make sure you have backups
```

**Action field:**
- `warn`: Shows warning but allows operation (default)
- `block`: Prevents operation from executing (PreToolUse) or stops session (Stop events)

### Advanced Rule (Multiple Conditions)

`.claude/hookify.sensitive-files.local.md`:
```markdown
---
name: warn-sensitive-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$|credentials|secrets
  - field: new_text
    operator: contains
    pattern: KEY
---

🔐 **Sensitive file edit detected!**

Ensure credentials are not hardcoded and file is in .gitignore.
```

**All conditions must match** for the rule to trigger.
```
