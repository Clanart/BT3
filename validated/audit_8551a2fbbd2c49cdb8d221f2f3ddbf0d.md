### Title
`parse_diff_into_files` mis-splits diff on attacker-controlled content containing the literal `diff --git ` delimiter, corrupting file→content attribution - ([File: plugins/security-guidance/hooks/gitutil.py])

### Finding Description
`parse_diff_into_files` and `extract_file_paths_from_diff` both split the entire `git diff` output on the literal string `"diff --git "` via `file_diffs = diff_output.split("diff --git ")` [1](#0-0) , then treat `lines[0]` of every resulting chunk as a real diff header matched against `^a/(.+?) b/(.+)$` [2](#0-1) . `str.split` has no awareness of diff-hunk boundaries or the required preceding newline, so a hunk's added (`+`) line containing the literal substring `diff --git a/<path> b/<path>` is treated exactly as a real file boundary. This causes: (1) the real file's collected `diff_lines` to be truncated at the injection point, and (2) a spurious chunk beginning at the embedded text to be parsed as a new file header, with the remainder of the real file's hunk lines (up to the next genuine `diff --git ` boundary) attributed to that spurious path. If the attacker chooses the embedded string to exactly match a legitimate filename that also appears later in the same diff, two entries for that path can be produced — one fabricated from attacker-controlled content and one real — both fed downstream as `(file_path, diff_content)` tuples. If the attacker instead targets a non-reviewable extension (e.g. `.txt`, `.md`), the tail of the real hunk gets silently dropped by the `_is_reviewable_source` filter, removing content from the reviewer's view. No validation exists that checks the split fragment is actually preceded by a newline or immediately followed by `---`/`+++` file markers before trusting it as a header.

### Impact Explanation
This corrupts the diff-content-to-file binding that is the sole input to the security reviewer (`security_reminder_hook.py` calls `parse_diff_into_files` to build the review payload). Two concrete impacts: (a) content-exclusion — an attacker can smuggle a malicious tail of a hunk out of the reviewed source file into a spuriously-named, non-reviewable path, causing that code to be silently excluded from the LLM security review (`_is_reviewable_source` filter never sees it under the true, reviewable path); (b) content-spoofing — an attacker can inject a fabricated diff entry for another real file in the same commit, adding attacker-chosen text into the record the reviewer attributes to that other file, polluting or distracting the review of a legitimate file. Both degrade the review hook's core trust guarantee that "diff content must stay bound to its actual source file," but the effect is bypass/evasion of automated review, not direct code execution, file mutation, or credential exposure.

### Likelihood Explanation
Fully attacker-controlled: any file whose content an attacker can get diffed (including new/untracked files added via a Bash/Write tool call) can contain a line whose stripped text is exactly `diff --git a/<name> b/<name>`. This requires no special privilege beyond writing ordinary file content that ends up in the working tree diff — a normal, low-effort precondition. The parser applies no header-boundary validation (e.g., requiring a preceding `\n`, or requiring the following lines to include `--- a/... / +++ b/...`), so the split is reliably triggered on any matching substring, making this deterministic and repeatable.

### Recommendation
Parse diffs by anchoring on line boundaries instead of a raw substring split: split on `re.split(r'(?m)^diff --git ', diff_output)` (requiring the delimiter to start a line) and additionally require that the immediately following lines contain the expected `--- a/...` / `+++ b/...` markers before accepting a chunk as a new file header. Alternatively, use a proper unified-diff parser (e.g. `unidiff` or `git diff --numstat`/`-z` machine-readable output) rather than manual string splitting.

### Proof of Concept
Unit test in the style of the existing test suite for `gitutil.py`:
```python
diff_output = (
    "diff --git a/real.py b/real.py\n"
    "--- a/real.py\n"
    "+++ b/real.py\n"
    "@@ -0,0 +1,3 @@\n"
    "+line1\n"
    "+diff --git a/other.py b/other.py\n"
    "+line3\n"
    "diff --git a/other.py b/other.py\n"
    "--- a/other.py\n"
    "+++ b/other.py\n"
    "@@ -0,0 +1,1 @@\n"
    "+real_other_content\n"
)
result = parse_diff_into_files(diff_output)
paths = [fp for fp, _ in result]
# Expect exactly one entry per real file, each containing only its own
# genuine hunk content, matching the true `diff --git` header boundaries.
assert paths.count("other.py") == 1
real_py_content = dict(result)["real.py"]
assert "line3" in real_py_content  # not lost to the fake split
assert "real_other_content" not in real_py_content  # not merged across files
```
Running this against the current implementation is expected to fail: `other.py` appears twice (once fabricated from `line3`, once real), and `real.py`'s content is truncated before `line1`'s continuation, demonstrating the mis-split.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L615-624)
```python
def parse_diff_into_files(diff_output):
    """
    Parse unified diff output into a list of (file_path, diff_content) tuples.
    Only includes files with source code extensions.
    """
    if not diff_output or not diff_output.strip():
        return []

    files = []
    file_diffs = diff_output.split("diff --git ")
```

**File:** plugins/security-guidance/hooks/gitutil.py (L631-636)
```python
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue

        file_path = header_match.group(2) or header_match.group(1) or ''
```
