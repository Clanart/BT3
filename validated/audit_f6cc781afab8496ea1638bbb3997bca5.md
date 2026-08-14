### Title
Unbounded regex evaluation in hookify RuleEngine allows attacker-controlled rule pattern to trigger catastrophic backtracking (ReDoS) - ([File: plugins/hookify/core/rule_engine.py])

### Summary
`compile_regex()` and `RuleEngine._regex_match()` compile and execute rule patterns from `.claude/hookify.*.local.md` files with no complexity validation, length limits, or execution timeout. Because `pattern` and `field_value` are both untrusted (pattern comes from the rule file, `field_value` comes from `tool_input.command`/`new_text`/etc.), an attacker who can supply a hookify rule file can encode a catastrophic-backtracking pattern (e.g. `(a+)+$`) that, combined with adversarial input text, causes `re.search()` to run for an unbounded amount of time inside the PostToolUse/PreToolUse hook process.

### Finding Description
The evaluation path is exactly as described: `RuleEngine._rule_matches` → `_check_condition` (`plugins/hookify/core/rule_engine.py:144-180`) → `_regex_match` (`plugins/hookify/core/rule_engine.py:256-273`) → `compile_regex(pattern)` (`plugins/hookify/core/rule_engine.py:14-24`) → `regex.search(text)`. The `pattern` string originates unsanitized from rule frontmatter parsed by `config_loader.Condition.from_dict` (`plugins/hookify/core/config_loader.py:22-29`), and `text` (`field_value`) is extracted directly from the tool call being guarded, e.g. `tool_input.get('command', '')` for Bash or `tool_input.get('new_string', '')` for Edit/Write (`plugins/hookify/core/rule_engine.py:230-252`). `lru_cache` on `compile_regex` only memoizes compilation of the pattern object; it does nothing to bound the cost of `regex.search()`, which is where Python's backtracking engine can go exponential on patterns like `(a+)+$`, `(a|a)+$`, or `(.*)*$` against crafted input (e.g. many `a` characters followed by a non-matching character). There is no `signal.alarm`/thread-based timeout, no pattern-complexity check, and no input-length cap anywhere in `_regex_match` or its callers (`posttooluse.py`, `pretooluse.py`, `stop.py`, `userpromptsubmit.py` all call `RuleEngine.evaluate_rules` synchronously with no timeout wrapper). The `try/except re.error` only guards against invalid syntax, not runtime hangs.

### Impact Explanation
Hookify is a guardrail plugin whose stated purpose is to block dangerous Bash commands and file edits by evaluating rules on every PreToolUse/PostToolUse invocation. A hang in this evaluation stalls the hook process indefinitely for that tool call. Whether this becomes a full guard bypass depends on how the outer Claude Code CLI treats a hook that exceeds its own timeout (fail-open vs fail-closed) — that enforcement logic lives outside this repository and could not be verified here. Independent of that uncertainty, the finding itself is a real availability/DoS defect in the hookify component: a single malicious pattern makes the guard evaluation for matching tool calls non-deterministic in duration, directly violating the "guard evaluation must complete and enforce deterministically" invariant the question tests.

### Likelihood Explanation
Exploitability requires the attacker to control the content of a hookify rule file (`.claude/hookify.*.local.md`) that gets loaded by `load_rules()` (`plugins/hookify/core/config_loader.py:198-241`) — this is the explicit precondition given in the question. Given that precondition, triggering the hang is trivial and fully deterministic: any tool invocation whose relevant field (e.g. a Bash `command` or an Edit `new_string`) contains a string matching the adversarial shape (e.g. 30+ `a` characters followed by a character that breaks the match) will make `regex.search()` hang for an amount of time that grows exponentially with input length, with no upper bound in the code.

### Recommendation
Add defense-in-depth around regex evaluation in `_regex_match`/`compile_regex`:
- Enforce a hard wall-clock timeout around `regex.search()` (e.g. via a worker thread/process with `join(timeout)`, or `signal.alarm` on POSIX) and treat a timeout as a non-match (fail-safe, but log/warn loudly since this is a guard).
- Cap the length of `field_value` passed to regex matching to a reasonable bound.
- Consider validating/rejecting rule patterns with known catastrophic-backtracking shapes at load time, or switching to a linear-time regex engine (e.g. Google's `re2` via the `google-re2` Python binding) for rule evaluation.
- Since rule files are also parsed by a custom, error-tolerant "simple YAML" parser (`extract_frontmatter`), ensure rule-file provenance/trust is documented, since this whole class of issue is only reachable if rule files can be attacker-authored.

### Proof of Concept
Unit/fuzz test to add to `plugins/hookify/core/rule_engine.py` test suite:
```python
import signal, pytest
from hookify.core.rule_engine import RuleEngine
from hookify.core.config_loader import Condition, Rule

def test_regex_dos_bounded():
    rule = Rule(
        name="evil", enabled=True, event="bash",
        conditions=[Condition(field="command", operator="regex_match", pattern=r"(a+)+$")],
        message="x"
    )
    engine = RuleEngine()
    payload = "a" * 35 + "!"  # classic catastrophic backtracking trigger
    input_data = {"tool_name": "Bash", "tool_input": {"command": payload}}

    def handler(signum, frame):
        raise TimeoutError("regex evaluation exceeded bound")

    signal.signal(signal.SIGALRM, handler)
    signal.alarm(2)  # assert evaluation completes within 2s
    try:
        engine.evaluate_rules([rule], input_data)
    except TimeoutError:
        pytest.fail("ReDoS: rule evaluation did not complete within bounded time")
    finally:
        signal.alarm(0)
```
Expected (current) result: the test fails/times out, demonstrating unbounded evaluation time for a single attacker-authored rule pattern against a short adversarial input, confirming the DoS in the guard path.