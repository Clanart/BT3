### Title
Hookify's hand-rolled frontmatter parser silently downgrades `action: block` rules to warn on trivial formatting variance - ([File: plugins/hookify/core/config_loader.py])

### Finding Description
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` implements a minimal, hand-written line-based YAML parser instead of using a real YAML library. [1](#0-0) 
For simple `key: value` lines it only special-cases `true`/`false` (normalizing case for booleans), but performs no normalization, trimming of inline comments, or validation for any other scalar field — including `action`.

The parsed `action` value is passed straight through in `Rule.from_dict`: [2](#0-1) 

`rule_engine.py` then performs an exact, case-sensitive string comparison to decide whether a rule blocks or only warns: [3](#0-2) 

Because the comparison is `rule.action == 'block'` with no case-folding and no strict-schema validation, any value that is not the exact lowercase literal `block` (e.g. `action: Block`, `action: BLOCK`, or `action: block  # explanation`, since trailing inline comments are never stripped from scalar values) is silently routed into the `warning_rules` branch instead of `blocking_rules`. No error, warning, or parse failure is raised — `load_rule_file` only reports a problem for entirely missing frontmatter or actual Python exceptions, not for semantically-wrong-but-syntactically-plausible values. [4](#0-3) 

Rule files are auto-discovered from any `.claude/hookify.*.local.md` in the working directory via a glob, with no allow-list, signature, or provenance check: [5](#0-4) 

The `/hookify` command itself documents `action: {warn|block}` as the expected schema and encourages free-form authoring (including comments in the message body, and case-insensitive natural language elsewhere), making it plausible that a human or an LLM-generated rule file could end up with a slightly different casing/format for the `action:` field while still visually reading as "this blocks the command." [6](#0-5) 

### Impact Explanation
A `.claude/hookify.*.local.md` file that visibly declares `action: block` (or block-with-trailing-comment) for a dangerous-command rule (e.g. `rm -rf`, `curl | bash`) can be silently treated as `action: warn` by `RuleEngine.evaluate_rules`, which only shows a `systemMessage` and allows the operation to proceed instead of returning a `permissionDecision: deny` response. [7](#0-6) 
This breaks the invariant that rule semantics (block vs warn) must be a function of the declared intent, not incidental formatting, and results in unauthorized execution of a dangerous Bash/Edit/Stop-triggering command that a reviewer or user believed was hard-blocked — effectively bypassing the Claude Code approval/deny control implemented by hookify.

### Likelihood Explanation
Any ordinary contributor able to place or influence a `.claude/hookify.*.local.md` file (checked into a shared repo, introduced via a PR, or generated through `/hookify` with slightly non-canonical casing/comment style) can trigger this without any admin/maintainer privilege or credential leak. `load_rules()` has no schema enforcement or provenance restriction — it loads any matching glob file — so the mismatch is trivially reproducible and repeatable: it happens deterministically every time the field fails an exact `== 'block'` match. [5](#0-4) 

### Recommendation
- Replace the hand-rolled frontmatter parser with a proper YAML library (`yaml.safe_load`) that correctly handles comments, quoting, and case per the YAML spec, or at minimum strip inline comments and normalize whitespace for every scalar field, not just booleans.
- Normalize/validate the `action` field explicitly (e.g. `action = str(frontmatter.get('action', 'warn')).strip().lower()`) and reject/log unknown values instead of silently defaulting to `warn`.
- In `load_rule_file`, fail loudly (print a stderr warning, and optionally refuse to enable the rule) when `action` is present but not one of the exact recognized values (`warn`, `block`).

### Proof of Concept
Unit test to add to `plugins/hookify/core/` test suite:
```python
from hookify.core.config_loader import extract_frontmatter, Rule
from hookify.core.rule_engine import RuleEngine

def test_action_case_downgrade():
    content = """---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\\s+-rf
action: Block
---

This should BLOCK rm -rf.
"""
    frontmatter, message = extract_frontmatter(content)
    rule = Rule.from_dict(frontmatter, message)

    # Rule file visually declares a block rule
    assert frontmatter['action'] == 'Block'

    # But the engine treats it as a warn-only rule
    engine = RuleEngine()
    result = engine.evaluate_rules([rule], {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"}
    })
    # BUG: expected permissionDecision "deny", got only a systemMessage
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
```
Expected (buggy) result: the `rm -rf /` command is not denied — `evaluate_rules` returns only `{"systemMessage": ...}` instead of a `deny` decision — demonstrating that the parsed `Rule` object's enforced behavior differs from what the visible frontmatter (`action: Block`) declares.

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

**File:** plugins/hookify/core/config_loader.py (L145-152)
```python
            else:
                # Simple key-value pair
                value = value.strip('"').strip("'")
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                frontmatter[key] = value
```

**File:** plugins/hookify/core/config_loader.py (L207-226)
```python
    rules = []

    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)

    for file_path in files:
        try:
            rule = load_rule_file(file_path)
            if not rule:
                continue

            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)
```

**File:** plugins/hookify/core/config_loader.py (L244-262)
```python
def load_rule_file(file_path: str) -> Optional[Rule]:
    """Load a single rule file.

    Returns:
        Rule object or None if file is invalid.
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()

        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None

        rule = Rule.from_dict(frontmatter, message)
        return rule

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

**File:** plugins/hookify/commands/hookify.md (L91-107)
```markdown
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

**Action values:**
- `warn`: Show message but allow operation (default)
- `block`: Prevent operation or stop session

```
