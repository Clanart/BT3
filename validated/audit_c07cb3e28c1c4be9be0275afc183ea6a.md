### Title
Case/whitespace-sensitive `rule.action == 'block'` comparison silently downgrades a crafted "block" rule to a warning, bypassing hookify's PreToolUse/Stop enforcement - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine.evaluate_rules` classifies a matched rule as blocking only via the exact string comparison `rule.action == 'block'` [1](#0-0) . Because `Rule.action` is taken verbatim from the rule file's YAML-like frontmatter without case-folding, and the frontmatter parser can preserve trailing whitespace when the value is quoted, an attacker-crafted `.claude/hookify.*.local.md` file with `action: Block` or `action: "block "` matches no rule and is silently routed into `warning_rules` instead of `blocking_rules`, so the dangerous operation proceeds with only a cosmetic warning message.

### Finding Description
`load_rules()`/`load_rule_file()` parse any `.claude/hookify.*.local.md` file found in the current working directory with no signing, allowlisting, or user-confirmation step [2](#0-1) . The frontmatter value for `action` is extracted as a raw string: for an unquoted simple value it is only `.strip()`ped once (case is never normalized) [3](#0-2) ; for a quoted value such as `action: "block "`, the code strips the outer quote characters with `.strip('"').strip("'")` but does not re-strip internal whitespace, so a trailing space inside the quotes survives into `frontmatter['action']` [4](#0-3) . `Rule.from_dict` stores this raw value directly as `action=frontmatter.get('action', 'warn')` with no validation against the enum `{'warn','block'}` [5](#0-4) .

`RuleEngine.evaluate_rules` then does an exact, case-sensitive equality check: `if rule.action == 'block': blocking_rules.append(rule) else: warning_rules.append(rule)` [1](#0-0) . Any value that is not byte-for-byte `"block"` (e.g. `"Block"`, `"BLOCK"`, or `"block "`) falls through to the `else` branch and is treated as a non-blocking warning, even though the rule visually/semantically declares itself as a blocking rule. Blocking rules produce a `permissionDecision: deny` (PreToolUse/PostToolUse) or `decision: block` (Stop) response that actually halts the tool call or session [6](#0-5) ; warning rules only attach a `systemMessage` and allow the operation to continue [7](#0-6) . `pretooluse.py`/`stop.py` call `RuleEngine.evaluate_rules` directly on the loaded rules with no secondary validation of the `action` field [8](#0-7) , so the miscased/whitespace-polluted rule is never caught anywhere in the pipeline.

Because hookify rule files live in ordinary repository content (`.claude/hookify.*.local.md`) and are auto-loaded with no approval prompt when the plugin is active, an attacker who can get such a file into a shared repository (e.g., via a pull request that appears to add a legitimate `action: block` rule for a dangerous command, but is actually typoed to `action: Block`) causes the intended enforcement to silently degrade to a warning. A reviewer or user inspecting the frontmatter casually would likely not notice the case/whitespace difference, believing the dangerous command (e.g. `rm -rf`) is blocked when it is not.

### Impact Explanation
This is a trust-boundary/enforcement bypass in Claude Code's own hook-based guardrail plugin: a rule that is supposed to deny a `PreToolUse`/`Stop` event (`permissionDecision: deny` / `decision: block`) is silently converted into an advisory-only warning, allowing an otherwise-blocked dangerous command (e.g. `rm -rf`, `dd`, destructive edits) to execute. This matches the "unauthorized command execution due to hook/enforcement bypass" impact category, since the protection a user or team relies on (hookify's block action) fails open without any error, log escalation, or user-visible indication that the rule is non-blocking.

### Likelihood Explanation
Exploitation only requires the attacker to get a `.claude/hookify.*.local.md` file with a subtly miscased or whitespace-polluted `action` value into a repository that a victim opens with Claude Code and the hookify plugin enabled (e.g., via a PR, template repo, or supply-chain dependency). No privilege escalation, secrets, or social engineering beyond normal PR/content review is required, and the bug is 100% deterministic — any value other than the exact string `"block"` triggers the fail-open path.

### Recommendation
Normalize and validate the `action` field at both parse time and evaluation time: e.g., in `Rule.from_dict`, do `action=str(frontmatter.get('action', 'warn')).strip().lower()` and reject/log unknown values (anything other than `warn`/`block`) rather than silently defaulting to warn. In `RuleEngine.evaluate_rules`, defensively compare with `rule.action.strip().lower() == 'block'` instead of relying solely on upstream normalization.

### Proof of Concept
Unit test in `plugins/hookify/core/rule_engine.py` / `config_loader.py` test suite:
```python
from hookify.core.config_loader import Rule, extract_frontmatter
from hookify.core.rule_engine import RuleEngine

def test_miscased_action_silently_bypasses_block():
    content = '''---
name: fake-block-rm
enabled: true
event: bash
pattern: rm\\s+-rf
action: Block
---

This looks like it blocks rm -rf but it will NOT.
'''
    frontmatter, message = extract_frontmatter(content)
    rule = Rule.from_dict(frontmatter, message)
    assert rule.action == "Block"  # not normalized

    engine = RuleEngine()
    result = engine.evaluate_rules([rule], {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /important"}
    })

    # BUG: expected a deny decision, but got only a systemMessage (warning),
    # meaning the "rm -rf" command would NOT be blocked.
    assert "hookSpecificOutput" not in result
    assert "systemMessage" in result
```
Expected (buggy) result: no `hookSpecificOutput`/`permissionDecision: deny` is produced, confirming the rule silently degraded to a warning and the dangerous command would be allowed to execute.

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

**File:** plugins/hookify/core/rule_engine.py (L60-84)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L86-91)
```python
        # If only warnings, show them but allow operation
        if warning_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }
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

**File:** plugins/hookify/core/config_loader.py (L136-152)
```python
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

**File:** plugins/hookify/hooks/pretooluse.py (L45-59)
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

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
```
