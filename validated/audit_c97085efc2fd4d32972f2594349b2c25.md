Confirmed root cause: `check_patterns()` returns matches keyed only by `ruleName` (e.g. `"pickle_deserialization"`, `"eval_injection"`), not by the specific matched line/instance [1](#0-0) , and the baseline suppression logic drops any current match whose *rule name* also matched somewhere in the baseline content, regardless of whether it's the same occurrence [2](#0-1) .

### Title
Rule-Level (Not Instance-Level) Baseline Suppression Lets an Early Trivial Pattern Match Permanently Bypass Security-Pattern Warnings for an Entire File - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Summary
The `security-guidance` plugin's `PostToolUse` handler flags dangerous code patterns (e.g. `eval()`, `pickle.load`, disabled TLS verification) on every `Write` tool call, but suppresses any pattern whose *rule name* already existed anywhere in the pre-turn baseline version of the same file. Because suppression is keyed on rule identity rather than specific matched content, once any instance of a rule fires once in the baseline, all future instances of that same rule in that file are permanently invisible to the pattern-warning system for the rest of the session — this is directly analogous to the reported vesting bug where mutating shared state via one action (`spendLockTokens`) before another (`claim`) permanently and unfairly reduces the base used by later calculations.

### Finding Description
`check_patterns()` returns `(ruleName, reminder)` tuples with no location or exact-snippet identity [1](#0-0) . When the tool is `Write`, the hook fetches the pre-turn baseline content via `get_baseline_file_content()` and computes `baseline_matches = set(r for r, _ in check_patterns(file_path, baseline_content))`, then filters `pattern_matches = [(r, msg) for r, msg in pattern_matches if r not in baseline_matches]` [2](#0-1) . This is a set-membership check on the rule name, not a diff of the specific vulnerable line.

Consequence: if a file's baseline already contains *any* trivial/benign match for a given rule (e.g. a comment mentioning `pickle.loads(` in a docstring, or a genuinely pre-existing low-risk `eval(` call), then `ruleName` such as `"pickle_deserialization"` or `"eval_injection"` is added to `baseline_matches`. From that point on, **every subsequent `Write` to that file in the session** — including a fully new, unrelated, attacker/prompt-injection-influenced insertion of the same rule's dangerous sink elsewhere in the file — is silently dropped from `pattern_matches`, so no warning is ever emitted (`all_guidance` stays empty), and `record_pending_warnings` never records the new instance for the Stop-hook sweep either [3](#0-2) .

The ordering-dependence is the direct analog to the report: performing action A (a benign trigger of the rule) before action B (the actual malicious pattern) changes B's outcome for the worse — here, "worse" means B silently bypasses the security guidance entirely rather than merely being calculated unfairly. Note this only affects the deterministic regex/substring `PostToolUse` pattern-warning layer; the LLM-based diff review in `handle_stop_hook`/`handle_commit_review_posttooluse` is a separate, content-diff-based check and is not affected by this rule-name suppression logic.

### Impact Explanation
This is a hook-bypass class issue in an unprivileged-user-facing security guidance mechanism: a project whose repository already contains one instance of a flagged pattern (a common real-world situation — e.g., an existing `eval(` call, an existing `pickle.loads`, or a pre-existing `tls_verification_disabled` pattern anywhere in a large file) will never see PostToolUse warnings for *any new* instance of the same rule written into that file for the remainder of the turn/session, even if it's a completely different, genuinely dangerous, newly-introduced line. This weakens the "defense in depth" pattern-based layer that is meant to catch dangerous edits immediately at write time (separate from and faster than the LLM diff review), and it degrades silently — there is no signal to the user that suppression occurred beyond a debug log line (`"All patterns existed in baseline, skipping"`) that isn't surfaced to the user by default.

### Likelihood Explanation
Moderate-to-high likelihood in practice: any file that already contains one match of a monitored rule (plausible for `eval_injection`, `innerHTML_xss`, `document_write_xss`, `unsafe_yaml_load` in real codebases) permanently blinds the per-edit pattern check for that rule on that file for the rest of the session, with no attacker action required beyond normal editing of a file with pre-existing weak patterns — or, if attacker-influenced (e.g., prompt injection instructing the model to first touch a trivial matching line, then insert the real payload), it becomes a reliable, repeatable bypass technique.

### Recommendation
Change baseline suppression to be instance/content-based rather than rule-name-based: instead of comparing `set of ruleNames matched in baseline` vs `set of ruleNames matched in new content`, diff the specific matched substrings/spans (or line numbers) between baseline and new content, and only suppress a match whose exact matched text/location was already present in the baseline. This mirrors the exact-match filtering approach already used correctly elsewhere in the codebase for diff-based pre-existing-content filtering [4](#0-3) .

### Proof of Concept
1. In a session, edit `app.py` (whose current baseline already contains, anywhere, a benign or intentionally-annotated `eval("1+1")`) — `check_patterns` on the baseline returns `[("eval_injection", ...)]`, so `baseline_matches = {"eval_injection"}` [5](#0-4) .
2. Use the `Write` tool to fully rewrite `app.py`, this time inserting a genuinely dangerous new line: `eval(request.args.get("expr"))` (command/code injection from user input) far from the original `eval` call.
3. `check_patterns` on the new content returns `[("eval_injection", ...)]` again (same rule name, different vulnerable line).
4. The filter `pattern_matches = [(r, msg) for r, msg in pattern_matches if r not in baseline_matches]` drops this match entirely because `"eval_injection" in baseline_matches` is `True` [6](#0-5) .
5. No warning is shown to the user/model at write time (`all_guidance` is empty), and the pattern is never registered as `pending_warnings` for the Stop-hook fixed/unresolved sweep — the newly introduced, genuinely exploitable `eval()` call on user-controlled input silently bypasses the pattern-based security guidance for the rest of the session, for any further edits to that same file.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L386-426)
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

```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2136-2145)
```python
            if tool_name == "Write" and pattern_matches:
                cwd = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
                baseline_content = get_baseline_file_content(session_id, file_path, cwd)
                if baseline_content is not None:
                    baseline_matches = set(r for r, _ in check_patterns(file_path, baseline_content))
                    pattern_matches = [(r, msg) for r, msg in pattern_matches if r not in baseline_matches]
                    if pattern_matches:
                        debug_log(f"New patterns (not in baseline): {[r for r, _ in pattern_matches]}")
                    else:
                        debug_log("All patterns existed in baseline, skipping")
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2152-2156)
```python
            # Record matched rules as pending so the Stop-hook sweep can
            # later tally fixed vs unresolved. Only runs when patterns match.
            if pattern_matches:
                record_pending_warnings(session_id, file_path,
                                        [r for r, _ in pattern_matches])
```

**File:** plugins/security-guidance/hooks/gitutil.py (L657-722)
```python
def filter_preexisting_from_diff(diff_files, cwd, baseline_sha):
    """
    Filter out pre-existing content from diff files.
    When a file is fully rewritten (Write tool replaces entire content),
    git shows all lines as removed (-) then re-added (+). This function
    detects such rewrites and strips lines from the + section that also
    appeared in the - section, so the LLM reviewer only sees truly new code.
    """
    if not baseline_sha:
        return diff_files

    filtered = []
    for file_path, diff_content in diff_files:
        lines = diff_content.split('\n')

        # Collect removed and added lines (stripping the +/- prefix)
        removed_lines = set()
        added_lines = []
        for line in lines:
            if line.startswith('-') and not line.startswith('---'):
                removed_lines.add(line[1:].strip())
            elif line.startswith('+') and not line.startswith('+++'):
                added_lines.append(line[1:].strip())

        if not removed_lines:
            # New file, no pre-existing content to filter
            filtered.append((file_path, diff_content))
            continue

        # Check what fraction of added lines were pre-existing
        preexisting_count = sum(1 for l in added_lines if l in removed_lines)
        if preexisting_count == 0:
            filtered.append((file_path, diff_content))
            continue

        added_lines_set = set(added_lines)

        # Rebuild diff with pre-existing lines converted to context (space prefix).
        # Known imprecision: .strip() matches across indentation (so reindented
        # code is treated as unchanged) and the set lets one removal mask N
        # additions of the same stripped text. Accepted trade-off — this filter
        # exists for the full-file Write rewrite case where exact-match would
        # miss everything; the diff-review prompt's previous-findings recheck
        # is the backstop.
        new_lines = []
        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                content = line[1:].strip()
                if content in removed_lines:
                    # Convert to context line (pre-existing, not new)
                    new_lines.append(' ' + line[1:])
                else:
                    new_lines.append(line)
            elif line.startswith('-') and not line.startswith('---'):
                content = line[1:].strip()
                if content in added_lines_set:
                    # Skip removed lines that were re-added (they become context)
                    continue
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        filtered.append((file_path, '\n'.join(new_lines)))

    return filtered
```
