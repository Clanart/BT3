### Title
Fail-open exception handling in hookify's PreToolUse/Stop hook silently disables `block` rules on any error - (File: plugins/hookify/hooks/pretooluse.py)

### Summary
The `hookify` plugin's hook executors (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) wrap rule loading and evaluation in a blanket `try/except Exception`, and unconditionally call `sys.exit(0)` in a `finally` block regardless of what happened. If anything throws during `load_rules()` or `RuleEngine.evaluate_rules()`, the hook never emits the `hookSpecificOutput.permissionDecision: "deny"` payload that a matching `action: block` rule would have produced — it just prints a `systemMessage` and exits 0, which Claude Code's hook contract treats as "allow."

### Finding Description
The intended enforcement contract is defined in `RuleEngine.evaluate_rules()` [1](#0-0) : when a `block` rule matches a `PreToolUse` event, the function returns a dict containing `hookSpecificOutput.permissionDecision: "deny"`, which is the only signal Claude Code uses to actually refuse the tool call.

The hook entrypoint that is supposed to surface this decision, however, treats the entire evaluation as best-effort: [2](#0-1) 

If `load_rules()` or `engine.evaluate_rules()` raises for any reason (a malformed rule file, a corrupted `.claude/hookify.*.local.md`, an OS/permission error reading rule files, or any other unexpected exception unrelated to the specific `block` rule that should have fired), execution jumps to the `except Exception` branch, which only emits a `systemMessage` — never a `hookSpecificOutput`/`permissionDecision: "deny"` object — and the `finally: sys.exit(0)` always runs. This is the same class of bug as the report: the security-relevant computation (rule evaluation) can fail, but the calling code does not check for/propagate that failure into a revert/deny; it silently proceeds as if the decision resolved to "allow." The `_extract_field` transcript-reading path additionally swallows several I/O error types and substitutes empty strings rather than treating them as inputs that should abort a `block` decision [3](#0-2) , which is a smaller instance of the same silent-degrade pattern used by a `Stop` block rule that inspects the transcript.

This is a deliberate design choice documented in the source ("ALWAYS exit 0 - never block operations due to hook errors" / "On any error, allow the operation") [4](#0-3) , but it means a single malformed rule file (which an unprivileged collaborator, plugin author, or an attacker who can drop a file into `.claude/`, can create) silently disables every configured `block` rule for that hook invocation — including unrelated, correctly-formed `block` rules that would otherwise have stopped a dangerous `Bash`/`Write`/`Edit` operation.

### Impact Explanation
`hookify` is explicitly marketed for user-authored guardrails such as blocking `rm -rf`, blocking edits to `.env`/credential files, or requiring tests before stopping [5](#0-4) . Because the exception handler is a catch-all around the entire rule pipeline (load + evaluate) rather than being scoped to only the failing rule, any single broken/edge-case rule file, an I/O hiccup, or an unhandled input shape causes ALL blocking rules in that hook run to be bypassed instead of denied, and the tool call proceeds. This directly undermines the safety guarantee the plugin exists to provide — a `block` rule meant to stop `rm -rf /` or an edit to a `.env` file can be silently defeated, without any error surfaced to the user other than an informational `systemMessage` (which most flows won't distinguish from a normal warning).

### Likelihood Explanation
Triggering the fail-open path only requires causing an exception somewhere in `load_rules()`/`evaluate_rules()` while a `block` rule is configured — e.g., a rule file with unexpected YAML/frontmatter shape, a transient filesystem error, or any future code path that raises before the blocking-rule branch is reached. Since rule files live in a user/project-writable directory (`.claude/hookify.*.local.md`) and are re-read on every hook invocation without restart, this is easy to hit accidentally (a typo in a rule file) and also easy to trigger deliberately if an actor can influence rule file contents.

### Recommendation
Do not let exceptions in rule loading/evaluation collapse straight to "allow." Distinguish infrastructure failures from a no-match result: if any configured rule has `action: block`, a failure to load/evaluate rules should fail closed for that hook invocation (emit `hookSpecificOutput.permissionDecision: "deny"`/exit 2 with a clear "hookify rule evaluation failed" message) rather than silently permitting the operation. At minimum, scope exception handling to per-rule granularity so a single malformed rule file cannot suppress evaluation of all other, valid `block` rules, and make the failure visible/loud (non-zero exit, explicit error in transcript) instead of indistinguishable from "no rule matched."

### Proof of Concept
1. Create `.claude/hookify.block-rm.local.md` with a valid `block` rule: `pattern: rm\s+-rf`, `action: block`, `event: bash`.
2. Create a second rule file, e.g. `.claude/hookify.broken.local.md`, with malformed frontmatter/an unexpected field type that causes `load_rules()` (or a condition inside `evaluate_rules`/`_extract_field`) to throw an exception when parsed (e.g., invalid YAML, or a `conditions` entry with a non-string `pattern` that breaks `compile_regex`).
3. Ask Claude Code to run `rm -rf /tmp/test`.
4. `pretooluse.py` calls `load_rules()`/`evaluate_rules()`; the exception raised while processing the broken rule file causes the `except Exception` branch to run [6](#0-5) , only a `systemMessage` is emitted, and `sys.exit(0)` always fires [7](#0-6) .
5. Because no `hookSpecificOutput.permissionDecision: "deny"` was produced, Claude Code proceeds to execute `rm -rf /tmp/test` even though the correctly configured `block-rm` rule should have stopped it.

Note: I was unable to read `plugins/hookify/core/config_loader.py` directly (tool access issue in this session) to confirm the exact exception types `load_rules()` can raise; the PoC's "malformed rule file" trigger is inferred from the documented YAML/frontmatter-based rule format and the plugin's own error-handling comments, not from a verified stack trace in that file.

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

**File:** plugins/hookify/core/rule_engine.py (L211-225)
```python
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

**File:** plugins/hookify/README.md (L152-167)
```markdown
### Example 1: Block Dangerous Commands

```markdown
---
name: block-destructive-ops
enabled: true
event: bash
pattern: rm\s+-rf|dd\s+if=|mkfs|format
action: block
---

🛑 **Destructive operation detected!**

This command can cause data loss. Operation blocked for safety.
Please verify the exact path and use a safer approach.
```
```
