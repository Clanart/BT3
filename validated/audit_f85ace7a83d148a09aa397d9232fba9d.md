### Title
Uncaught `TypeError` from a boolean `pattern` field lets a crafted `.local.md` rule crash `evaluate_rules()` mid-loop, discarding already-matched `action: block` rules and causing `stop.py` to fail open — ([File: plugins/hookify/hooks/stop.py])

### Summary
`stop.py` wraps `load_rules()` and `RuleEngine.evaluate_rules()` in a single broad `try/except Exception` that, on *any* exception, discards all rule-evaluation state and emits only `{"systemMessage": ...}` with no `decision: block` key [1](#0-0) . A malicious `hookify.*.local.md` rule file using an unquoted top-level `pattern: true` value is parsed by the mini-YAML parser into a Python `bool`, which later reaches `re.compile(pattern, ...)` and raises an uncaught `TypeError` (not `re.error`), aborting `evaluate_rules()` after it has already accumulated a genuinely matching `blocking_rules` entry from an earlier rule in the same file set.

### Finding Description
`extract_frontmatter()` in `config_loader.py` converts any bare top-level `key: value` pair where the value textually equals `true`/`false` into a Python `bool` [2](#0-1) . `Rule.from_dict()` uses this top-level `pattern` field directly as `Condition.pattern` for a hardcoded `operator='regex_match'` condition without ever coercing it to `str` [3](#0-2) .

During evaluation, `RuleEngine._check_condition()` dispatches `regex_match` conditions to `_regex_match(pattern, field_value)` [4](#0-3) , which calls `compile_regex(pattern)` → `re.compile(pattern, re.IGNORECASE)` [5](#0-4) . When `pattern` is the Python bool `True`, `re.compile` raises `TypeError`, which is *not* a subclass of `re.error` and is therefore not caught by the `except re.error` handler in `_regex_match` [6](#0-5) .

This exception propagates unhandled through `_check_condition` → `_rule_matches` → the `for rule in rules` loop in `evaluate_rules()` [7](#0-6) . If, earlier in iteration order, a genuine `action: block` rule already matched and was appended to the local `blocking_rules` list, that list is never returned — the exception unwinds the whole function, and `stop.py`'s outer handler catches it and emits only `{"systemMessage": "Hookify error: ..."}` with no `decision`/`reason` keys [8](#0-7) . Since `glob.glob` file ordering is attacker-influenceable by file naming (`.claude/hookify.*.local.md`) [9](#0-8) , an attacker can reliably place the crashing rule after the legitimate block rule.

### Impact Explanation
This is a fail-open bypass of the block-enforcement path: an actually-matching `action: block` Stop-hook rule is silently discarded and the Stop event proceeds unblocked, defeating the intended enforcement guarantee that hook errors should not let a matched block rule pass through. This maps to an approval/enforcement bypass impact — a rule that is supposed to halt the agent (Stop hook block) fails to do so due to an unrelated crash triggered by attacker-controlled configuration content.

### Likelihood Explanation
Exploitation only requires the ability to place or influence a `.claude/hookify.*.local.md` file (e.g., via a plugin, shared repo config, or any workflow that writes hookify rule files from untrusted content) with a bare `pattern: true` top-level field, ordered after a legitimate blocking rule. No special privileges, injected keys, or social engineering are needed; the mini-YAML parser's boolean coercion and the `re.compile`/`re.error` mismatch are deterministic, so the crash is 100% repeatable once such a file is loaded for a Stop event.

### Recommendation
- In `_regex_match`, catch `TypeError` (or broaden to `except (re.error, TypeError)`) and treat it as a non-match/invalid-pattern warning, mirroring existing defensive handling of `re.error`.
- In `Condition.from_dict`/`Rule.from_dict`, coerce `pattern` to `str` explicitly before storing, so a YAML-coerced bool/other type can never reach `re.compile`.
- More fundamentally, make `evaluate_rules()` fail closed for `action: block` semantics: compute `blocking_rules` incrementally and short-circuit/return a block decision as soon as one is found (or wrap each rule's evaluation in a per-rule try/except so one bad rule cannot discard already-matched blocking rules), instead of relying on `stop.py`'s outer catch-all which can only fail open.

### Proof of Concept
Integration test outline (pytest):
1. Create two files in a temp `.claude` directory:
   - `hookify.block.local.md`:
     ```
     ---
     name: legit-block
     enabled: true
     event: stop
     action: block
     pattern: "SECRET_LEAK"
     ---
     Blocked: secret detected.
     ```
   - `hookify.crash.local.md` (sorts after the above via `glob.glob`):
     ```
     ---
     name: crasher
     enabled: true
     event: stop
     pattern: true
     ---
     Should never run cleanly.
     ```
2. Set stdin input JSON with `hook_event_name: "Stop"` and a `transcript_path` (or `reason`) field containing `"SECRET_LEAK"` so the legit rule's condition matches.
3. Run `plugins/hookify/hooks/stop.py` as a subprocess (or call `main()` directly with mocked stdin/cwd pointed at the temp `.claude` dir).
4. Assert:
   - Without the crasher file: output JSON contains `"decision": "block"`.
   - With both files present (crasher ordered after legit block rule): output JSON contains only `"systemMessage"` and **omits** `"decision": "block"`, proving the fail-open bypass — even though `legit-block` genuinely matched the transcript content.

### Citations

**File:** plugins/hookify/hooks/stop.py (L32-55)
```python
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

**File:** plugins/hookify/core/config_loader.py (L146-152)
```python
                # Simple key-value pair
                value = value.strip('"').strip("'")
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                frontmatter[key] = value
```

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

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

**File:** plugins/hookify/core/rule_engine.py (L53-58)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)
```

**File:** plugins/hookify/core/rule_engine.py (L166-167)
```python
        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
```

**File:** plugins/hookify/core/rule_engine.py (L266-273)
```python
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            return bool(regex.search(text))

        except re.error as e:
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False
```
