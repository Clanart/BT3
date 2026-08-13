### Title
Diff header regex misattributes file paths for filenames containing " b/" substrings - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`extract_file_paths_from_diff` and `parse_diff_into_files` split diff text on `"diff --git "` and match the first line against `^a/(.+?) b/(.+)$` to recover the file path, using a non-greedy first group. When an attacker-controlled filename itself contains a `" b/"` substring, this regex is ambiguous and will match at the *first* occurrence of `" b/"` in the line rather than the true `a/... b/...` boundary, causing `file_path` (taken from `group(2)`) to be a garbled string that does not equal the actual file git diffed.

### Finding Description
Both functions do:
```python
file_diffs = diff_output.split("diff --git ")
...
lines = file_diff.split('\n')
header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
file_path = header_match.group(2) or header_match.group(1) or ''
``` [1](#0-0) [2](#0-1) 

For a real git diff of a file literally named `x b/evil.py`, the header line git emits is `diff --git a/x b/evil.py b/x b/evil.py`. After splitting on `"diff --git "`, `lines[0]` is `a/x b/evil.py b/x b/evil.py`. The non-greedy `(.+?)` in the regex stops at the first candidate that lets ` b/` match, i.e. it matches `group(1) = "x"` and ` b/` against the substring right after it, leaving `group(2) = "evil.py b/x b/evil.py"` for the rest of the line (since the second group `.+` is greedy and consumes to end of line). The code then sets `file_path = group(2)`, which is neither the real path `x b/evil.py` nor a sane prefix of it — it is an artifact of where the ambiguous split happened, not the actual file.

The `diff_lines`/hunk-content extraction that follows (`parse_diff_into_files`) still walks `lines[1:]` looking for `@@` markers, so the *content* attached to this mis-derived `file_path` is still the correct hunk body for the file in question — but it is now labeled with the wrong path string. Downstream consumers (`_is_reviewable_source(file_path)` filtering, and the LLM/agentic review that is told "here is file `<file_path>`: `<diff_content>`") therefore see an incorrect path for real diff content. This can cause:
- The extension/basename-based reviewability filter (`_is_reviewable_source`) to make its accept/reject decision on the wrong (attacker-crafted) string instead of the real extension, potentially causing a genuinely reviewable source file to be dropped from review or an unintended file to appear reviewable.
- The security reviewer/LLM to attribute findings to a fabricated path, breaking the trust boundary between "what content was reviewed" and "what path the reviewer/user is told was reviewed."

No existing validation catches this: there is no cross-check against `git diff --name-only` output, and the regex has no anchoring against embedded `" b/"` sequences (e.g., requiring the split point to be the *last* possible one, or reconstructing from `a/`/`b/` prefix symmetry, or using `--` NUL-delimited names from `--name-only`/`-z`, which the code already uses elsewhere for exactly this ambiguity, e.g. `_git_name_only`) [3](#0-2) .

### Impact Explanation
This is a parser-differential bug in trusted review/attribution logic: the security-guidance plugin's Stop-hook / commit-review pipeline uses these path extractions to decide which files get sent to the LLM security reviewer and what path label is attached to findings. An attacker who can name a file in the repository (an ordinary, unprivileged action — no special git or filesystem permission needed) can engineer situations where a genuinely malicious diff either (a) is silently dropped from review because the corrupted `file_path` fails `_is_reviewable_source`'s extension check, or (b) is reported/attributed under an incorrect, attacker-chosen path string, undermining the reliability of the review/attribution output that downstream logic and users rely on to locate and act on flagged content. This matches a trust-boundary-bypass / review-evasion class of impact rather than remote code execution — it degrades the guarantee that "the file path shown for a finding matches the file that was actually diffed."

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: the attacker only needs to create/rename a file whose name contains a literal `" b/"` substring in a repository that will be diffed by this plugin (e.g. via `Write`, `Edit`, or a crafted PR checked out locally). This requires no special privilege beyond normal repo write access already assumed for an "unprivileged" contributor scenario, and is fully deterministic/repeatable — any diff touching such a filename triggers the mis-parse every time.

### Recommendation
Do not rely on a best-effort `a/... b/...` regex split on the header line to recover paths when filenames can contain the literal `" b/"` sequence. Options:
- Prefer parsing the `--- a/<path>` / `+++ b/<path>` lines instead of the `diff --git` summary line, and validate they refer to the same base path (still ambiguous for embedded `" b/"`/`" a/"` but combinable with a length-symmetry check since `a/<path>` and `b/<path>` must be equal-length after their 2-char prefixes).
- More robustly, cross-check/derive the file list authoritatively via `git diff --name-only -z` (already implemented in `_git_name_only`) and use that NUL-delimited, unambiguous path list to drive which per-file diff chunk maps to which path, instead of trusting the regex split on `diff --git` text alone.
- At minimum, when `" b/"` appears more than once as a candidate split in the header line, treat the match as ambiguous and fall back to the authoritative `--name-only` mapping rather than guessing.

### Proof of Concept
Unit test in `plugins/security-guidance/hooks/gitutil.py` test suite:
```python
def test_extract_file_paths_handles_embedded_b_slash(tmp_path):
    import subprocess
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True)
    weird_name = "x b/evil.py"
    (repo / weird_name).write_text("import os\nos.system('rm -rf /')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add weird file"], cwd=repo, check=True)
    diff = subprocess.run(
        ["git", "diff", "--no-color", "HEAD~1", "HEAD"],
        cwd=repo, capture_output=True, text=True
    ).stdout
    real_paths = set(subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD~1", "HEAD"],
        cwd=repo, capture_output=True, text=True
    ).stdout.split("\0")) - {""}

    from gitutil import extract_file_paths_from_diff
    extracted = extract_file_paths_from_diff(diff)

    # Expected: extracted path(s) must exactly match git's own name-only output.
    assert set(extracted) == real_paths, (
        f"parser differential: extracted={extracted!r} real={real_paths!r}"
    )
```
Expected current (buggy) result: `extracted` is `["evil.py b/x b/evil.py"]` (or similar garbled string), not equal to `real_paths == {"x b/evil.py"}`, demonstrating the misattribution. A fix should make the assertion pass.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L303-318)
```python
def _git_name_only(cwd, base, include_untracked=False):
    """Return the set of repo-root-relative paths that differ from `base`,
    or None if git failed (unresolvable ref, not a repo, timeout). Callers
    must distinguish None (error → don't trust as a filter) from set()
    (genuinely nothing changed). `-c core.quotePath=false -z` keeps non-ASCII
    and space-containing paths intact."""
    def _run(env):
        result = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "diff", "--name-only", "-z", base],
            cwd=cwd, capture_output=True, text=True, timeout=30,
            env=env,
        )
        if result.returncode != 0:
            debug_log(f"_git_name_only({base!r}) rc={result.returncode}: {result.stderr[:200]}")
            return None
        return {p for p in result.stdout.split("\0") if p}
```

**File:** plugins/security-guidance/hooks/gitutil.py (L596-611)
```python
    paths = []
    file_diffs = diff_output.split("diff --git ")

    for file_diff in file_diffs:
        if not file_diff.strip():
            continue
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue
        file_path = header_match.group(2) or header_match.group(1) or ''
        if not _is_reviewable_source(file_path):
            continue
        paths.append(file_path)

    return paths
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
