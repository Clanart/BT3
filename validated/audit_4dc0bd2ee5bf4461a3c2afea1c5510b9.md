### Title
`_diff_pathspec` mis-detects out-of-repo escapes via prefix check, letting in-repo files whose names begin with ".." silently drop out of the diff pathspec and skip the security review - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`_diff_pathspec` decides whether a touched path is inside the repo by testing `r.startswith("..")` on the `os.path.relpath` result, instead of checking that `r` equals `os.pardir` or starts with `os.pardir + os.sep`. This conflates "path traverses out of `cwd`" with "path's leftmost component's string happens to start with two dots," so a legitimately in-repo file or directory literally named e.g. `..sneaky.py` or `..build/output.py` (both valid POSIX filenames) is wrongly treated as external and dropped from the pathspec.

### Finding Description
```python
def _diff_pathspec(cwd, paths):
    ...
    for p in paths:
        try:
            r = os.path.relpath(os.path.realpath(p), cwd_abs)
        except ValueError:
            continue
        if r.startswith(".."):
            continue
        rel.append(r)
    return ["--"] + rel if rel else []
``` [1](#0-0) 

`os.path.relpath` correctly returns `".."`-prefixed output when the target is outside `cwd_abs` (e.g. `"../secret.txt"`), but it returns the *same string shape* for an in-repo file whose own name begins with two dots and no other separators, e.g. a file `..sneaky.py` sitting directly in the repo root produces `r == "..sneaky.py"`. `r.startswith("..")` is `True` in both cases, so the correct-but-poorly-named in-repo path is incorrectly filtered out as if it escaped the repo.

The touched paths reaching this function are attacker-influenceable: `compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py` builds `review_paths` directly from `git status --porcelain` / `git diff --name-only` output joined onto the repo root [2](#0-1) , i.e. whatever filenames actually exist in the working tree — including any adversarial repo content that creates or renames a file to start with `..`. Nested/worktree directory names beginning with `..` trigger the same false match on the first path component.

Downstream, `get_git_diff` treats an empty pathspec as "nothing in scope" and short-circuits the diff entirely:
```python
pathspec = _diff_pathspec(cwd, paths)
if paths and not pathspec:
    return ""
``` [3](#0-2) 
If the only (or all) touched paths in a turn have this `..`-prefixed shape, `pathspec` ends up `[]`, and `get_git_diff` returns `""` instead of performing a restricted diff — an empty diff is treated by the Stop-hook review pipeline as "nothing changed, nothing to review," silently skipping the LLM security review for that edit.

### Impact Explanation
This is a logic bug that misclassifies legitimate in-scope files as out-of-scope, defeating the Stop-hook's security review guard for edits to any file/directory whose name begins with `..` (a valid, attacker-choosable filename on POSIX systems). That matches the "Logic-level service disruption caused by bypassing a required guard" category: the required LLM vulnerability-scan guard over Claude's diffs can be silently skipped by naming/creating files this way, without any privilege escalation — an unprivileged actor only needs to get Claude to touch a file with such a name (e.g. via repository content or instructions that lead Claude to create/edit `..config.py`).

### Likelihood Explanation
Low complexity, fully reproducible: any working tree containing a tracked or newly-created file/dir whose basename starts with `..` and is touched during a Claude turn will hit this path. No special git configuration, symlinks, or unicode tricks are required — plain ASCII filenames like `..env` or `..build/x.py` suffice. The bug is deterministic (pure string-prefix logic), not timing- or race-dependent.

### Recommendation
Replace the prefix check with a proper path-component comparison:
```python
if r == os.pardir or r.startswith(os.pardir + os.sep):
    continue
```
This distinguishes the actual `..` traversal component from filenames that merely start with the two-character string `".."`. Add a regression test covering a same-directory file named `..sneaky.py` and a nested dir `..build/output.py` to ensure they remain in the returned pathspec.

### Proof of Concept
Unit test (pytest) to add near existing `_diff_pathspec` tests:
```python
def test_diff_pathspec_does_not_drop_dotdot_prefixed_filenames(tmp_path):
    repo = tmp_path
    victim = repo / "..sneaky.py"
    victim.write_text("x = 1\n")
    nested_dir = repo / "..build"
    nested_dir.mkdir()
    nested_file = nested_dir / "output.py"
    nested_file.write_text("y = 2\n")

    result = _diff_pathspec(str(repo), [str(victim), str(nested_file)])

    # Expected: both in-repo paths retained in the pathspec.
    assert result == ["--", "..sneaky.py", "..build/output.py"]
    # Current buggy behavior: result == [] because both `r` values
    # start with "..", causing get_git_diff() to return "" and the
    # Stop-hook review to be skipped for these files.
```
Expected assertion failure against current code confirms the bug: `_diff_pathspec` returns `[]` instead of including the two legitimate in-repo paths, and a follow-up integration test on `get_git_diff` with `paths=[victim]` should assert it does **not** return `""` when `victim` is genuinely inside `cwd`.

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

**File:** plugins/security-guidance/hooks/diffstate.py (L386-428)
```python
    tracked_dirty, untracked = _git_status_porcelain(repo)
    if tracked_dirty is None:
        return [], "HEAD", repo, [], {"dirty_now_count": -1, "changed_since_count": -1, "review_set_count": 0}

    def _unchanged_since_baseline(p):
        base_mtime = untracked_at_baseline.get(p)
        if base_mtime is None:
            return False
        try:
            return os.stat(os.path.join(repo, p)).st_mtime_ns == base_mtime
        except OSError:
            return False

    preexisting_unchanged = {p for p in untracked if _unchanged_since_baseline(p)}
    new_untracked = untracked - preexisting_unchanged
    dirty_now = tracked_dirty | new_untracked

    diff_base = "HEAD"
    current_head = _git_rev_parse_head(repo)
    if (head_at_capture and current_head and head_at_capture != current_head
            and _is_ancestor(repo, head_at_capture, current_head)):
        dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
        diff_base = head_at_capture

    # changed_since: tracked files vs the stash baseline (no temp index — the
    # stash never contained untracked files anyway), then union with
    # currently-untracked. The previous `include_untracked=True` arm cost a
    # full `git add -N .` (slow in large repos) per call to surface
    # untracked files in the diff output — but `git diff <stash>` already
    # lists them as "only in worktree" without that, and we have the explicit
    # set from status regardless.
    if baseline_sha:
        changed_since = _git_name_only(repo, baseline_sha)
        if changed_since is not None:
            changed_since |= new_untracked
    else:
        changed_since = None
    # changed_since is None on missing baseline OR on git error (e.g. the
    # dangling stash SHA was pruned). Either way, don't intersect with ∅ —
    # that would silently zero the review set. Fall back to dirty_now.
    review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now

    review_paths = [os.path.join(repo, p) for p in sorted(review_set)]
```
