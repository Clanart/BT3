### Title
Hookify silently fails to block already-completed tool actions on PostToolUse — inconsistent "block" semantics between hook events - (File: plugins/hookify/core/rule_engine.py)

### Summary
This is the closest genuine analog to the reported oracle-boolean-inconsistency bug class. In the FEI report, a decision-signal (`IOracle.update`'s boolean) has different meanings depending on which contract consumes it, causing some call sites to treat a signal as blocking when it isn't. In `hookify`, `RuleEngine.evaluate_rules` emits the exact same "block" payload shape (`hookSpecificOutput.permissionDecision: "deny"`) for both `PreToolUse` and `PostToolUse` events, even though these two events have fundamentally different semantics with respect to whether the underlying tool action can actually be prevented.

### Finding Description
`RuleEngine.evaluate_rules` in `plugins/hookify/core/rule_engine.py` branches on `hook_event`: [1](#0-0) 

For `hook_event in ['PreToolUse', 'PostToolUse']`, it returns an identical `{"hookSpecificOutput": {"hookEventName": hook_event, "permissionDecision": "deny"}}` structure. The plugin's own documentation confirms `permissionDecision` is meaningful for `PreToolUse` (it gates whether the tool runs at all) but `PostToolUse`'s documented output behavior only supports `exit 0`/`exit 2`/`systemMessage` — it has no ability to prevent an action that has already executed: [2](#0-1) 

The `posttooluse.py` executor calls this same `evaluate_rules` and prints whatever it returns without any special-casing for the fact that the tool has already run: [3](#0-2) 

The plugin's own README documents `action: block` as "Prevents operation from executing (PreToolUse) or stops session (Stop events)" — it never describes a distinct/effective behavior for `PostToolUse`, implying users can attach the same `action: block` rule to `event: bash`/`event: file` rules that fire on both `PreToolUse` and `PostToolUse` matchers: [4](#0-3) 

This mirrors the oracle report's core problem: a single boolean/decision value ("block"/`deny`) is treated identically across two call sites whose actual effect differs — one prevents the action, the other is a no-op deception after the fact.

### Impact Explanation
A user (or a plugin author distributing shared hookify rule files) who defines an `action: block` rule believing it will stop a dangerous `Bash` command or a sensitive file write may have that rule fire on the `PostToolUse` path (e.g., due to how rules or matchers are wired, or if a rule is intentionally/accidentally scoped to react after tool completion). Because `evaluate_rules` returns the same "deny" `hookSpecificOutput` shape regardless of timing, the user is shown a `systemMessage` claiming a block occurred while the destructive command (e.g., `rm -rf`, writing secrets, `chmod 777`) has already executed. This creates a false sense of protection for an unprivileged user relying on local, project-scoped hookify rules to prevent dangerous local actions — a workspace/local-compromise-relevant trust boundary issue, not merely cosmetic.

### Likelihood Explanation
Likelihood is moderate: hookify explicitly supports registering the same rule set for `bash`/`file` events which are wired to both `PreToolUse` and `PostToolUse` hooks via `pretooluse.py`/`posttooluse.py`, both of which call the identical `load_rules(event=event)` + `RuleEngine.evaluate_rules` path with no differentiation. Any misconfiguration (or intentional deployment) that attaches an `action: block` rule to the `PostToolUse` hook path — which the current README/docs do not clearly warn against — reproduces this every time the pattern matches, deterministically, with no attacker interaction needed beyond normal usage.

### Recommendation
- In `RuleEngine.evaluate_rules`, do not emit `permissionDecision: "deny"` for `hook_event == 'PostToolUse'`; that field has no preventive effect post-execution. Instead surface a clearly distinct payload (e.g., `systemMessage`-only with explicit "action already occurred" wording, or route to a corrective decision if the framework supports one).
- Update `plugins/hookify/README.md` and `SKILL.md` to explicitly state that `action: block` has no effect for events that only fire post-execution (`PostToolUse`), so plugin/rule authors do not build false safety expectations.
- Add a test/lint check to `hook-linter.sh` (which already inspects `PreToolUse|Stop` decision JSON) to flag rule files with `action: block` scoped only to post-execution events.

### Proof of Concept
1. Create `.claude/hookify.block-rm.local.md`:
```markdown
---
name: block-rm
enabled: true
event: bash
action: block
pattern: "rm\s+-rf"
---
Blocked dangerous rm -rf command.
```
2. Because `event: bash` rules are loaded by both `pretooluse.py` and `posttooluse.py` (both filter with the same `event='bash'`), and `evaluate_rules` treats both hook events identically at lines 72-79 of `rule_engine.py`, if this rule's condition happens to only be reachable/triggered via the `PostToolUse` invocation path (e.g. a matcher/config wiring that registers hookify only on `PostToolUse`, or a future/plugin config that does so), Claude Code will run `rm -rf ...` to completion and only afterward display `{"hookSpecificOutput": {"permissionDecision": "deny"}, "systemMessage": "Blocked dangerous rm -rf command."}` — falsely indicating the destructive command was prevented when the files are already deleted.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L60-79)
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
```

**File:** plugins/plugin-dev/skills/hook-development/SKILL.md (L144-179)
```markdown
**Output for PreToolUse:**
```json
{
  "hookSpecificOutput": {
    "permissionDecision": "allow|deny|ask",
    "updatedInput": {"field": "modified_value"}
  },
  "systemMessage": "Explanation for Claude"
}
```

### PostToolUse

Execute after tool completes. Use to react to results, provide feedback, or log.

**Example:**
```json
{
  "PostToolUse": [
    {
      "matcher": "Edit",
      "hooks": [
        {
          "type": "prompt",
          "prompt": "Analyze edit result for potential issues: syntax errors, security vulnerabilities, breaking changes. Provide feedback."
        }
      ]
    }
  ]
}
```

**Output behavior:**
- Exit 0: stdout shown in transcript
- Exit 2: stderr fed back to Claude
- systemMessage included in context
```

**File:** plugins/hookify/hooks/posttooluse.py (L30-62)
```python
def main():
    """Main entry point for PostToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Determine event type based on tool
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

    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/README.md (L93-95)
```markdown
**Action field:**
- `warn`: Shows warning but allows operation (default)
- `block`: Prevents operation from executing (PreToolUse) or stops session (Stop events)
```
