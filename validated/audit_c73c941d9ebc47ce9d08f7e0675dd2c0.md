### Title
Hookify block rules bypassed via `MultiEdit` tool due to incomplete tool matcher/field extraction - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
`RuleEngine._matches_tool` performs exact string matching on `tool_name` against author-specified patterns (e.g. `"Edit|Write"`), and `RuleEngine._extract_field` only special-cases `old_string`/`new_string`/`content` extraction for `tool_name in ['Write', 'Edit']`, never for `MultiEdit`. A block rule written to protect against dangerous file edits via `Edit`/`Write` silently does not fire when the functionally-equivalent `MultiEdit` tool is used to perform the same mutation, and even when a rule's `tool_matcher` does include `MultiEdit`, `old_text`/`old_string` conditions can never match because that field is never extracted for `MultiEdit`.

### Finding Description
`_rule_matches` first calls `_matches_tool(rule.tool_matcher, tool_name)` [1](#0-0) , which does exact literal comparison of `tool_name` against a `|`-split pattern list with no normalization or tool-family awareness [2](#0-1) . Claude Code exposes three distinct but functionally overlapping file-mutation tools — `Edit`, `Write`, and `MultiEdit` — as confirmed by both hook dispatchers treating all three as the same `event = 'file'` category [3](#0-2) [4](#0-3) . A rule author who writes a block rule with `tool_matcher: "Edit|Write"` (a natural, commonly-documented pattern) will never have that rule evaluated when the same underlying file mutation is instead performed via `MultiEdit`, because `"MultiEdit"` is not one of the two literal patterns.

Even in the case where a rule matcher does include `MultiEdit` or uses `*`, `_extract_field` compounds the problem: for `tool_name == 'MultiEdit'` it only supports `field_path` and `new_text`/`content` (concatenated `new_string` values from the `edits` list) [5](#0-4) . There is no handling of `old_text`/`old_string` for `MultiEdit`, and since `MultiEdit`'s `tool_input` has no top-level `old_string` key (it is nested inside `edits[]` items), the "Direct tool_input fields" fast path at the top of `_extract_field` also fails to find it [6](#0-5) . This causes `_extract_field` to return `None`, which makes `_check_condition` immediately return `False` regardless of the actual (dangerous) content being removed [7](#0-6) .

Because `Rule.from_dict` simply copies `tool_matcher` verbatim from the markdown frontmatter without validation or canonicalization of tool aliases/families [8](#0-7) , this gap is baked directly into every rule file a user or team authors, and any agent action (whether from a legitimate task, prompt-injected instructions in repo content, or an issue/PR body that steers Claude to prefer `MultiEdit`) that performs the protected mutation through the unmatched tool representation slips past the intended `action: block` rule and reaches `evaluate_rules`'s "no matches - allow operation" path [9](#0-8) .

No other layer compensates: `pretooluse.py`/`posttooluse.py` pass `input_data` straight to `RuleEngine.evaluate_rules` with no additional tool-name canonicalization, and any hook error is designed to fail-open (`sys.exit(0)` always, allowing the operation) [10](#0-9) .

### Impact Explanation
A locally deployed block rule that is intended to reliably deny a dangerous file mutation (e.g., "don't remove security markers", "don't edit files outside this repo path", "block edits to `.github/workflows/*`") can be silently bypassed simply by the agent using `MultiEdit` instead of `Edit`/`Write`, or by removing text (`old_text`) via `MultiEdit`. This breaks the core invariant "a matching block rule must reliably deny the protected operation," enabling unauthorized file mutation that the operator believed was blocked — a Cross-repo/cross-session/wrong-target mutation impact consistent with the target bounty category, since the block was never actually enforced for a standard, commonly-used tool.

### Likelihood Explanation
This is trivially reachable in normal operation: no privilege escalation or credential leak is needed. Any rule author following the documented pattern `tool_matcher: "Edit|Write"` (as suggested in `plugins/hookify/skills/writing-rules/SKILL.md`, which separately documents `MultiEdit` as a distinct tool) is exposed by default. An attacker who can influence agent behavior via repository content/prompt injection (a normal, in-scope vector per the rules) merely needs to cause the agent to prefer `MultiEdit` for the mutating edit, which is common/idiomatic when editing multiple locations in one file. This is deterministic and 100% reproducible — no race condition or timing dependency.

### Recommendation
- In `_matches_tool`, treat `Edit`, `Write`, and `MultiEdit` as a normalized "file" tool family (or require rule authors to explicitly opt out) so that `tool_matcher: "Edit|Write"` intentions are not silently narrower than the actual tool surface; alternatively, warn/fail loudly at rule-load time when a `tool_matcher` covers some but not all of the file-editing tool set.
- In `_extract_field`, add explicit `old_text`/`old_string` extraction support for `MultiEdit` (concatenating `old_string` across `edits[]`, mirroring the existing `new_text` handling) so field-based conditions are evaluated consistently across all file-editing tools.
- Add a config-load-time lint/validation step that flags `tool_matcher` values referencing an incomplete subset of known tool aliases for a given `event: file` rule.

### Proof of Concept
Unit test additions to `plugins/hookify/core/rule_engine.py`'s test harness (or a new pytest module):

```python
def test_multiedit_bypasses_edit_write_matcher():
    rule = Rule(
        name="block-secret-removal",
        enabled=True,
        event="file",
        tool_matcher="Edit|Write",
        conditions=[Condition(field="old_text", operator="contains", pattern="SECURITY_CHECK")],
        action="block",
        message="Cannot remove security check"
    )
    engine = RuleEngine()

    # Same dangerous removal performed via MultiEdit instead of Edit
    input_data = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "app.py",
            "edits": [{"old_string": "if not SECURITY_CHECK(): raise", "new_string": ""}]
        }
    }
    result = engine.evaluate_rules([rule], input_data)
    # Expected (secure) behavior: block
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "Dangerous removal via MultiEdit was NOT blocked despite equivalent Edit/Write rule"
```

Expected result today: `result == {}` (operation allowed) — confirming the bypass. A second test should confirm that the identical mutation performed via `tool_name: "Edit"` with `old_string` correctly returns `permissionDecision: deny`, demonstrating the inconsistency across "equivalent encodings" of the same operation.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L93-94)
```python
        # No matches - allow operation
        return {}
```

**File:** plugins/hookify/core/rule_engine.py (L110-113)
```python
        # Check tool matcher if specified
        if rule.tool_matcher:
            if not self._matches_tool(rule.tool_matcher, tool_name):
                return False
```

**File:** plugins/hookify/core/rule_engine.py (L127-142)
```python
    def _matches_tool(self, matcher: str, tool_name: str) -> bool:
        """Check if tool_name matches the matcher pattern.

        Args:
            matcher: Pattern like "Bash", "Edit|Write", "*"
            tool_name: Actual tool name

        Returns:
            True if matches
        """
        if matcher == '*':
            return True

        # Split on | for OR matching
        patterns = matcher.split('|')
        return tool_name in patterns
```

**File:** plugins/hookify/core/rule_engine.py (L158-160)
```python
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False
```

**File:** plugins/hookify/core/rule_engine.py (L195-200)
```python
        # Direct tool_input fields
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

**File:** plugins/hookify/hooks/pretooluse.py (L46-49)
```python
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'
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

**File:** plugins/hookify/hooks/posttooluse.py (L39-42)
```python
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'
```

**File:** plugins/hookify/core/config_loader.py (L75-84)
```python
        return cls(
            name=frontmatter.get('name', 'unnamed'),
            enabled=frontmatter.get('enabled', True),
            event=frontmatter.get('event', 'all'),
            pattern=simple_pattern,
            conditions=conditions,
            action=frontmatter.get('action', 'warn'),
            tool_matcher=frontmatter.get('tool_matcher'),
            message=message.strip()
        )
```
