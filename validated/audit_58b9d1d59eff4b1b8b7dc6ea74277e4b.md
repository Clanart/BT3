### Title
ReDoS via attacker-controlled regex pattern in committed hookify rule causes indefinite hang on every `UserPromptSubmit` event - ([File: plugins/hookify/hooks/userpromptsubmit.py])

### Finding Description
`load_rules(event='prompt')` in `plugins/hookify/core/config_loader.py` discovers and parses every `.claude/hookify.*.local.md` file in the workspace via `glob.glob(os.path.join('.claude', 'hookify.*.local.md'))` [1](#0-0) . `extract_frontmatter` parses the YAML-like frontmatter of each file and `Rule.from_dict`/`Condition.from_dict` copy the `pattern` (or `conditions[].pattern`) field verbatim into a `Rule`/`Condition` object with no sanitization, length limit, or complexity check [2](#0-1) .

On every prompt submission, `plugins/hookify/hooks/userpromptsubmit.py` calls `load_rules(event='prompt')` and then `RuleEngine.evaluate_rules()` [3](#0-2) . For each matching rule, `_check_condition()` dispatches `regex_match` conditions to `RuleEngine._regex_match(pattern, field_value)` [4](#0-3) , which compiles the attacker-supplied pattern via the module-level `compile_regex()` (only `lru_cache`-wrapped, no timeout) and runs `regex.search(text)` directly against the user's prompt text [5](#0-4) .

The only error handling present is a `try/except re.error`, which catches *invalid* regex syntax, not runtime hangs from catastrophic backtracking (e.g. `(a+)+$`, `(a|a)*$`) [6](#0-5) . Because Python's `re` engine is backtracking-based and there's no `signal.alarm`/thread-based timeout or `regex` module timeout used anywhere in this call chain, a crafted pattern combined with an adversarial-length input string causes `re.search` to block the interpreter for an exponential amount of time. The `finally: sys.exit(0)` in `userpromptsubmit.py` [7](#0-6)  only executes once the `try` block returns or raises — it cannot rescue a hung regex evaluation, so the hook process itself hangs rather than exiting cleanly.

Because rule files live in `.claude/` and the plugin's own documentation notes they "should be git-ignored (add to .gitignore if needed)" — implying they are not gitignored by default — an attacker can commit a `.claude/hookify.evil.local.md` file with a catastrophic-backtracking `pattern` (or `conditions[].pattern`) to a shared repository/branch/PR. Any victim who checks out that content into a workspace where Claude Code + hookify plugin is active will have this rule loaded fresh from disk on every subsequent `UserPromptSubmit` event, since `load_rules` re-reads and re-parses the file each time rather than caching validated rules across events.

### Impact Explanation
Every user prompt submitted in the affected workspace triggers `userpromptsubmit.py`, which re-loads and re-evaluates the malicious rule, hanging the hook process on the pathological regex. This blocks/denies the `UserPromptSubmit` hook pipeline for that workspace on every single prompt, effectively making the session (and any workspace sharing the same `.claude` rule files) unusable — a persistent, repeatable, workspace-wide availability/DoS impact requiring no elevated privilege, matching a hook-enforcement denial-of-service impact category.

### Likelihood Explanation
Preconditions are minimal and realistic: the attacker needs only the ability to add a file under `.claude/hookify.*.local.md` in content that a victim will check out (e.g., a branch, fork, or PR merged into a shared repo) with the hookify plugin enabled. No approval, admin/maintainer privilege, or social engineering beyond normal repository contribution is required. The bug is 100% reproducible — the same malicious pattern hangs every subsequent prompt for as long as the rule file remains on disk in `.claude/`.

### Recommendation
Enforce a hard execution timeout around every untrusted-pattern regex evaluation (e.g., run `regex.search` in a worker thread/process with a strict wall-clock timeout, or use the `regex` module's `timeout=` parameter / `signal.alarm` on POSIX), and reject/skip rules whose evaluation exceeds the budget instead of hanging the hook. Additionally, validate patterns at load time with a static ReDoS heuristic (e.g., reject nested quantifiers like `(a+)+`) and/or cap input length passed to `regex.search` for `prompt`/`stop` events, and cap total per-event rule-evaluation time in `RuleEngine.evaluate_rules()` so a single bad rule cannot block the whole hook.

### Proof of Concept
Unit/fuzz test to add near `plugins/hookify/core/rule_engine.py` tests:
```python
import time
import pytest
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Rule, Condition

CATASTROPHIC_PATTERNS = [
    r"(a+)+$",
    r"(a|a)*$",
    r"([a-zA-Z]+)*$",
    r"(a|aa)+$",
]

@pytest.mark.parametrize("pattern", CATASTROPHIC_PATTERNS)
def test_regex_match_bounded_time(pattern):
    engine = RuleEngine()
    rule = Rule(
        name="evil-rule", enabled=True, event="prompt",
        conditions=[Condition(field="user_prompt", operator="regex_match", pattern=pattern)],
        message="x",
    )
    payload = "a" * 40 + "!"  # non-matching tail forces full backtracking
    input_data = {"hook_event_name": "UserPromptSubmit", "user_prompt": payload}

    start = time.monotonic()
    engine.evaluate_rules([rule], input_data)
    elapsed = time.monotonic() - start

    # Invariant: evaluation must be bounded regardless of untrusted pattern
    assert elapsed < 1.0, f"ReDoS: pattern {pattern!r} took {elapsed:.2f}s on {len(payload)}-char input"
```
Running this against the current implementation demonstrates `elapsed` growing exponentially with payload length (e.g., seconds to minutes for 30-40 `a`s), confirming the unbounded-hang invariant violation described in `RuleEngine._regex_match` / `compile_regex`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L22-29)
```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )
```

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/hooks/userpromptsubmit.py (L36-41)
```python
        # Load user prompt rules
        rules = load_rules(event='prompt')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
```

**File:** plugins/hookify/hooks/userpromptsubmit.py (L52-54)
```python
    finally:
        # ALWAYS exit 0
        sys.exit(0)
```

**File:** plugins/hookify/core/rule_engine.py (L166-167)
```python
        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
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
