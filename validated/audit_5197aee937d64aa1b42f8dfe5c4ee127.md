### Title
MultiEdit rule bypass via malformed `edits` entries causing `_extract_field` crash and fail-open in `pretooluse.py` - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._extract_field` concatenates `new_string` values from `MultiEdit`'s `edits` list using `e.get('new_string', '')` without validating that each entry `e` is a dict, so a crafted `edits` array containing non-dict items raises an unhandled `AttributeError`. This exception propagates through `evaluate_rules`/`_rule_matches` up to `pretooluse.py`'s top-level `except Exception` handler, which always logs and then unconditionally exits 0, meaning the intended `file`-event block rule for that `MultiEdit` call never fires and the tool call proceeds unblocked.

### Finding Description
`_extract_field` handles the `MultiEdit` tool type at [1](#0-0) , building the `new_text`/`content` field via `' '.join(e.get('new_string', '') for e in edits)`. This assumes every element `e` of `tool_input['edits']` is a dict. Because `tool_input` is fully attacker/tool-caller controlled JSON passed into the PreToolUse hook, an `edits` list entry that is a string, number, `null`, or list (anything lacking `.get`) causes `AttributeError: 'str' object has no attribute 'get'` (or similar) during the list comprehension.

This is called from `_check_condition` at [2](#0-1) , itself called from `_rule_matches` at [3](#0-2) , itself called from `evaluate_rules` at [4](#0-3) . None of these functions catch the exception.

`pretooluse.py`'s `main()` wraps rule evaluation in a broad `try/except Exception` that, on any error, only emits a `systemMessage` and — critically — the `finally` block unconditionally calls `sys.exit(0)`, i.e. fail-open regardless of the exception: [5](#0-4) . So for any `MultiEdit` call whose `edits` array contains a malformed (non-dict) entry, any `file`-event blocking rule that depends on `new_text`/`content` matching never gets a chance to evaluate that condition correctly — instead the whole rule evaluation aborts with an exception that is swallowed, and the tool call is allowed to proceed as if no rule matched.

### Impact Explanation
This breaks the fundamental security invariant of the hookify plugin: a PreToolUse hook rule configured to block dangerous file edits (e.g., blocking edits containing secrets, dangerous patterns, or protected paths) can be silently bypassed by an attacker-controlled/tool-caller-controlled `MultiEdit` `tool_input` shape, since Claude Code tool-input JSON originates from model/tool-call content that can be influenced by untrusted repository content or crafted prompts driving the assistant to issue such a call. The result is unauthorized file mutation proceeding despite a configured block rule — a hook/enforcement bypass.

### Likelihood Explanation
The trigger requires only that a `MultiEdit` tool call be issued with an `edits` array containing at least one non-dict item alongside the intended edit content — a shape that Claude Code's tool schema does not appear to strictly enforce before reaching the hook, and is fully within the shape of JSON passed to the PreToolUse hook via stdin. No special privileges are needed beyond causing a `MultiEdit` tool call, which is a normal, frequently used editing operation; the bypass is deterministic and repeatable whenever such malformed input reaches an active `file`-event blocking rule.

### Recommendation
In `_extract_field`, defensively filter/validate `edits` entries before calling `.get`, e.g. `' '.join(e.get('new_string', '') for e in edits if isinstance(e, dict))`, and consider having `pretooluse.py` distinguish "safe to fail-open" errors (e.g., missing rule config) from malformed-input errors, or at minimum treat unexpected input shapes during rule evaluation as matching (fail-closed) for blocking rules rather than fail-open, especially since the current design explicitly prioritizes availability over enforcement integrity.

### Proof of Concept
Unit test in `plugins/hookify/core/rule_engine.py` test suite:
```python
def test_multiedit_malformed_edits_does_not_bypass_block():
    from hookify.core.config_loader import Rule, Condition
    rule = Rule(
        name="block-secret",
        enabled=True,
        event="file",
        tool_matcher="MultiEdit",
        conditions=[Condition(field="new_text", operator="contains", pattern="SECRET")],
        message="Blocked: secret detected",
        action="block",
    )
    engine = RuleEngine()
    input_data = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "foo.py",
            "edits": [
                "not_a_dict_entry",
                {"new_string": "SECRET_KEY=123"},
            ],
        },
    }
    # Expected: either evaluate_rules raises a handled, explicit error (not silently
    # swallowed to fail-open), or it correctly evaluates the dict entry and returns
    # a block decision. Currently it raises AttributeError inside _extract_field,
    # which pretooluse.py's main() catches and converts into exit(0) (allow),
    # demonstrating the bypass.
    result = engine.evaluate_rules([rule], input_data)
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
```
Running this against current code raises `AttributeError` from `_extract_field` (line 252) instead of returning the expected `deny` decision, confirming the bypass path when wired through `pretooluse.py`'s fail-open exception handler.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L53-58)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)
```

**File:** plugins/hookify/core/rule_engine.py (L120-123)
```python
        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False
```

**File:** plugins/hookify/core/rule_engine.py (L157-158)
```python
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
```

**File:** plugins/hookify/core/rule_engine.py (L246-252)
```python
        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)
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
