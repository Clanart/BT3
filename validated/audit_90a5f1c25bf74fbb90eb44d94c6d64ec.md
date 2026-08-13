### Title
`_has_redos_structure` ReDoS heuristic bypass in `security-patterns.yaml`/`.json` regex validation allows attacker-controlled catastrophic-backtracking regex to hang the PostToolUse pattern-check hook - (File: `plugins/security-guidance/hooks/extensibility.py`)

### Summary
`_validate_pattern` only rejects user-supplied `regex` entries that match three narrow static shapes in `_has_redos_structure` (nested `+`/`*` inside a single unnested group, `(.*)*`, and prefix-overlapping alternation under `+`/`*`). Several well-known catastrophic-backtracking constructs — e.g. a group quantified with `?` under repetition, such as `(a?)+`, or multiple adjacent quantified groups like `(a+)(a+)…(a+)$` — fall outside these three shapes and pass validation unflagged, then get executed via `re.search` on every subsequent `Edit`/`Write` in `check_patterns`.

### Finding Description
`_validate_pattern` (`plugins/security-guidance/hooks/extensibility.py:199-244`) calls `_has_redos_structure(regex)` before accepting a user-supplied `regex` field, and only skips the regex if that heuristic returns `True`: [1](#0-0) 

`_has_redos_structure` (`extensibility.py:262-289`) checks only two compiled regexes plus one alternation heuristic: [2](#0-1) 

`_REDOS_SHAPES[0]` (`\([^()]*[+*][^()]*\)[+*?]`) requires a literal `+` or `*` *inside* the parenthesized group before it will flag the group as nested-quantifier-dangerous. It does **not** check for `?` inside the group, so `(a?)+` (a textbook catastrophic-backtracking "optional-under-repetition" shape) never matches this pattern and is not flagged. Likewise, classic sequential/adjacent quantified-group ReDoS shapes (e.g. `(a+)(a+)(a+)...(a+)$`, no single group nested or alternated) are outside all three heuristics since each shape only looks for quantifiers *inside a single unnested group* or *alternation inside a single unnested group*.

Once such a regex passes `_has_redos_structure` and `re.compile` succeeds, it is stored in `_user_patterns` via `_load_user_patterns`/`load_for_session` (`extensibility.py:60-77`, `147-168`) and merged into every PostToolUse check via `check_patterns`: [3](#0-2) 

Crucially, the `re.search` call in `check_patterns` is wrapped only in `except Exception: pass` — this catches regex *compile/runtime errors*, not runaway backtracking, which is CPU-bound and does not raise any exception; it simply blocks the process. No `signal.alarm`, thread-based timeout, or subprocess isolation exists around this call, and I could not find a `hooks.json` for this plugin in the index to confirm any external hook-level timeout that would bound this call (index limitation — recommend verifying in a full checkout).

### Impact Explanation
An attacker who can get a `.claude/security-patterns.yaml` (or `.json`) file committed into a repository the victim opens with Claude Code (an ordinary, unprivileged PR/commit — no admin/maintainer rights needed on the victim's machine) can smuggle a catastrophic-backtracking regex that bypasses the ReDoS heuristic. Because `_load_user_patterns` reads the **project** `.claude/security-patterns.yaml` (committed, non-local) path with the same precedence as user/local configs (`_config_paths`, `extensibility.py:92-102`), a plain repository checkout is sufficient to load it — no elevated trust is required beyond normal repo content, matching the threat model's "malicious PR/plugin file" vector. Once loaded, the crafted regex is applied via `re.search` on the content of every `Edit`/`Write`/`MultiEdit` during the session (`security_reminder_hook.py:2112-2129`). If any edited/written file content contains (or is crafted by the attacker's own follow-up PR to contain) the catastrophic-backtracking trigger substring, the PostToolUse hook process hangs indefinitely on that call, since there is no timeout guarding `re.search`. This is a denial-of-service against the security-guidance hook itself: it blocks or stalls the PostToolUse pipeline (and, depending on Claude Code's own hook-timeout enforcement — which I could not verify in the indexed files — potentially the whole tool-use turn), defeating the very guard meant to warn about dangerous code changes and degrading normal editing workflow.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs write access sufficient to add a file to `.claude/security-patterns.yaml`/`.json` in a repo the victim will open (e.g., via a PR merged by someone else, or a repo the victim clones) — no special privilege on the victim's machine. The bypass is a static, well-known regex-crafting technique (optional-quantifier-under-repetition, or multiple adjacent quantified groups) that does not depend on any race condition or unusual environment; it will trigger deterministically as soon as matching content is edited. The main constraint is that the attacker needs edited-file content to contain a substring matching the regex's blow-up trigger, which is either satisfiable by picking common characters (e.g., whitespace/newline-based patterns) or by the attacker also contributing files containing the trigger content.

### Recommendation
- Rewrite `_has_redos_structure` to check for `?` (and `{0,n}`/`{,n}`-style optional bounded quantifiers) in addition to `+`/`*` when looking for quantified content inside a group followed by an outer quantifier.
- Add detection for sequences of ≥2 adjacent independently-quantified groups without an intervening anchor/literal boundary (a common polynomial/exponential backtracking shape not requiring nesting).
- Prefer a real defense over heuristics: enforce an actual execution timeout on `re.search` calls (e.g., run matching in a subprocess/thread with `SIGALRM` or use a bounded-time regex engine such as Python's `re` with the `regex` module's timeout support, or Google's `re2` which has no catastrophic backtracking), applied to *all* pattern matches including built-ins, not just a load-time static heuristic.
- As defense-in-depth, cap input length passed to user-supplied regexes independently of `PATTERN_REMINDER_MAX_BYTES` (which only caps the reminder text, not the content being scanned).

### Proof of Concept
Unit/fuzz test to add to the plugin's test suite (e.g., `plugins/security-guidance/tests/test_extensibility.py`):

```python
import re, time, pytest
from extensibility import _has_redos_structure

REDOS_CORPUS = [
    r"(a?)+$",                 # optional-under-repetition, not +/* inside group
    r"(a??)+$",
    r"(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)$",  # adjacent quantified groups
    r"(\s?)+\S$",
]

@pytest.mark.parametrize("pattern", REDOS_CORPUS)
def test_redos_heuristic_misses_known_shapes(pattern):
    # Demonstrates the bypass: heuristic says "safe" for a known-dangerous shape.
    assert _has_redos_structure(pattern) is False

@pytest.mark.parametrize("pattern,attack", [
    (r"(a?)+$", "a" * 30 + "!"),
    (r"(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)$", "a" * 40 + "!"),
])
def test_bypassed_pattern_hangs_re_search(pattern, attack):
    compiled = re.compile(pattern)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        # wrap re.search in a hard timeout (e.g. via a signal.alarm helper
        # or multiprocessing) to prove catastrophic behavior deterministically
        run_with_timeout(lambda: compiled.search(attack), seconds=2)
    # In production, check_patterns()'s `except Exception: pass` around
    # re.search would NOT catch this — the process just hangs here.
```

Integration-level PoC: write a `.claude/security-patterns.yaml` containing `regex: "(a?)+$"`, invoke `security_reminder_hook.py` as a `PostToolUse` hook for an `Edit` whose `new_string` ends in `"a"*40 + "!"`, and assert the subprocess does not return within a bounded wall-clock timeout (e.g. 5s), demonstrating the hang in `check_patterns`.

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L386-422)
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
```
