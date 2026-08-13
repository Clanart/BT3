### Title
Uncommitted dangerous edits fold into `capture_git_baseline` snapshot before review, causing `compute_v2_review_set` to silently drop them - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`capture_git_baseline` uses `git stash create`, which snapshots the *entire* current working tree (HEAD + all uncommitted changes) into a new baseline SHA on every `UserPromptSubmit` unless an explicit preservation guard fires. Because `record_touched_path` is only invoked for `Edit`/`Write` tool calls (not for file edits made via `Bash`, e.g. `sed`/`cat`/patch commands), a dangerous change written to a tracked file through Bash never populates `touched_paths`, so the preservation guard in `handle_user_prompt_submit` does not hold the old baseline back. On the next `UserPromptSubmit` the new baseline snapshot already contains the unreviewed dangerous edit, and `compute_v2_review_set`'s `changed_since` set (diff against the new baseline) no longer contains that file, dropping it out of `review_set = dirty_now & changed_since` even though it remains uncommitted and was never reviewed.

### Finding Description
`capture_git_baseline` (`plugins/security-guidance/hooks/diffstate.py:163-204`) captures the full working-tree state via `git stash create`, explicitly including uncommitted edits: [1](#0-0) 

`handle_user_prompt_submit` (`security_reminder_hook.py:446-515`) is supposed to prevent re-baselining past unreviewed work by checking `touched_paths`: [2](#0-1) 

But `touched_paths` is populated by `record_touched_path`, which is only reachable from the `Edit`/`Write` `PostToolUse` path — Bash-authored file mutations never call it. This is explicitly acknowledged in the `compute_v2_review_set` docstring itself: [3](#0-2) 

Once the guard fails to fire (because `touched_paths` is empty despite a real unreviewed edit sitting in the working tree), the next `capture_git_baseline` call folds the dangerous, still-uncommitted edit into the new baseline SHA. `compute_v2_review_set` then computes: [4](#0-3) 
`changed_since` is `git diff <new baseline_sha>`, and since the new baseline snapshot already contains the dangerous file's current content, that file no longer appears as changed relative to baseline — even though `dirty_now` (from `git status` vs `HEAD`) still flags it as uncommitted. The intersection `dirty_now & changed_since` silently excludes the file from `review_set`, so the Stop-hook LLM review never sees it in a subsequent turn.

### Impact Explanation
The Stop-hook LLM security review is the plugin's core enforcement surface (`analyze_code_security`/`agentic_review`, gated by `ENABLE_STOP_REVIEW`). If a dangerous, still-uncommitted change can be permanently excluded from `review_set` by an ordinary sequence of git/Bash operations across two turns, the "review must include everything Claude touched" invariant is broken and the review is silently routed around — matching the "security-control bypass that silently disables or routes around review boundaries" impact category. Because the plugin is advisory (exit(2) forces a continuation prompt rather than blocking a tool call), the practical severity is limited to loss of detection coverage rather than direct privilege escalation.

### Likelihood Explanation
Requires: (1) a dangerous edit made via `Bash` to a tracked file (not `Edit`/`Write`) so `record_touched_path` never fires, and (2) a subsequent `UserPromptSubmit` occurring before that edit is committed or reviewed (e.g., the turn ends/aborts before `Stop` runs, or the user sends a follow-up prompt). Both preconditions are ordinary, attacker-influenceable flows (e.g., repository content instructing Claude to modify a file via a shell command) and require no special privilege — consistent with "normal git operations in a cloned repo." The maintainers' own docstring acknowledges the Bash-only-touched-paths gap, indicating the scenario is realistic and already partially understood, though not fully closed for the multi-turn baseline-advance case.

### Recommendation
Do not rely solely on `touched_paths` to gate baseline preservation. Before advancing `baseline_sha` in `handle_user_prompt_submit`, compare the new `git stash create` state against the *previous* baseline (or previous HEAD) independent of `touched_paths`, and preserve the old baseline whenever there is any uncommitted diff at all relative to the last reviewed baseline (i.e., treat "no touched_paths but dirty worktree changed since baseline" the same as "touched_paths non-empty"). Alternatively, make `compute_v2_review_set` retain a rolling union of previously-computed `review_set` file paths across baseline advances until they are explicitly marked reviewed, rather than deriving purely from a single-step `changed_since` diff against the latest baseline.

### Proof of Concept
Integration test outline (pytest, using a temp git repo):
1. Init repo, commit an initial safe file, capture `baseline_sha1 = capture_git_baseline(cwd)`.
2. Simulate a Bash-authored dangerous edit to a tracked file (`os.system`/`subprocess` writing the file directly — not via the `Edit`/`Write` tool path), without calling `record_touched_path`.
3. Call `compute_v2_review_set(cwd, baseline_sha1, head_at_capture)` — assert the dangerous file IS in `review_set` (sanity check, same-turn detection works).
4. Simulate the next `UserPromptSubmit` without an intervening `Stop` consuming state: call `capture_git_baseline(cwd)` again to get `baseline_sha2`, and directly call `compute_v2_review_set(cwd, baseline_sha2, head_at_capture2)`.
5. Assert: the dangerous file is now ABSENT from `review_set`, even though it remains uncommitted/unreviewed in the working tree — demonstrating the invariant break.

Expected result: step 5 fails today (file dropped), confirming the bypass; after the recommended fix, the file should remain present in `review_set` until actually committed/reviewed.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L163-176)
```python
def capture_git_baseline(cwd):
    """
    Capture a git ref representing the current working tree state.
    Uses `git stash create` which creates a commit object for the current state
    (HEAD + uncommitted changes) without modifying the stash list or working tree.
    Falls back to HEAD if the working tree is clean.
    Returns the SHA string, or None if not in a git repo or if the repo has no commits.

    NOTE: `git stash create` does NOT capture untracked files. UPS pairs this
    SHA with a `_list_untracked()` snapshot stored as `untracked_at_baseline`,
    and `compute_v2_review_set` subtracts that set so pre-existing untracked
    files are not reviewed as Claude-authored.
    """
    try:
```

**File:** plugins/security-guidance/hooks/diffstate.py (L368-370)
```python
    Known limitation: a Bash-only turn that's interrupted before Stop fires
    leaves touched_paths empty, so the next UPS re-baselines past those edits.
    v1 never reviews Bash-only turns at all, so v2 is no worse there.
```

**File:** plugins/security-guidance/hooks/diffstate.py (L417-426)
```python
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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L486-503)
```python
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
