### Title
Symlink retargeting inside the repo causes `_diff_pathspec` to drop the symlink's own diff entry from security review - ([File: plugins/security-guidance/hooks/gitutil.py])

### Finding Description
`_diff_pathspec` (`plugins/security-guidance/hooks/gitutil.py:70-88`) decides whether a touched path is "in scope" for the Stop-hook / commit-review diff by comparing `os.path.realpath(p)` against `os.path.realpath(cwd)`: [1](#0-0) 

This is correct for the macOS `/var` ↔ `/private/var` case the comment describes, but it is wrong whenever `p` is itself a tracked symlink whose target string points outside the repo. Git tracks the symlink object (mode `120000`, content = target-path string) as a first-class file; when its target is retargeted, `git diff`/`git status` report a change *to the symlink path itself* (e.g. `-old_target +new_target`), and that symlink path is what ends up in `review_paths`/`touched_paths` (built in `diffstate.py:428` as `os.path.join(repo, p)` from `git status`/`diff --name-only`, or from raw `file_path` recorded via `record_touched_path` in `security_reminder_hook.py:2122`).

`os.path.realpath()` follows symlinks, so `os.path.realpath(p)` resolves to the (now out-of-repo) target, not to the symlink's own location inside the repo. `os.path.relpath(realpath(p), cwd_abs)` then starts with `".."`, and the path is silently dropped from the pathspec list at line 85-87. `get_git_diff` (`gitutil.py:406-414`) then runs `git diff ... -- <pathspec-without-symlink>`, so the pathspec-restricted `git diff` never shows the symlink's change, even though it is a real, in-repo, tracked modification.

No other check compensates: `compute_v2_review_set`/`_git_status_porcelain` correctly include the symlink path in the review set, but `_diff_pathspec`'s realpath-based confinement check silently narrows it back out before the diff command runs.

### Impact Explanation
An attacker who can get a tracked symlink retargeted inside the reviewed repo (e.g. via a normal `Edit`/`Write`/`ln -sf` operation performed by the agent, possibly under prompt injection from repository content) can make that specific change invisible to the security-guidance Stop hook and commit-review hook. This is a deny-bypass of the intended "review every file changed this turn" invariant — the exact scoped impact called out in the question: attacker-controlled file edits excluded from git diff review. This is not a full sandbox escape, but a real security-guidance blind spot for symlink-target changes.

### Likelihood Explanation
Preconditions: the attacker (or an agent acting on attacker-supplied instructions) needs to create/retarget a symlink inside the tracked repo tree so it points to a path outside `cwd`/repo root — a routine filesystem operation, not requiring elevated privilege. The bug then triggers deterministically every time `get_git_diff` is called with that path in `paths` (both the v2 Stop-hook flow and any caller passing touched paths), since `_diff_pathspec` unconditionally applies `realpath` to both sides.

### Recommendation
Don't resolve symlinks when computing the in-repo confinement check for a pathspec entry. Use `os.path.abspath` (or `os.path.normpath`) on `p` itself — not `os.path.realpath` — to decide whether the *symlink path* lies under the repo root, reserving `realpath` only for resolving `cwd` itself (to handle the `/var`↔`/private/var` case). E.g.:
```python
cwd_abs = os.path.realpath(cwd)
...
p_abs = os.path.abspath(p)
r = os.path.relpath(p_abs, cwd_abs)
```
so a tracked symlink's own path is judged by its location, not by where it points.

### Proof of Concept
```python
import os, subprocess, tempfile
from gitutil import _diff_pathspec

with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
    subprocess.run(["git", "init"], cwd=repo, check=True)
    target = os.path.join(outside, "secret.txt")
    open(target, "w").close()
    link_path = os.path.join(repo, "link")
    os.symlink(target, link_path)
    subprocess.run(["git", "add", "link"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add symlink"], cwd=repo, check=True)

    # Attacker retargets the symlink to a different out-of-repo location —
    # this is a real, reviewable change to the tracked symlink object.
    os.remove(link_path)
    os.symlink(os.path.join(outside, "other.txt"), link_path)

    pathspec = _diff_pathspec(repo, [link_path])
    # BUG: pathspec is [] / omits "link", so `git diff -- <pathspec>`
    # silently excludes the symlink retarget from review.
    assert "link" in pathspec, "symlink's own in-repo change was dropped from diff scope"
```
Expected (fixed) behavior: `pathspec` includes `"link"` because the symlink path itself resides under `repo`, regardless of where it points.

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
