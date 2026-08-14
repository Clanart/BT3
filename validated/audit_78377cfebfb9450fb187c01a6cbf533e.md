### Title
Non-linear HEAD movement (checkout/reset/rebase) makes `compute_v2_review_set` intersect an empty `dirty_now` and silently drops committed dangerous changes - (File: plugins/security-guidance/hooks/diffstate.py)

### Summary
`compute_v2_review_set` builds the reviewed file set as `dirty_now ∩ changed_since`, where `dirty_now` is derived only from `git status` plus commits detected by a linear-ancestry check (`_is_ancestor(head_at_capture, current_head)`). When a session's git history advances non-linearly relative to `head_at_capture` (e.g. `git checkout <other-ref>`, `git reset --hard <sha>`, or a rebase that leaves the working tree clean against the new HEAD), `dirty_now` collapses to empty while `changed_since` (`git diff --name-only baseline_sha`) still contains the dangerous file — but the empty `dirty_now` intersection zeroes the review set, so the file is never sent to the LLM reviewer or reported to the user.

### Finding Description
`capture_git_baseline` (`plugins/security-guidance/hooks/diffstate.py:163`) and `handle_user_prompt_submit` (`plugins/security-guidance/hooks/security_reminder_hook.py:446`) snapshot `baseline_sha` (via `git stash create`) and `head_at_capture` (`git rev-parse HEAD`) at the start of each turn.

At Stop, `compute_v2_review_set` (`plugins/security-guidance/hooks/diffstate.py:353`) computes:
```python
tracked_dirty, untracked = _git_status_porcelain(repo)
...
dirty_now = tracked_dirty | new_untracked
diff_base = "HEAD"
current_head = _git_rev_parse_head(repo)
if (head_at_capture and current_head and head_at_capture != current_head
        and _is_ancestor(repo, head_at_capture, current_head)):
    dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
    diff_base = head_at_capture
...
changed_since = _git_name_only(repo, baseline_sha)  # git diff --name-only baseline_sha (vs worktree)
...
review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
``` [1](#0-0) 

`dirty_now`'s "committed changes" term is gated strictly on `_is_ancestor` — i.e. HEAD must have moved forward linearly from `head_at_capture`. [2](#0-1) 

If instead HEAD moves via `git checkout <other-branch-or-commit>`, `git reset --hard <sha>`, or an interactive rebase, and the working tree becomes clean relative to that new HEAD, `git status --porcelain` reports no dirty tracked files (`_git_status_porcelain`), so `tracked_dirty` is empty. Since `is_ancestor` fails for a non-descendant HEAD, the commit-pickup branch never executes, so `dirty_now` stays empty (or contains only unrelated untracked noise).

Meanwhile `changed_since = _git_name_only(repo, baseline_sha)` runs `git diff --name-only baseline_sha`, which diffs the baseline tree against the actual current working tree/HEAD content — this still contains the dangerous file, because its content genuinely differs from the pre-turn baseline.

The intersection `dirty_now & changed_since` is then empty, so `review_paths` returned to the Stop-hook caller is empty, and no diff is ever generated or sent to the LLM reviewer — the dangerous change is never flagged, and `get_git_diff`'s pathspec-based diff also depends on this same review set for the file list that gets shown/reviewed (`plugins/security-guidance/hooks/gitutil.py:391`). No allowlist, workspace guard, or session-binding check catches this because the entire mechanism assumes HEAD monotonically advances between baseline capture and Stop; there is no fallback path in `compute_v2_review_set` for a non-ancestor HEAD move that still leaves a clean-but-different working tree.

This is reachable from a normal, unprivileged cloned-repo workflow: a repository under attacker influence (e.g. via prompt-injected instructions in README/CI files, or a branch/tag containing pre-built malicious code) can lead Claude to run ordinary git commands — `git checkout <branch>`, `git reset --hard <sha>`, `git switch`, or a rebase — that land dangerous, already-committed content into the working tree while leaving it clean relative to the new HEAD. The Stop-hook security review then computes an empty review set for that file and skips it entirely.

### Impact Explanation
The security-guidance Stop hook is the last line of defense that inspects code Claude introduced/executed during a turn and exits with code 2 to force remediation before the agent continues. Silently emptying the review set for a dangerous, already-applied change means Claude Code proceeds without ever surfacing (and thus without blocking/warning about) attacker-planted or dangerous code that was merged into the working tree via ordinary git operations. This effectively bypasses the plugin's approval/warning gate for a class of git-driven content changes, matching "Unauthorized local command execution / dangerous code execution that bypasses Claude Code approval or deny controls" since the subsequently-untouched, unreviewed dangerous file can then be executed or further acted upon by Claude without a security-guidance intervention.

### Likelihood Explanation
This requires no elevated privilege — only the ability to influence the git operations Claude performs on a cloned repo (a normal capability for anyone able to place instructions/content in a repo that Claude is asked to work with, or a user directing Claude through ordinary branch/rebase/reset workflows). Non-linear HEAD movement via checkout/reset/rebase is an everyday git operation, not an edge case, making this readily reproducible. The bug is deterministic given the described git sequence and doesn't depend on race conditions or timing.

### Recommendation
`compute_v2_review_set` should not rely solely on linear-ancestry `is_ancestor` checks to detect "content that changed since baseline." Instead, when `head_at_capture != current_head` (regardless of ancestry direction), include `_git_name_only(repo, head_at_capture)` (diff of current worktree against `head_at_capture`, not just the ancestor range) in `dirty_now`, or drop the `dirty_now` gate entirely for the "committed since baseline" term and rely on `changed_since` (diff vs `baseline_sha`) as the primary review-set signal, falling back to `dirty_now` only to prioritize/filter noise rather than to exclude files outright. At minimum, add a fallback branch for the non-ancestor case that computes the file-level diff between `head_at_capture` and `current_head` unconditionally so non-linear history changes are still captured in `dirty_now`.

### Proof of Concept
Integration test plan (pytest style, mirroring existing hook test patterns):
1. Initialize a temp git repo, commit an initial safe file, capture `baseline_sha`/`head_at_capture` via `capture_git_baseline`/`_git_rev_parse_head` (simulating UPS).
2. Create a second branch `evil` from the initial commit, add a dangerous file (e.g. `os.system(user_input)`), commit it there.
3. From the main working directory (still on original branch, matching `head_at_capture`), run `git checkout evil` (or `git reset --hard <evil-sha>`), leaving the working tree clean relative to the new HEAD.
4. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, untracked_at_baseline={})`.
5. Assert: the dangerous file IS present in the returned `review_paths` (expected/desired behavior) — current implementation will show it is **absent**, i.e. `review_set == []`/`review_set_count == 0`, despite `changed_since` (verifiable via direct `git diff --name-only baseline_sha`) containing the file. This assertion failure demonstrates the vulnerability: the invariant "the review set must stay bound to the right repo, baseline, and touched paths" is broken because a real content change since baseline is dropped due to `dirty_now`'s empty-status/non-ancestor gating.

### Citations

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
