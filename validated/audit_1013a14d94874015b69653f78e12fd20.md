### Title
Hand-rolled YAML frontmatter parser silently drops/misparses `action: block` (inline comments, `:` in value, or 1-2 space indentation), causing hookify to silently downgrade a "block" rule to "warn" - ([File: plugins/hookify/core/config_loader.py])

### Summary
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` is a hand-rolled line-based YAML subset parser that does not strip inline `#` comments, does not correctly handle a `:` occurring inside a scalar value, and silently discards any top-level `key: value` line that is indented by 1-2 spaces instead of raising an error. Because `RuleEngine.evaluate_rules` decides blocking vs. warning based on an exact string comparison `rule.action == 'block'` [1](#0-0) , any of these malformed-but-plausible-looking frontmatter values cause the parsed `action` to differ from `'block'` while the raw file still visually reads `action: block`, silently reducing a supposed blocking rule to a non-blocking warning.

### Finding Description
`extract_frontmatter` parses top-level lines with `indent == 0 and ':' in line` by doing `key, value = line.split(':', 1)` and then only stripping surrounding double/single quotes: `value = value.strip('"').strip("'")` [2](#0-1) . It never strips trailing `#` comments from a value line (only whole lines starting with `#` are skipped) [3](#0-2) , and it does not handle a `:` appearing again inside the value. It also only recognizes top-level keys at `indent == 0`; any top-level-looking key indented by 1-2 spaces falls through every branch (`indent==0` false, not a list item, and `indent > 2` false) and is silently dropped with no error or warning [4](#0-3) .

Consequences:
- `action: block  # blocks dangerous rm commands` → value becomes the literal string `"block  # blocks dangerous rm commands"`, not `"block"`.
- `action: block` indented by one or two spaces (e.g. accidentally nested under a comment/list, or crafted to look correct) → the line matches none of the parser branches and is dropped entirely, so `'action'` never appears in the resulting dict.

In both cases `Rule.from_dict` falls back to the default: `action=frontmatter.get('action', 'warn')` [5](#0-4) . `RuleEngine._rule_matches`/`evaluate_rules` then only ever appends the rule to `warning_rules`, not `blocking_rules`, because `rule.action == 'block'` is False [6](#0-5) . In `evaluate_rules`, a warning-only match still allows the tool call to proceed (`systemMessage` only, no `permissionDecision: deny`) [7](#0-6) , whereas the equivalent `hookSpecificOutput.permissionDecision = "deny"` path used for real blocking rules is never taken [8](#0-7) .

Critically, `load_rule_file`/`load_rules` treat this as a fully successful parse — no exception is raised, no warning is printed — unlike a real YAML parser (`yaml.safe_load`), which would either correctly strip the comment or raise an indentation/scanning error on the malformed indentation, alerting whoever authored/reviewed the file. Here the file loads silently and "looks" correct on inspection (`git diff`/PR review shows `action: block`), but the effective behavior is `warn`.

### Impact Explanation
`.claude/hookify.*.local.md` rule files are exactly the kind of "plugin/config content" that can be introduced or modified via ordinary repository content (e.g., a contributed PR adding or editing a safety rule, or a shared/templated rule file copied between projects). If such a rule is meant to hard-block a dangerous action (e.g. `rm -rf`, forced pushes, `curl | sh`) and is crafted (or accidentally written) with a trailing inline comment or with one line indented by 1-2 spaces, a maintainer reviewing the diff sees `action: block` and reasonably believes the dangerous command class is enforced-blocked by the PreToolUse hook. In reality the hook only emits a warning `systemMessage` and lets the tool call proceed [7](#0-6) . This is a trust-boundary/hook-enforcement bypass: the security control a human believes is active (blocking) is silently degraded to advisory-only, allowing the guarded dangerous command/file mutation to execute when it should have been denied.

### Likelihood Explanation
No special privilege is required beyond the ability to introduce or edit a `.claude/hookify.*.local.md` file (via a PR or shared config) — a very ordinary contribution path. The malformed constructs (trailing `#` comment on a value line, or 1-2 space indentation) are easy to produce, either accidentally by a well-meaning contributor documenting their rule inline, or deliberately by someone wanting a rule to look like a hard block while functioning as a no-op guard. Because `extract_frontmatter` raises no error and `load_rules` silently swallows/normalizes the result, there is no signal to the reviewer or the loader that anything is wrong, making the bug fully reproducible and easy to conceal in a normal-looking diff.

### Recommendation
Replace the hand-rolled parser with a real YAML parser (`yaml.safe_load`) restricted to the expected schema, or, at minimum: (1) strip unquoted trailing `#` comments from scalar values before quote-stripping, respecting quoted strings; (2) validate that `action` is exactly one of `{"warn", "block"}` and raise/log a hard parse error otherwise instead of silently defaulting; (3) treat any line with unexpected/inconsistent indentation as a parse error rather than silently dropping it, so malformed rule files fail loudly during `load_rule_file` instead of loading as a degraded rule.

### Proof of Concept
Unit test against `extract_frontmatter`/`Rule.from_dict`/`RuleEngine`:

```python
from hookify.core.config_loader import extract_frontmatter, Rule
from hookify.core.rule_engine import RuleEngine

content = """---
name: block-rm
enabled: true
event: bash
action: block  # blocks dangerous rm commands
conditions:
  - field: command, operator: regex_match, pattern: rm -rf
---
Dangerous rm blocked!
"""

fm, msg = extract_frontmatter(content)
rule = Rule.from_dict(fm, msg)

# A real YAML parser would set action == "block"
assert rule.action == "block"   # FAILS: actual value is "block  # blocks dangerous rm commands"

engine = RuleEngine()
result = engine.evaluate_rules([rule], {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /"}
})

# Expect the dangerous command to be denied
assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
# ACTUAL: result only contains {"systemMessage": ...} — command is allowed to proceed
```

A second test with 1-2 space indentation of `action: block` demonstrates the field being dropped entirely (`'action' not in fm`), producing the same silent downgrade to the `warn` default.

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

**File:** plugins/hookify/core/rule_engine.py (L72-79)
```python
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"
                    },
                    "systemMessage": combined_message
                }
```

**File:** plugins/hookify/core/rule_engine.py (L86-94)
```python
        # If only warnings, show them but allow operation
        if warning_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }

        # No matches - allow operation
        return {}
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

**File:** plugins/hookify/core/config_loader.py (L116-119)
```python
        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
```

**File:** plugins/hookify/core/config_loader.py (L121-187)
```python
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

        # List item (starts with -)
        elif stripped.startswith('-') and in_list:
            # Save previous dict item if any
            if in_dict_item and current_dict:
                current_list.append(current_dict)
                current_dict = {}

            item_text = stripped[1:].strip()

            # Check if this is an inline dict (key: value on same line)
            if ':' in item_text and ',' in item_text:
                # Inline comma-separated dict: "- field: command, operator: regex_match"
                item_dict = {}
                for part in item_text.split(','):
                    if ':' in part:
                        k, v = part.split(':', 1)
                        item_dict[k.strip()] = v.strip().strip('"').strip("'")
                current_list.append(item_dict)
                in_dict_item = False
            elif ':' in item_text:
                # Start of multi-line dict item: "- field: command"
                in_dict_item = True
                k, v = item_text.split(':', 1)
                current_dict = {k.strip(): v.strip().strip('"').strip("'")}
            else:
                # Simple list item
                current_list.append(item_text.strip('"').strip("'"))
                in_dict_item = False

        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
            # This is a field of the current dict item
            k, v = stripped.split(':', 1)
            current_dict[k.strip()] = v.strip().strip('"').strip("'")
```
