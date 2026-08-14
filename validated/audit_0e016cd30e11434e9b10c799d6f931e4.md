### Title
Git history rewrite during a turn (commit --amend / rebase) drops committed dangerous changes from `compute_v2_review_set`, bypassing Stop-hook security review - (File: plugins/security-guidance/hooks/diffstate.py)

### Summary
`compute_v2_review_set` builds the review set as the intersection of `dirty_now` (git status–dirty files vs current HEAD) and `changed_since` (files differing from the turn-start baseline). When a file is committed during the turn, it drops out of `dirty_now` and can only re-enter via the `head_at_capture..HEAD` union, which is gated on `_is_ancestor(head_at_capture, current_head)`. Ordinary git operations (`git commit --amend`, `git rebase`, `git reset` followed by a new commit) make `head_at_capture` a non-ancestor of the new HEAD, so that union never fires, and the dangerous file is excluded from the intersection entirely even though `changed_since` still shows it as content-changed.

### Finding Description
`save_baseline_sha`/`capture_git_baseline` record a `git stash create` SHA and `head_at_capture` at `UserPromptSubmit` [1](#0-0) . At `Stop`, `compute_v2_review_set` computes:

- `dirty_now` = currently-dirty tracked files (`git status --porcelain`) plus new untracked files [2](#0-1) 
- If `head_at_capture` is an ancestor of the current HEAD (i.e., HEAD moved forward linearly via plain commits), `dirty_now` is unioned with `head_at_capture..HEAD` name-only diff, and `diff_base` becomes `head_at_capture` [3](#0-2) 
- `changed_since` = files differing from `baseline_sha` (the turn-start stash) regardless of commit state [4](#0-3) 
- `review_set = dirty_now ∩ changed_since` [5](#0-4) 

If a dangerous edit is committed and then the history is rewritten (`git commit --amend`, `git rebase -i`, `git reset --hard` + new commit) before Stop fires, the file becomes clean in `git status` (removed from `dirty_now`), and `_is_ancestor(head_at_capture, current_head)` returns `False` because `head_at_capture` is no longer reachable from the new HEAD [6](#0-5) . The linear-advance union is skipped, so `dirty_now` never regains the file. Even though `changed_since` (diff against the stash baseline) still lists the file as content-changed, the intersection with an empty-for-that-file `dirty_now` excludes it from `review_paths`, and the Stop hook’s `get_git_diff`/LLM review never sees it [7](#0-6) . This is a normal, unprivileged sequence of git operations reachable during any turn (no credentials, no admin rights) that silently shifts the effective review boundary and causes a genuinely new/dangerous change to be treated as "not dirty" and skipped.

### Impact Explanation
This breaks the stated invariant that the review set must remain bound to the correct baseline/HEAD/touched-paths, causing the mandatory Stop-hook security-diff review (the guard preventing vulnerable code from being merged/left in place) to be silently bypassed for changes committed and then rewound/amended within the same turn. This is a logic-level bypass of a required security guard — matching the "Logic-level service disruption caused by bypassing a required guard or misbinding security state" impact class, since a dangerous diff can be introduced but never flagged by the LLM review that is otherwise the source of truth for `exit code 2`/continuation enforcement.

### Likelihood Explanation
No special privilege is required beyond running ordinary git commands inside the working repo during a turn (something Claude/agents/automation routinely do, e.g. "clean up the commit history" or "amend the last commit message"). The condition only requires: (1) a commit with a dangerous change happens after baseline capture, (2) some non-linear history rewrite (amend/rebase/reset) occurs before Stop fires. This is a common, everyday workflow, not an exotic edge case, making it highly feasible and repeatable.

### Recommendation
Do not gate the `head_at_capture..HEAD` inclusion strictly on `_is_ancestor`. Instead, when HEAD has changed at all since capture (`head_at_capture != current_head`), also union in files that differ between `head_at_capture` and current HEAD content-wise (e.g., via `git diff --name-only head_at_capture HEAD` even for non-ancestor cases, treating it as a three-dot/two-dot diff rather than requiring linear ancestry), or simply drop the `dirty_now` intersection requirement when `changed_since` already proves content differs from the pre-turn stash — i.e., make `review_set` the union of dirty_now and changed_since restricted to files that still exist, rather than the intersection, so a file can't disappear from review purely because it became "clean" via commit/rewrite.

### Proof of Concept
Integration test in the existing test suite for `compute_v2_review_set`:
1. Init a repo, commit an initial file, capture `baseline_sha` via `capture_git_baseline` and `head_at_capture` via `_git_rev_parse_head` (mirrors UPS).
2. Write a dangerous change to a tracked file, `git add` + `git commit` it (now `dirty_now` would include it via the ancestor union — sanity check it's reviewed).
3. Run `git commit --amend --no-edit` (or `git reset --hard HEAD~1 && git commit -am "innocuous"`) to rewrite history so the new HEAD is not a descendant reachable via the old `head_at_capture` in a simple ancestor sense — assert `_is_ancestor(head_at_capture, new_head)` is `False`.
4. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, {})` and assert the dangerous file path IS still present in `review_paths` (expected assertion that currently fails, proving the bypass).
5. Additionally assert `get_git_diff` output/Stop-hook flow would have surfaced the finding, by wiring through `handle_stop_hook` with a mocked LLM call and confirming `sys.exit(2)` with guidance mentioning the dangerous file is emitted — before the fix, no vulnerability is reported (`_skip(9, ...)`/`_skip(6)` empty-review-set/no-diff path fires instead).

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L463-503)
```python
    session_id = input_data.get("session_id", "default")
    # stash-create and ls-files both walk the worktree (~2-5s each in a very
    # large repo). Run them concurrently so UPS latency stays ≈ max(both).
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=2) as _ex:
        _f_sha = _ex.submit(capture_git_baseline, cwd)
        _f_ut = _ex.submit(_list_untracked, cwd)
        sha = _f_sha.result()
        # Always capture the untracked snapshot. `git stash create` returns
        # empty when there are no TRACKED changes, but pre-existing untracked
        # files still need to be excluded from the next Stop's review_set —
        # otherwise an untracked-only working tree gets every untracked file
        # reviewed on every turn until something tracked is dirtied.
        untracked_now = _f_ut.result() or {}
    head = _git_rev_parse_head(cwd)

    # If the previous turn's Stop hook never ran (user interrupt, follow-up
    # during work, tool-reject, model crash, maxTurns, PostToolUse block…),
    # touched_paths is still populated because consume_stop_state is the only
    # consumer and it runs under the state lock. Overwriting baseline_sha now
    # would re-baseline *past* those unreviewed edits, making them permanently
    # invisible to the next Stop. Preserve the old baseline so the next Stop
    # diffs the aborted turn's edits plus the new turn's edits together.
    preserved = {"value": False}

    def _save(state):
        # Only preserve if there's actually an old baseline to preserve.
        # First UPS of a session can have touched_paths if PostToolUse
        # somehow ran first (print mode, odd harnesses) — in that case
        # we still need to capture a baseline.
        if state.get("touched_paths") and state.get("baseline_sha"):
            preserved["value"] = True
            return
        if sha:
            state["baseline_sha"] = sha
            state["head_at_capture"] = head
        # untracked_at_baseline is independent of whether the stash produced
        # a SHA — write it unconditionally so compute_v2_review_set's
        # preexisting-untracked exclusion works in untracked-only trees.
        state["untracked_at_baseline"] = untracked_now
    with_locked_state(session_id, _save)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1792-1798)
```python
    review_paths, diff_base, repo_root, untracked, v2_metrics = compute_v2_review_set(
        cwd, baseline_sha, head_at_capture, untracked_at_baseline
    )
    if not review_paths:
        debug_log("Stop hook: empty review set")
        _skip(9, touched_paths_count=len(touched_paths))
    debug_log(f"Stop hook: review_set={len(review_paths)} base={diff_base[:12]} dirty_now={v2_metrics['dirty_now_count']} changed_since={v2_metrics['changed_since_count']}")
```

**File:** plugins/security-guidance/hooks/diffstate.py (L386-401)
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
```

**File:** plugins/security-guidance/hooks/diffstate.py (L403-408)
```python
    diff_base = "HEAD"
    current_head = _git_rev_parse_head(repo)
    if (head_at_capture and current_head and head_at_capture != current_head
            and _is_ancestor(repo, head_at_capture, current_head)):
        dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
        diff_base = head_at_capture
```

**File:** plugins/security-guidance/hooks/diffstate.py (L417-422)
```python
    if baseline_sha:
        changed_since = _git_name_only(repo, baseline_sha)
        if changed_since is not None:
            changed_since |= new_untracked
    else:
        changed_since = None
```

**File:** plugins/security-guidance/hooks/diffstate.py (L426-426)
```python
    review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
```

**File:** plugins/security-guidance/hooks/gitutil.py (L377-387)
```python
def _is_ancestor(cwd, maybe_ancestor, descendant):
    """True if `maybe_ancestor` is reachable from `descendant` (i.e. HEAD
    moved forward via commit/merge, not sideways via checkout)."""
    try:
        result = subprocess.run(
            [*GIT_CMD, "merge-base", "--is-ancestor", maybe_ancestor, descendant],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
```
