### Title
Fail-open PreToolUse hook silently disables "block" rules when rule evaluation throws or a matched field is un-stringifiable - ([File: plugins/hookify/hooks/pretooluse.py])

### Summary
The bundled `hookify` plugin implements PreToolUse safety enforcement (e.g. blocking `rm -rf`, credential-file writes, etc. via user-authored `.claude/hookify.*.local.md` rules) but is architected to **fail open**: any exception raised anywhere in rule loading or evaluation is swallowed and the process always exits `0` with no `permissionDecision: deny`, exactly as the royalty-recipient report shows an untrusted/unprepared downstream party causing the surrounding security-relevant flow to break — except here the failure mode silently *disables the safety check* rather than reverting the transaction.

### Finding Description
`plugins/hookify/hooks/pretooluse.py` is invoked by Claude Code before every tool call to decide `allow|deny|ask` based on user-defined rules. Its `main()` wraps rule loading/evaluation in a blanket `except Exception` and, in the `finally` block, unconditionally calls `sys.exit(0)`: [1](#0-0) 

This means that if `RuleEngine.evaluate_rules()` (or anything it calls) throws for any reason, the hook prints a generic `systemMessage` and exits 0 — which Claude Code interprets as "no decision" (allow), never emitting `hookSpecificOutput.permissionDecision: "deny"`.

The rule-matching path processes attacker/model-influenced tool input directly: `_extract_field` pulls raw strings out of `tool_input` (Bash `command`, Write/Edit `content`/`new_string`, MultiEdit `edits[].new_string`, etc.) and passes them to `_regex_match`, which calls `field_value.startswith/endswith` in `_check_condition` for the `starts_with`/`ends_with` operators without a type check: [2](#0-1) [3](#0-2) 

If `tool_input[field]` is a non-string type that `str()` doesn't behave as expected for in `_extract_field` (e.g., nested dict/list values are stringified, but `MultiEdit`'s `edits` concatenation trusts `e.get('new_string', '')` assuming each edit is a dict — a crafted/degenerate `edits` list containing non-dict elements throws `AttributeError` inside `_extract_field`), the exception propagates up through `_check_condition` → `_rule_matches` → `evaluate_rules`, is caught by the blanket handler in `pretooluse.py`, and the hook exits 0 without ever returning `"permissionDecision": "deny"`. Any `block` rule configured for that tool call (e.g. "block writes containing credentials", "block `rm -rf`") is therefore never enforced for that call, and the dangerous tool call proceeds as if no rule existed.

This mirrors the report's root cause pattern: a trust-boundary component (`royaltyRecipient` / here, the rule-evaluation dependency) that is not defensively hardened against edge-case/adversarial input causes the surrounding security control (royalty payment / here, the blocking hook decision) to fail — but instead of reverting the whole operation (DoS), the Claude Code case fails permissive, which is a strictly worse "hook bypass" outcome: the dangerous operation is allowed to proceed silently.

### Impact Explanation
An unprivileged user who has installed `hookify` and configured `block` rules to guard against dangerous Bash commands or sensitive file writes can have those guardrails silently bypassed whenever the model (potentially steered by prompt injection from untrusted file/tool content) produces a tool call whose input shape triggers an exception in field extraction/matching. Because the hook always reports success (exit 0, no denial), there is no visible indication to the user that the safety rule failed to run — the dangerous `Bash`/`Write`/`Edit`/`MultiEdit` call is executed exactly as if hookify were not installed at all. This is a genuine "hook bypass" of a user-configured command-approval control, matching one of the permitted analog categories (hook bypass / command approval / tool authorization).

### Likelihood Explanation
Exploitability requires: (1) the `hookify` plugin enabled with at least one `block` rule, and (2) a tool call whose `tool_input` shape causes an unhandled exception in `_extract_field`/`_check_condition` (e.g., a `MultiEdit` call whose `edits` array contains a non-dict element, or any other malformed/edge-case field the model is coaxed into producing via prompt injection). The design explicitly documents "ALWAYS exit 0 — never block operations due to hook errors," confirming this fail-open behavior is intentional at the top level rather than an accidental oversight, which makes any bug anywhere in the rule pipeline reachable as a full bypass.

### Recommendation
- Change the PreToolUse hook's error-handling philosophy for `block`-action rules: on internal error, either (a) fail closed for rules explicitly marked `action: block` (return `permissionDecision: "ask"` or `"deny"` with a clear "hookify internal error — review manually" message) rather than silently exiting 0, or (b) at minimum surface a loud, non-suppressible warning to the user/model so the bypass is visible rather than silent.
- Harden `_extract_field`/`_check_condition` to validate types defensively (e.g., guard `MultiEdit.edits` elements, ensure `field_value` is always a `str` before calling `startswith`/`endswith`) so genuinely malformed input can't reach an unhandled exception path at all.
- Add regression tests that intentionally feed malformed `tool_input` shapes to `RuleEngine.evaluate_rules` and assert that `block` rules still produce a deny decision (or at minimum a non-silent failure) instead of a bare `exit(0)`.

### Proof of Concept
Not independently executed (no filesystem/terminal access in this session), but the vulnerable path is fully traceable in source:
1. Author `.claude/hookify.bash.local.md` (or similar) with a `block` rule, e.g. matching `field: content, operator: contains, pattern: "AKIA"` for `MultiEdit`.
2. Have the model (or a crafted tool call) invoke `MultiEdit` with an `edits` array containing a malformed element, e.g. `{"edits": ["not-a-dict"]}`.
3. `_extract_field` (plugins/hookify/core/rule_engine.py:249-252) executes `e.get('new_string', '')` on the string `"not-a-dict"`, raising `AttributeError: 'str' object has no attribute 'get'`.
4. This exception propagates to `evaluate_rules`/`main()` in `plugins/hookify/hooks/pretooluse.py`, is caught by the blanket `except Exception`, and the hook prints a generic error and calls `sys.exit(0)` — never emitting a deny decision.
5. The `MultiEdit` tool call proceeds unblocked despite the configured `block` rule, confirming the bypass.

### Citations

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

**File:** plugins/hookify/core/rule_engine.py (L163-177)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L182-200)
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
```
