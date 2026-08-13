### Title
`RuleEngine._rule_matches` silently no-ops `action: block` rules that have empty/malformed `conditions`, defeating deny-means-deny enforcement - ([File: plugins/hookify/core/rule_engine.py])

### Summary
A hookify rule file with `event: stop`, `action: block`, and an empty or malformed `conditions` list is accepted by `Rule.from_dict`/`load_rule_file` without any warning, but `RuleEngine._rule_matches` unconditionally returns `False` when `rule.conditions` is empty. This causes `stop.py` to always report `{}` (allow) for that rule while exiting `0`, so an intended stop-blocking security control never fires and no diagnostic is ever surfaced.

### Finding Description
`stop.py::main` reads stdin JSON, calls `load_rules(event='stop')`, then `RuleEngine.evaluate_rules(rules, input_data)`, and always prints a JSON result and `sys.exit(0)` regardless of outcome [1](#0-0) .

`load_rules` iterates `.claude/hookify.*.local.md` files, calls `load_rule_file` for each, and includes the resulting `Rule` in the enabled list as long as parsing doesn't throw and `rule.enabled` is true — there is no check that a `block` rule actually has enforceable conditions [2](#0-1) .

`Rule.from_dict` builds `conditions` from the frontmatter `conditions` list, only falling back to the legacy `pattern` field "if `simple_pattern and not conditions`". If the frontmatter declares `conditions: []` (or a malformed/empty list) and omits `pattern`, `conditions` stays `[]` with no error and no fallback [3](#0-2) .

`RuleEngine._rule_matches` explicitly treats an empty `conditions` list as non-matching: "If no conditions, don't match ... return False" [4](#0-3) . Consequently in `evaluate_rules`, the rule is never added to `blocking_rules`, and if it's the only rule, the function returns `{}` (empty allow response) [5](#0-4) .

At no point in this chain (`load_rule_file` → `Rule.from_dict` → `load_rules` → `evaluate_rules` → `stop.py`) is a warning emitted that a `block`-action rule has zero enforceable conditions; the file is treated as a normal, valid, enabled rule.

### Impact Explanation
A hookify `stop` rule with `action: block` is a user/maintainer-authored security control (e.g., "require tests before stopping", per the shipped example in `plugins/hookify/README.md:189-208`) intended to prevent Claude from ending a session under unsafe conditions. If such a file is checked into a repository with `conditions: []` (accidentally, or via a crafted PR from a contributor who wants the rule to appear to enforce a policy while it is actually inert), any developer/reviewer relying on it will believe the block is active while it silently never triggers. `stop.py` still exits `0` and prints only `{}`, giving no indication of misconfiguration — violating the deny-means-deny invariant for a declared blocking rule and creating a false sense of enforced safety.

### Likelihood Explanation
This requires only the ability to add or modify a `.claude/hookify.*.local.md` file that the project later merges/relies on — no elevated privilege, exploit of parsing internals, or special timing is needed. The malformed-frontmatter case (`conditions: []`, no `pattern`) is a single, minimal, easily-produced YAML snippet, and is fully deterministic/repeatable every time `stop.py` runs.

### Recommendation
In `load_rule_file` (or `Rule.from_dict`), validate that any rule with `action: block` has at least one usable condition (either a non-empty `conditions` list with valid `field`/`operator`/`pattern`, or a legacy `pattern`); if not, log a clear error/warning to stderr and either reject the rule (`load_rule_file` returns `None`) or fail closed for `stop`/blocking events instead of silently returning an unenforceable rule. Consider also making `_rule_matches` distinguish "misconfigured rule" from "rule with zero valid conditions" so operators get actionable feedback rather than a silent no-op.

### Proof of Concept
Unit test (extends existing config_loader/rule_engine test patterns):
```python
def test_block_rule_with_empty_conditions_is_silently_inert(tmp_path, monkeypatch):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    rule_file = claude_dir / "hookify.require-tests.local.md"
    rule_file.write_text(
        "---\n"
        "name: require-tests-run\n"
        "enabled: true\n"
        "event: stop\n"
        "action: block\n"
        "conditions: []\n"
        "---\n\n"
        "Tests not detected!\n"
    )
    monkeypatch.chdir(tmp_path)

    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine

    rules = load_rules(event='stop')
    assert len(rules) == 1
    assert rules[0].action == 'block'
    assert rules[0].conditions == []  # accepted with no error

    engine = RuleEngine()
    result = engine.evaluate_rules(rules, {"hook_event_name": "Stop"})
    # Expected (desired) behavior: a decision block, or at minimum a warning
    # Actual behavior: silently allowed
    assert result == {}  # demonstrates the block rule never fires
```
Expected assertion for a fix: `load_rules`/`load_rule_file` should either drop this rule with a logged error, or `evaluate_rules` should return a `decision: block`/error response instead of `{}`.

### Citations

**File:** plugins/hookify/hooks/stop.py (L30-55)
```python
def main():
    """Main entry point for Stop hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Load stop rules
        rules = load_rules(event='stop')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        # On any error, allow the operation
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/core/config_loader.py (L44-84)
```python
    @classmethod
    def from_dict(cls, frontmatter: Dict[str, Any], message: str) -> 'Rule':
        """Create Rule from frontmatter dict and message body."""
        # Handle both simple pattern and complex conditions
        conditions = []

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

**File:** plugins/hookify/core/config_loader.py (L198-241)
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

        except (IOError, OSError, PermissionError) as e:
            # File I/O errors - log and continue
            print(f"Warning: Failed to read {file_path}: {e}", file=sys.stderr)
            continue
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            # Parsing errors - log and continue
            print(f"Warning: Failed to parse {file_path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            # Unexpected errors - log with type details
            print(f"Warning: Unexpected error loading {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
            continue

    return rules
```

**File:** plugins/hookify/core/rule_engine.py (L53-94)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)

        # If any blocking rules matched, block the operation
        if blocking_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in blocking_rules]
            combined_message = "\n\n".join(messages)

            # Use appropriate blocking format based on event type
            if hook_event == 'Stop':
                return {
                    "decision": "block",
                    "reason": combined_message,
                    "systemMessage": combined_message
                }
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"
                    },
                    "systemMessage": combined_message
                }
            else:
                # For other events, just show message
                return {
                    "systemMessage": combined_message
                }

        # If only warnings, show them but allow operation
        if warning_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }

        # No matches - allow operation
        return {}
```

**File:** plugins/hookify/core/rule_engine.py (L115-118)
```python
        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            return False
```
