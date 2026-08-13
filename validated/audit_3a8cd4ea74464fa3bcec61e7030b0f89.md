### Title
Untracked-file review bypass via mtime-only baseline comparison in `_unchanged_since_baseline` - ([File: plugins/security-guidance/hooks/diffstate.py])

### Summary
`compute_v2_review_set` decides whether an untracked file is "pre-existing and unchanged" (and therefore excluded from review) purely by comparing `os.stat(...).st_mtime_ns` against the value captured at UPS time, never by content. Any process that can rewrite a file's bytes while leaving (or restoring) its mtime — e.g. `touch -d`, a crafted script, or a filesystem operation that preserves timestamps — causes a genuinely new/attacker-controlled untracked file to be silently excluded from `new_untracked` and thus from the review set, forever.

### Finding Description
`_list_untracked` snapshots untracked files at UPS as `{path: mtime_ns}` [1](#0-0) . Later, at Stop, `compute_v2_review_set` computes `_unchanged_since_baseline(p)` which returns `True` solely when `os.stat(os.path.join(repo, p)).st_mtime_ns == base_mtime` [2](#0-1) , with no content hash or size comparison at all. Files for which this returns `True` are placed into `preexisting_unchanged` and subtracted out of `untracked`, so they never enter `new_untracked`, `dirty_now`, or ultimately `review_set` [3](#0-2) .

The docstring explicitly states the design intent — "A file is excluded only if it was untracked at baseline AND its mtime is unchanged — an in-place edit during the turn is still reviewed" [4](#0-3)  — but this assumption is false: mtime is attacker-settable independently of content (e.g., `touch -d @<timestamp> file`, or writing via a mechanism that doesn't bump mtime such as certain hardlink/rename tricks). An attacker who can get any command executed in the workspace between the UPS snapshot and the Stop check (a malicious build/test/postinstall script, a crafted Makefile target, or any bash tool invocation the agent runs) can:
1. Create the untracked file before/at the UPS snapshot with innocuous content, letting `_list_untracked` record its mtime.
2. Overwrite the file's content with attacker-controlled payload and restore the original mtime via `touch -d`/`utimensat`.
3. On the next Stop, `_unchanged_since_baseline` sees identical mtime and classifies the file as `preexisting_unchanged`, so it's excluded from `new_untracked`/`review_set` even though its content is entirely different from what was reviewed (or never reviewed).

No other check compensates: `review_set` is derived only from `dirty_now` (tracked-dirty ∪ new_untracked) intersected with `changed_since` (git diff against the stash baseline) [5](#0-4) , and since the file is untracked, git diff/status never surfaces content changes for it — the mtime gate is the only signal.

### Impact Explanation
This is a security-review bypass: attacker-authored or attacker-modified file content in an untracked file is permanently excluded from the `security-guidance` plugin's review pipeline, defeating the plugin's core purpose of flagging Claude-authored or newly introduced code for security review. In a Claude Code session operating on an attacker-supplied or attacker-influenced repository, this allows malicious code to persist undetected across turns.

### Likelihood Explanation
Requires the attacker to get some file-content-mutating action to run in the workspace across two turns/UPS-Stop cycles with a way to control or preserve mtime (readily done via `touch -d`, or any tool that copies attacns like `cp --preserve=timestamps`, or scripted rewrites). This is plausible in agentic workflows where the agent executes build scripts, test suites, or repository automation supplied by the (untrusted) repo content — no elevated privilege is needed beyond normal command execution the agent already performs.

### Recommendation
Replace or supplement the mtime-only comparison in `_unchanged_since_baseline` with a content-based check (e.g., size + content hash, or `git hash-object`) captured at UPS and re-verified at Stop, so a content change is detected even when mtime is forged/preserved.

### Proof of Concept
Unit test: monkeypatch `os.stat` (or use real filesystem with `os.utime`) so that a file untracked at baseline has `mtime_ns == base_mtime` recorded in `untracked_at_baseline`, but replace file content between the UPS snapshot and the `compute_v2_review_set` call (write new bytes, then reset mtime with `os.utime(path, ns=(base_mtime, base_mtime))`). Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, untracked_at_baseline)` and assert the file's absolute path is absent from the returned `review_paths` despite its content differing from what was present at baseline — demonstrating the bypass.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L319-348)
```python
def _list_untracked(cwd):
    """Repo-root-relative untracked (and not-ignored) path → mtime_ns, or {}
    on error. Used at UPS to snapshot the pre-turn untracked set so the Stop
    hook can exclude unchanged pre-existing untracked files from review.
    mtime is captured so an in-place edit during the turn is still reviewed.

    Uses ls-files (not status) for the UPS path: the index diff isn't needed,
    and ls-files --others only walks the worktree against .gitignore."""
    try:
        repo = _git_toplevel(cwd) or cwd
        r = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "ls-files",
             "--others", "--exclude-standard", "-z"],
            cwd=repo, capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            debug_log(f"_list_untracked rc={r.returncode}: {r.stderr[:200]}")
            return {}
        out = {}
        for p in r.stdout.split("\0"):
            if not p:
                continue
            try:
                out[p] = os.stat(os.path.join(repo, p)).st_mtime_ns
            except OSError:
                out[p] = 0
            if len(out) >= UNTRACKED_BASELINE_CAP:
                debug_log(f"_list_untracked: capped at {UNTRACKED_BASELINE_CAP}")
                break
        return out
```

**File:** plugins/security-guidance/hooks/diffstate.py (L365-366)
```python
    A file is excluded only if it was untracked at baseline AND its mtime is
    unchanged — an in-place edit during the turn is still reviewed.
```

**File:** plugins/security-guidance/hooks/diffstate.py (L386-426)
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
```
