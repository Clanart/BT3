### Title
Attacker-controlled filenames matching `SKIP_PATH_PATTERNS`/`SKIP_FILE_SUFFIXES` substrings permanently bypass source-code review classification - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`_is_reviewable_source` decides whether a changed file is sent to the LLM diff/commit reviewer by doing a pure substring/suffix match against `SKIP_PATH_PATTERNS` and `SKIP_FILE_SUFFIXES`, with no verification that the file is actually vendored, generated, or a real lockfile. An attacker who controls file naming in the working tree (e.g. via a Write/Edit tool call or a crafted repo layout) can place genuinely new, malicious source code at a path or with a suffix that matches these patterns and have it silently excluded from review.

### Finding Description
`_is_reviewable_source` in [1](#0-0)  normalizes the path and checks path-component/suffix membership:
```
norm = "/" + file_path.replace("\\", "/")
if any(("/" + p) in norm for p in SKIP_PATH_PATTERNS):
    return False
if file_path.endswith(SKIP_FILE_SUFFIXES):
    return False
```
`SKIP_PATH_PATTERNS` (`node_modules/`, `dist/`, `build/`, `.next/`, `vendor/`, `__generated__/`, `__pycache__/`, `.venv/`, `target/`) and `SKIP_FILE_SUFFIXES` (`.min.js`, `.min.css`, `.d.ts`, `.d.mts`, `.d.cts`, `.lock`, `_pb2.py`, `.pb.go`) are defined at [2](#0-1) . Neither check verifies provenance (e.g. whether the file is actually gitignored, tracked as a submodule/dependency, or produced by a real build/codegen tool) — it is a pure filename/path heuristic on attacker-controlled input.

This classifier gates the two call sites that feed the LLM-based review: `extract_file_paths_from_diff` ( [3](#0-2) ) and `parse_diff_into_files` ( [4](#0-3) ), both of which call `_is_reviewable_source(file_path)` and `continue` (drop the file) when it returns `False`. These are used by the Stop-hook diff review and the commit/push review pipeline in `security_reminder_hook.py` (imported at [5](#0-4) ).

Exploit flow: an attacker (or an agent acting on attacker-supplied instructions/content) creates a file such as `src/vendor/malicious_exec.py` or `payload.lock` containing `os.system(attacker_input)`. Because `'/vendor/'` is a substring of `'/src/vendor/malicious_exec.py'`, and `payload.lock` ends with `.lock`, `_is_reviewable_source` returns `False` for both — regardless of the file being genuinely a third-party dependency or lockfile. The file is therefore never included in `diff_files` passed to `analyze_code_security`/`agentic_review`, so the LLM-based Stop-hook and commit-review layers never see it. No allowlist, workspace guard, or content-based check corroborates the path/suffix classification before exclusion.

### Impact Explanation
This causes a genuine trust-boundary bypass in the security-guidance plugin: attacker-authored source code (including code with real exploit primitives like `os.system`, `eval`, `pickle.loads`, etc.) can be introduced into the repository with a filename engineered to match a skip pattern, and it will be completely excluded from the Stop-hook LLM diff review and the commit/push LLM review — the two layers meant to catch exactly this class of change. The result is a fully unreviewed code-introduction path, defeating the plugin's core security invariant that "untrusted repo content must not silently broaden capability by evading trusted validation."

### Likelihood Explanation
Highly feasible and repeatable: the only precondition is the ability to create or rename a file under a path containing one of nine common directory-name substrings, or with one of eight suffixes — all trivially chosen by whoever writes the file (a compromised/prompt-injected agent turn, or a repo with attacker-influenced content that gets copied/renamed during agent work). No special privilege is needed beyond normal file write capability already assumed for the attacker in this threat model.

### Recommendation
Do not rely solely on filename/path substring heuristics to exclude files from security review. At minimum: (1) verify actual provenance before skipping — e.g., only skip paths that are tracked as ignored via `.gitignore`/`git check-ignore`, or that are genuinely under a package-manager-owned directory (verified via lockfile/manifest cross-reference) rather than any path merely containing the string; (2) restrict `SKIP_PATH_PATTERNS` matching to path prefixes/anchors from the repo root rather than substring matches anywhere in the path, so `src/vendor/...` isn't conflated with a real top-level `vendor/`; (3) for newly-added (untracked/new) files in particular, prefer failing safe (reviewing) over silently skipping, since generated/vendor directories are typically pre-existing, not newly authored in the session being reviewed.

### Proof of Concept
Unit test in the module that owns `_is_reviewable_source`:
```python
def test_vendor_substring_bypasses_review():
    # Attacker-authored file nested under a path containing 'vendor/'
    # but not a real vendored dependency.
    assert _is_reviewable_source("src/vendor/malicious_exec.py") is False  # should be True (real new code)

def test_lock_suffix_bypasses_review():
    # Attacker names a payload file with a .lock suffix that is not a
    # real dependency lockfile.
    assert _is_reviewable_source("payload.lock") is False  # should be True

def test_pipeline_drops_attacker_file_from_review(monkeypatch):
    diff = (
        "diff --git a/src/vendor/malicious_exec.py b/src/vendor/malicious_exec.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/vendor/malicious_exec.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+os.system(input())\n"
    )
    files = extract_file_paths_from_diff(diff)
    assert "src/vendor/malicious_exec.py" not in files  # demonstrates false-negative opt-out
```
Expected assertion: the classifier and downstream `extract_file_paths_from_diff`/`parse_diff_into_files` drop the attacker-created file even though it is genuinely new, attacker-authored code — confirming it never reaches `analyze_code_security`/`agentic_review`.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L472-479)
```python
SKIP_PATH_PATTERNS = (
    'node_modules/', 'dist/', 'build/', '.next/', 'vendor/',
    '__generated__/', '__pycache__/', '.venv/', 'target/',
)
SKIP_FILE_SUFFIXES = (
    '.min.js', '.min.css', '.d.ts', '.d.mts', '.d.cts',
    '.lock', '_pb2.py', '.pb.go',
)
```

**File:** plugins/security-guidance/hooks/gitutil.py (L550-559)
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
```

**File:** plugins/security-guidance/hooks/gitutil.py (L587-611)
```python
def extract_file_paths_from_diff(diff_output):
    """
    Extract file paths from unified diff output (without content).
    Only includes files with source code extensions.
    Returns a list of file paths.
    """
    if not diff_output or not diff_output.strip():
        return []

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

**File:** plugins/security-guidance/hooks/gitutil.py (L615-654)
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

        # Extract the diff content (from first @@ onwards)
        diff_lines = []
        in_hunks = False
        for line in lines[1:]:
            if line.startswith('@@'):
                in_hunks = True
            if in_hunks:
                diff_lines.append(line)

        if diff_lines:
            files.append((file_path, '\n'.join(diff_lines)))

    return files
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L97-110)
```python
from gitutil import (  # noqa: E402,F401
    GIT_CMD,
    _git_rev_parse_head, _find_git_index, _diff_pathspec, _temp_index,
    _git_toplevel, _git_dir, _git_rev_list_range, _git_diff_range,
    _detect_main_branch, _git_reflog_recent_commits, _git_name_only,
    _git_status_porcelain, _is_ancestor, get_git_diff,
    SOURCE_CODE_EXTENSIONS, SOURCE_CODE_BASENAMES,
    NON_SOURCE_EXTENSIONLESS_BASENAMES, SKIP_PATH_PATTERNS,
    SKIP_FILE_SUFFIXES, _SECURITY_RISK_PATH_TOKENS,
    _LOW_PRIORITY_SUFFIXES, _LOW_PRIORITY_PATH_TOKENS,
    _prioritize_diff_files, _is_reviewable_source,
    extract_file_paths_from_diff, parse_diff_into_files,
    filter_preexisting_from_diff,
)
```
