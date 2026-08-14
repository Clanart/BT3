### Title
Full-file rewrite diff filter hides genuinely new dangerous code as pre-existing context via whitespace-insensitive text matching - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`filter_preexisting_from_diff` treats any added line whose `.strip()`ed text exactly matches a removed line's `.strip()`ed text as "pre-existing" and rewrites it as a diff context line (space-prefixed), which the LLM reviewer is explicitly instructed to ignore. Because the match is purely textual (mod indentation) rather than semantic, an attacker who controls the file's prior content (e.g. an inert occurrence of a dangerous line inside a docstring/comment) and a later full-file rewrite can promote that exact text to live, executable code while it is silently converted to a context line and dropped from LLM review.

### Finding Description
In `filter_preexisting_from_diff` [1](#0-0) , `removed_lines` is built as a *set* of `.strip()`ed text from all `-` lines, and any `+` line whose stripped content is in that set is rewritten with a space prefix (context) instead of `+` [2](#0-1) . The function's own comment acknowledges this is a "known imprecision" — `.strip()` matches across indentation, and a set lets one removal mask any number of additions with the same stripped text [3](#0-2) .

This matching is purely lexical, not semantic. If an attacker plants a line whose text is, say, `os.system(cmd)` inside an inert construct (a triple-quoted docstring, a `# ` comment, a disabled/dead branch, a test fixture string) in the file's pre-turn baseline content, that line appears verbatim in the `-` section of a subsequent full-file rewrite diff. If the attacker's rewrite then reintroduces the identical text as a live, executable statement, the added line's stripped text equals a removed line's stripped text, so it is converted to a context line by this filter.

Downstream, `analyze_code_security` in `llm.py` explicitly instructs the reviewer model to never flag anything in space-prefixed context lines, even if it looks dangerous [4](#0-3) . The comment's claimed backstop — "the diff-review prompt's previous-findings recheck" — only re-verifies findings that were *already surfaced* in a prior fire of the same turn (matched by `filePath` + `category`) [5](#0-4) ; it does not catch a finding that was never flagged in the first place because the filter hid it as context. This is invoked from the real Stop-hook review path: `get_git_diff` → `parse_diff_into_files` → `filter_preexisting_from_diff` → reviewer prompt [6](#0-5) .

### Impact Explanation
Genuinely new, attacker/agent-introduced dangerous code (e.g. command injection via `os.system`, hardcoded secrets, SSRF, etc.) can be smuggled past the automated LLM security reviewer during a full-file rewrite, defeating the tool's core "deny-means-deny"/detect-new-vulnerable-code guarantee. This matches a trust-boundary/review-bypass bounty impact class: untrusted content (file content the attacker fully controls) suppresses its own security review, allowing a vulnerable code change to reach the developer/agent without the LLM security gate ever seeing it as new.

### Likelihood Explanation
Exploitation requires the attacker to control both the pre-turn file content and a subsequent full-file rewrite of the same file within (or across) turns — feasible whenever an agent session writes attacker-influenced content in two steps (e.g. content originating from a prompt-injected file, PR description, or fetched snippet that first lands inertly, then is "activated" in a rewrite). This is a realistic and repeatable pattern (docstring/comment staging → later live promotion), not a one-off edge case, and is explicitly called out as an accepted trade-off in the code's own comments rather than something already mitigated.

### Recommendation
Restrict the pre-existing filter to true full-file rewrites detected structurally (e.g., correlate by line position/diff hunk alignment or use a proper line-diff/LCS alignment instead of an unordered stripped-text set), and/or require exact (non-stripped) content equality plus positional correspondence before demoting a `+` line to context. At minimum, exclude single lines that appear only inside multi-line string/comment tokens in the removed content from being used as a match key for a `+` line that is not itself inside a string/comment.

### Proof of Concept
Unit test against `filter_preexisting_from_diff`:
```python
diff = '''@@ -1,4 +1,4 @@
-DOCS = """
-os.system(cmd)
-"""
-x = 1
+import os
+os.system(cmd)
+y = 2
'''
diff_files = [("app.py", diff)]
filtered = filter_preexisting_from_diff(diff_files, cwd, baseline_sha="HEAD")
_, filtered_diff = filtered[0]
# Assert the newly-live dangerous line is NOT hidden as context
assert "+os.system(cmd)" in filtered_diff.splitlines(), (
    "os.system(cmd) was incorrectly converted to a context line and "
    "hidden from the LLM reviewer despite being newly-executable code"
)
```
Expected (current, vulnerable) behavior: `+os.system(cmd)` is rewritten to ` os.system(cmd)` (context), because its stripped text matches the removed docstring line, so the assertion fails — demonstrating the bypass.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L672-692)
```python
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
```

**File:** plugins/security-guidance/hooks/gitutil.py (L694-700)
```python
        # Rebuild diff with pre-existing lines converted to context (space prefix).
        # Known imprecision: .strip() matches across indentation (so reindented
        # code is treated as unchanged) and the set lets one removal mask N
        # additions of the same stripped text. Accepted trade-off — this filter
        # exists for the full-file Write rewrite case where exact-match would
        # miss everything; the diff-review prompt's previous-findings recheck
        # is the backstop.
```

**File:** plugins/security-guidance/hooks/gitutil.py (L701-716)
```python
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
```

**File:** plugins/security-guidance/hooks/llm.py (L750-758)
```python
    if is_diff:
        diff_instruction = """Note: You are reviewing a unified diff. Unmarked lines (starting with a space) are UNCHANGED context — they were already in the file before this session. Lines starting with + are ADDITIONS made in this session. Lines starting with - are REMOVALS.

CRITICAL: ONLY flag vulnerabilities that are NEWLY INTRODUCED in + lines. Do NOT flag:
- Issues in unmarked context lines (space-prefixed = pre-existing code). Even if a context line contains SECRET_KEY = 'hardcoded', DEBUG=True, hardcoded passwords, SQL injection, or any other vulnerability — it is PRE-EXISTING and must be ignored.
- Issues where the SAME pattern existed in the removed (-) lines and was re-added in + lines (this means the code was rewritten/reformatted but the pattern is pre-existing)
- Pre-existing patterns that Claude simply preserved when rewriting a file
- Any vulnerability whose vulnerable code snippet appears in context (space-prefixed) lines
- Vulnerabilities in the ORIGINAL/STARTER code that the developer was given to work with. If a file was fully rewritten (all lines show as - then +), compare the + content against the - content. Only flag NEWLY INTRODUCED patterns that did NOT exist in the - lines.
```

**File:** plugins/security-guidance/hooks/llm.py (L773-782)
```python
        prev_section = (
            "PREVIOUS FINDINGS (already surfaced to the developer earlier this turn — DO NOT re-flag):\n"
            "The exact findings below were already shown to the developer, who has either fixed them or "
            "acknowledged them as not applicable. DO NOT report any finding whose (filePath, category) pair "
            "matches an entry below — it was already handled. The vulnerableCode may differ slightly from "
            "what you see now (diff context lines shift between fires) — match on file + category, not exact "
            "code bytes. ONLY re-flag a (filePath, category) from this list if the code at that location was "
            "CHANGED since the prior review and the change is an incomplete fix or introduces a new issue.\n"
            f"{prev_lines}\n"
        )
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1808-1847)
```python
    diff_output = get_git_diff(repo_root, content_base, full_context=False,
                               paths=review_paths, untracked_paths=untracked)
    if diff_output is None and content_base != diff_base:
        debug_log(f"Stop hook: diff against {content_base[:12]} failed — falling back to {diff_base}")
        diff_output = get_git_diff(repo_root, diff_base, full_context=False,
                                   paths=review_paths, untracked_paths=untracked)
    # filter_preexisting_from_diff needs a resolvable pre-turn ref; fall
    # back to HEAD when UPS never captured a baseline (print mode).
    if not baseline_sha:
        baseline_sha = "HEAD"

    if not diff_output or not diff_output.strip():
        debug_log("Stop hook: no changes since baseline")
        _skip(6)

    # Parse diff into per-file content
    diff_files = parse_diff_into_files(diff_output)
    if not diff_files:
        debug_log("Stop hook: no source code files in diff")
        _skip(7)

    # Mirror commit-review: hard-bail only on pathological diffs (>300 files,
    # usually a bad baseline), otherwise prioritize by security-risk path
    # tokens and review the top MAX_DIFF_FILES. Stop is the only surface for
    # uncommitted edits; the old hard-skip at >30 files dropped the 31-300
    # bucket entirely, which is where cross-file source→sink vulns hide.
    # _cap_files_for_prompt already bounds bytes downstream.
    _stop_dropped = 0
    if len(diff_files) > 10 * MAX_DIFF_FILES:
        debug_log(f"Stop hook: pathological diff ({len(diff_files)} files > "
                  f"{10 * MAX_DIFF_FILES}), skipping")
        _skip(8, diff_files_count=len(diff_files))
    if len(diff_files) > MAX_DIFF_FILES:
        diff_files, _stop_dropped = _prioritize_diff_files(
            diff_files, MAX_DIFF_FILES)
        debug_log(f"Stop hook: prioritized to {len(diff_files)} files "
                  f"(dropped {_stop_dropped} lower-risk)")

    # Filter out pre-existing content from file rewrites
    diff_files = filter_preexisting_from_diff(diff_files, cwd, baseline_sha)
```
