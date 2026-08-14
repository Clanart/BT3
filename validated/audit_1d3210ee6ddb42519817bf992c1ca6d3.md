### Title
Missing global cap on concatenated PostToolUse pattern reminders allows up to ~50KB of attacker-controlled text via `PATTERN_MAX_RULES` × `PATTERN_REMINDER_MAX_BYTES` - ([File: plugins/security-guidance/hooks/extensibility.py])

### Summary
`extensibility.py` caps each user-defined pattern rule to `PATTERN_REMINDER_MAX_BYTES` (1024 bytes) and caps the number of loaded rules to `PATTERN_MAX_RULES` (50), but there is no cap on the combined size of reminders once `check_patterns` in `security_reminder_hook.py` matches multiple rules against the same file/content and returns all of them. Unlike `_load_guidance`, which explicitly truncates the combined guidance text to `GUIDANCE_MAX_BYTES` (8 KiB) with `combined[:GUIDANCE_MAX_BYTES]` [1](#0-0) , the pattern-reminder path has no equivalent global truncation.

### Finding Description
`_validate_pattern` truncates each individual reminder to 1024 bytes [2](#0-1) , and `_load_user_patterns` caps the rule count to `PATTERN_MAX_RULES` (50) [3](#0-2) . `check_patterns` in the hook then iterates `SECURITY_PATTERNS + extensibility.user_patterns()` and appends `(ruleName, reminder)` for every rule that matches, with no limit on how many matches accumulate or on the total byte size of `matches` [4](#0-3) . An attacker who controls a repo's `.claude/security-patterns.yaml` can define 50 rules that all match the same trigger (e.g., all with `substrings: ["def "]` or a path filter matching every `.py` file being edited), each with a reminder just under 1024 bytes. Because there is no analogous truncation step for the concatenated pattern-match reminders (unlike the `GUIDANCE_MAX_BYTES` truncation applied to `claude-security-guidance.md`), the caller that joins these matched reminders into the PostToolUse `additionalContext` can receive up to ~50 KB of attacker-controlled text in a single response, reassembled from fragments that individually respect the per-rule cap.

### Impact Explanation
This allows a large multi-KB prompt-injection payload to reach the model's context via the PostToolUse `additionalContext` channel despite the documented intent that "reminder length is capped" per the module's trust-model docstring [5](#0-4) . The per-reminder cap creates a false sense of a bounded injection surface; in reality the total injected text scales linearly with `PATTERN_MAX_RULES`, undermining the "additive guidance must not silently balloon into an unbounded injection surface" invariant.

### Likelihood Explanation
Feasibility is high given only repo content control (no elevated privilege): an attacker just needs their `security-patterns.yaml` to be loaded via `.claude/security-patterns.yaml` (project, committed) and crafts 50 rules matching common file edits. This is fully repeatable — every matching edit reproduces the full concatenated payload.

### Recommendation
Add a global byte cap (e.g., `PATTERN_REMINDERS_TOTAL_MAX_BYTES`) enforced in `check_patterns` (or at the call site that joins `matches` into `additionalContext`), truncating or dropping additional matched reminders once the combined size exceeds the cap — mirroring the truncation already done for `_load_guidance`'s `GUIDANCE_MAX_BYTES`.

### Proof of Concept
Unit test: populate `.claude/security-patterns.yaml` with 50 valid rules, each `substrings: ["TARGET"]` and a distinct ~1000-byte reminder string. Call `extensibility.load_for_session(cwd)` then `check_patterns("file.py", "TARGET")` (or the real hook's Edit/Write dispatch) and assert:
1. `len(extensibility.user_patterns()) == 50` (rules loaded, confirming the per-rule cap works as designed).
2. `sum(len(r) for _, r in check_patterns("file.py", "TARGET"))` is currently ~50,000 bytes — i.e., unbounded by any global cap, only bounded by `PATTERN_MAX_RULES * PATTERN_REMINDER_MAX_BYTES`.
3. Expected (fixed) behavior: total concatenated reminder bytes emitted into `additionalContext` must be `<= <GLOBAL_CAP>` (e.g. 8 KiB), with excess matches truncated/dropped and logged via `debug_log`.

### Citations

**File:** plugins/security-guidance/hooks/extensibility.py (L26-28)
```python
    cannot suppress findings.
  - Custom pattern reminders go into the same provenance-tagged block as the
    built-in ones. Reminder length is capped.
```

**File:** plugins/security-guidance/hooks/extensibility.py (L118-125)
```python
    combined = "\n\n".join(parts)
    if len(combined) > GUIDANCE_MAX_BYTES:
        debug_log(
            f"extensibility: claude-security-guidance.md combined size "
            f"{len(combined)} > {GUIDANCE_MAX_BYTES}; truncating"
        )
        combined = combined[:GUIDANCE_MAX_BYTES]
    return combined
```

**File:** plugins/security-guidance/hooks/extensibility.py (L163-168)
```python
        if len(rules) >= PATTERN_MAX_RULES:
            break
    if len(rules) > PATTERN_MAX_RULES:
        debug_log(f"extensibility: {len(rules)} user patterns > cap {PATTERN_MAX_RULES}; truncating")
        rules = rules[:PATTERN_MAX_RULES]
    return rules
```

**File:** plugins/security-guidance/hooks/extensibility.py (L209-210)
```python
    if len(reminder) > PATTERN_REMINDER_MAX_BYTES:
        reminder = reminder[:PATTERN_REMINDER_MAX_BYTES]
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
