### Title
`_diff_pathspec`'s naive `startswith("..")` check silently drops in-repo files whose names begin with two dots, causing them to escape the git-diff security review scope - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`_diff_pathspec` is meant to convert Claude-touched absolute paths into a repo-relative pathspec, dropping only paths that resolve *outside* the repo (parent-traversal). Its exclusion test is `r.startswith("..")` on the raw relpath string, which also matches any in-repo file or directory whose basename simply begins with two literal dots (e.g. `..payload.py`), not just genuine `../`-style parent components. Such legitimately in-repo, session-touched files are wrongly treated as "outside cwd" and dropped from the pathspec, so `git diff -- <pathspec>` silently never surfaces their content to the Stop-hook LLM security reviewer.

### Finding Description
`_diff_pathspec(cwd, paths)` in [1](#0-0)  computes, for each touched path:

```python
r = os.path.relpath(os.path.realpath(p), cwd_abs)
if r.startswith(".."):
    continue
rel.append(r)
```

The intent (per the docstring) is to drop paths that are outside the repo — normally represented by a relpath starting with `../`. However, the check is a bare string prefix test on `..`, not a path-aware check (e.g. splitting on `os.sep` and checking the first component equals `os.pardir`). Consequently, any file that is genuinely *inside* the repo but whose name happens to start with the two-character sequence `..` — a fully valid POSIX/NTFS filename such as `..config.py`, `..scripts/deploy.sh`, or a directory `..hidden/` — produces a relpath like `..config.py` that also starts with `".."` and gets excluded, even though `os.path.relpath` never actually walked up past `cwd_abs`.

This function is called from `get_git_diff` in [2](#0-1) , which builds the `git diff ... --  <pathspec>` command used by the Stop-hook and commit-review LLM security review pipeline (touched paths are recorded via `record_touched_path` in [3](#0-2)  whenever Claude edits/writes a file, then consumed and passed as `paths` into `get_git_diff`). If a session touches several files and one of them has a leading `..` basename, that one file is quietly stripped from the pathspec while the rest remain — the `git diff` command still runs (it is not empty), but it never includes the odd-named file's diff. The `get_git_diff` short-circuit `if paths and not pathspec: return ""` only fires when *every* touched path is dropped, so a mix of normal + `..`-prefixed files results in a scoped, non-empty diff that omits the crafted file entirely.

Because Claude Code sessions can be driven to create/edit arbitrarily-named files (e.g. via prompt injection from repository content, issue text, or normal user/agent workflow), an unprivileged actor who can influence what Claude writes can name a file to exploit this string-prefix flaw and have its contents pass through the write/edit flow untouched by the security-guidance LLM review, while other touched files in the same turn are reviewed normally (making the omission non-obvious).

### Impact Explanation
This breaks the stated invariant that git path scoping must never let content escape the intended review target: a file that is unambiguously part of the repo tree is functionally excluded from the diff fed to the LLM security reviewer, bypassing the plugin's core enforcement mechanism (the Stop-hook `git diff` scan and commit/push review pipelines that gate on `_diff_pathspec`/`get_git_diff`). This is a logic-level bypass of a required security guard — the plugin's entire purpose is to ensure edited code gets reviewed, and this bug lets specially-named files silently skip that review while the rest of the workflow proceeds normally (no error, no skip signal).

### Likelihood Explanation
No special privileges are required beyond the ordinary ability to get Claude to create/write a file with an attacker-chosen name (a routine capability in agentic coding sessions, and achievable via prompt injection from untrusted repo/issue content). The trigger condition (a basename starting with `..`) is trivial to construct and is a valid filename on all major filesystems Claude Code runs on. The bug is deterministic given such a filename, and the resulting exclusion is silent (no error path, no metric distinguishing "file dropped due to spurious `..` match" from an intentionally-external path), making it easily repeatable and hard to detect from telemetry.

### Recommendation
Replace the string-prefix check with a path-component-aware traversal check, e.g.:
```python
parts = r.split(os.sep)
if parts[0] == os.pardir or r == os.pardir:
    continue
```
or equivalently use `os.path.commonpath([cwd_abs, os.path.realpath(p)]) == cwd_abs` to decide inclusion, so that only genuine `../`-style escapes are dropped, and any legitimately in-repo path (regardless of leading dots in its basename) is preserved in the pathspec.

### Proof of Concept
Unit test to add near existing `_diff_pathspec` tests (or new test file) in the security-guidance test suite:

```python
def test_diff_pathspec_keeps_dotdot_prefixed_basename(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "..payload.py"
    target.write_text("x = 1\n")

    from gitutil import _diff_pathspec
    result = _diff_pathspec(str(repo), [str(target)])

    # EXPECTED (currently fails): the in-repo file must remain scoped.
    assert result == ["--", "..payload.py"]
    # ACTUAL (bug): result == [] because relpath "..payload.py" starts
    # with ".." and is incorrectly treated as outside the repo.
```

Integration-level assertion: combine with a second, normally-named touched file and confirm the resulting pathspec (and therefore the `git diff` output fed to `analyze_code_security`/`agentic_review`) includes both files, not just the normally-named one — demonstrating that a crafted filename can be used to exclude a specific file's changes from the LLM security review while the rest of the turn is reviewed as usual.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L70-88)
```python
def _diff_pathspec(cwd, paths):
    """Convert absolute touched-paths to repo-relative pathspec args for
    git diff. Paths outside cwd (e.g. ~/.claude/…) are dropped. Returns the
    list to splice after `--`, or [] for an unrestricted diff. realpath both
    sides so the macOS /var ↔ /private/var symlink doesn't make in-repo
    paths look external."""
    if not paths:
        return []
    cwd_abs = os.path.realpath(cwd)
    rel = []
    for p in paths:
        try:
            r = os.path.relpath(os.path.realpath(p), cwd_abs)
        except ValueError:
            continue
        if r.startswith(".."):
            continue
        rel.append(r)
    return ["--"] + rel if rel else []
```

**File:** plugins/security-guidance/hooks/gitutil.py (L406-414)
```python
    pathspec = _diff_pathspec(cwd, paths)
    if paths and not pathspec:
        # Caller restricted to specific paths but none are inside this repo
        # (e.g. only ~/.claude/... edits). Returning "" flows to skip(6); an
        # empty pathspec would mean an UNRESTRICTED diff — the bug this whole
        # change exists to fix.
        return ""

    cmd = [*GIT_CMD, "diff", "--no-color", "--no-ext-diff", baseline_sha] + (["--unified=99999"] if full_context else []) + pathspec
```

**File:** plugins/security-guidance/hooks/diffstate.py (L57-71)
```python
def record_touched_path(session_id, file_path):
    """Append a file path to the touched_paths list (deduped, capped at 200).

    Stop is the consumer and clears under the same lock it reads with; UPS
    no longer wipes. The cap is a defensive bound for sessions where Stop
    never fires (disabled mid-session, abort) — git diff naturally filters
    stale paths so over-retention is harmless, just wasteful.
    """
    def _record(state):
        paths = state.setdefault("touched_paths", [])
        if file_path not in paths:
            paths.append(file_path)
            if len(paths) > 200:
                del paths[:len(paths) - 200]
    with_locked_state(session_id, _record)
```
