I have sufficient evidence to render a verdict now.

### Title
Regex-based Bash command matching in Hookify's `_regex_match` is trivially evaded via shell expansion tricks (IFS/ANSI-C quoting), allowing blocked destructive commands to execute - ([File: plugins/hookify/core/rule_engine.py])

### Summary
Hookify's `RuleEngine._check_condition` / `_regex_match` matches a rule's `pattern` against the *literal, unexpanded* `tool_input.command` string using `re.search`, without any shell tokenization or normalization. An attacker-influenced Bash command (e.g. from repository automation, PR-suggested commands, or agent-generated shell) can use shell substitution primitives such as `${IFS}` or ANSI-C quoting (`$'\x20'`) in place of literal whitespace to keep the textual command outside the scope of a `rm\s+-rf`-style pattern while the shell still expands and executes the equivalent destructive command.

### Finding Description
The call chain is `plugins/hookify/hooks/pretooluse.py:main()` → `RuleEngine.evaluate_rules()` → `RuleEngine._rule_matches()` → `RuleEngine._check_condition()` → `RuleEngine._regex_match()`. [1](#0-0)  The engine extracts the raw `command` field from `tool_input` via `_extract_field` [2](#0-1)  and, for `regex_match` conditions, runs `re.compile(pattern, re.IGNORECASE).search(text)` against that literal string. [3](#0-2) 

A recommended/typical `block-dangerous-rm` rule uses `pattern: rm\s+-rf` as documented in the plugin's own README and skill docs. [4](#0-3)  `\s+` only matches whitespace characters within the literal string being matched. Bash, however, performs word-splitting/expansion at execution time using several mechanisms that do not require a literal space character in the source text: `IFS`-based splitting (`rm${IFS}-rf`, `rm${IFS n}-rf`), ANSI-C quoting (`rm$'\x20'-rf`), or other de-obfuscation vectors (`rm -r""f`, backslash-newline continuations, brace/variable expansion of the flag). None of these produce a literal `rm -rf`-matching substring at hook time, so `_regex_match` returns `False` and `evaluate_rules` returns `{}` (no block), while the shell that actually executes the command still expands these constructs into the equivalent of `rm -rf`. There is no command normalization, `shlex`/AST-based parsing, or actual dry-run/simulation of shell expansion anywhere in `rule_engine.py` or `pretooluse.py` before the regex check, and the hook always exits 0 without altering the underlying tool execution path other than the `permissionDecision: deny` message when a match is found. [5](#0-4) 

### Impact Explanation
This breaks the deny/enforcement invariant the plugin advertises: a user (or automation) that has configured a `block-dangerous-rm` (or similar destructive-operation) rule believes destructive Bash operations are prevented, but an attacker who can influence the exact command text passed to the `Bash` tool (e.g., through repository content, generated scripts, or PR/issue text that Claude is asked to run) can construct a command that is byte-for-byte different from the blocked pattern yet semantically identical once the shell expands it. The result is unauthorized execution of a destructive operation (e.g., `rm -rf`) despite an active, enabled `action: block` rule — a direct bypass of a security control the user explicitly configured to prevent unwanted/destructive command execution.

### Likelihood Explanation
Preconditions are modest and realistic: the victim only needs one hookify rule with `event: bash` and a regex pattern targeting a dangerous command (the plugin's own documentation and default examples recommend exactly this, e.g. `rm\s+-rf`). The bypass techniques (`$IFS`, ANSI-C quoting, string concatenation of flags) are well-known, standard Bash obfuscation idioms requiring no special privileges — any command text reaching the `Bash` tool (whether typed by the user, suggested by an LLM completion influenced by adversarial repo/PR content, or scripted) can use this form. The bug is deterministic and 100% reproducible for any regex-based rule that assumes literal whitespace/token boundaries.

### Recommendation
Do not rely on raw regex matching against the unexpanded command string for security-relevant blocking:
- Normalize/tokenize the command before pattern matching, e.g. using `shlex.split` combined with expansion of common obfuscation primitives (`$IFS`, ANSI-C `$'...'`, string concatenation of adjacent quoted fragments) or, better, invoke `bash -n`/`shellcheck`-style AST parsing to canonicalize the command into its constituent argv-equivalent before matching.
- Alternatively/additionally, match against a shell-expanded preview (e.g., `bash -c 'echo ...'`-style dry expansion in a sandbox) rather than the literal source text.
- At minimum, document this limitation prominently and treat hookify `block` rules as best-effort deterrents rather than a security boundary, since regex-on-literal-text can never be a complete defense against Bash's expansion semantics.

### Proof of Concept
Integration test extending the existing `RuleEngine` test pattern in `plugins/hookify/core/rule_engine.py`:
```python
from hookify.core.config_loader import Condition, Rule
from hookify.core.rule_engine import RuleEngine

rule = Rule(
    name="block-dangerous-rm",
    enabled=True,
    event="bash",
    action="block",
    conditions=[Condition(field="command", operator="regex_match", pattern=r"rm\s+-rf")],
    message="Dangerous rm command!"
)
engine = RuleEngine()

evasive_commands = [
    "rm${IFS}-rf${IFS}/tmp/test",
    "rm$'\\x20'-rf$'\\x20'/tmp/test",
    "rm${IFS}-r${IFS}-f${IFS}/tmp/test",
]

for cmd in evasive_commands:
    input_data = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
    }
    result = engine.evaluate_rules([rule], input_data)
    # Expected (desired) behavior: block; Actual behavior: {} (no block)
    assert result == {}, f"Rule unexpectedly blocked evasive command: {cmd}"
```
Expected assertion outcome demonstrates that `evaluate_rules` returns `{}` (allow) for all evasive variants, while a real Bash shell run of the same `cmd` string executes the equivalent of `rm -rf /tmp/test`, proving the deny invariant is violated.

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L51-59)
```python
        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
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

**File:** plugins/hookify/core/rule_engine.py (L231-233)
```python
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')
```

**File:** plugins/hookify/core/rule_engine.py (L256-273)
```python
    def _regex_match(self, pattern: str, text: str) -> bool:
        """Check if pattern matches text using regex.

        Args:
            pattern: Regex pattern
            text: Text to match against

        Returns:
            True if pattern matches
        """
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            return bool(regex.search(text))

        except re.error as e:
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False
```

**File:** plugins/hookify/README.md (L75-83)
```markdown
`.claude/hookify.dangerous-rm.local.md`:
```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---
```
