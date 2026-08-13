I have enough evidence to identify a concrete analog vulnerability rooted in the same bug class (unhandled errors → silent failure with no compensating action, leaving a security-relevant control effectively bypassed).

### Title
Hookify's `PreToolUse`/`PostToolUse`/`Stop`/`UserPromptSubmit` hook executors fail-open on any exception, silently bypassing "block/deny" rules - (File: `plugins/hookify/hooks/pretooluse.py`)

### Summary
The `hookify` plugin's hook executors (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) wrap the entire rule-loading and rule-evaluation pipeline in a single broad `try/except Exception`, and on **any** unhandled error, they print only a generic `systemMessage` and unconditionally `sys.exit(0)`. This mirrors the reported `swap-router` bug class: an operation that is supposed to have consequences on failure (deny a dangerous action / revert a swap) instead does nothing meaningful and lets execution proceed as if nothing happened.

### Finding Description
The hookify hooks are designed so that users author `.claude/hookify.*.local.md` rule files with `action: block` to deny dangerous `Bash`/`Write`/`Edit` operations before they execute [1](#0-0) . The evaluation path is: read stdin JSON → `load_rules()` → `RuleEngine().evaluate_rules()` → print JSON result to stdout → exit 0 [2](#0-1) .

If any exception occurs anywhere in that chain — for example, a `TypeError`/`AttributeError` from malformed `tool_input` reaching `RuleEngine._extract_field`/`_check_condition` (e.g. `field_value.startswith(pattern)` when `field_value` is not actually a string in an edge case, or a `KeyError`/`re.error` propagating out of an unexpected code path not caught by the narrower internal handlers) — the outer `except Exception` in the hook entrypoint catches it, replaces the intended `hookSpecificOutput: {"permissionDecision": "deny"}` response with a bare `{"systemMessage": f"Hookify error: {str(e)}"}`, and the `finally: sys.exit(0)` guarantees the process always exits successfully [3](#0-2) .

Critically, a `PreToolUse` hook that returns a plain `systemMessage` (no `hookSpecificOutput.permissionDecision`) is treated by Claude Code as **not blocking** — the tool call proceeds. So any error thrown while evaluating a user's `block` rule causes that rule to be silently skipped for that invocation, with no `deny`, no retry, and no clear signal to the user that their guard-rail rule failed to run rather than "not matching." The same fail-open behavior is repeated identically in `posttooluse.py`, `stop.py`, and `userpromptsubmit.py` [4](#0-3) [5](#0-4) [6](#0-5) , and even the `ImportError` path at module load explicitly documents "allow operation and log error" [7](#0-6) .

This is structurally the same root cause as the report: a failure path exists (`success == false` / an exception is raised) but instead of reverting/denying or clearly failing closed, the code takes essentially no corrective action and lets the transaction/tool-call proceed, only emitting a message.

### Impact Explanation
For an unprivileged user (or a model acting under auto-mode/bypass settings) relying on `hookify` `block` rules as a safety net (e.g., to prevent `rm -rf`, writes to secret files, or other destructive Bash/Edit/Write operations), any runtime exception in the rule pipeline causes that safety check to be **skipped entirely** for the current tool call — the dangerous operation is auto-allowed rather than denied. Because the hook always exits 0, there is no exit-code-2 blocking fallback and no forced-ask degradation; the failure is invisible unless the user is watching the transcript's `systemMessage`. This is a hook-bypass trust-boundary issue: a guard-rail control degrades to "allow" on error instead of "deny."

### Likelihood Explanation
Likelihood is moderate: it requires a rule-evaluation-time exception, which can be triggered by malformed/edge-case `tool_input` values (e.g., non-string fields reaching `.startswith`/`.endswith`/`in` operators in `_check_condition`), unusual Unicode/regex patterns not caught by the narrower `re.error` handler in `_regex_match`, or any other uncaught exception type in `_extract_field`/`_rule_matches` [8](#0-7) . Because `hookify` is a plugin distributed for general use with user-authored rule files, the space of malformed inputs it must tolerate is broad, and the current design does not fail closed for `PreToolUse`/blocking events.

### Recommendation
- For `PreToolUse` hooks specifically, decide a fail-closed policy for rule-evaluation errors that occur while any `block`-action rule was in scope: either return `permissionDecision: "ask"` (prompt the user) or explicitly note in the `systemMessage` that a rule failed to evaluate and could not be enforced, rather than silently allowing.
- Avoid one broad `except Exception` around the whole evaluation pipeline; catch specific expected error types close to where they occur (as already done inside `rule_engine.py`'s `_regex_match` and `config_loader.py`'s file-loading paths) and let genuinely unexpected errors surface distinctly so users notice their rule is broken.
- Surface hook-execution failures more visibly (not just a transcript `systemMessage`) so users can distinguish "rule didn't match" from "rule failed to run."

### Proof of Concept
1. Author a `.claude/hookify.bad.local.md` rule with `action: block`, `event: bash`, and a condition whose `field`/`operator` combination can receive a non-string value at runtime (e.g., a condition on a field that `_extract_field` can return as a non-string in some edge case, or supply a `tool_input.command` that is a nested object rather than a string in a crafted MCP/tool call path feeding into `PreToolUse`).
2. Trigger a `Bash` tool call that should be blocked by this rule.
3. Because `_check_condition`/`_extract_field` raises an uncaught exception (e.g. `AttributeError: 'dict' object has no attribute 'startswith'`), `pretooluse.py`'s outer `except Exception` catches it, and the hook returns `{"systemMessage": "Hookify error: ..."}` with exit code 0 instead of `{"hookSpecificOutput": {"permissionDecision": "deny"}}` [3](#0-2) .
4. Observe that the otherwise-blocked dangerous Bash command executes normally, with only a debug-level `systemMessage` hinting at the failure — the intended guard-rail was silently bypassed.

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L1-6)
```python
#!/usr/bin/env python3
"""PreToolUse hook executor for hookify plugin.

This script is called by Claude Code before any tool executes.
It reads .claude/hookify.*.local.md files and evaluates rules.
"""
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

**File:** plugins/hookify/hooks/pretooluse.py (L35-60)
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

**File:** plugins/hookify/hooks/posttooluse.py (L54-62)
```python
    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/hooks/stop.py (L46-55)
```python
    except Exception as e:
        # On any error, allow the operation
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/hooks/userpromptsubmit.py (L46-54)
```python
    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/core/rule_engine.py (L144-254)
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

        # Apply operator
        operator = condition.operator
        pattern = condition.pattern

        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
        elif operator == 'contains':
            return pattern in field_value
        elif operator == 'equals':
            return pattern == field_value
        elif operator == 'not_contains':
            return pattern not in field_value
        elif operator == 'starts_with':
            return field_value.startswith(pattern)
        elif operator == 'ends_with':
            return field_value.endswith(pattern)
        else:
            # Unknown operator
            return False

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
