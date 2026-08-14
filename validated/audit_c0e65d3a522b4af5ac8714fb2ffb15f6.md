### Title
Off-by-one indentation boundaries in `extract_frontmatter()` silently drop scoping fields (`event`, `tool_matcher`, condition fields), letting an approved 'bash'-only rule load as `event='all'`/`tool_matcher=None` - (File: plugins/hookify/core/config_loader.py)

### Summary
`extract_frontmatter()` uses hard-coded indentation thresholds (`indent == 0` for top-level keys, `indent > 2` for dict-item continuations) with no `else`/fallback branch, so any line whose indentation falls between these boundaries (e.g. 1–2 leading spaces) is silently discarded instead of raising an error. A rule author or reviewer who visually approves `event: bash` / `tool_matcher: Bash` in a `.claude/hookify.*.local.md` file can have that exact field vanish from the parsed frontmatter if it carries slightly different indentation than expected, causing `Rule.from_dict()` to fall back to its permissive defaults (`event='all'`, `tool_matcher=None`).

### Finding Description
`extract_frontmatter()` (`plugins/hookify/core/config_loader.py:87-195`) implements a hand-rolled state machine over three mutually-exclusive branches:

- Top-level key: requires `indent == 0` [1](#0-0) 
- List item: requires `stripped.startswith('-') and in_list` [2](#0-1) 
- Dict-item continuation: requires `indent > 2 and in_dict_item` [3](#0-2) 

There is no `else` clause. A non-empty, non-comment line with `indent` in `{1, 2}` that is not a list item (e.g. `" event: bash"` with one leading space, or `"  tool_matcher: Bash"` with two) matches none of the three conditions and is dropped without any warning, exception, or log line.

Because `Rule.from_dict()` reads every field with `.get(key, default)` — `frontmatter.get('event', 'all')`, `frontmatter.get('tool_matcher')`, `frontmatter.get('action', 'warn')` [4](#0-3)  — a silently dropped `event` key resolves to the maximally permissive `'all'`, and a dropped `tool_matcher` resolves to `None` (i.e. "no override," matching all tools) instead of raising a parse error. The same `indent > 2` boundary also silently drops fields inside multi-line condition dict items (e.g. `pattern:` under a `- field: command` list entry indented by exactly 2 spaces), producing incomplete `Condition` objects with empty `pattern`/`operator` defaults from `Condition.from_dict()` [5](#0-4) .

`load_rule_file()` treats any resulting `Rule` as valid as long as `frontmatter` is non-empty — it never validates that expected keys were actually captured [6](#0-5) . There is no schema check, no re-render/diff of parsed vs. source, and `load_rules()` then applies the rule using `rule.event`/`rule.tool_matcher` as-authoritative for event filtering [7](#0-6) .

### Impact Explanation
A hookify rule that a human reviewer visually approves as scoped to `event: bash` (or with a specific `tool_matcher`) can silently execute for `event='all'` or with no tool restriction, because a single space of accidental/adversarial indentation in the frontmatter causes that key to be dropped during parsing with no error surfaced. This breaks the "consent is explicit and scoped" invariant: the reviewed artifact and the loaded/enforced automation behavior diverge, expanding the hook's blast radius (e.g. a warn/block rule intended only for `bash` commands now also evaluates on `file` or `stop` events, or a rule intended to target only the `Bash` tool now matches any tool) beyond what was approved.

### Likelihood Explanation
No special privilege is required — only the ability to author or modify a `.claude/hookify.*.local.md` file (e.g. via a PR to a repo using hookify, or a plugin/template distributing such rule files). The trigger is a one- or two-character whitespace difference that is easy to introduce accidentally (copy-paste from a differently-indented template, editor auto-indent, tab/space mixing) and easy to disguise for an adversarial contributor, since diff viewers and code review typically don't flag single-space indentation changes as suspicious. The bug is deterministic and 100% reproducible for any line with `indent in {1,2}` outside a list context.

### Recommendation
Replace the ad-hoc indentation thresholds with a strict, validated grammar: require an explicit `else: raise ValueError(...)` (or at least a logged warning) for any non-blank, non-comment line that doesn't match a recognized indentation/pattern, and make the "top-level key" and "continuation" indent boundaries consistent (e.g. always compare against `list_indent + 2`, computed dynamically from the actual list marker indentation, rather than the fixed constants `0` and `2`). Additionally, after parsing, `Rule.from_dict()`/`load_rule_file()` should validate that all frontmatter keys present in the raw text were actually captured (e.g. compare key count via a regex scan of `^\s*(\w+):` against `frontmatter.keys()`) and reject the file if there's a mismatch, rather than silently falling back to permissive defaults.

### Proof of Concept
Fuzz/invariant test:
```python
import itertools, random
from plugins.hookify.core.config_loader import extract_frontmatter, Rule

CANONICAL = """---
name: test-rule
enabled: true
event: bash
action: warn
tool_matcher: Bash
pattern: "rm -rf"
---

message body
"""

def indent_variant(content, spaces):
    lines = content.split('\n')
    out = []
    for line in lines:
        if line.strip() and ':' in line and not line.strip().startswith('-') and not line.startswith('---'):
            out.append(' ' * spaces + line.lstrip())
        else:
            out.append(line)
    return '\n'.join(out)

canonical_fm, canonical_msg = extract_frontmatter(CANONICAL)
canonical_rule = Rule.from_dict(canonical_fm, canonical_msg)

for spaces in [0, 1, 2, 3, 4]:
    variant = indent_variant(CANONICAL, spaces)
    fm, msg = extract_frontmatter(variant)
    rule = Rule.from_dict(fm, msg)
    # Invariant: scoped fields must never silently change across whitespace-only variants
    assert rule.event == canonical_rule.event, f"event drifted at indent={spaces}: {rule.event}"
    assert rule.tool_matcher == canonical_rule.tool_matcher, f"tool_matcher drifted at indent={spaces}: {rule.tool_matcher}"
    assert rule.action == canonical_rule.action, f"action drifted at indent={spaces}: {rule.action}"
```
Expected result: the assertions fail for `spaces in {1, 2}` — `rule.event` becomes `'all'` instead of `'bash'` and `rule.tool_matcher` becomes `None` instead of `'Bash'`, confirming that a single/double-space indentation change silently broadens the rule's approved scope without any parse error.

### Citations

**File:** plugins/hookify/core/config_loader.py (L22-29)
```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )
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

**File:** plugins/hookify/core/config_loader.py (L124-125)
```python
        # Top-level key (no indentation or minimal)
        if indent == 0 and ':' in line and not line.strip().startswith('-'):
```

**File:** plugins/hookify/core/config_loader.py (L154-155)
```python
        # List item (starts with -)
        elif stripped.startswith('-') and in_list:
```

**File:** plugins/hookify/core/config_loader.py (L183-184)
```python
        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
```

**File:** plugins/hookify/core/config_loader.py (L219-226)
```python
            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)
```

**File:** plugins/hookify/core/config_loader.py (L254-261)
```python
        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None

        rule = Rule.from_dict(frontmatter, message)
        return rule
```
