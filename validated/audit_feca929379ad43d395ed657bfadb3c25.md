### Title
ReDoS in hookify pattern matching causes PreToolUse hook timeout, allowing fail-open bypass of "block" rules - (File: plugins/hookify/core/rule_engine.py)

### Summary
`compile_regex`/`_regex_match` in `rule_engine.py` compiles and runs attacker-controlled regex patterns from `.claude/hookify.*.local.md` rule files with no timeout, complexity limit, or safe-regex validation. A repo-checked-in rule with a catastrophic-backtracking pattern (e.g. `(a+)+b`) evaluated against a sufficiently long, non-matching string (which `tool_input.command`/`new_text` can contain, including strings the assistant itself generates during autonomous edits) causes `re.search` to hang, exhausting the hook's 10s timeout budget defined in `hooks.json`.

### Finding Description
`load_rules()` in `plugins/hookify/core/config_loader.py` globs `.claude/hookify.*.local.md` [1](#0-0)  and parses `pattern`/`conditions[].pattern` directly from YAML frontmatter with no validation of regex safety [2](#0-1) . These files are ordinary repository content — if checked into a repo, an unprivileged attacker who contributes the repo controls the `pattern` field entirely.

At evaluation time, `pretooluse.py` calls `RuleEngine.evaluate_rules` → `_rule_matches` → `_check_condition` → `_regex_match` → `compile_regex(pattern).search(text)` with no timeout, resource limit, or ReDoS-safety check [3](#0-2) . `compile_regex` is a plain `lru_cache`-wrapped `re.compile` [4](#0-3) . Python's `re` engine is a backtracking engine vulnerable to catastrophic backtracking on patterns like `(a+)+b` when matched against long adversarial strings (e.g., many `a`s with no trailing `b`).

The hook process itself only guards against *exceptions* — `pretooluse.py` wraps evaluation in try/except with a `finally: sys.exit(0)` so normal errors fail open safely [5](#0-4) , but an infinite/very-long-running regex match never raises an exception and never reaches that `finally` — the interpreter is simply stuck in `re.search`, and control returns only when the external process is killed by the host's hook timeout (`hooks.json` sets `"timeout": 10` for the PreToolUse hook) [6](#0-5) .

The attack requires the field being matched (`command` for bash, `new_text`/`content` for file edits) to contain a long enough string to trigger exponential backtracking. The scenario described — code-simplifier's autonomous refactor emitting long matching strings during file edits — supplies exactly that: `Edit`/`Write`/`MultiEdit` tool calls route through the same `_extract_field`/`_regex_match` path for `event: file` rules [7](#0-6) , so a long string produced by an autonomous refactor (e.g., a long line of repeated characters) can trigger the pathological pattern.

No existing check in this codebase stops it: there is no pattern-safety linter, no `re.error`-independent timeout, no complexity budget, and no fail-closed behavior if the hook is killed by timeout — killing the process simply means Claude Code receives no hook output for that invocation.

### Impact Explanation
If the host (Claude Code) treats a killed/timed-out PreToolUse hook as "no decision" (fail-open), then a `block`-action hookify rule silently fails to enforce, and the dangerous command/edit proceeds without any blocking decision or warning — a `deny`-should-mean-`deny` enforcement bypass. This is scoped to hookify's PreToolUse enforcement path only.

### Likelihood Explanation
Requires only that (1) a rule file with an adversarial pattern be present under `.claude/hookify.*.local.md` (attacker can check this into a shared repository, since these files are ordinary project content, not privileged config) and (2) some tool input field being matched grows long enough to trigger backtracking (readily achievable for `bash` commands or file content, and plausible under autonomous long-running refactor sessions). No special privileges are required beyond contributing a file to the repo.

### Recommendation
- Validate/sanitize patterns at rule-load time using a safe-regex checker (e.g., reject nested quantifiers like `(x+)+`, `(x*)*`) or compile with a bounded/linear-time engine (e.g., Python's `re` alternative `regex` module's timeout parameter, or Google's `re2` which has no backtracking).
- Enforce a hard per-match timeout around `_regex_match` (e.g., using `signal.alarm`, a worker process with `multiprocessing` + timeout, or `regex.search(..., timeout=...)`), treating a timeout as a match failure that still allows a fail-closed decision for `block` rules if desired.
- Cap the length of text passed to `_regex_match` for matching, or short-circuit patterns pre-validated as exponential-risk.
- Document/require that hookify rule files loaded from an untrusted/checked-in repo be treated as untrusted input requiring review before being trusted for `action: block`.

### Proof of Concept
Fuzz/unit test plan for `plugins/hookify/core/rule_engine.py`:
```python
import time
from hookify.core.rule_engine import RuleEngine, compile_regex
from hookify.core.config_loader import Rule, Condition

def test_redos_bounded_time():
    rule = Rule(
        name="evil",
        enabled=True,
        event="bash",
        action="block",
        conditions=[Condition(field="command", operator="regex_match", pattern=r"(a+)+b")],
        message="danger"
    )
    engine = RuleEngine()
    evil_command = "a" * 40 + "!"  # no trailing 'b' -> triggers catastrophic backtracking
    input_data = {"tool_name": "Bash", "tool_input": {"command": evil_command}}

    start = time.time()
    result = engine.evaluate_rules([rule], input_data)
    elapsed = time.time() - start

    # Expect bounded evaluation (e.g. < 1s) or a fail-closed block decision.
    assert elapsed < 1.0, f"regex evaluation took {elapsed}s, unbounded/ReDoS risk"
```
Expected current result: the test hangs or takes exponential time as the length of the `a` run increases (e.g. 30, 35, 40 chars roughly doubling time each increment), demonstrating unbounded CPU time consistent with the hook's 10s timeout being exceeded in `pretooluse.py`.

### Citations

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

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/core/rule_engine.py (L13-24)
```python
# Cache compiled regexes (max 128 patterns)
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

**File:** plugins/hookify/core/rule_engine.py (L235-244)
```python
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
