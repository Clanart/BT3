### Title
Hookify PreToolUse hook fails open on any exception, silently disabling user-defined "block" rules for dangerous commands - (File: plugins/hookify/hooks/pretooluse.py)

### Summary
The Sherlock report describes `VUSD#processWithdrawals` swallowing a failed transfer and simply moving on, permanently losing the enforcement action (the withdrawal) with no way to recover it. The analogous pattern in this repo is the `hookify` plugin's `PreToolUse` hook: whenever rule loading, parsing, or evaluation throws *any* exception, the hook does not deny/block the tool call — it prints a diagnostic `systemMessage` and unconditionally exits `0`, which Claude Code treats as "allow." Just like the failed withdrawal that silently vanishes instead of blocking/retrying, a failed rule-engine invocation silently vanishes instead of blocking the (potentially dangerous) tool call the user configured it to stop.

### Finding Description
`pretooluse.py` is registered as a `PreToolUse` hook and is the single enforcement point for user-authored "block" rules created via `/hookify` (e.g., "block `rm -rf` without asking"). [1](#0-0) 

The `main()` function wraps rule loading (`load_rules`) and evaluation (`RuleEngine.evaluate_rules`) in a bare `try/except Exception`. On any exception — a malformed rule file, a bad regex, an `AttributeError` in `_extract_field`, a `KeyError`, or any other unexpected error thrown deep inside `config_loader.py`/`rule_engine.py` — the handler does not attempt to fail closed. It emits `{"systemMessage": f"Hookify error: {str(e)}"}` and, critically, the `finally: sys.exit(0)` always runs, which is explicitly commented "ALWAYS exit 0 - never block operations due to hook errors."

This mirrors `config_loader.load_rules`, which also swallows `IOError`/`ValueError`/`Exception` per rule file and simply `continue`s past malformed rule files rather than surfacing a hard failure: [2](#0-1) 

The `RuleEngine.evaluate_rules` method also returns an empty allow (`{}`) whenever no rule matches, and blocking is only ever emitted via `hookSpecificOutput.permissionDecision: "deny"` when a `block`-action rule is explicitly matched and the evaluation completes without error: [3](#0-2) 

Because the whole chain (`load_rules` → `RuleEngine._rule_matches` → `_check_condition` → `_extract_field`/`_regex_match`) has multiple unguarded dict/string operations (e.g. `field_value.startswith(pattern)`, `.endswith(pattern)`, `pattern in field_value` at lines 169–177 which assume `field_value` is always a string) any malformed rule file, unexpected tool_input shape, or transient exception anywhere in this pipeline causes the *entire enforcement decision for that tool call* to be dropped and the operation to proceed as if no rule existed — exactly analogous to the VUSD contract's `continue`-past-failure that permanently loses the failed withdrawal instead of stopping/retrying.

### Impact Explanation
This is a hook-bypass trust-boundary issue: a user who configured `hookify` to `block` dangerous commands (the plugin's own worked example is blocking/warning on `rm -rf`) relies on `PreToolUse` to actually stop the tool call. If evaluation throws for any reason (malformed `.claude/hookify.*.local.md`, an unexpected `tool_input` shape, a regex compilation edge case not caught elsewhere, non-string values reaching `_check_condition`), the intended `block` decision is silently discarded and the tool executes anyway — a false sense of security with no error surfaced to the user other than a system message they may not read before the (already-executed) dangerous command runs. This matches the report's core harm pattern: a critical safety action is dropped rather than retried or fail-closed, and there is no mechanism to recover or reprocess the dropped enforcement decision.

### Likelihood Explanation
Likelihood is moderate: this requires either (a) a bug/edge case in the rule engine (unhandled type, bad user-authored rule file) or (b) any of the already-broad exception surfaces in `config_loader.py`/`rule_engine.py` (file I/O races, YAML-like parsing quirks in the hand-rolled `extract_frontmatter`, non-string tool_input fields hitting `.startswith`/`.endswith`) being triggered during normal use of a feature whose entire purpose is enforcing user safety rules. Because this is a design decision (explicit comment "never block operations due to hook errors") rather than a rare corner case, any exception in this code path reliably produces a fail-open outcome.

### Recommendation
Fail closed instead of fail open for `block`-configured rules: when the rule engine cannot complete evaluation, either (a) deny/prompt for the specific tool call that triggered the error rather than silently allowing it, or (b) at minimum, surface a hard, blocking warning (not just an informational `systemMessage`) so the user is aware enforcement did not run before the risky command executes. Malformed rule files should also be validated with a clear error surfaced to the user (not just `stderr`, which is not visible in the transcript) rather than silently skipped in `load_rules`.

### Proof of Concept
1. Create `.claude/hookify.block-rm.local.md` with a `block` rule targeting `rm -rf` per the plugin's own documented workflow (`plugins/hookify/commands/hookify.md`).
2. Craft a rule/tool_input combination that causes `_extract_field`/`_check_condition` in `rule_engine.py` to raise (e.g., an operator like `contains`/`starts_with` invoked against a non-string `field_value`, or a crafted `tool_input` value that isn't a `str`/`int` such that `str(value)` still succeeds but a later downstream assumption fails — or simply corrupt/mid-write the rule `.md` file during a race so `config_loader.load_rule_file` raises inside the outer `try` in `main()`).
3. Trigger the guarded `Bash` tool call (e.g., `rm -rf /some/path`) while the exception condition holds.
4. Observe that `pretooluse.py`'s `except Exception` branch fires, prints only a `systemMessage`, and `finally: sys.exit(0)` allows Claude Code to proceed with the tool call — the configured `block` rule never took effect, and the dangerous command executes despite the user's explicit block configuration.

Note: I was not able to execute this end-to-end (no runtime/terminal access in this mode) to confirm a concrete exception-triggering input reaches the `try` block in practice; the code path and fail-open behavior are directly confirmed by reading `pretooluse.py`, `config_loader.py`, and `rule_engine.py`, but the exact minimal malformed-rule or tool_input payload that reliably raises inside `_check_condition`/`_extract_field` would need to be validated with actual execution.

### Citations

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

**File:** plugins/hookify/core/config_loader.py (L213-241)
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

    return rules
```

**File:** plugins/hookify/core/rule_engine.py (L35-94)
```python
    def evaluate_rules(self, rules: List[Rule], input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate all rules and return combined results.

        Checks all rules and accumulates matches. Blocking rules take priority
        over warning rules. All matching rule messages are combined.

        Args:
            rules: List of Rule objects to evaluate
            input_data: Hook input JSON (tool_name, tool_input, etc.)

        Returns:
            Response dict with systemMessage, hookSpecificOutput, etc.
            Empty dict {} if no rules match.
        """
        hook_event = input_data.get('hook_event_name', '')
        blocking_rules = []
        warning_rules = []

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
