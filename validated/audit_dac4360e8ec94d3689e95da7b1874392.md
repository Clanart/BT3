### Title
ReDoS heuristic (`_has_redos_structure`) has exploitable false negatives, allowing attacker-controlled `security-patterns.yaml` regexes to hang `check_patterns`'s untimed `re.search` on every file write - ([File: plugins/security-guidance/hooks/extensibility.py])

### Summary
`_validate_pattern` gates untrusted regexes from `security-patterns.yaml` behind `_has_redos_structure`, a narrow syntactic heuristic, before calling `re.compile` and storing the rule. The heuristic only recognizes a single flat shape (`(<no-nested-parens><quantifier>)<quantifier>`, a literal `.*` group under repetition, or literal-prefix alternation branches under immediate repetition), so several well-known catastrophic-backtracking constructions bypass it and are accepted, then executed unbounded via `re.search(pattern['regex'], content)` in `check_patterns` on every subsequent file write.

### Finding Description
`_validate_pattern` in `plugins/security-guidance/hooks/extensibility.py` runs `_has_redos_structure(regex)` and only rejects the rule if it matches; otherwise it proceeds to `re.compile(regex)` and stores `rule["regex"] = regex`, and the rule is later invoked with no timeout at `re.search(pattern["regex"], content)` inside `check_patterns`. [1](#0-0) 

The heuristic itself is a narrow static check: [2](#0-1) 

`_REDOS_SHAPES[0]` (`\([^()]*[+*][^()]*\)[+*?]`) only fires when a *single, non-nested* group's internal quantified content is *immediately* followed by a quantifier on the same closing paren. It does not detect:
- **Sequential quantified groups** (polynomial backtracking blowup), e.g. `(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)$` — no group here is immediately followed by `[+*?]`, so the regex never matches, yet matching this against a long non-terminating string is O(n^k) in group count k.
- **Backreference-driven amplification**, e.g. `(a+)\1+` — the quantifier sits on the backreference `\1`, not on the group's closing paren, so `_REDOS_SHAPES[0]` never fires even though this is a textbook catastrophic-backtracking shape.
- `_ALT_UNDER_REP` only flags alternation branches related by *literal string prefix* (`a.startswith(b)`), which misses semantically-overlapping but non-literal-prefix branches.

Once such a regex passes `_has_redos_structure`, `re.compile` succeeds (these are syntactically valid Python regexes), and the rule is appended to `_user_patterns` via `_load_user_patterns`, then merged into every `check_patterns` call: [3](#0-2) 

`check_patterns` is invoked on the `PostToolUse` path for every `Write`/`Edit`/`MultiEdit` whose content is extracted via `extract_content_from_input`, with no per-call timeout, resource limit, or subprocess isolation around `re.search`. Any file write matching the rule's (optional) `path_filter` will run the attacker's regex against the full written content on the main hook thread.

### Impact Explanation
This is a denial-of-service of the `security-guidance` PostToolUse enforcement hook: a single malicious `security-patterns.yaml` committed to a repo causes every subsequent matching file write in that session to invoke a catastrophically slow `re.search` with no timeout, hanging the hook process. Because pattern checks gate the `additionalContext`/warning response for that tool call, this stalls the hook's response for that PostToolUse invocation, and repeatable across every matching edit, effectively disabling the pattern-based warning path project-wide for the session and burning CPU/wall-clock resources indefinitely per edit. If the CC harness's hook framework has no independent hard timeout, this can also fail-open (skip enforcement) or hang the interactive session.

### Likelihood Explanation
Preconditions are modest and realistic: an attacker only needs a merged PR (or any write access to the repo) adding `.claude/security-patterns.yaml` with `rule_name`, `reminder`, and a `regex` field. `_load_user_patterns` and `_config_paths` load this directly from `<cwd>/.claude/security-patterns.yaml` with no signing/trust check beyond "does it parse", and `_validate_pattern`'s only defense is the flawed heuristic. The victim just needs to edit any file (or any file matching an optional `path_filter` glob) to trigger `check_patterns` → `re.search` on the attacker's regex. This is fully reproducible and repeatable on every matching edit for the rest of the session.

### Recommendation
Do not rely on syntactic heuristics alone to vet untrusted regexes. Options:
1. Enforce a hard wall-clock timeout around every `re.search` call in `check_patterns` (e.g., run pattern matching in a worker process/thread with `signal.alarm`, or use a regex engine with guaranteed linear-time matching, such as Google's `re2` via the `re2` binding, for user-supplied patterns).
2. At load time, actually exercise each candidate regex against a small set of adversarial probe strings with a strict per-pattern timeout (e.g., `multiprocessing` + `timeout`) and reject/skip patterns that exceed it, rather than trusting a static shape heuristic.
3. Cap regex complexity more conservatively (e.g., disallow backreferences and more than N groups) if a bounded engine is not adopted.

### Proof of Concept
Add a fuzz/differential test comparing `_has_redos_structure`'s verdict against actual `re.search` wall-clock time:
```python
import re, time
from extensibility import _has_redos_structure

ADVERSARIAL = [
    r"(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)$",  # sequential quantified groups
    r"(a+)\1+",                                     # backreference amplification
]

for pattern in ADVERSARIAL:
    flagged = _has_redos_structure(pattern)
    payload = "a" * 40 + "!"  # deliberately non-matching tail
    start = time.time()
    try:
        re.search(pattern, payload)
    except Exception:
        pass
    elapsed = time.time() - start
    # Expected failure: flagged == False but elapsed exceeds a hang threshold,
    # proving a heuristic false negative that reaches an unbounded re.search.
    assert not (elapsed > 2.0 and not flagged), (
        f"heuristic missed catastrophic pattern {pattern!r}: took {elapsed}s, flagged={flagged}"
    )
```
Expected result: for `(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)$` and `(a+)\1+`, `_has_redos_structure` returns `False` while `re.search` runtime grows superlinearly with payload length, demonstrating the bypass reaches `check_patterns`'s untimed `re.search` at `plugins/security-guidance/hooks/security_reminder_hook.py:419`.

### Citations

**File:** plugins/security-guidance/hooks/extensibility.py (L223-232)
```python
    if regex:
        if _has_redos_structure(regex):
            debug_log(f"extensibility: skipping {name}: regex looks ReDoS-prone: {regex!r:.60}")
            return None
        try:
            rule["regex"] = regex
            re.compile(regex)
        except re.error as e:
            debug_log(f"extensibility: skipping {name}: invalid regex: {e}")
            return None
```

**File:** plugins/security-guidance/hooks/extensibility.py (L265-289)
```python
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
    return False
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L386-427)
```python
def check_patterns(file_path, content):
    """Check if file path or content matches any security patterns. Returns ALL matches."""
    normalized_path = file_path.lstrip("/")
    matches = []

    for pattern in list(SECURITY_PATTERNS) + extensibility.user_patterns():
        # path_filter is a gate: when present, the rule only applies to
        # matching paths. Distinct from path_check, which is itself a
        # positive match condition (e.g. .github/workflows/).
        if "path_filter" in pattern:
            try:
                if not pattern["path_filter"](normalized_path):
                    continue
            except Exception:
                continue

        matched = False

        if "path_check" in pattern:
            try:
                if pattern["path_check"](normalized_path):
                    matched = True
            except Exception:
                pass

        if not matched and "substrings" in pattern and content:
            for substring in pattern["substrings"]:
                if substring in content:
                    matched = True
                    break

        if not matched and "regex" in pattern and content:
            try:
                if re.search(pattern["regex"], content):
                    matched = True
            except Exception:
                pass

        if matched:
            matches.append((pattern["ruleName"], pattern["reminder"]))

    return matches
```
