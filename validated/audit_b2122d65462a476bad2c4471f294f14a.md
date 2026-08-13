### Title
`extract_frontmatter`'s naive triple-dash split silently drops `action: block` into the message body, downgrading deny rules to warn - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`extract_frontmatter` locates the frontmatter block with `content.split('---', 2)`, which treats *any* literal `---` substring inside a frontmatter value (e.g. a `pattern` regex containing `---`, a common construct for matching markdown horizontal rules, diff/merge markers, or YAML separators) as the closing delimiter. Any frontmatter lines that appear after that embedded `---` — including `action: block` — are cut out of the parsed frontmatter dict and appended to the plain-text `message` body instead, so `Rule.from_dict` falls back to the default `action = "warn"`. This lets a rule file that visibly reads `action: block` actually be enforced as a non-blocking warning.

### Finding Description
`extract_frontmatter` in `plugins/hookify/core/config_loader.py` (lines 87–103) determines the frontmatter boundaries this way: [1](#0-0) 

`str.split('---', 2)` splits on the first *two* occurrences of the literal substring `---` anywhere in the file, not just on lines that are exactly `---` delimiters. If a `pattern:` (or any other) value inside the frontmatter block itself contains `---` (e.g. `pattern: "^---$"` to detect embedded YAML frontmatter, or a pattern matching a markdown horizontal rule / diff separator), that occurrence is consumed as the second delimiter. Everything after it — even though it is still visually "inside" the `---`/`---` block a human reviewer sees — becomes part of `parts[2]`, which is treated purely as the free-text `message` body and is never fed into the line-by-line YAML mini-parser (lines 105–195).

Concretely, for a file whose visible content is:
```
---
name: guard-force-push
event: bash
pattern: "git push.*---force"
action: block
---
Blocks dangerous force-pushes.
```
`content.split('---', 2)` finds delimiter 1 at position 0, and delimiter 2 inside the `pattern` value (`"...---force"`), so `frontmatter_text` ends at `pattern: "git push.*`, and `action: block` plus the real closing `---` and the intended message all become the `message` string. `Rule.from_dict` (lines 44–84) then reads `frontmatter.get('action', 'warn')` and gets the default `"warn"`, even though the file unambiguously states `action: block` to any human or automated reviewer.

Because `load_rule_file` and `load_rules` (lines 198–275) only check `if not frontmatter: ...` for a missing-frontmatter warning, and otherwise silently accept whatever `Rule.from_dict` produces, no error or warning is ever surfaced about the misparsed `action` field — the downgrade is completely silent. `RuleEngine.evaluate_rules` (`plugins/hookify/core/rule_engine.py` lines 55–58) then routes the rule into `warning_rules` instead of `blocking_rules`, so `PreToolUse`/`PostToolUse`/`Stop` hooks return a non-blocking `systemMessage` instead of `permissionDecision: "deny"` for the dangerous tool invocation the rule was meant to stop.

### Impact Explanation
This breaks the stated invariant that a deny rule must never be parsed into a non-blocking configuration. A `.claude/hookify.*.local.md` rule file that appears, on visual/code review, to `block` a dangerous `Bash`/`Edit`/`Write` operation can be silently enforced as a mere warning at runtime purely due to an unrelated regex value elsewhere in the same frontmatter block containing `---`. This is a Security-control bypass: a protection that a reviewer believes is blocking (e.g. force-push protection, `rm -rf` guard, secret-file edit guard) is silently downgraded to advisory-only, allowing the dangerous tool call to proceed while only printing a warning message.

### Likelihood Explanation
No special privilege is required beyond the ability to introduce or modify a `.claude/hookify.*.local.md` file that ends up in the loader's search path (`load_rules` globs `.claude/hookify.*.local.md` relative to CWD, `plugins/hookify/core/config_loader.py` lines 209–211). Such files are plausible to appear in shared repositories (team-distributed hookify policies) and could be introduced or edited by a lower-privileged contributor via ordinary PR content without triggering suspicion, since the file's rendered `action: block` is exactly what a reviewer expects — the discrepancy only manifests at parse time. Triggering only requires a `pattern` (or other) field value containing a literal `---`, which is a realistic authoring choice (matching horizontal rules, diff markers, or other YAML-looking content) rather than a contrived adversarial string, making the bug easy to hit even unintentionally, and trivial to hit deliberately.

### Recommendation
Replace the naive `content.split('---', 2)` with a delimiter check that only matches `---` when it appears alone on its own line (e.g. using `re.split(r'(?m)^---\s*$', content, maxsplit=2)` or scanning line-by-line for a line that, after stripping, equals exactly `---`). Additionally, consider using a real YAML parser (e.g. `yaml.safe_load`) instead of the hand-rolled line parser to eliminate this and similar class of parsing-ambiguity bugs, and add a validation step that fails closed (treats the rule as unparseable/disabled, not silently downgraded to `warn`) when `action` cannot be confidently determined.

### Proof of Concept
Unit test to add to `plugins/hookify/core/config_loader.py` test suite (or standalone pytest):
```python
from hookify.core.config_loader import extract_frontmatter, Rule

content = '''---
name: guard-force-push
event: bash
pattern: "git push.*---force"
action: block
---
Blocks dangerous force-pushes.
'''

frontmatter, message = extract_frontmatter(content)
rule = Rule.from_dict(frontmatter, message)

# Expected (per visible file): rule.action == "block"
# Actual (bug): action line is swallowed into `message`, action defaults to "warn"
assert rule.action == "block", f"Deny rule silently downgraded to '{rule.action}'"
```
Integration PoC: place the above content in `.claude/hookify.bash.guard-force-push.local.md` in a cloned repo, then pipe a `PreToolUse` `Bash` event with `command: "git push --force"` into `plugins/hookify/hooks/pretooluse.py`. Expected (per the visible rule) is `hookSpecificOutput.permissionDecision == "deny"`; actual observed output is a non-blocking `systemMessage` only, allowing the force-push to proceed — confirming the parsed `Rule` object differs from the visibly-authored file and that a deny rule was converted into a non-blocking configuration.

### Citations

**File:** plugins/hookify/core/config_loader.py (L97-103)
```python
    # Split on --- markers
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_text = parts[1]
    message = parts[2].strip()
```
