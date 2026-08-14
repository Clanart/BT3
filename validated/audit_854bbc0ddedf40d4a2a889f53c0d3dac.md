### Title
Hookify legacy "file" rules never inspect `Write` tool content, allowing dangerous `Write` operations to bypass block rules - (File: `plugins/hookify/core/rule_engine.py`)

### Summary
`RuleEngine._extract_field` maps the field name `new_text`/`new_string` to `tool_input.get('new_string', '')` for both `Write` and `Edit` tools, but the `Write` tool never populates `new_string` — it only populates `content`. Any hookify rule authored with the legacy `pattern:` shorthand and `event: file` (which `config_loader.Rule.from_dict` compiles into a condition on field `new_text`) will therefore silently evaluate against an empty string for every `Write` call, causing dangerous content written via `Write` to never match the intended block/warn condition, even though the same rule correctly detects the same payload delivered via `Edit`.

### Finding Description
`Rule.from_dict` in `plugins/hookify/core/config_loader.py` (lines 56-73) converts a simple legacy `pattern:` rule with `event: file` into a `Condition(field='new_text', operator='regex_match', pattern=simple_pattern)`. This is the officially supported "simple" rule authoring style for file operations. [1](#0-0) 

At evaluation time, `RuleEngine._rule_matches` calls `_check_condition`, which calls `_extract_field(condition.field, tool_name, tool_input, input_data)`. For `field == 'new_text'` and `tool_name in ['Write', 'Edit']`, the code executes:
```python
elif field == 'new_text' or field == 'new_string':
    return tool_input.get('new_string', '')
``` [2](#0-1) 

The `Write` tool's `tool_input` schema contains a `content` key, not `new_string` (only `Edit`/`MultiEdit` provide `new_string`). Consequently, for any `Write` invocation, `tool_input.get('new_string', '')` always returns the empty string `''` — not `None`. Because `_check_condition` only short-circuits (returns `False`/no-match) when the extracted value is `None`, an empty string is passed on to the operator check (e.g. `regex_match('')`), which will not match the dangerous pattern. The condition therefore evaluates to "no match," the rule does not fire, and `evaluate_rules` allows the operation through. This directly violates the stated invariant: "a matching block rule must reliably deny the protected operation," because the same dangerous content that is correctly blocked when introduced via `Edit`'s `new_string` field is silently permitted when introduced via `Write`'s `content` field, despite the rule's `tool_matcher` (default or explicit `Write|Edit`) matching both tools.

This is fully reachable through the normal, unprivileged workflow the question describes: an attacker who can cause Claude Code to perform a `Write` tool call with attacker-influenced content (e.g., via repository content, generated code, or prompt injection through files/PRs that the agent later writes back) will have that content evaluated by this field-extraction bug, and any hookify content-guard rule based on the standard/legacy `new_text` field mapping will never see it.

### Impact Explanation
This is a logic-level bypass of a user-configured security guard. Hookify block rules (e.g., rules intended to stop writing secrets, dangerous scripts, or destructive code to disk) are meant to reliably deny matching `Write`/`Edit` operations regardless of which specific file-editing tool delivered the content. Because of the field-name/tool mismatch in `_extract_field`, `Write`-based dangerous content silently bypasses detection while `Edit`-based content with the same payload is correctly blocked — an inconsistent, exploitable gap in the enforcement layer. This matches "Logic-level service disruption caused by bypassing a required guard," since the intended protective control fails to trigger for an entire class of otherwise-covered operations.

### Likelihood Explanation
High feasibility and fully repeatable: no special privileges are required beyond normal use of hookify with a legacy/simple `pattern:` + `event: file` rule (the documented simple-authoring style), and any workflow where the agent performs a `Write` tool call with the dangerous content. Every `Write` invocation deterministically hits the same code path and always yields `''` for `new_text`, so the bypass is not probabilistic — it is guaranteed for 100% of `Write` calls when rules rely on `new_text`/`new_string` extraction.

### Recommendation
In `RuleEngine._extract_field`, align field aliasing with the actual tool schema: for `field in ('new_text', 'new_string', 'content')` and `tool_name == 'Write'`, extract from `tool_input.get('content', '')`; for `tool_name in ('Edit', 'MultiEdit')`, extract from `new_string`/`edits[].new_string` as already done. Alternatively, unify all content-bearing fields (`content`, `new_text`, `new_string`) into a single extraction helper that consults `content` first, then falls back to `new_string`, for every applicable tool, so rule authors cannot unintentionally create tool-specific blind spots. Add regression tests asserting that a rule with `field: new_text` fires identically for equivalent dangerous payloads delivered via `Write.content`, `Edit.new_string`, and `MultiEdit.edits[].new_string`.

### Proof of Concept
Unit test to add to `plugins/hookify/core/rule_engine.py` test suite (or a new test file):
```python
from hookify.core.config_loader import Rule, Condition
from hookify.core.rule_engine import RuleEngine

def test_write_content_bypasses_new_text_rule():
    rule = Rule(
        name="block-secret",
        enabled=True,
        event="file",
        conditions=[Condition(field="new_text", operator="contains", pattern="SECRET_KEY")],
        action="block",
        tool_matcher="Write|Edit",
        message="Secret detected!"
    )
    engine = RuleEngine()

    # Edit delivers the payload via new_string -> correctly blocked
    edit_input = {"tool_name": "Edit", "tool_input": {"file_path": "a.py", "new_string": "SECRET_KEY=abc"}}
    assert engine.evaluate_rules([rule], edit_input) != {}

    # Write delivers the SAME payload via content -> should also be blocked but is NOT
    write_input = {"tool_name": "Write", "tool_input": {"file_path": "b.py", "content": "SECRET_KEY=abc"}}
    result = engine.evaluate_rules([rule], write_input)
    assert result != {}, "BUG: Write operation with dangerous content bypassed the block rule"
```
Expected (current, buggy) behavior: the `Edit` case is blocked (`result != {}`), while the `Write` case returns `{}` (no match), demonstrating the guard-bypass. After the fix, both assertions should pass.

### Citations

**File:** plugins/hookify/core/config_loader.py (L60-73)
```python
            # Infer field from event
            event = frontmatter.get('event', 'all')
            if event == 'bash':
                field = 'command'
            elif event == 'file':
                field = 'new_text'
            else:
                field = 'content'

            conditions = [Condition(
                field=field,
                operator='regex_match',
                pattern=simple_pattern
            )]
```

**File:** plugins/hookify/core/rule_engine.py (L235-244)
```python
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
```
