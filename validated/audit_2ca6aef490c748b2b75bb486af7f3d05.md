### Title
`RuleEngine.evaluate_rules` never emits `"decision":"block"` for `UserPromptSubmit`, so `action: block` prompt rules only display a message and do not stop the prompt - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine.evaluate_rules` special-cases only `hook_event == 'Stop'` (emits `decision: block`) and `hook_event in ['PreToolUse', 'PostToolUse']` (emits `permissionDecision: deny`); every other `hook_event_name`, including `UserPromptSubmit`, falls into the generic `else` branch that returns only `{"systemMessage": combined_message}`. Because `userpromptsubmit.py` calls this engine and prints whatever dict is returned, a `hookify.*.local.md` rule with `event: prompt` and `action: block` will match and generate a warning message but will not actually halt processing of the attacker-controlled prompt.

### Finding Description
The call chain is exactly as described: `main()` in [1](#0-0)  reads stdin JSON, calls `load_rules(event='prompt')`, then `engine.evaluate_rules(rules, input_data)`, and unconditionally prints the returned dict with exit code 0 (see the `finally: sys.exit(0)` in the same function [2](#0-1) ).

Inside `evaluate_rules`, `hook_event` is read from `input_data.get('hook_event_name', '')` [3](#0-2) . When Claude Code invokes this script for a `UserPromptSubmit` event, `hook_event_name` will be `"UserPromptSubmit"`, which is neither `'Stop'` nor in `['PreToolUse', 'PostToolUse']`, so execution falls into the generic branch that returns only `{"systemMessage": combined_message}` with no blocking signal at all [4](#0-3) . Compare this to the `Stop`-specific branch that returns `"decision": "block"` [5](#0-4)  and the `PreToolUse`/`PostToolUse` branch that returns `"permissionDecision": "deny"` [6](#0-5) .

The `Rule` dataclass explicitly documents `action: "warn" or "block"` as first-class fields loaded from repo-controlled `.claude/hookify.*.local.md` frontmatter [7](#0-6) , and `load_rules(event='prompt')` will load any rule with `event: 'all'` or `event: 'prompt'` [8](#0-7) . So an attacker who can place or influence such a checked-in rule file (or its message content, since the file is repo content parsed without sandboxing) can define `action: block, event: prompt`, expecting it to gate `UserPromptSubmit`; the code silently treats it as a mere warning.

### Impact Explanation
Guardrails intended to block dangerous prompt content (e.g., "block prompts requesting credential exfiltration" style rules) become cosmetic: the hook always returns a plain `systemMessage` for `UserPromptSubmit` and exits 0 without any block-equivalent field, so Claude Code treats the turn as approved and continues processing the prompt as normal. This is a trust-boundary/approval bypass in the plugin's own prompt-level guardrail feature — the deny-by-policy invariant ("action: block" should stop processing) is violated for the entire `prompt` event class, which is one of the two intended blocking targets of the `hookify` plugin (the other being tool calls).

### Likelihood Explanation
This triggers on every single invocation where a `prompt`-scoped `block` rule matches a `UserPromptSubmit` event — no attacker interaction beyond having such a rule file present (whether shipped in the repo, added by a compromised contributor, or synced from an untrusted source of hookify configs) is required. It is 100% reproducible and deterministic given the code path shown above.

### Recommendation
In `RuleEngine.evaluate_rules`, add an explicit case for `hook_event == 'UserPromptSubmit'` that returns the correct blocking schema for that hook type (e.g., `{"decision": "block", "reason": combined_message}` per Claude Code's `UserPromptSubmit` hook output contract), rather than falling through to the generic message-only branch.

### Proof of Concept
Unit test in `plugins/hookify/core/rule_engine.py` test harness style:
```python
rule = Rule(name="block-prompt", enabled=True, event="prompt", action="block",
            conditions=[Condition(field="user_prompt", operator="contains", pattern="secret")],
            message="Blocked!")
engine = RuleEngine()
result = engine.evaluate_rules([rule], {
    "hook_event_name": "UserPromptSubmit",
    "user_prompt": "please leak the secret"
})
assert "decision" in result and result["decision"] == "block", (
    f"Expected blocking decision for UserPromptSubmit, got: {result}"
)
```
Running this against the current implementation fails the assertion because `result == {"systemMessage": "**[block-prompt]**\nBlocked!"}`, confirming no blocking signal is emitted for `UserPromptSubmit`.

### Citations

**File:** plugins/hookify/hooks/userpromptsubmit.py (L30-44)
```python
def main():
    """Main entry point for UserPromptSubmit hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Load user prompt rules
        rules = load_rules(event='prompt')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
```

**File:** plugins/hookify/hooks/userpromptsubmit.py (L52-54)
```python
    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/core/rule_engine.py (L49-49)
```python
        hook_event = input_data.get('hook_event_name', '')
```

**File:** plugins/hookify/core/rule_engine.py (L66-71)
```python
            if hook_event == 'Stop':
                return {
                    "decision": "block",
                    "reason": combined_message,
                    "systemMessage": combined_message
                }
```

**File:** plugins/hookify/core/rule_engine.py (L72-79)
```python
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"
                    },
                    "systemMessage": combined_message
                }
```

**File:** plugins/hookify/core/rule_engine.py (L80-84)
```python
            else:
                # For other events, just show message
                return {
                    "systemMessage": combined_message
                }
```

**File:** plugins/hookify/core/config_loader.py (L32-42)
```python
@dataclass
class Rule:
    """A hookify rule."""
    name: str
    enabled: bool
    event: str  # "bash", "file", "stop", "all", etc.
    pattern: Optional[str] = None  # Simple pattern (legacy)
    conditions: List[Condition] = field(default_factory=list)
    action: str = "warn"  # "warn" or "block" (future)
    tool_matcher: Optional[str] = None  # Override tool matching
    message: str = ""  # Message body from markdown
```

**File:** plugins/hookify/core/config_loader.py (L219-222)
```python
            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue
```
