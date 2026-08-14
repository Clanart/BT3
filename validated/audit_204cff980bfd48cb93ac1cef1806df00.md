### Title
`extract_frontmatter` silently truncates rule frontmatter on embedded `---` delimiters, dropping block conditions/action - ([File: plugins/hookify/core/config_loader.py])

### Finding Description
`extract_frontmatter` splits the raw file content with `content.split('---', 2)`, assuming exactly two `---` markers delimit the YAML block [1](#0-0) . Because `maxsplit=2` is used, the *first* `---` encountered after the opening one is treated as the closing delimiter, regardless of whether it is a legitimate YAML separator or an incidental/attacker-inserted line inside the intended frontmatter body. Any line consisting of `---` placed before the field the author intended to close the block with (e.g. before a `pattern:` or `action:` line) causes everything after it to be silently reclassified as `message` body text rather than frontmatter, with no error, warning, or parse failure raised to the user. `load_rule_file` only checks `if not frontmatter` to detect failure [2](#0-1) , so a partially-truncated (but non-empty) frontmatter dict passes silently into `Rule.from_dict`.

Downstream, `Rule.from_dict` computes `conditions` only from an explicit `conditions:` list or a `pattern:` field found in the (truncated) frontmatter dict [3](#0-2) . If the `pattern`/`conditions` key was pushed past the injected `---` and into the discarded/relocated message text, the resulting `Rule` ends up with `conditions=[]` and `pattern=None`, even though `action` (e.g. `"block"`) may still have been captured correctly. A rule visibly authored to deny/block a dangerous tool invocation is thus parsed into an object whose matching predicate is empty or effectively inert, defeating the deny semantics without any indication to the user that the file was mis-parsed. `load_rules` automatically discovers and loads every `.claude/hookify.*.local.md` file via `glob.glob` on every hook invocation with no signature, provenance, or schema validation [4](#0-3) , meaning a rule file simply present in a cloned repository (not just one manually authored by the user) will be picked up and enforced (or silently not enforced) automatically.

### Impact Explanation
A malicious repository can ship a `.claude/hookify.block-dangerous.local.md` file whose visible YAML frontmatter appears to define a legitimate `action: block` rule guarding a dangerous command/file pattern (e.g. blocking `rm -rf`, `curl|bash`, or writes outside the workspace), while an embedded stray `---` line silently truncates the parsed frontmatter so the actual `Rule` object carries no matching conditions/pattern. The victim's Claude Code session then loads and trusts what looks like an active guard rail, but the corresponding hook (`pretooluse.py`/`stop.py`) never blocks the dangerous invocation because the parsed `Rule` is inert. This breaks the stated invariant that a deny rule must never be parsed into a non-blocking configuration, enabling unauthorized command execution or file read/write outside the intended, user-approved scope.

### Likelihood Explanation
The precondition is limited to the victim cloning/using a repository that contains a crafted `.claude/hookify.*.local.md` file — no maintainer/admin privileges, leaked credentials, or social engineering beyond normal repository consumption are required, matching the allowed threat model ("ordinary repository content, plugin files"). The parser bug is deterministic and trivially reproducible: any frontmatter body containing an extra `---` line before a security-relevant field will exhibit the truncation. `load_rules`/`load_rule_file` provide no schema/consistency validation to catch this, so the issue is 100% repeatable.

### Recommendation
Replace the naive `content.split('---', 2)` frontmatter extraction with a delimiter-aware parser that only treats a `---` as the closing marker when it appears on its own line at the start of the document scan (e.g., iterate line-by-line, tracking whether inside the frontmatter block, rather than using `str.split`). Additionally, after parsing, validate that rules with `action: block` (or `deny`) always resolve to at least one non-empty condition/pattern, and fail loudly (reject the rule / abort loading, not just print a warning) if a rule claims a block action but no enforceable condition can be derived — never silently default to a matching-everything or matching-nothing state for block rules.

### Proof of Concept
Add a unit test to the hookify test suite:

```python
def test_action_block_survives_embedded_delimiter():
    content = """---
name: deny-rm
enabled: true
event: bash
action: block
---
pattern: "rm -rf"
---

This should block rm -rf!
"""
    frontmatter, message = extract_frontmatter(content)
    rule = Rule.from_dict(frontmatter, message)

    # Expectation: a rule declared as "action: block" must retain
    # its intended matching pattern/condition.
    assert rule.action == "block"
    assert rule.pattern == "rm -rf" or rule.conditions, (
        "Block rule lost its condition due to embedded '---' delimiter; "
        "rule is now inert and will never trigger the block action."
    )
```

Running this against the current `extract_frontmatter`/`Rule.from_dict` implementation shows `rule.action == "block"` but `rule.pattern is None` and `rule.conditions == []`, confirming the parsed `Rule` diverges from the visibly-authored file and the deny rule is silently converted into a non-blocking (inert) configuration.

### Citations

**File:** plugins/hookify/core/config_loader.py (L50-73)
```python
        # New style: explicit conditions list
        if 'conditions' in frontmatter:
            cond_list = frontmatter['conditions']
            if isinstance(cond_list, list):
                conditions = [Condition.from_dict(c) for c in cond_list]

        # Legacy style: simple pattern field
        simple_pattern = frontmatter.get('pattern')
        if simple_pattern and not conditions:
            # Convert simple pattern to condition
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

**File:** plugins/hookify/core/config_loader.py (L97-103)
```python
    # Split on --- markers
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_text = parts[1]
    message = parts[2].strip()
```

**File:** plugins/hookify/core/config_loader.py (L209-226)
```python
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

**File:** plugins/hookify/core/config_loader.py (L256-258)
```python
        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None
```
