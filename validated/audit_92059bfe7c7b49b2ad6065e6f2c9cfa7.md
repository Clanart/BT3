### Title
Unhandled exception in `RuleEngine.evaluate_rules` (MultiEdit `edits` field) causes fail-open bypass of block rules - ([File: plugins/hookify/hooks/posttooluse.py])

### Finding Description
`main()` in `posttooluse.py` wraps the entire rule evaluation in a blanket `try/except Exception`, and on any exception it prints `{"systemMessage": f"Hookify error: {str(e)}"}` (no `hookSpecificOutput`/`permissionDecision`) and unconditionally calls `sys.exit(0)` in the `finally` block. [1](#0-0) 

`RuleEngine._extract_field` handles the `MultiEdit` tool by iterating `tool_input.get('edits', [])` and calling `.get('new_string', '')` on every element without validating that `edits` is a list of dicts: [2](#0-1) 

If `tool_input['edits']` contains a non-dict element (e.g. a string) or `edits` itself is a non-iterable/non-list value, this raises `AttributeError` (`'str' object has no attribute 'get'`) or `TypeError`. This exception propagates from `_extract_field` → `_check_condition` → `_rule_matches` → `evaluate_rules`, none of which catch it: [3](#0-2) [4](#0-3) 

It is finally caught only in `main()`'s generic `except Exception`, which discards the blocking decision entirely and always returns exit code 0 with just a `systemMessage`: [5](#0-4) 

Critically, the crash happens *during field extraction*, before the condition's pattern (`regex_match`/`contains`/etc.) is ever evaluated at lines 166-180. So a rule that would otherwise match dangerous MultiEdit content (e.g. a block rule matching `new_text`/`content` for secrets or dangerous code) never gets the chance to fire — the malformed `edits` shape short-circuits evaluation into a crash, and the crash handler silently allows the operation by omitting `permissionDecision: "deny"`. This is a fail-open failure mode for a security enforcement hook, violating "deny means deny."

### Impact Explanation
Any hookify block rule configured against `MultiEdit` content fields (`new_text`/`content`) can be bypassed if the `edits` array in the `MultiEdit` tool call contains a non-dict element or is malformed. Because `evaluate_rules` never reaches the point of returning `{"hookSpecificOutput": {"permissionDecision": "deny"}}` (compare with normal blocking path at lines 72-79), a configured guardrail intended to block dangerous file edits silently fails and the hook exits 0, treating the call as allowed.

### Likelihood Explanation
Exploitation requires only that a `MultiEdit` `tool_input` reach the hook with a malformed `edits` field (non-dict entries or wrong type). This is reachable whenever repository content/prompt-injected instructions can influence the shape of tool calls issued for a file (the stated precondition: "repo content shapes the tool_input passed to the hook"). No privileges beyond normal tool invocation are required, and the crash is deterministic given the malformed shape, making it fully repeatable.

### Recommendation
- In `RuleEngine._extract_field`, validate that `edits` is a list and that each entry is a `dict` before calling `.get`; skip or coerce invalid entries instead of raising.
- More generally, do not let `main()` fail open: on exception, return a blocking decision by default (`permissionDecision: "deny"`) for events where enforcement matters (`PreToolUse`/`PostToolUse`), or at minimum log/flag the failure distinctly so unmatched exceptions cannot silently permit operations.
- Add defensive type-checking (`isinstance`) throughout `_extract_field`/`_check_condition` for all tool-input-derived fields, not just `edits`.

### Proof of Concept
Unit test (extends existing `rule_engine.py` self-test pattern):
```python
from hookify.core.config_loader import Condition, Rule
from hookify.core.rule_engine import RuleEngine

rule = Rule(
    name="block-secret-edit",
    enabled=True,
    event="file",
    tool_matcher="MultiEdit",
    conditions=[Condition(field="new_text", operator="contains", pattern="SECRET_KEY")],
    action="block",
    message="Blocked: secret in edit",
)

engine = RuleEngine()
malicious_input = {
    "tool_name": "MultiEdit",
    "hook_event_name": "PostToolUse",
    "tool_input": {
        "file_path": "config.py",
        "edits": ["SECRET_KEY=1234"]  # non-dict entry, would otherwise match pattern
    }
}

try:
    result = engine.evaluate_rules([rule], malicious_input)
    assert False, "Expected exception, but evaluate_rules returned normally"
except AttributeError:
    pass  # confirms crash path is reachable
```
Integration test calling `posttooluse.py main()` with the same `tool_input` piped via stdin should assert:
- exit code == 0
- output JSON lacks `hookSpecificOutput.permissionDecision == "deny"`
- output JSON contains only `systemMessage` starting with `"Hookify error:"`

despite `rule` being a configured block rule that should have matched `"SECRET_KEY"` in the edit content.

### Citations

**File:** plugins/hookify/hooks/posttooluse.py (L47-62)
```python
        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/core/rule_engine.py (L121-125)
```python
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True
```

**File:** plugins/hookify/core/rule_engine.py (L158-160)
```python
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False
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
