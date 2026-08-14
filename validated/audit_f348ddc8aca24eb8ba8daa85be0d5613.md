### Title
Git diff header regex silently drops quoted/non-ASCII filenames from security review, bypassing detection - ([File: plugins/security-guidance/hooks/gitutil.py])

### Finding Description
`get_git_diff` runs `git diff` using `GIT_CMD`, which sets `core.fsmonitor=false` and `core.hooksPath=/dev/null` but **never** sets `core.quotePath=false` [1](#0-0) [2](#0-1) . Git's default `core.quotePath=true` means any path containing a non-ASCII byte (UTF‑8 character), backslash, double quote, or control character (e.g. a literal newline) is emitted C-quoted, e.g. `diff --git "a/caf\303\251.py" "b/caf\303\251.py"` instead of the plain `diff --git a/café.py b/café.py`.

`extract_file_paths_from_diff` and `parse_diff_into_files` both split the diff on the literal `"diff --git "` delimiter and then try to parse the first line of each chunk with `re.match(r'^a/(.+?) b/(.+)$', lines[0])` [3](#0-2) [4](#0-3) . This regex requires the line to start literally with `a/`. When git quotes the path (leading `"` character), the regex fails to match, `header_match` is `None`, and the `continue` statement causes the **entire file's diff block — header and all hunks — to be dropped** from the returned file list, without any fallback unquoting logic.

Because `parse_diff_into_files` feeds the Stop-hook's LLM security review pipeline (per `security_reminder_hook.py`'s Stop-hook review of the `git diff` against the session baseline [5](#0-4) ), any file whose path contains a character that triggers git's default path-quoting is silently excluded from both vulnerability scanning and areas-of-concern analysis. An attacker (or even an ordinary contributor) only needs to name/rename a file with a non-ASCII character, backslash, or embedded control character to make its diff invisible to the review pipeline — a low-effort, fully unprivileged, repository-content-only action.

### Impact Explanation
This is a security-control bypass: security-relevant source changes (e.g., injected backdoors, hardcoded secrets, command injection) placed in a file with a non-ASCII/quoted path name are never presented to the LLM reviewer, so the Stop-hook's vulnerability scan and areas-of-concern analysis both silently miss them. This matches the "detection bypass" impact class — the plugin's core security guarantee (all session-changed code gets reviewed) is broken for an entire, easily-triggered class of filenames.

### Likelihood Explanation
Highly feasible and repeatable: no special privilege is needed, just committing or renaming a file to include a single non-ASCII character (e.g., `café.py`, `résumé.js`, or any filename with an emoji/accented character) or a backslash — all of which are legal, unremarkable filenames that occur naturally in real repositories (i18n content, localized file names), making both deliberate abuse and incidental false negatives likely.

### Recommendation
- Run diff generation with `-c core.quotePath=false` (as already done for `_git_name_only`/`_git_status_porcelain`) so paths are emitted unquoted UTF‑8, and additionally handle the case where a filename still needs quoting (embedded backslash/quote/control char) by detecting the leading `"` and unquoting/unescaping the C-style octal escapes before matching.
- Prefer a more robust extraction strategy such as parsing the `--- a/...` / `+++ b/...` lines (which are also subject to quoting) or invoking `git diff --name-only -z` in parallel to authoritatively map file identities, rather than relying solely on a regex over the `diff --git` header line.
- Add an explicit test asserting that a file whose header line does not match the plain-path regex is never silently dropped — either raise/log a warning or fall back to a safe default (e.g., treat as reviewable) rather than `continue`.

### Proof of Concept
Unit test in `gitutil.py`'s test suite:
1. Create a temp git repo, `git config core.quotePath true` (default), add and commit a base file, then add a new file named `café.py` (or any filename with a non-ASCII character) containing an obvious vulnerability marker string, e.g. `os.system(user_input)`.
2. Call `get_git_diff(repo, baseline_sha)` and feed the result to `parse_diff_into_files(diff_output)`.
3. Assert that `café.py` (or its quoted form) is present in the returned list of `(file_path, diff_content)` tuples and that the vulnerability marker string is included in `diff_content`.
4. Expected (buggy) result: the returned list is empty / does not contain the file, demonstrating the diff for that file is silently dropped — proving the detection bypass.
5. As a differential invariant check, compare the set of files reported by `git diff --name-only -z` (ground truth) against the set of file paths returned by `parse_diff_into_files`/`extract_file_paths_from_diff` for repos containing non-ASCII, backslash, or embedded-control-character filenames, asserting they must always match.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L25-29)
```python
GIT_CMD = [
    "git",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
]
```

**File:** plugins/security-guidance/hooks/gitutil.py (L414-424)
```python
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

**File:** plugins/security-guidance/hooks/gitutil.py (L631-640)
```python
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue

        file_path = header_match.group(2) or header_match.group(1) or ''

        # Filter to source code files only
        if not _is_reviewable_source(file_path):
            continue
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L17-22)
```python
2. **Stop hook (final review)**: When Claude finishes, uses `git diff` against a
   baseline SHA (captured at UserPromptSubmit) to get only the code changed during the
   session. Runs two Haiku analyses on the diff:
   a) Concrete vulnerability scan with severity ratings
   b) Areas-of-concern analysis identifying categories to investigate
   Exits with code 2 to force Claude to continue and address findings.
```
