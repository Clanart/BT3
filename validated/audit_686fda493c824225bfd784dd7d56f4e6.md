### Title
Naive comma-splitting in inline YAML dict parsing corrupts/truncates rule `pattern` values containing commas, silently weakening blocking hook rules - ([File: plugins/hookify/core/config_loader.py])

### Finding Description
`extract_frontmatter` implements a hand-rolled YAML subset parser. For inline list items of the form `- field: command, operator: regex_match, pattern: ...`, the code detects the presence of both `:` and `,` in the line and naively splits the entire item on `,` [1](#0-0) :

```python
if ':' in item_text and ',' in item_text:
    item_dict = {}
    for part in item_text.split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            item_dict[k.strip()] = v.strip().strip('"').strip("'")
    current_list.append(item_dict)
```

This splitting is applied blindly, without any awareness of quoting or escaping. If the `pattern` value itself contains a literal comma (e.g. `pattern: "curl.*|wget.*,.*sh"` or `pattern: a,b`), the comma inside the value is treated as a field separator: the value is fragmented into multiple pieces, and any trailing fragment without a `:` (e.g. the tail after the last comma) is silently discarded because of the `if ':' in part` guard. The resulting `item_dict['pattern']` therefore holds only the portion of the intended regex before the first embedded comma, rather than the full pattern the rule author wrote.

This corrupted dict is passed straight to `Condition.from_dict`, which simply reads `data.get('pattern', '')` with no validation [2](#0-1) , and that `Condition` becomes part of a `Rule` that hookify enforcement uses to `block`/`warn` on matching commands. Because the corruption happens silently (no parse error, no warning), a rule author (or a repository whose `.claude/hookify.*.local.md` rule file is later modified/introduced by an attacker-controlled contribution) can end up with a "blocking" rule whose actual enforced regex is a truncated fragment of the intended pattern — weakening or nullifying the intended dangerous-command detection without any visible sign of failure. Quoting the value does not help, since quote-stripping (`.strip('"').strip("'")`) happens *after* the destructive comma split, so quoted commas are not protected either.

### Impact Explanation
This is a security-control-integrity bug: a hookify rule intended to `block` dangerous commands (e.g., destructive shell commands, exfiltration patterns) can be silently defanged by the parser itself whenever the intended regex pattern contains a comma. The effective enforcement pattern the runtime actually matches against is not the pattern the rule author specified, so a rule that appears in the rule file to deny a dangerous action can end up matching a narrower (or invalid) regex, or missing a chunk of an alternation, allowing dangerous commands through undetected. This is a "deny-means-deny" invariant violation caused purely by parser logic, not by explicit intent, and it is undetectable at rule-authoring time since no error/warning is produced.

### Likelihood Explanation
Trigger requires only that a hookify rule file (`.claude/hookify.*.local.md`) contains an inline-style condition list item whose `pattern` field includes a comma — a very plausible authoring pattern for regex alternations, character-class-adjacent syntax, or literal comma matching. No special privileges are needed to introduce or modify such a file within the attacker-controlled surface (repository content/plugin rule files), and the bug fires deterministically and silently every time the inline-comma branch is taken with a comma-containing pattern.

### Recommendation
Replace the hand-rolled comma-split inline dict parser with a proper YAML parser (e.g. `yaml.safe_load`) for frontmatter parsing, or at minimum implement quote-aware splitting that does not split on commas within quoted strings, and add validation/round-trip checks (re-serialize and compare) so any lossy parse raises a loud error instead of silently truncating rule data.

### Proof of Concept
Unit test to add near `extract_frontmatter`:

```python
def test_inline_condition_pattern_with_comma_is_corrupted():
    content = '''---
name: block-exfil
conditions:
  - field: command, operator: regex_match, pattern: "curl.*|wget.*,.*sh"
action: block
---

blocked
'''
    fm, _ = extract_frontmatter(content)
    cond = fm['conditions'][0]
    # Expected: full intended pattern preserved
    assert cond['pattern'] == 'curl.*|wget.*,.*sh'
    # Actual (bug): pattern truncated at first embedded comma
    # cond['pattern'] == 'curl.*|wget.*' and trailing '.*sh"' fragment silently dropped
```

Running this against the current implementation shows `cond['pattern']` does not equal the original intended string, confirming the corruption and demonstrating that a `block` rule built from this condition would enforce a truncated/incorrect regex.

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

**File:** plugins/hookify/core/config_loader.py (L163-171)
```python
            # Check if this is an inline dict (key: value on same line)
            if ':' in item_text and ',' in item_text:
                # Inline comma-separated dict: "- field: command, operator: regex_match"
                item_dict = {}
                for part in item_text.split(','):
                    if ':' in part:
                        k, v = part.split(':', 1)
                        item_dict[k.strip()] = v.strip().strip('"').strip("'")
                current_list.append(item_dict)
```
