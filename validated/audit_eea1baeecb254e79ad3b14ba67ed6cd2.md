### Title
Hookify's `_regex_match`/`compile_regex` have no ReDoS protection or execution timeout, allowing catastrophic-backtracking regex to hang PreToolUse evaluation and bypass a `block` rule via hook timeout - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`RuleEngine._regex_match()` calls `regex.search(text)` on attacker-influenceable text (e.g. `new_text`/`command` derived from repo/PR/issue content) using a pattern from a hookify rule, with no length cap, no complexity check, and no wall-clock timeout around the match call. If the pattern (from a `.claude/hookify.*.local.md` rule, itself repo content) exhibits catastrophic backtracking, or is paired with a crafted non-matching suffix, `re.search()` can run for an unbounded time, exceeding the 10-second hook `timeout` declared in `plugins/hookify/hooks/hooks.json` and preventing the `block` decision from ever being emitted.

### Finding Description
The call chain is `pretooluse.py:main()` → `load_rules()` → `RuleEngine.evaluate_rules()` → `_rule_matches()` → `_check_condition()` → `_regex_match()` → `compile_regex()`/`re.search()`. [1](#0-0) 

`compile_regex` simply does `re.compile(pattern, re.IGNORECASE)` with an LRU cache, and `_regex_match` calls `regex.search(text)` directly with no timeout mechanism (no `signal.alarm`, no thread-based timeout, no regex-shape validation): [2](#0-1) 

The matched field values (`command`, `new_text`, `content`) come straight from `tool_input`, which is populated from the Bash command or file edit content Claude Code is about to execute/write — content that can originate from repository/issue/PR text the model copies into a `Write`/`Edit` call: [3](#0-2) 

Both the regex *pattern* and the *text* side of the match are sourced from repo-adjacent content: rule patterns live in `.claude/hookify.*.local.md` files that are ordinary repository files parsed by `config_loader.py`'s `load_rule_file`/`extract_frontmatter`, with the `pattern` field taken verbatim from YAML frontmatter and passed unchecked into `Condition.pattern`: [4](#0-3) 

Notably, the sibling `security-guidance` plugin already recognizes this exact class of risk and defends against it with a heuristic `_has_redos_structure()` check (nested quantifiers, `(.*)*`, overlapping alternation under repetition) before accepting any user-supplied regex: [5](#0-4) 

`plugins/hookify/core/rule_engine.py` has no equivalent check at all — any pattern from a `.local.md` rule file is compiled and executed against attacker-influenceable text with zero mitigation.

The hook process itself only has a generic timeout configured externally in `hooks.json` (`"timeout": 10`): [6](#0-5) 

`pretooluse.py`'s own error-handling philosophy is explicitly fail-open: any exception inside `main()` is caught, an allow-response is printed, and the process always `sys.exit(0)`s so it "never block[s] operations due to hook errors": [7](#0-6) 

If `re.search()` hangs inside `_regex_match()`, this try/except never even triggers (the process is stuck in native regex backtracking, not raising a Python exception), so the process must be killed externally when the 10-second hook timeout elapses. Because a `block`-action rule's deny decision is only ever emitted after `evaluate_rules()` fully returns, a hang guarantees the `deny` response is never produced in time — the process is terminated by the hook-timeout mechanism instead of returning JSON, which is exactly the same "no valid decision received" condition that the code around it treats as allow-and-continue.

### Impact Explanation
This defeats the fail-closed guarantee that a hookify `action: block` rule (e.g. blocking `rm -rf`, secret-looking edits, or dangerous file writes) is supposed to provide. An attacker who can get repository content matched against a block rule's pattern (via a `new_text`/`command` value ultimately derived from PR/issue text that Claude Code writes/executes) can force the PreToolUse hook to time out instead of returning a `deny` decision, letting the dangerous `Bash`/`Write`/`Edit` operation proceed — a real block-hook bypass via resource exhaustion rather than logic bypass.

### Likelihood Explanation
Feasibility depends on two independent preconditions being met by an unprivileged actor:
1. A hookify rule with a catastrophic-backtracking pattern must exist and be enabled with `action: block`. This can happen either because a maintainer wrote such a pattern unknowingly (there is no linting/validation preventing it, unlike `security-guidance`), or because the attacker's own PR contributes/modifies a `.claude/hookify.*.local.md` file that gets merged (repo content, not privileged config).
2. The attacker needs to get a crafted pathological string into the matched field (`command`/`new_text`/`content`), which is plausible when Claude Code copies PR/issue text verbatim into a `Write`/`Edit` tool call or constructs a `Bash` command from repo-derived text.

Given zero regex-shape or timeout hardening in `rule_engine.py`, the mechanism itself is fully reproducible with a classic ReDoS pattern such as `(a+)+$` and standard trigger inputs (`"a"*N + "!"`), which is well-documented to hang `re.search()` for exponential time in CPython.

### Recommendation
- Add the same (or stronger) ReDoS-structure validation used in `plugins/security-guidance/hooks/extensibility.py` (`_has_redos_structure`) to `config_loader.load_rule_file`/`Rule.from_dict`, rejecting or warning on patterns with nested quantifiers, wildcard groups under repetition, or overlapping alternation under repetition.
- Wrap `re.search()` in `_regex_match()` with a hard per-match timeout (e.g. via a worker thread/process with `concurrent.futures` and a short deadline, or use the `regex` module's timeout support), and treat a timeout as `False` (no match) rather than letting the whole hook process hang.
- Consider making resource-exhaustion/timeout in a `block` rule's evaluation fail closed (deny) rather than silently letting Claude Code proceed when the PreToolUse hook process is killed by the external timeout.
- Cap the length of matched `field_value` text passed into regex evaluation for safety.

### Proof of Concept
Add a fuzz/unit test in the hookify test suite (or a new test file) that:
1. Constructs a `Rule` with `action='block'`, `event='bash'`, and `conditions=[Condition(field='command', operator='regex_match', pattern='(a+)+$')]`.
2. Builds a malicious `tool_input.command` value: `"a" * 40 + "!"` (classic ReDoS trigger for `(a+)+$`).
3. Calls `RuleEngine().evaluate_rules([rule], {"tool_name": "Bash", "tool_input": {"command": payload}, "hook_event_name": "PreToolUse"})` inside a hard wall-clock timeout (e.g. 3 seconds using `signal.alarm` or a subprocess with `timeout=`).
4. Assert either: (a) the call returns within the timeout with a `permissionDecision: deny` result, or (b) if the call does NOT return within the timeout, this itself proves the vulnerability (evaluation time grows exponentially with payload length: 30/35/40/45 'a' characters should show doubling runtime), demonstrating that a `block` rule can be starved out rather than deterministically deny.
5. End-to-end variant: invoke `plugins/hookify/hooks/pretooluse.py` as a subprocess with the same crafted `tool_input` piped via stdin and `subprocess.run(..., timeout=10)` matching the `hooks.json` configured timeout, asserting the subprocess is killed/times out instead of producing a deny JSON response — proving the fail-open path is reached.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L14-24)
```python
@lru_cache(maxsize=128)
def compile_regex(pattern: str) -> re.Pattern:
    """Compile regex pattern with caching.

    Args:
        pattern: Regex pattern string

    Returns:
        Compiled regex pattern
    """
    return re.compile(pattern, re.IGNORECASE)
```

**File:** plugins/hookify/core/rule_engine.py (L230-252)
```python
        # Handle special cases by tool type
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')

        elif tool_name in ['Write', 'Edit']:
            if field == 'content':
                # Write uses 'content', Edit has 'new_string'
                return tool_input.get('content') or tool_input.get('new_string', '')
            elif field == 'new_text' or field == 'new_string':
                return tool_input.get('new_string', '')
            elif field == 'old_text' or field == 'old_string':
                return tool_input.get('old_string', '')
            elif field == 'file_path':
                return tool_input.get('file_path', '')

        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)
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

**File:** plugins/hookify/core/config_loader.py (L56-73)
```python
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

**File:** plugins/security-guidance/hooks/extensibility.py (L262-288)
```python
# Catastrophic backtracking: nested quantifiers, overlapping alternations
# under repetition, and wildcard groups under repetition. Static check, not a
# proof — catches the common shapes that hang the hook on every edit.
_REDOS_SHAPES = [
    re.compile(r"\([^()]*[+*][^()]*\)[+*?]"),  # nested quantifier: (a+)*  (a*b)*
    re.compile(r"\(\.\*[^()]*\)[+*]"),         # wildcard group: (.*)*
]
_ALT_UNDER_REP = re.compile(r"\(([^()]*)\|([^()|]*)(?:\|[^()]*)*\)[+*]")


def _has_redos_structure(regex: str) -> bool:
    """Heuristic catastrophic-backtracking check. Not a proof. Catches:
      - nested quantifiers ((a+)*, (a*b)+)
      - wildcard groups under repetition ((.*)*)
      - alternation under repetition where one branch is a prefix of another
        ((a|aa)*, (ab|a)*) — these overlap and explode on non-matching input.
    Does NOT flag non-overlapping alternation ((a|b)*) which is safe."""
    if any(p.search(regex) for p in _REDOS_SHAPES):
        return True
    for m in _ALT_UNDER_REP.finditer(regex):
        branches = [b for b in m.group(0).strip("()*+").split("|") if b]
        for i, a in enumerate(branches):
            for b in branches[i + 1:]:
                # If one branch is a literal prefix of another, the alternation
                # overlaps and the engine backtracks combinatorially.
                if a.startswith(b) or b.startswith(a):
                    return True
```

**File:** plugins/hookify/hooks/hooks.json (L4-13)
```json
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
            "timeout": 10
          }
        ]
      }
```

**File:** plugins/hookify/hooks/pretooluse.py (L61-70)
```python
    except Exception as e:
        # On any error, allow the operation and log
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0 - never block operations due to hook errors
        sys.exit(0)
```
