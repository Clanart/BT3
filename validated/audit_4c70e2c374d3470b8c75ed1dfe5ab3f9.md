### Title
Naive `---` splitting in `extract_frontmatter` silently downgrades `action: block` rules to `warn` - ([File: plugins/hookify/core/config_loader.py])

### Summary
`extract_frontmatter` locates the frontmatter/body boundary using a plain substring split on `'---'` with `maxsplit=2`, rather than parsing line-delimited YAML fences. Any literal occurrence of the three-character substring `---` inside a frontmatter field value (e.g. inside `pattern:`, `name:`, or a comment) before the intended closing fence is treated as the closing delimiter, truncating the parsed frontmatter dict and pushing the remaining lines — including `action: block` — into the "message" body instead. Because `Rule.from_dict` defaults `action` to `'warn'` when the key is absent, a rule file that visibly reads `action: block` can be parsed into a non-blocking `Rule`.

### Finding Description
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` does:
```python
parts = content.split('---', 2)
frontmatter_text = parts[1]
message = parts[2].strip()
``` [1](#0-0) 
This splits on the first two literal occurrences of `---` anywhere in the file, not on lines that are exactly the YAML fence `---`. If a frontmatter value (most plausibly the `pattern` field, which is a regex string and can legitimately contain three consecutive dashes, e.g. matching a diff hunk marker or `---` separator text) contains `---`, that occurrence is consumed as the closing fence. For example:

```
---
name: block-thing
pattern: a---b
action: block
---
message body
```

`content.split('---', 2)` yields `parts[1] = "\nname: block-thing\npattern: a"` and `parts[2] = "b\naction: block\n---\nmessage body"`. The resulting `frontmatter_text` no longer contains the `action:` line at all — it has been shifted into what becomes the `message` string. `load_rule_file` then calls `Rule.from_dict(frontmatter, message)` [2](#0-1) , and since `'action'` is missing from the frontmatter dict, `Rule.from_dict` falls back to the default:
```python
action=frontmatter.get('action', 'warn'),
``` [3](#0-2) 
So a rule file whose visible frontmatter clearly states `action: block` is parsed into a `Rule` object with `action == 'warn'`.

This `Rule` then flows into `RuleEngine.evaluate_rules`, which branches purely on `rule.action`:
```python
if rule.action == 'block':
    blocking_rules.append(rule)
else:
    warning_rules.append(rule)
``` [4](#0-3) 
A rule downgraded to `warning_rules` only produces a `systemMessage` and never sets `permissionDecision: deny` / `decision: block`, so the corresponding `PreToolUse`/`Stop` hook (`plugins/hookify/hooks/pretooluse.py`, `stop.py`) allows the operation to proceed [5](#0-4) . No schema validation, allowlist, or sanity check exists anywhere in `load_rules`/`load_rule_file` that would detect the discrepancy between the on-disk text and the parsed `Rule` (the only error handling is around I/O/type exceptions, not semantic parsing correctness) [6](#0-5) .

### Impact Explanation
`.claude/hookify.*.local.md` files are ordinary repository content that ships inside a cloned repo and is loaded automatically by `load_rules()` via a glob over the working directory, with no signing, path restriction beyond `.claude/`, or content sanity check [7](#0-6) . A malicious or compromised repository can ship a rule file that a human reviewer would read as an active `block` rule (e.g., "block-dangerous-rm") but that actually parses to `action: warn` due to an embedded `---` in the `pattern` value — silently disabling the intended safety guardrail without altering its visible/audited text meaning. Any subsequent dangerous tool invocation that the rule was meant to block (e.g., a destructive `Bash` command, or a `Stop` guard requiring tests before finishing) is instead only warned about, and in autonomous/auto-approved sessions the warning does not stop execution. This breaks the invariant that a `block`/deny rule must never be parsed into a non-blocking configuration, enabling unintended command execution or file mutation the user believed was hard-blocked — a real, repo-content-driven security control bypass.

### Likelihood Explanation
Exploitation only requires an attacker to control the *content* of a `.claude/hookify.*.local.md` file that ends up in a victim's working tree (a normal, unprivileged repo-content path — no admin access, no leaked keys, no social engineering beyond "clone this repo" or "adopt this rule file suggested by /hookify agent output"). The `pattern` field is free-form regex text, so embedding a literal `---` is trivial and unsuspicious (e.g., a pattern intended to match multi-dash separators, diff markers, or Markdown horizontal rules). The bug triggers deterministically on every load (`load_rules` is called on every `PreToolUse`/`Stop`/`UserPromptSubmit` hook invocation), requires no timing race, and is 100% reproducible given the crafted file.

### Recommendation
Rewrite `extract_frontmatter` to split strictly on lines that are exactly `---` (optionally with trailing whitespace), e.g. by scanning line-by-line for a line equal to `'---'` rather than doing a substring `str.split('---', 2)`. Additionally, replace the ad-hoc YAML subset parser with a real YAML parser (e.g. `yaml.safe_load`) applied only to the text between the correctly identified fence lines, and add a post-parse invariant check (e.g., a test/assertion) that if the raw frontmatter block textually contains an `action:` line, the parsed `Rule.action` must match it — failing/logging loudly rather than silently defaulting to `warn`.

### Proof of Concept
Unit test to add to `plugins/hookify/core/config_loader.py` (or a new test file):
```python
def test_embedded_dashes_do_not_downgrade_block_action():
    content = (
        "---\n"
        "name: block-thing\n"
        "enabled: true\n"
        "event: bash\n"
        "pattern: a---b\n"
        "action: block\n"
        "---\n"
        "message body\n"
    )
    frontmatter, message = extract_frontmatter(content)
    rule = Rule.from_dict(frontmatter, message)
    # Expected (correct) behavior:
    assert frontmatter.get('action') == 'block'
    assert rule.action == 'block'
```
Running this against the current implementation fails: `frontmatter` lacks the `action` key (it was pushed into `message`), and `rule.action == 'warn'` — demonstrating that a visibly `action: block` rule is parsed into a non-blocking `Rule`, satisfying the "parsed Rule object differs from the visible file" validation criterion.

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

**File:** plugins/hookify/core/config_loader.py (L198-211)
```python
def load_rules(event: Optional[str] = None) -> List[Rule]:
    """Load all hookify rules from .claude directory.

    Args:
        event: Optional event filter ("bash", "file", "stop", etc.)

    Returns:
        List of enabled Rule objects matching the event.
    """
    rules = []

    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/core/config_loader.py (L244-274)
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

    except (IOError, OSError, PermissionError) as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return None
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        print(f"Error: Malformed rule file {file_path}: {e}", file=sys.stderr)
        return None
    except UnicodeDecodeError as e:
        print(f"Error: Invalid encoding in {file_path}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: Unexpected error parsing {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
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

**File:** plugins/hookify/hooks/pretooluse.py (L35-59)
```python
def main():
    """Main entry point for PreToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Determine event type for filtering
        # For PreToolUse, we use tool_name to determine "bash" vs "file" event
        tool_name = input_data.get('tool_name', '')

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

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
```
