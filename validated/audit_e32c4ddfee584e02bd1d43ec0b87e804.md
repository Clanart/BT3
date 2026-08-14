### Title
Full-file rewrite diff filter allows malicious `+` lines to be masked as pre-existing context via stripped-text collision - ([File: plugins/security-guidance/hooks/gitutil.py])

### Finding Description
`filter_preexisting_from_diff` is used to strip pre-existing content out of a diff when a file is fully rewritten (e.g. via the Write tool), so the LLM reviewer only sees "truly new" code [1](#0-0) . It builds a `removed_lines` set from all `-` lines (post `.strip()`) and an `added_lines` list from all `+` lines (post `.strip()`), then rewrites any `+` line whose stripped content is present in `removed_lines` into a context line (` ` prefix) — regardless of where in the file that line originally was [2](#0-1) .

The matching is purely a set-membership check on stripped text, with no positional/hunk correlation between the specific removed line and the specific added line. This means an attacker who fully rewrites a tracked file (Write tool triggers a full delete+recreate, so git shows the whole old file as `-` lines and the whole new file as `+` lines) can engineer a malicious added line whose `.strip()`-normalized text exactly matches some unrelated removed line elsewhere in the same file (e.g. a removed blank/boilerplate/common statement like `except Exception:` or `return None` or `import os`). That malicious `+` line is then demoted to a context line and effectively hidden from the LLM reviewer's diff-based per-line/anchor analysis.

This is not merely theoretical — the code's own comment explicitly documents this exact class of imprecision as a known, accepted trade-off: *"the set lets one removal mask N additions of the same stripped text... Accepted trade-off"* [3](#0-2) . The comment claims the diff-review prompt's "previous-findings recheck" is the backstop, but I was unable to locate or confirm this backstop mechanism in `llm.py` or `security_reminder_hook.py` within the available index, so its existence/effectiveness could not be verified.

### Impact Explanation
If exploited, a malicious line introduced via a full-file rewrite (Write tool) would not appear as a `+` diff line to the LLM security reviewer, causing it to be silently skipped from per-line security analysis. This is a trust-boundary bypass of the diff-based review pipeline: attacker-controlled new code is misrepresented as pre-existing/unchanged, defeating the intended purpose of `filter_preexisting_from_diff` and of the surrounding review hook. This matches a "review/export logic bypass" impact class — the reviewer's visibility into genuinely new code is what the entire mechanism exists to guarantee, and this collision breaks that guarantee.

### Likelihood Explanation
Preconditions: (1) the file must be fully rewritten via the Write tool (not a partial Edit) so the diff takes the delete-all/add-all shape that triggers this filter path (`removed_lines` non-empty and some fraction of `added_lines` match) [4](#0-3) ; (2) the attacker needs to choose malicious code whose `.strip()`-normalized text exactly matches a line that existed (and was removed) elsewhere in the original file. Since `.strip()` only removes leading/trailing whitespace (not internal formatting) and only requires an exact string match, this is feasible for short/common lines but requires the attacker to know or control the original file's content to pick a colliding string — which is realistic since the attacker is the one rewriting the file and can inspect it first. This is a self-contained, deterministic, and repeatable bypass (no timing/race conditions).

### Recommendation
Replace the global "removed lines as a set" matching with position-aware diff alignment (e.g. line-by-line/sequence diff such as Python's `difflib.SequenceMatcher` operating on the pre-image and post-image, marking only lines that align to an equivalent position/block as "moved/unchanged"), or use `git diff --unified=... -B/-M` style move-detection semantics. At minimum, do not allow a single removed line to mask more than one added occurrence, and require the matched removed line to be reasonably close (e.g. within the same hunk or a small line-distance window) rather than anywhere in the file, to prevent unrelated cross-file-position collisions from suppressing genuinely new, potentially malicious content.

### Proof of Concept
Unit test in the style of the existing test suite for `gitutil.py`:
```python
def test_filter_preexisting_does_not_mask_unrelated_collision():
    diff_content = (
        "@@ -1,3 +1,3 @@\n"
        "-return None\n"          # benign removed line elsewhere in file
        "-old_helper()\n"
        "+return None\n"          # legitimately unchanged -> ok to mask
        "+os.system(cmd)\n"       # malicious new line
    )
    # Craft the malicious added line's stripped text to collide with a
    # DIFFERENT removed line than the one it logically replaces:
    diff_content_attack = (
        "@@ -1,4 +1,4 @@\n"
        "-return None\n"
        "-os.system(cmd)\n"        # pre-existing benign occurrence, unrelated
        "+import subprocess\n"
        "+os.system(cmd)\n"        # attacker's NEW malicious line, collides with above
    )
    result = filter_preexisting_from_diff(
        [("evil.py", diff_content_attack)], cwd=".", baseline_sha="deadbeef"
    )
    _, filtered = result[0]
    # Expect the malicious os.system(cmd) line to remain a '+' (new) line,
    # NOT be converted to a context ' ' line, even though its stripped text
    # collided with an unrelated removed line.
    assert "+os.system(cmd)" in filtered.split("\n")
    assert " os.system(cmd)" not in filtered.split("\n")
```
Running this against the current implementation is expected to FAIL (the malicious line gets converted to a context line), demonstrating the bypass.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L657-666)
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
```

**File:** plugins/security-guidance/hooks/gitutil.py (L672-719)
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

```
