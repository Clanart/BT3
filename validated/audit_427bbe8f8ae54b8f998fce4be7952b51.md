### Title
Non-exact `enabled:` value in hookify rule frontmatter is coerced to truthy, silently activating rules intended to be disabled - (File: plugins/hookify/core/config_loader.py)

### Summary
The hand-rolled YAML-like parser in `extract_frontmatter` only recognizes exact `true`/`false` string values (after `.strip()` and quote-stripping) and only strips full-line comments, not inline/trailing comments. Any `enabled:` value that isn't an exact case-insensitive `true`/`false` token (e.g., `enabled: false  # disabled for CI`) is stored as a raw non-empty string, which `Rule.from_dict` and `load_rules` then treat as truthy, silently enabling a rule the file's author or a reviewer believed was disabled.

### Finding Description
`extract_frontmatter` in [1](#0-0)  parses top-level `key: value` lines by splitting on the first `:`, stripping whitespace and surrounding quotes, and only converting the value to a Python boolean when `value.lower() == 'true'` or `value.lower() == 'false'` exactly. Full-line comments (`stripped.startswith('#')`) are skipped, but there is no handling for inline/trailing comments appended to a value on the same line.

For a rule file containing:
```
enabled: false  # disabled for CI
```
the parser produces `value = "false  # disabled for CI"`, which fails both the `'true'`/`'false'` equality checks, so it is stored verbatim as a non-empty string in `frontmatter['enabled']`.

`Rule.from_dict` then does `enabled=frontmatter.get('enabled', True)` [2](#0-1) , assigning this string directly to the `enabled` field (typed `bool` but not validated/coerced).

`load_rules` gates inclusion with `if rule.enabled:` [3](#0-2) . In Python, any non-empty string is truthy, so the rule is loaded and evaluated by `RuleEngine` on every matching `PreToolUse`/`PostToolUse`/`Stop`/`UserPromptSubmit` event [4](#0-3)  — even though the frontmatter explicitly says `false`. The reverse direction (a malformed `true` value) is not exploitable the same way since it also ends up truthy, coincidentally matching intent; the asymmetry means malformed `enabled:` values can only ever silently force a rule ON, never OFF.

No caller validates that `rule.enabled` is an actual `bool`, and no YAML library (e.g., PyYAML) is used, so no proper type/comment handling exists anywhere in the load path.

### Impact Explanation
Hookify rules can carry `action: block` (blocking a Bash/Edit/Write operation) or `action: warn` (injecting a message into Claude's context on tool use). A rule file committed to a repo with a trailing comment on `enabled: false` will silently execute as if `enabled: true`, causing:
- Unexpected blocking of legitimate developer operations (denial of intended actions) when a reviewer/author believed the rule was inactive, and
- Unexpected `warn` messages injected into the agent's context/output on matching tool calls, which is a form of unintended prompt content being delivered to Claude even though the repo's own documentation (`enabled: false` = disabled) told the reviewer otherwise.

This breaks the "declared state == effective state" invariant for the plugin's local configuration files and can silently change block/allow/warn outcomes for anyone who clones a repository containing such a file, without any explicit consent step confirming the rule is actually active.

### Likelihood Explanation
Fully attacker-controlled and trivially reproducible: any repository containing a `.claude/hookify.*.local.md` file (these files are only *recommended*, not enforced, to be gitignored per `plugins/hookify/commands/help.md` line 138) with a trailing comment, extra whitespace variant, or any non-exact `true`/`false` token on the `enabled:` line will hit this parser gap on every load. No special privileges are required — merely having such a file present when Claude Code loads hookify rules for the repo triggers it.

### Recommendation
- Replace the hand-written frontmatter parser with a real YAML parser (e.g., PyYAML `safe_load`), which correctly handles inline comments, quoting, and type coercion.
- If keeping the custom parser, explicitly strip inline comments before value comparisons (e.g., split on unquoted `#`), and treat any value that doesn't exactly match a recognized boolean token as a parse error (fail closed / skip the rule with an explicit warning) rather than silently coercing to a truthy string.
- Add a runtime type check in `Rule.from_dict`/`load_rule_file` that rejects or normalizes non-bool `enabled` values before use in `if rule.enabled:`.

### Proof of Concept
Unit/fuzz test targeting `extract_frontmatter` and `Rule.from_dict` in `plugins/hookify/core/config_loader.py`:

```python
import pytest
from plugins.hookify.core.config_loader import extract_frontmatter, Rule

@pytest.mark.parametrize("raw_value,expected_enabled", [
    ("true", True),
    ("false", False),
    ("  false  ", False),
    ("FALSE", False),
    ("false  # disabled for CI", False),   # currently fails: becomes truthy string
    ("true   # keep on", True),            # currently "passes" by coincidence
    ("false\t# trailing tab comment", False),  # currently fails
])
def test_enabled_matches_declared_intent(raw_value, expected_enabled):
    content = f"""---
name: test-rule
enabled: {raw_value}
event: bash
pattern: rm -rf
---

message
"""
    fm, msg = extract_frontmatter(content)
    rule = Rule.from_dict(fm, msg)
    assert isinstance(rule.enabled, bool), f"enabled is not bool: {rule.enabled!r}"
    assert rule.enabled == expected_enabled
```

Expected result: the assertions for the `# comment`-suffixed cases fail against current code, because `rule.enabled` is the raw string `"false  # disabled for CI"` (truthy, `isinstance(..., bool)` is False), demonstrating that `load_rules`'s `if rule.enabled:` check at [3](#0-2)  loads a rule the frontmatter explicitly marked disabled.

### Citations

**File:** plugins/hookify/core/config_loader.py (L75-78)
```python
        return cls(
            name=frontmatter.get('name', 'unnamed'),
            enabled=frontmatter.get('enabled', True),
            event=frontmatter.get('event', 'all'),
```

**File:** plugins/hookify/core/config_loader.py (L115-152)
```python
    for line in lines:
        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # Check indentation level
        indent = len(line) - len(line.lstrip())

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

**File:** plugins/hookify/core/config_loader.py (L224-226)
```python
            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)
```

**File:** plugins/hookify/hooks/pretooluse.py (L44-56)
```python

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
```
