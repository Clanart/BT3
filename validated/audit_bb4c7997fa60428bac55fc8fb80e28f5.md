### Title
Diff header regex mis-splits `a/<path> b/<path>` when a file path contains the literal substring `" b/"`, causing diff content to be bound to a mangled/wrong `file_path` - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`extract_file_paths_from_diff` and `parse_diff_into_files` both derive the reviewed file's path from the raw `diff --git` header line using `re.match(r'^a/(.+?) b/(.+)$', lines[0])`. Because the regex has no anchor tied to the actual `a/`/`b/` prefix boundary beyond the literal substring `" b/"`, a file whose repo-relative path itself contains `" b/"` produces a header line with multiple `" b/"` occurrences, and the non-greedy/greedy group split resolves at the *first* occurrence rather than the true path/path boundary, yielding a corrupted `file_path` while the diff content extraction (which is driven independently by `@@` hunk markers) still corresponds to the real file.

### Finding Description
Both functions split the diff on `"diff --git "` and then run: [1](#0-0) 
`parse_diff_into_files` does the same match at: [2](#0-1) 

For a non-renamed file, git emits `diff --git a/<path> b/<path>` where `<path>` is identical on both sides. If an attacker (who controls filenames they commit, per the stated precondition) creates or renames a file to something like `pkg/thing b/evil.py`, the header line becomes:

```
a/pkg/thing b/evil.py b/pkg/thing b/evil.py
```

The regex `^a/(.+?) b/(.+)$` is lazy on group 1, so it matches the *shortest* possible prefix ending in the literal `" b/"` token — i.e. it splits at the first occurrence embedded inside the filename itself, not at the real `a/...` → `b/...` boundary. This produces:
- `group(1)` = `"pkg/thing"`
- `group(2)` = `"evil.py b/pkg/thing b/evil.py"`

`file_path = header_match.group(2)` is then this garbled string, not the real repo path `pkg/thing b/evil.py`. The hunk/content extraction loop in `parse_diff_into_files` (lines 642-652) is independent of the header parse — it just scans for `@@` markers — so the *content* returned is the correct diff for the real file, but it is bound to the wrong `file_path` key in the returned tuple.

`_is_reviewable_source` is then evaluated against this mangled string: [3](#0-2) 
Because `os.path.splitext` only looks at the text after the last `/`, and the mangled string still ends in `.py` in this example, the file is still classified reviewable — but under the wrong path. In other constructions (e.g., a real path with a source extension whose mangled tail loses/changes the extension), the same bug can instead cause the file to be *dropped* from `_is_reviewable_source`, silently excluding it from the security review pipeline.

No existing validation catches this: the regex is the only gate, there is no cross-check against `git diff --name-only`/`git status` output, and the `---`/`+++` lines (which also embed the same ambiguous path) are never consulted as a corroborating source of truth.

### Impact Explanation
This is scoped entirely to `security-guidance`'s own diff-review pipeline, which is advisory (it prints findings / exits 2 to nudge Claude to fix code) and not an enforcement/approval boundary. Two concrete effects:
1. **Misattribution**: LLM-review findings get reported against a bogus, non-existent path, degrading the usefulness/accuracy of the review output shown to the user/Claude.
2. **Coverage bypass**: crafted filenames can make the mangled path fail `_is_reviewable_source`, silently excluding the actually-changed (and potentially malicious/vulnerable) file from the LLM security review entirely — the file's real content changes go unreviewed by the plugin.

This does not itself grant command execution, secret disclosure, or workspace escape; it degrades/bypasses the security-guidance plugin's own detection coverage for the file that was manipulated to trigger it.

### Likelihood Explanation
Requires only that the attacker (already assumed to have commit access to the repository per the question's precondition) name or rename a file to include the literal substring `" b/"` in its path. This is trivial to reproduce deterministically and does not depend on race conditions, privilege escalation, or environment-specific behavior — the flaw is a pure string/regex parsing defect exercised by `git diff` output.

### Recommendation
Do not derive `file_path` by regex-splitting on the literal `" b/"` token. Instead:
- Prefer using `git diff --name-only -z` / `git status --porcelain -z` (NUL-delimited, unambiguous) to obtain the authoritative set of changed paths, as `_git_name_only`/`_git_status_porcelain` already do elsewhere in this module, and correlate diff sections to those paths positionally (they appear in the same order as `git diff` emits them).
- If header parsing must stay, parse using the `---`/`+++ ` lines together with an equality/length-based disambiguation (e.g., since `a/<path>` and `b/<path>` are equal-length except for the prefix, the true split point is `len(line) - len(a/<path>)`, computable once you know the whole line is `a/<path> b/<path>` with `<path>` repeated) rather than a literal `" b/"` search.

### Proof of Concept
Unit test to add near existing diff-parsing tests:
```python
def test_parse_diff_into_files_path_containing_space_b_slash():
    real_path = "pkg/thing b/evil.py"
    diff_output = (
        f"diff --git a/{real_path} b/{real_path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        f"--- /dev/null\n"
        f"+++ b/{real_path}\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+os.system('evil')\n"
    )
    files = parse_diff_into_files(diff_output)
    assert len(files) == 1
    parsed_path, content = files[0]
    # Currently FAILS: parsed_path == "evil.py b/pkg/thing b/evil.py"
    assert parsed_path == real_path
```
Expected: the assertion fails against current code, demonstrating `parsed_path` is `"evil.py b/pkg/thing b/evil.py"` instead of the real repo-relative path `"pkg/thing b/evil.py"`, confirming the mis-binding of diff content to an incorrect file path.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L550-562)
```python
def _is_reviewable_source(file_path):
    # Normalize for component matching: a path like `.next/x.js` or
    # `pkg/node_modules/y.ts` should both be excluded; matching against
    # `'/' + path` lets each pattern be checked as `'/' + p in '/' + path`
    # without false-positiving on `rebuild/` matching `build/`.
    norm = "/" + file_path.replace("\\", "/")
    if any(("/" + p) in norm for p in SKIP_PATH_PATTERNS):
        return False
    if file_path.endswith(SKIP_FILE_SUFFIXES):
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in SOURCE_CODE_EXTENSIONS:
        return True
```

**File:** plugins/security-guidance/hooks/gitutil.py (L602-609)
```python
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue
        file_path = header_match.group(2) or header_match.group(1) or ''
        if not _is_reviewable_source(file_path):
            continue
        paths.append(file_path)
```

**File:** plugins/security-guidance/hooks/gitutil.py (L630-640)
```python
        # Extract filename from first line: "a/path/to/file b/path/to/file"
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue

        file_path = header_match.group(2) or header_match.group(1) or ''

        # Filter to source code files only
        if not _is_reviewable_source(file_path):
            continue
```
