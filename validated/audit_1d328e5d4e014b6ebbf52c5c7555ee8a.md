### Title
`extract_frontmatter` naive `str.split('---', 2)` delimiter parsing silently downgrades `action: block` rules to `warn` - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` splits the raw markdown content on the literal substring `'---'` anywhere in the file rather than matching `---` only on its own delimiter line. If any frontmatter value (e.g. a `pattern:` regex) contains the three-character sequence `---`, the second split point lands inside that value instead of at the real closing delimiter, truncating the frontmatter text before later keys — including `action: block` — are parsed. Because `Rule.from_dict` defaults `action` to `'warn'` when the key is missing, a rule whose raw file clearly shows `action: block` gets silently parsed as a warn-only rule.

### Finding Description
`extract_frontmatter` does: [1](#0-0) 
`content.split('---', 2)` finds the first two literal occurrences of `---` anywhere in the string, not just delimiter lines. If a frontmatter field value (most plausibly the free-form `pattern:` regex, which routinely contains character-class ranges, repeated dashes, or literal `---` sequences) happens to contain `---`, that embedded occurrence is consumed as the "second delimiter," so `parts[1]` (the parsed frontmatter block) is truncated right there and everything after — including a later `action: block` line and the real closing `---` — is shoved into `parts[2]`, i.e. the rule's displayed `message` body.

`Rule.from_dict` then builds the rule with: [2](#0-1) 
Since the `action` key never made it into the `frontmatter` dict (it ended up as inert text inside the message), `frontmatter.get('action', 'warn')` silently defaults to `'warn'`.

This propagates directly into `RuleEngine.evaluate_rules`, which decides blocking vs. warning purely from `rule.action`: [3](#0-2) 
A rule that a reviewer visually confirms as `action: block` (the text is still present in the file, just now displayed as part of the rule's message rather than parsed as a control key) is actually evaluated as a non-blocking warning rule — operations that should be denied are instead only warned about and allowed to proceed.

Reachability: rule files are created by the `/hookify` command, which generates `pattern:`/`action:` frontmatter based on user arguments and/or an LLM-driven conversation analysis of recent messages: [4](#0-3) 
Any path where the `pattern` value fed into the generated file contains a `---` substring (a plausible regex fragment, e.g. matching triple dashes/markdown separators/YAML frontmatter delimiters/CLI flag ranges) triggers the bug, whether that content originates from ordinary user text, from `$ARGUMENTS`, or from analysis of repository/conversation content that an attacker influenced via prompt injection.

No existing validation catches this: `load_rule_file` only raises a warning when frontmatter is completely empty, not when it is partially and silently truncated: [5](#0-4) 
There is no re-serialization or round-trip check comparing the parsed `Rule` against the raw file, so the divergence between what a human sees (`action: block` present in the file) and what is enforced (`action` absent → defaults to `warn`) goes completely undetected.

### Impact Explanation
This breaks the core hookify invariant that a `block` rule must never be enforced as non-blocking. In practice, a rule intended to hard-block dangerous operations (e.g. `rm -rf`, edits to `.env`, disallowed `Bash` invocations) is silently downgraded to a warning that only prints a message but allows the tool call to proceed (`PreToolUse`/`Stop` hook paths in `plugins/hookify/hooks/pretooluse.py` and `stop.py`). This is a genuine "security-control bypass that silently disables or routes around blocking" as scoped by the audit — a deny-list/permission-boundary mechanism is neutered without any visible error, log, or indication to the user or reviewer, since the raw file still displays `action: block` (just relocated into the message body).

### Likelihood Explanation
No special privileges are required — only the ability to influence the text that ends up in a hookify rule's `pattern` (or any other frontmatter) field so that it contains a `---` substring, which is a common and unremarkable regex/text fragment (e.g., three consecutive dashes, YAML-like separators, markdown horizontal rules, or numeric/date ranges). This can occur incidentally through normal `/hookify` usage or be deliberately induced through prompt-injected repository content that influences the conversation-analyzer agent's suggested pattern. The bug is 100% deterministic given such input — no race conditions or timing dependencies — making it fully reproducible.

### Recommendation
Replace the naive `content.split('---', 2)` with delimiter-line-anchored matching, e.g. `re.split(r'(?m)^---\s*$', content, maxsplit=2)`, so that only bona fide `---` lines (not `---` occurring inside field values) are treated as frontmatter boundaries. Additionally, add a sanity check in `load_rule_file`/`Rule.from_dict` that flags/logs when a `block`-intended keyword string appears in the resulting `message` body but not in the parsed frontmatter action, or better, use a real YAML parser (`yaml.safe_load`) for the frontmatter block instead of the hand-rolled line parser, eliminating this whole class of delimiter/quoting parsing bugs.

### Proof of Concept
Unit test against `extract_frontmatter`/`Rule.from_dict` in `plugins/hookify/core/config_loader.py`:
```python
from hookify.core.config_loader import extract_frontmatter, Rule

content = (
    "---\n"
    "name: block-dangerous-rm\n"
    "enabled: true\n"
    "event: bash\n"
    "pattern: rm\\s+---\\s*rf\n"   # attacker/incidental '---' inside pattern value
    "action: block\n"
    "---\n\n"
    "Dangerous rm command blocked!\n"
)

fm, msg = extract_frontmatter(content)
rule = Rule.from_dict(fm, msg)

# Expected (bug): action silently defaults to 'warn' even though the raw file says 'action: block'
assert 'action' not in fm
assert rule.action == 'warn'          # should be 'block'
assert 'action: block' in msg         # the directive leaked into the message body instead of being parsed
```
Integration confirmation: place this crafted content as `.claude/hookify.dangerous-rm.local.md`, then pipe a matching `Bash` tool-use JSON payload into `plugins/hookify/hooks/pretooluse.py`. Expected (buggy) result: `hookSpecificOutput.permissionDecision` is absent (only `systemMessage` warning returned) instead of `"permissionDecision": "deny"`, i.e. the dangerous command is not blocked.

### Citations

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

**File:** plugins/hookify/core/config_loader.py (L94-103)
```python
    if not content.startswith('---'):
        return {}, content

    # Split on --- markers
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_text = parts[1]
    message = parts[2].strip()
```

**File:** plugins/hookify/core/config_loader.py (L254-258)
```python
        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None
```

**File:** plugins/hookify/core/rule_engine.py (L53-58)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)
```

**File:** plugins/hookify/commands/hookify.md (L82-102)
```markdown
### Step 3: Generate Rule Files

For each confirmed behavior, create a `.claude/hookify.{rule-name}.local.md` file:

**Rule naming convention:**
- Use kebab-case
- Be descriptive: `block-dangerous-rm`, `warn-console-log`, `require-tests-before-stop`
- Start with action verb: block, warn, prevent, require

**File format:**
```markdown
---
name: {rule-name}
enabled: true
event: {bash|file|stop|prompt|all}
pattern: {regex pattern}
action: {warn|block}
---

{Message to show Claude when rule triggers}
```
```
