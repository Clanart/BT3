### Title
Hookify frontmatter parser silently downgrades `action: block` rules due to indentation/delimiter ambiguity - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` implements a hand-rolled YAML subset parser with brittle, exact-match indentation rules (`indent == 0` for top-level keys, `indent > 2` for list-item continuation lines). A rule file whose `action: block` line — or the `conditions` list fields feeding it — is indented in a way that is valid-looking YAML but does not satisfy these exact thresholds is silently dropped from the parsed dict, causing `Rule.from_dict` to fall back to its `action` default of `"warn"` (`config_loader.py` line 81) or to produce an empty `conditions` list, which `RuleEngine._rule_matches` treats as "never match" (`rule_engine.py` lines 117-118). The visible markdown file still reads as a `block` rule, but the enforced `Rule` object is a no-op or warn-only rule.

### Finding Description
`extract_frontmatter` (`plugins/hookify/core/config_loader.py:87-195`) is invoked by `load_rule_file` → `load_rules`, which is called from every hookify hook script (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) on every tool invocation via `load_rules(event=event)` [1](#0-0) .

The parser's top-level key detection requires exact zero indentation: [2](#0-1) 

Continuation lines belonging to a list-item dict (used for `conditions:` entries) require indentation strictly greater than 2: [3](#0-2) 

Any line that does not land exactly on `indent == 0` (top-level) or `indent > 2` while `in_dict_item` is active is silently discarded — there is no error, warning, or fallback. If an `action: block` line (or a `conditions` field such as `pattern:`) is written with an indentation style a human author would consider correct YAML (e.g., 2-space list continuation instead of >2-space, a stray leading space introduced by copy/paste or an editor’s auto-indent, or mixed tab/space indentation), that key is simply never added to the `frontmatter` dict.

Downstream, `Rule.from_dict` defaults `action` to `"warn"` when the key is absent: [4](#0-3) 

And `RuleEngine._rule_matches` treats a rule with no successfully-parsed conditions as never matching: [5](#0-4) 

`RuleEngine.evaluate_rules` only escalates to a hard `"permissionDecision": "deny"` / `"decision": "block"` response for rules whose `action == 'block'`; anything else is downgraded to a non-blocking `systemMessage`: [6](#0-5) 

All hookify hook entrypoints are fail-open by design (`sys.exit(0)` in a `finally` block, and any exception just prints a warning and continues), so there is no secondary layer that would catch or flag a rule silently degrading from block to warn/ignore: [7](#0-6) 

Because the raw `.md` file still visually shows `action: block` with a seemingly valid `pattern`/`conditions` block, the repository maintainer or Claude Code user has no indication that the rule enforced at runtime differs from what they wrote and reviewed.

### Impact Explanation
Hookify block rules are the mechanism by which a repo defines local, custom deny rules for dangerous `Bash`/`Edit`/`Stop` operations, layered on top of (or in place of) other approval mechanisms. A rule authored to block a dangerous command pattern (e.g., `rm -rf`, credential exfiltration, `curl | sh`) can be silently reduced to a `warn`-only message or to a non-matching rule (empty `conditions`) purely through common, plausible-looking indentation choices — with zero error output distinguishing this from a correctly enforced block. This breaks the invariant that "rule semantics must not change because of formatting ambiguity" and results in dangerous commands executing without being denied, even though the project's own security configuration explicitly states they should be blocked. This matches unauthorized local command execution bypassing a deny control that the user/repo relies on.

### Likelihood Explanation
No special privilege is required: any contributor of a `.claude/hookify.*.local.md` file (via `/hookify`, a PR, or a cloned template repo) can produce this outcome, and it can also occur unintentionally from ordinary variance in how people/editors indent nested YAML-like lists (2-space vs 4-space continuation, tabs, stray leading spaces from copy-paste). Because there is no validation, linting, or error surfaced when a field is dropped, the bug is easily missed and highly repeatable — the exact same file will deterministically parse to a weaker rule every time it is loaded.

### Recommendation
Replace the hand-rolled indentation-sensitive parser with a real YAML parser (e.g., PyYAML `safe_load`) for frontmatter extraction, or, if a custom parser must be kept, make indentation handling structural (track indentation levels via a stack rather than fixed magic thresholds like `== 0` / `> 2`), and fail loudly (reject the file, do not silently default `action` to `warn`) whenever a recognized key like `action` or `conditions` cannot be unambiguously parsed. Additionally, add a post-parse invariant check that a rule declared as `block` in the source text is not silently defaulted to `warn`/no-match — e.g., re-serialize the parsed `Rule` and diff against a strict YAML parse, or require `action`/`conditions` presence with an explicit parse error on ambiguity.

### Proof of Concept
Add a unit/differential test in a new `plugins/hookify/tests/test_config_loader.py`:

```python
from plugins.hookify.core.config_loader import extract_frontmatter, Rule

MALFORMED_BLOCK_RULE = """---
name: block-rm-rf
enabled: true
event: bash
conditions:
  - field: command
    operator: regex_match
    pattern: "rm -rf"
action: block
---
This should be blocked.
"""

def test_indentation_ambiguity_preserves_block_action():
    """A visually-correct 'action: block' rule must not silently become warn/no-op."""
    # Variant using 2-space continuation instead of >2-space (common valid-looking YAML style)
    variant = MALFORMED_BLOCK_RULE.replace(
        "    operator: regex_match\n    pattern: \"rm -rf\"\n",
        "  operator: regex_match\n  pattern: \"rm -rf\"\n"
    )
    fm_baseline, msg_baseline = extract_frontmatter(MALFORMED_BLOCK_RULE)
    fm_variant, msg_variant = extract_frontmatter(variant)

    rule_baseline = Rule.from_dict(fm_baseline, msg_baseline)
    rule_variant = Rule.from_dict(fm_variant, msg_variant)

    # EXPECTED (invariant): both should parse to an identical, enforceable block rule.
    assert rule_variant.action == "block", (
        f"Expected 'block' action preserved, got {rule_variant.action!r} — "
        "rule semantics changed due to indentation/formatting ambiguity"
    )
    assert rule_variant.conditions, "Conditions were silently dropped by the parser"
```

Running this against the current implementation is expected to fail: `rule_variant.action` resolves to `"warn"` (the `Rule.from_dict` default) and/or `rule_variant.conditions` is incomplete/empty, demonstrating that `RuleEngine._rule_matches` (`plugins/hookify/core/rule_engine.py:117-118`) will never fire the rule, or `RuleEngine.evaluate_rules` will only emit a non-blocking `systemMessage` instead of a `permissionDecision: deny`/`decision: block` response for an operation the file visibly declares should be blocked.

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L51-56)
```python
        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
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

**File:** plugins/hookify/core/config_loader.py (L124-152)
```python
        # Top-level key (no indentation or minimal)
        if indent == 0 and ':' in line and not line.strip().startswith('-'):
            # Save previous list/dict if any
            if in_list and current_key:
                if in_dict_item and current_dict:
                    current_list.append(current_dict)
                    current_dict = {}
                frontmatter[current_key] = current_list
                in_list = False
                in_dict_item = False
                current_list = []

            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()

            if not value:
                # Empty value - list or nested structure follows
                current_key = key
                in_list = True
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

**File:** plugins/hookify/core/config_loader.py (L183-187)
```python
        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
            # This is a field of the current dict item
            k, v = stripped.split(':', 1)
            current_dict[k.strip()] = v.strip().strip('"').strip("'")
```

**File:** plugins/hookify/core/rule_engine.py (L53-94)
```python
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

**File:** plugins/hookify/core/rule_engine.py (L115-118)
```python
        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            return False
```
