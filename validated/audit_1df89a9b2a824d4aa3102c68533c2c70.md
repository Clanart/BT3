### Title
`_diff_pathspec` mis-scopes legitimately in-repo paths whose relative path begins with a literal `..` prefix, silently disabling the security-diff review for those files - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`_diff_pathspec` in `plugins/security-guidance/hooks/gitutil.py` decides whether a touched file is "inside" or "outside" the repo by testing `r.startswith("..")` on the `os.path.relpath` result, rather than checking for an exact `".."` path component (e.g. `r == ".." or r.startswith(".." + os.sep)`). A file or directory whose name legitimately begins with two dots (e.g. `..config/secret.py`) produces a `relpath` string such as `..config/secret.py`, which also satisfies `str.startswith("..")` even though the path is genuinely inside the repo. This causes such touched files to be dropped from the diff pathspec and, if they are the only touched path, causes `get_git_diff` to return `""` and skip the review entirely.

### Finding Description
`_diff_pathspec` is the sole guard that decides which touched paths are passed through to `git diff -- <pathspec>` for the Stop-hook / commit-review LLM security scan: [1](#0-0) 

For each candidate path it does:
```python
r = os.path.relpath(os.path.realpath(p), cwd_abs)
...
if r.startswith(".."):
    continue
```
`str.startswith("..")` is a naive prefix test. It is meant to detect the classic "parent directory traversal" pattern where `relpath` returns something like `../../etc/passwd`. However, POSIX/git allow ordinary directory or file names that begin with two literal dots followed by other characters (e.g. `..config`, `..hidden_dir`, `..bak`), which are not `..` traversal components at all. For such a name, `os.path.relpath` legitimately returns a string like `..config/secret.py` for a file that is fully inside `cwd_abs`. That string also matches `startswith("..")`, so the loop `continue`s and silently drops the path from `rel`.

Consequence in the caller, `get_git_diff`:
```python
pathspec = _diff_pathspec(cwd, paths)
if paths and not pathspec:
    return ""
``` [2](#0-1) 

If the mis-classified path is the only (or the last remaining) touched path for the turn, `pathspec` ends up empty and `get_git_diff` returns `""` — the code that consumes this treats an empty diff as "nothing to review," so the Stop-hook LLM security review, the commit-review, and push-sweep review are all silently skipped for edits made under such a directory/filename, even though the file is a completely ordinary tracked/untracked source file inside the target repository.

Attacker reachability: `paths` originates from touched-file paths recorded during the session (Edit/Write/MultiEdit tool calls), which are themselves derived from file paths that Claude is instructed to write to. An attacker who controls repository content (e.g. issue text, a README, or any prompt-injectable artifact) can steer Claude into creating or editing files under a directory literally named starting with `..` (a valid, if unusual, git-trackable path). No symlink or elevated privilege is required — plain repo content controls the directory name that ends up as the touched path.

Existing validation does not catch this: there is no test coverage for `_diff_pathspec` in the repository, and the realpath-based symlink handling documented in the docstring is orthogonal to this bug — the flaw is purely in the string-prefix check applied to an in-repo relative path.

### Impact Explanation
This is a genuine security-control bypass: it silently disables the LLM security-diff review (Stop hook), the commit-review, and the push-sweep review for a subset of legitimately in-repo file paths, without any error, warning, or telemetry indicating the change was skipped for a "path escape" reason that isn't actually true. An attacker who can influence which paths Claude writes to (via prompt injection embedded in repository content) can cause security-sensitive edits to be made under a `..`-prefixed directory name and have those edits pass through completely unreviewed, defeating the plugin's core "review every diff" guarantee. This matches the "Security-control bypass that silently disables or routes around blocking, review, or permission boundaries" impact category.

### Likelihood Explanation
Preconditions are low: only that (a) the repository has (or the attacker can get Claude to create) a directory/file component beginning with `..` followed by other characters, and (b) Claude's touched-path set for the turn is restricted to path(s) under that name (either exclusively, or such that `_diff_pathspec` returns an empty pathspec, causing `get_git_diff` to bail with `""`). No admin privilege, symlink, or unicode trickery is even necessary — a plain ASCII directory name such as `..config/` suffices. It is fully repeatable/deterministic, since `os.path.relpath`'s output format is stable, and the flawed `startswith("..")` check will always match. The one caveat is exploitability depends on whether the plugin computes per-file pathspecs (dropping just the affected file) versus an all-or-nothing empty diff for the whole batch; both outcomes still constitute a review bypass (either that one file is silently excluded from review, or the whole diff review is skipped if that's the only touched file).

### Recommendation
Replace the substring check with an exact-component test, e.g.:
```python
if r == os.pardir or r.startswith(os.pardir + os.sep):
    continue
```
or equivalently check `r.split(os.sep, 1)[0] == ".."`. This correctly identifies true parent-directory escapes (`..`, `../foo`) while no longer misclassifying legitimate in-repo names like `..config/secret.py`.

### Proof of Concept
Unit test to add to a new/existing test module for `gitutil.py`:
```python
import os
from gitutil import _diff_pathspec

def test_diff_pathspec_does_not_drop_dotdot_prefixed_names(tmp_path):
    repo = tmp_path / "repo"
    (repo / "..config").mkdir(parents=True)
    target = repo / "..config" / "secret.py"
    target.write_text("x = 1\n")

    result = _diff_pathspec(str(repo), [str(target)])

    # Expected: the file is inside the repo and must remain in scope.
    assert result == ["--", os.path.join("..config", "secret.py")]
    # Bug: current implementation returns [] because relpath()
    # returns "..config/secret.py", which starts with "..".
```

Integration-level assertion: call `get_git_diff(str(repo), baseline_sha, paths=[str(target)])` after modifying `target` and assert the returned diff text is non-empty and contains `secret.py`, rather than the empty string that the flawed pathspec currently produces (which the Stop-hook/commit-review treats as "nothing to review").

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

**File:** plugins/security-guidance/hooks/gitutil.py (L406-412)
```python
    pathspec = _diff_pathspec(cwd, paths)
    if paths and not pathspec:
        # Caller restricted to specific paths but none are inside this repo
        # (e.g. only ~/.claude/... edits). Returning "" flows to skip(6); an
        # empty pathspec would mean an UNRESTRICTED diff — the bug this whole
        # change exists to fix.
        return ""
```
