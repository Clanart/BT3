### Title
MultiEdit blocking rules can be bypassed via malformed `edits` entries crashing `_extract_field` and causing hookify's fail-open handler to allow the operation - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._extract_field` for `tool_name == 'MultiEdit'` builds the `new_text`/`content` field value with `' '.join(e.get('new_string', '') for e in edits)` without validating that each `e` is a dict or that `new_string` is a string. A malformed `edits` entry causes an unhandled `TypeError`/`AttributeError` that propagates up through `evaluate_rules`, which is caught by the top-level `except Exception` in `pretooluse.py`/`posttooluse.py`, and the hook always exits `0` without emitting a deny decision — silently bypassing any blocking rule targeting MultiEdit content.

### Finding Description
In `_extract_field`, the MultiEdit-specific branch does: [1](#0-0) 
```
elif tool_name == 'MultiEdit':
    if field == 'file_path':
        return tool_input.get('file_path', '')
    elif field in ['new_text', 'content']:
        # Concatenate all edits
        edits = tool_input.get('edits', [])
        return ' '.join(e.get('new_string', '') for e in edits)
```
Unlike the direct-field extraction at the top of the function, which safely casts non-string values with `str(value)`: [2](#0-1) 
this MultiEdit branch performs no type checking. If any `edits` entry `e` is not a dict (e.g. a string), `e.get(...)` raises `AttributeError`. If `new_string` is present but not a string (e.g. an int, list, or dict), `' '.join(...)` raises `TypeError: sequence item N: expected str instance, ... found`.

This exception propagates: `_extract_field` → `_check_condition` → `_rule_matches` → `evaluate_rules`. All four `hookify` entry scripts (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) wrap the call to `evaluate_rules` in a broad `try/except Exception` that, on any error, emits only `{"systemMessage": "Hookify error: ..."}` and unconditionally calls `sys.exit(0)`: [3](#0-2) 

Because no `hookSpecificOutput.permissionDecision: "deny"` is produced in this error path, Claude Code has no signal to block the tool call, so the MultiEdit operation proceeds — regardless of whether a configured blocking rule (e.g. matching dangerous content patterns via `content`/`new_text` field conditions) would otherwise have matched and denied it.

### Impact Explanation
This allows a full bypass of any user-configured `hookify` blocking rule that inspects MultiEdit content (`new_text`/`content` field conditions), by simply causing one `edits` entry to have a non-string or missing `new_string`/non-dict structure. Since PreToolUse hookify rules are the mechanism intended to enforce "don't allow dangerous file edits" policies, this is a hook-enforcement/approval bypass: a dangerous MultiEdit call that should be denied is instead silently allowed with only a generic, unrelated `systemMessage`, and the blocking message/deny decision the rule author configured is lost entirely.

### Likelihood Explanation
Exploitation only requires the tool call being evaluated to include a `MultiEdit` `tool_input` where `edits` contains an entry that is not a dict, or whose `new_string` is not a string (e.g. produced by a manipulated/injected tool call flow, a compromised MCP tool response shaping the arguments, or any path where `tool_input` construction isn't strictly schema-validated before the PreToolUse hook runs). Given the hookify scripts are explicitly documented to "ALWAYS exit 0 — never block operations due to hook errors," any such malformed input reliably and repeatably defeats a blocking rule targeting MultiEdit content, with no special privileges needed beyond being able to trigger the specific malformed `tool_input` shape.

### Recommendation
Harden `_extract_field`'s MultiEdit branch to defensively coerce values, mirroring the safe handling already used elsewhere in the function:
```python
elif field in ['new_text', 'content']:
    edits = tool_input.get('edits', [])
    parts = []
    for e in edits:
        if isinstance(e, dict):
            v = e.get('new_string', '')
            parts.append(v if isinstance(v, str) else str(v))
    return ' '.join(parts)
```
Additionally, consider changing the fail-open exception handler in the hook entry scripts to fail-closed (deny) for PreToolUse when a rule-evaluation crash occurs, since silently allowing on internal errors defeats the purpose of a security-blocking hook.

### Proof of Concept
Unit test against `RuleEngine`:
```python
from hookify.core.config_loader import Rule, Condition
from hookify.core.rule_engine import RuleEngine

rule = Rule(
    name="block-secret-exfil",
    enabled=True,
    event="file",
    action="block",
    tool_matcher="MultiEdit",
    conditions=[Condition(field="content", operator="contains", pattern="AKIA")],
    message="Blocked dangerous edit"
)

engine = RuleEngine()

# Malformed edits: new_string is an int, not a string
malicious_input = {
    "tool_name": "MultiEdit",
    "tool_input": {
        "file_path": "/tmp/x.py",
        "edits": [{"old_string": "a", "new_string": 12345}]
    }
}

result = engine.evaluate_rules([rule], malicious_input)
# Expect: raises TypeError inside evaluate_rules (uncaught within RuleEngine),
# demonstrating the crash that pretooluse.py's broad except swallows and
# then allows the operation via sys.exit(0) instead of returning a deny decision.
```
Expected assertion: calling `evaluate_rules` raises `TypeError` (join of non-str), confirming that any wrapping hook script that catches this generically will fail open and never surface the intended `"permissionDecision": "deny"` for this MultiEdit call.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L196-200)
```python
        if field in tool_input:
            value = tool_input[field]
            if isinstance(value, str):
                return value
            return str(value)
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
