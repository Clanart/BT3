## Analysis

This external report is about an unhandled revert in an oracle/price-feed call causing a hard failure that removes protective functionality with no fallback. The closest reachable analog in this repository is in the **hookify** plugin's hook executors, where an unhandled exception during rule evaluation causes the *entire* security-blocking mechanism to fail open by design.

### Title
Unhandled exception during hookify rule evaluation fails open, bypassing all PreToolUse/Stop block rules for that call - (File: `plugins/hookify/hooks/pretooluse.py`)

### Summary
The hookify plugin implements a user-configurable command/action-blocking mechanism (`.claude/hookify.*.local.md` rules with `action: block`) that is supposed to deny dangerous tool calls. All four hook entrypoints wrap the entire rule-loading + rule-evaluation pipeline in a single top-level `try/except Exception`, and on *any* exception they discard the evaluation result and return a benign response, explicitly commented as "allow the operation."

### Finding Description
Each hook executor (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) calls `load_rules()` and `RuleEngine.evaluate_rules()` inside one `try` block: [1](#0-0) 

If evaluation raises any exception, the `except Exception` branch is hit and the hook emits only a `systemMessage`, never `hookSpecificOutput.permissionDecision: "deny"`, and always `sys.exit(0)` — i.e., the tool call proceeds as if no blocking rule matched, even if a `block` rule actually should have fired.

The rule engine itself provides multiple ways to trigger such an exception. `RuleEngine._check_condition` applies operators directly to `condition.pattern` without type validation: [2](#0-1) 

`condition.pattern` originates from the hand-rolled YAML-like frontmatter parser in `config_loader.extract_frontmatter`, which auto-coerces unquoted `true`/`false` values into Python `bool`: [3](#0-2) 

If a rule's `pattern` value is written unquoted as `true`/`false` (an easy authoring mistake, since the docs show `pattern` as a bare scalar), `Condition.pattern` becomes a Python `bool`. Using operators `contains`, `not_contains`, `starts_with`, or `ends_with` on a `bool` pattern against the string `field_value` raises `TypeError` (e.g., `True in "some string"` or `"cmd".startswith(True)`), which is not caught anywhere inside `rule_engine.py` and propagates up to the hook's outer `except Exception`, wiping out evaluation of *every* rule for that call — including unrelated, correctly-authored `block` rules that would have denied a dangerous `Bash` command.

### Impact Explanation
This is a fail-open trust-boundary defect in a user-facing "hook bypass" surface: a single malformed or adversarially crafted rule file (or a rule accidentally invalidated by a project's own edits) silently disables the entire PreToolUse/Stop blocking mechanism for that invocation. An attacker who can influence `.claude/hookify.*.local.md` content (e.g., via a poisoned repo, a prompt-injected instruction that gets Claude to write such a rule, or shared team config) can guarantee that dangerous commands sail through unblocked, without any visible denial — the user only sees a generic "Hookify error" message rather than a blocked action, closely mirroring the oracle case where an unhandled revert silently removes price-feed protection with no fallback.

### Likelihood Explanation
Moderate-to-high: the frontmatter parser's `true`/`false` auto-coercion is undocumented as a pitfall, so it can be triggered accidentally by any rule author, and can also be deliberately triggered by anyone able to place or modify a `.claude/hookify.*.local.md` file in the project. No special privileges beyond normal project/workspace write access (or prompt-injection-driven file writes) are required.

### Recommendation
- Validate `Condition.pattern` (and other frontmatter-derived fields) to be strings before applying string operators; reject/skip the rule with a warning instead of letting a `TypeError` bubble out.
- Wrap `RuleEngine.evaluate_rules` per-rule (as `load_rules` already does per-file) so one bad rule cannot suppress evaluation of all other rules.
- Change the fail-open default: when an unexpected error occurs while evaluating `block` rules, prefer fail-closed (deny) or at minimum surface a clear, non-suppressible warning rather than silently allowing the operation.

### Proof of Concept
1. Create `.claude/hookify.test.local.md`:
```
---
name: bad-rule
enabled: true
event: bash
conditions:
  - field: command
    operator: contains
    pattern: true
---
This should never match.
```
2. Also create a legitimate blocking rule, e.g. `.claude/hookify.block-rm.local.md` with `action: block`, `pattern: rm\s+-rf` for `event: bash`.
3. Run a `Bash` tool call with `command: "rm -rf /tmp/x"`.
4. `RuleEngine._rule_matches` evaluates `bad-rule` first (order depends on `glob.glob` result), hits `pattern in field_value` with `pattern=True`, raises `TypeError`, which is uncaught inside `evaluate_rules`/`_rule_matches`/`_check_condition` and propagates to `pretooluse.py`'s outer `except Exception`.
5. The hook emits `{"systemMessage": "Hookify error: ..."}` with no `permissionDecision`, and exits 0 — the `rm -rf` command is **not blocked** despite the `block-rm` rule being enabled and matching. [4](#0-3) [5](#0-4)

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

**File:** plugins/hookify/core/rule_engine.py (L162-180)
```python
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
```

**File:** plugins/hookify/core/config_loader.py (L144-152)
```python
                current_list = []
            else:
                # Simple key-value pair
                value = value.strip('"').strip("'")
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                frontmatter[key] = value
```
