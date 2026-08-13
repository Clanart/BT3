### Title
`filter_preexisting_from_diff` masks genuinely new/relocated malicious lines as pre-existing context via content-only (not position-aware) matching - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`filter_preexisting_from_diff` treats any `+` line whose `.strip()`-ed text matches *any* `-` line's stripped text in the same file diff as "pre-existing," converting it to a context line before the diff is handed to the LLM reviewer. Because the matching is done via an unordered `set` of stripped text with no positional/contextual correlation, an attacker doing a full-file rewrite (e.g. via the `Write` tool) can move or duplicate an existing line's exact text to a new, dangerous call site while removing the original occurrence elsewhere, causing the newly-relevant dangerous line to be silently converted to a context marker and hidden from the LLM-based diff reviewer.

### Finding Description
`get_git_diff` → `parse_diff_into_files` → `filter_preexisting_from_diff(diff_files, cwd, baseline_sha)` is the path documented in `security_reminder_hook.py`'s Stop-hook review flow. Inside `filter_preexisting_from_diff` [1](#0-0) , `removed_lines` is built as a `set` of stripped `-` line text and `added_lines`/`added_lines_set` from stripped `+` line text, with no positional, hunk, or ordering information retained. The rebuild loop then converts any `+` line to a context (` `) line whenever its stripped text is present in `removed_lines`, regardless of where in the file that removed line originally was or where the new line now appears [2](#0-1) . The code comment explicitly acknowledges this: "`.strip()` matches across indentation ... and the set lets one removal mask N additions of the same stripped text" [3](#0-2) .

This means a single removed line (e.g., a benign line whose stripped text happens to equal a dangerous line's text, or the attacker's own relocated dangerous line moved from an old, inert location to a new dangerous call site) is sufficient to mask the corresponding added line from the reviewer, converting `+os.system(x)` into a plain context line so the diff shown to the LLM reviewer looks unchanged for that content.

### Impact Explanation
This masks genuinely new/relocated dangerous code from the automated LLM security reviewer that Claude Code's Stop hook relies on to catch vulnerabilities introduced during a session [4](#0-3) . If an attacker (via prompt injection or a compromised file/task instructing a full-file `Write` rewrite) can engineer such a rewrite, the semantic change (e.g., moving a `os.system(...)` call to operate on new, attacker-influenced input) is smuggled past the reviewer as "pre-existing," undermining the trust boundary the Stop-hook review is meant to enforce. This is a scoped, non-trivial evasion of a security control, not a request for direct command execution — the ultimate impact severity depends on what the smuggled content does once unreviewed, but the review-bypass itself is real and exploitable.

### Likelihood Explanation
Requires: (1) a full-file rewrite whose diff naturally produces all-removed/all-added hunks (e.g., `Write` tool), which is a normal, attacker-reachable operation; and (2) engineering the removed/added stripped-text collision, which the attacker fully controls when crafting the new file content — they can choose which line to duplicate/relocate and which line to drop. `preexisting_count` only needs to be `> 0`, i.e., at least one non-zero content match. This is straightforward to construct deterministically, and the code's own comment confirms the authors were aware of exactly this imprecision but treated it as an "accepted trade-off," with the only backstop cited being the "diff-review prompt's previous-findings recheck" (i.e., a separate LLM state-diffing mechanism, not a guarantee that the masked line's new context will be re-surfaced). Given attacker controls 100% of the new file content in a full-file rewrite, this is a repeatable and reliable evasion.

### Recommendation
Make the matching hunk/position-aware instead of relying on a flat stripped-text set across the whole file: e.g., use a real diff/LCS algorithm (such as Python's `difflib.SequenceMatcher` on line lists) to identify lines that are truly unchanged (same relative context/position), rather than any-to-any content equality. At minimum, do not fully mask an added line as context when it appears at a different index/proximity than its "matching" removed line, and cap the amount of "masking" so a single removed line cannot suppress N unrelated additions — require the matched removed line to be consumed at most once and be positionally close to reduce false negatives, or drop this heuristic filter and always send the reviewer the full generated diff with an explicit "may be a rewrite" hint.

### Proof of Concept
Unit test in `gitutil.py`'s existing test suite (mirroring the module's `filter_preexisting_from_diff` tests):

1. Construct a synthetic diff string simulating a full-file `Write` rewrite of `app.py` where:
   - Old file line 5: `    logging.info("start")`
   - New file line 40: `    os.system(user_input)`
   - Old file also had another line `    os.system(user_input)` somewhere harmless/inert (e.g., inside a doc-comment or dead code) that gets removed, while the new dangerous call site at line 40 is genuinely new/reachable code.
   - Diff shows `-    os.system(user_input)` (old, inert one) and `+    os.system(user_input)` (new, dangerous one) as separate hunks/positions.
2. Call `filter_preexisting_from_diff([("app.py", diff_content)], cwd, baseline_sha)`.
3. Assert that the resulting diff still contains a `+` marker on the dangerous line (`assert "+    os.system(user_input)" in result_diff`) rather than being converted to a context line (` `-prefixed). Currently, the function converts it to context (masks it), which the PoC would demonstrate by showing the assertion fails against current behavior — proving the bypass.
4. A fuzz/property-based variant: generate random full-file rewrites where a "sacrificial" old line's stripped text is duplicated as a new line at a different position/hunk, and assert that `filter_preexisting_from_diff` never masks an added line whose surrounding context (preceding/following lines) differs from the removed line's original context — currently this invariant is violated.

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L17-23)
```python
2. **Stop hook (final review)**: When Claude finishes, uses `git diff` against a
   baseline SHA (captured at UserPromptSubmit) to get only the code changed during the
   session. Runs two Haiku analyses on the diff:
   a) Concrete vulnerability scan with severity ratings
   b) Areas-of-concern analysis identifying categories to investigate
   Exits with code 2 to force Claude to continue and address findings.

```
