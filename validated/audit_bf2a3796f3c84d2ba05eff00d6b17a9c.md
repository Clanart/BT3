## Analysis: Missing Field Validation Analog in Hookify Rule Engine

### Title
Missing Field Validation for `action`/`event`/`operator` in Hookify Rules Causes Silent Block-to-Warn Downgrade (Fail-Open) - (File: `plugins/hookify/core/config_loader.py`, `plugins/hookify/core/rule_engine.py`)

### Summary
The external report's bug class — data structures consumed without validating required/enum fields, leading to silently-accepted malformed values — has a direct analog in the `hookify` plugin's rule loading pipeline. `Rule.from_dict` and `Condition.from_dict` accept arbitrary, unvalidated values for security-relevant fields (`action`, `event`, `operator`), and the rule engine's downstream logic treats any unrecognized value as the permissive path rather than raising an error. This causes a hookify rule that is *intended* to block a dangerous tool call to silently become a non-blocking warning (or never fire at all) when a field is misspelled or malformed, with no validation error surfaced to the user.

### Finding Description
`Rule.from_dict` builds the `Rule` dataclass directly from parsed YAML frontmatter with no enum/format validation on any field: [1](#0-0) 

- `action` is defined as `"warn" or "block" (future)` but nothing enforces that the loaded value is one of these two strings: [2](#0-1) 

Downstream, `RuleEngine.evaluate_rules` uses a strict equality check to decide whether a matched rule blocks or merely warns: [3](#0-2) 

Any value other than the exact literal string `"block"` (e.g. `"Block"`, `"deny"`, `" block"`, or a typo) falls through to the `else` branch and is treated as a warning-only rule — the tool call is allowed to proceed. There is no error, warning, or validation step that would inform the rule author their `action:` field is invalid; `load_rule_file` only checks for I/O and gross parsing errors, not semantic field validity: [4](#0-3) 

The same unvalidated-enum pattern applies to `event` (compared for exact string equality against `'all'`/event name in `load_rules`, with no whitelist check) and to `operator` in `_check_condition`, where an unrecognized operator silently returns `False` ("no match") instead of surfacing a configuration error: [5](#0-4) [6](#0-5) 

This mirrors the reported bug class exactly: fields that should be validated against an enum/required set (`status`, `blockchain` in the original report; `action`, `event`, `operator` here) are consumed without any validation routine, and malformed values are silently accepted rather than rejected.

### Impact Explanation
Hookify's own documentation explicitly frames `action: block` as a security control ("Block or Warn: Rules can either `block` operations (prevent execution) or `warn`"): [7](#0-6) 

A project maintainer authoring a `.claude/hookify.*.local.md` rule to block a dangerous `Bash` pattern (e.g. `rm -rf`, credential exfiltration, `eval(`) can introduce a case/typo error in `action:` and believe the rule is enforcing a block, while in practice the dangerous tool call is permitted to run with only a cosmetic `systemMessage` warning. Because there is no schema-validation utility analogous to `validate-hook-schema.sh` (which validates Claude Code's native `hooks.json` type/matcher fields) applied to hookify's own rule files, this misconfiguration is undetectable until the "blocked" operation actually executes. This is a genuine hook-bypass condition reachable by any project contributor who can add `.claude/hookify.*.local.md` files.

### Likelihood Explanation
Moderate. Exploitation requires only a typo or minor formatting deviation in a hand-authored YAML-like frontmatter field (`action: block` vs `action: Block`/`deny`/etc.) — no adversarial input or privilege escalation needed. Given the parser is a hand-rolled, indentation-sensitive YAML subset (`extract_frontmatter`), such deviations are plausible in normal use, and the failure mode is silent (no error printed, no test hook run), making misconfigurations likely to go unnoticed.

### Recommendation
Add explicit validation in `Rule.from_dict`/`load_rule_file`:
- Reject or normalize `action` to only `"warn"`/`"block"`, printing a warning (or failing the rule load) on any other value instead of defaulting silently.
- Validate `event` against the known set (`"bash"`, `"file"`, `"stop"`, `"all"`, etc.).
- Validate `operator` against the known set in `Condition.from_dict`, rejecting unknown operators at load time rather than at match time.
- Extend a schema-validation script (mirroring `plugins/plugin-dev/skills/hook-development/scripts/validate-hook-schema.sh`) to cover hookify's own `.claude/hookify.*.local.md` rule format.

### Proof of Concept
1. Create `.claude/hookify.block-rm.local.md`:
```
---
name: block-dangerous-rm
enabled: true
event: bash
pattern: "rm\\s+-rf"
action: Block
---
Blocked dangerous rm -rf command.
```
2. Load the rule via `load_rules()` — `Rule.action` is set to the literal string `"Block"` (capital B) with no error.
3. Invoke `RuleEngine.evaluate_rules` with a `Bash` tool call containing `rm -rf /tmp/test`. The condition matches, but `rule.action == 'block'` evaluates `False` (`'Block' != 'block'`), so the rule is placed in `warning_rules` instead of `blocking_rules`.
4. The engine returns only a `systemMessage`, and the tool call is **not denied** — despite the rule author's clear intent to block it, verified against `rule_engine.py:55-58` and the absence of any validation in `config_loader.py:75-84`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L38-42)
```python
    pattern: Optional[str] = None  # Simple pattern (legacy)
    conditions: List[Condition] = field(default_factory=list)
    action: str = "warn"  # "warn" or "block" (future)
    tool_matcher: Optional[str] = None  # Override tool matching
    message: str = ""  # Message body from markdown
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

**File:** plugins/hookify/core/rule_engine.py (L162-180)
```python
        # Apply operator
        operator = condition.operator
        pattern = condition.pattern

        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
        elif operator == 'contains':
            return pattern in field_value
        elif operator == 'equals':
            return pattern == field_value
        elif operator == 'not_contains':
            return pattern not in field_value
        elif operator == 'starts_with':
            return field_value.startswith(pattern)
        elif operator == 'ends_with':
            return field_value.endswith(pattern)
        else:
            # Unknown operator
            return False
```

**File:** plugins/hookify/commands/help.md (L136-136)
```markdown
**Block or Warn**: Rules can either `block` operations (prevent execution) or `warn` (show message but allow). Set `action: block` or `action: warn` in the rule's frontmatter.
```
