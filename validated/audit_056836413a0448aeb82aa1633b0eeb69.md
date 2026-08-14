### Title
Unquoted diff-header regex lets unicode/special-character filenames bypass security review filtering - (File: plugins/security-guidance/hooks/gitutil.py)

### Summary
`get_git_diff` invokes `git diff` using `GIT_CMD`, which never sets `core.quotePath=false`, unlike the other git helpers in the same file (`_git_name_only`, `_git_status_porcelain`) that explicitly disable quoting [1](#0-0) . Because `core.quotePath` defaults to `true`, git diff output for any file with non-ASCII (unicode) or otherwise "unusual" characters in its path is emitted C-quoted and octal-escaped (e.g. `"a/\346\226\207.py" "b/\346\226\207.py"`), and the downstream parsers `extract_file_paths_from_diff` and `parse_diff_into_files` only match the unquoted pattern `^a/(.+?) b/(.+)$` [2](#0-1) [3](#0-2) .

### Finding Description
`get_git_diff` builds its diff command from `GIT_CMD` plus `diff --no-color --no-ext-diff <baseline_sha> [--unified=99999] <pathspec>` and returns the raw stdout as text [4](#0-3) . Unlike `_git_name_only` and `_git_status_porcelain`, which both explicitly pass `-c core.quotePath=false` to keep non-ASCII paths intact for their name-only parsing [5](#0-4) [6](#0-5) , the primary review-diff command in `get_git_diff` does not set this option and inherits `core.quotePath`'s default value of `true`.

When `core.quotePath=true` (the default), git quotes any path containing non-ASCII bytes or other "unusual" characters in the `diff --git a/... b/...` header line, wrapping each side in double quotes and backslash/octal-escaping the bytes. The header parser used by `extract_file_paths_from_diff` and `parse_diff_into_files`, `re.match(r'^a/(.+?) b/(.+)$', lines[0])`, requires the line to start literally with `a/`. A quoted header instead starts with `"`, so the regex fails to match, `header_match` is `None`, and the entire per-file diff block for that path is silently skipped via `continue` [7](#0-6) [8](#0-7) .

Because a repository under an attacker's (or a compromised dependency's/PR's) control can freely choose file names, committing or modifying a source file whose path contains non-ASCII characters (e.g. a directory or filename with emoji, CJK, accented characters, or any byte ≥ 0x80) causes that file's entire diff content to be invisibly dropped from both `extract_file_paths_from_diff` (used to decide which files are "reviewable source") and `parse_diff_into_files` (used to extract the actual hunks fed to the LLM/commit reviewer). The file's real changes are still present in the working tree/commit and will still be executed/loaded by tooling, but the security review path that is supposed to gate `Stop-hook and commit-review diff collection` never sees them.

### Impact Explanation
This breaks the stated invariant that "reviewable-source filtering must not let attacker-crafted filenames hide dangerous source changes." A malicious or compromised change delivered through a unicode-named source file (e.g. a `.py`/`.sh`/`Dockerfile`-equivalent placed at `文件.py` or `résumé/deploy.sh`) is committed normally, but the commit/Stop-hook review pipeline that summarizes and reviews diffs for dangerous commands silently omits it from the reviewed content. Since Claude Code's security-guidance review is a control meant to catch dangerous local command execution/backdoors before they are trusted, an attacker who can land such a file in the repo (e.g. via a PR, a fork merge, or any repo-controlled path) can smuggle unauthorized local command execution logic past the review gate — matching the target impact of "Unauthorized local command execution that bypasses Claude Code approval or deny controls."

### Likelihood Explanation
The attacker needs no privilege beyond the ability to add or modify a file in the repository (a completely ordinary contribution/PR/merge scenario) using a filename containing at least one non-ASCII character — a low bar, common in real-world repos with internationalized filenames, and trivially craftable by an attacker. No symlink trickery, race condition, or special git configuration is required; this triggers purely from `core.quotePath`'s standard default behavior interacting with the review parser's naive regex. It is fully reproducible and deterministic.

### Recommendation
Add `-c core.quotePath=false` to the `git diff` invocation used in `get_git_diff` (or to `GIT_CMD` globally) so review diffs are emitted with literal, unquoted paths, consistent with `_git_name_only` and `_git_status_porcelain`. Additionally, harden `extract_file_paths_from_diff`/`parse_diff_into_files`'s header parsing to detect and unquote C-quoted `"a/..." "b/..."` headers defensively (in case `quotePath` is re-enabled by user config elsewhere, e.g. via a repo-level `.git/config` an attacker could also try to influence), rather than silently dropping unmatched headers.

### Proof of Concept
Unit test plan for `plugins/security-guidance/hooks/gitutil.py`:
1. Create a temp git repo; commit an initial baseline file.
2. Add a new file with a non-ASCII path, e.g. `文件.py`, containing an obviously dangerous line such as `os.system(input())`.
3. Call `get_git_diff(cwd, baseline_sha)` and inspect the raw returned diff text — assert it contains a quoted header line (e.g. starts with `diff --git "a/...` rather than `diff --git a/...`).
4. Call `extract_file_paths_from_diff(diff_text)` and `parse_diff_into_files(diff_text)` on that output.
   - Expected (buggy) result: the unicode-named file is absent from both return values, i.e. the dangerous `os.system(input())` addition never reaches the reviewer.
   - Expected (fixed) result after adding `core.quotePath=false`: the unicode path appears verbatim in `extract_file_paths_from_diff`'s output list and its hunk content (including the `os.system` line) appears in `parse_diff_into_files`'s tuples.
5. Add a regression assertion in the existing test suite (if present under a `tests/` directory for `security_reminder_hook`) that a unicode-filename source change is never silently excluded from the reviewed diff set.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L25-29)
```python
GIT_CMD = [
    "git",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
]
```

**File:** plugins/security-guidance/hooks/gitutil.py (L303-311)
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
```

**File:** plugins/security-guidance/hooks/gitutil.py (L343-346)
```python
        r = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "status",
             "--porcelain=v1", "-uall", "-z"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
```

**File:** plugins/security-guidance/hooks/gitutil.py (L406-424)
```python
    pathspec = _diff_pathspec(cwd, paths)
    if paths and not pathspec:
        # Caller restricted to specific paths but none are inside this repo
        # (e.g. only ~/.claude/... edits). Returning "" flows to skip(6); an
        # empty pathspec would mean an UNRESTRICTED diff — the bug this whole
        # change exists to fix.
        return ""

    cmd = [*GIT_CMD, "diff", "--no-color", "--no-ext-diff", baseline_sha] + (["--unified=99999"] if full_context else []) + pathspec
    try:
        with _temp_index(cwd, untracked_paths) as env:
            # env is None when no index could be found (bare repo / not a
            # repo) — diff still runs, just without untracked-file support.
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=30, env=env)
        if result.returncode != 0:
            debug_log(f"git diff failed: {result.stderr[:200].decode('utf-8', errors='replace')}")
            return None
        # Decode with errors='replace' so binary diffs don't crash
        return result.stdout.decode("utf-8", errors="replace")
```

**File:** plugins/security-guidance/hooks/gitutil.py (L599-609)
```python
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
```

**File:** plugins/security-guidance/hooks/gitutil.py (L626-641)
```python
    for file_diff in file_diffs:
        if not file_diff.strip():
            continue

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
