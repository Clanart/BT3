### Title
`compute_v2_review_set`'s ancestor-gated diff-base advancement lets ordinary non-linear git history operations (reset/rebase/amend-onto-different-parent) drop a committed dangerous change from the Stop-hook review set - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`capture_git_baseline` records a pre-turn snapshot (`baseline_sha` via `git stash create`, plus `head_at_capture`) at `UserPromptSubmit`, and the Stop hook's `compute_v2_review_set` decides what to review by intersecting `dirty_now` (files dirty vs current HEAD, plus untracked additions) with `changed_since` (diff vs `baseline_sha`). `dirty_now` is only widened to include committed-this-turn files when `head_at_capture` is a **linear ancestor** of the current HEAD. Ordinary git operations that rewrite history non-linearly during the same turn (e.g. `git reset --hard` to a divergent point, an interactive rebase, or an amend/cherry-pick that reparents the commit) break that ancestor relationship, silently dropping the review scope back to "only what's currently dirty" - which is empty once the dangerous change has been committed and the tree is clean.

### Finding Description
`capture_git_baseline` (`plugins/security-guidance/hooks/diffstate.py:163-204`) captures `baseline_sha` via `git stash create` at `UserPromptSubmit`, and `handle_user_prompt_submit` (`security_reminder_hook.py:446-515`) separately records `head_at_capture = _git_rev_parse_head(cwd)` into session state.

At Stop time, `compute_v2_review_set` (`diffstate.py:353-438`) builds the review set as:

```
dirty_now = tracked_dirty | new_untracked
if head_at_capture and current_head and head_at_capture != current_head
        and _is_ancestor(repo, head_at_capture, current_head):
    dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD")
    diff_base = head_at_capture
...
review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
``` [1](#0-0) 

This is the only mechanism that folds committed-during-the-turn files into `dirty_now`; it is strictly gated on `_is_ancestor(head_at_capture, current_head)`. If a Claude turn (driven by ordinary, unprivileged git commands — no special access needed, just commands executed inside the agent's own working clone) commits the dangerous change and then performs a normal history-rewriting operation that leaves `current_head` NOT a descendant of `head_at_capture` (e.g., `git reset --hard <sibling-commit>` and re-commit, an interactive rebase that drops/reorders commits, or `git commit --amend` onto a different parent after a reset), the ancestor check fails. `diff_base` stays `"HEAD"`, and the committed-range term is never unioned in. Because the dangerous file is now committed and the working tree is clean, `tracked_dirty` no longer contains it, so it is absent from `dirty_now` entirely — and since `review_set = dirty_now ∩ changed_since`, the file is dropped from the review set even though `changed_since` (diff vs the pre-turn `baseline_sha`) would still show it as modified. The intersection design means a false negative in `dirty_now` alone is enough to suppress review; there is no fallback path that unions `changed_since` in when `dirty_now`'s commit-range term is unavailable.

This breaks exactly the invariant under test: "the review set must stay bound to the right repo, baseline, and touched paths" — the baseline/HEAD-tracking assumption (linear advancement) does not hold for legitimate git operations, so the code silently narrows scope rather than failing safe to a broader review.

### Impact Explanation
A dangerous change (e.g. a hardcoded secret, backdoor, or injected command) that is committed and then has its ancestry rewritten via a normal git operation within the same turn is skipped by the Stop-hook's LLM security review entirely. This defeats the plugin's core safety mechanism (git-diff-based Stop review is the stated last line of defense per `security_reminder_hook.py`'s module docstring), allowing the dangerous code to be pushed/left in the repo without the intended LLM flagging, i.e. sensitive/dangerous code reaching the codebase (or being pushed) without the disclosure/blocking that the plugin exists to provide.

### Likelihood Explanation
No elevated privilege is required — only the ability to run ordinary git commands in the working clone during an agent turn (something a prompt-injected instruction, a scripted workflow, or the agent itself following seemingly benign instructions like "clean up the commit history" or "squash these commits" could trigger). The `_is_ancestor` gate is a narrow linear-history assumption that real repositories violate routinely (rebase, amend-after-reset, cherry-pick). The scenario is deterministic and reproducible with a small git script, not a race condition.

### Recommendation
Do not gate the committed-range term solely on strict ancestor-linearity. When `head_at_capture != current_head` and it is not a strict ancestor, still compute `changed_since` (already does, independent of `dirty_now`) but also union in the files touched by the non-linear history change — e.g. use `git diff --name-only head_at_capture...HEAD` (triple-dot / merge-base diff) or fall back to reviewing the full `changed_since` set (not intersected with `dirty_now`) whenever HEAD has moved at all since capture, rather than defaulting to "HEAD-only" dirty state. Alternatively, treat any HEAD movement (linear or not) as sufficient to widen `dirty_now`, and only skip the intersection narrowing when it's provably safe (i.e., when HEAD is unchanged and no history rewrite occurred).

### Proof of Concept
Add an integration test under the diffstate/hook test suite:
1. Init a repo with one commit `A`. Set `head_at_capture = A`, capture `baseline_sha` via `capture_git_baseline` (clean tree ⇒ `baseline_sha == A`).
2. Simulate the turn: write `dangerous.py` with a flagged pattern, `git add && git commit -m "danger"` → HEAD is `B` (child of `A`).
3. Simulate a history rewrite within the same turn: `git reset --hard A` then re-commit an innocuous unrelated file to create `C` (also child of `A`, sibling of `B`, so `B`'s content is no longer reachable from `C` and `A` is still technically an ancestor of `C` — to force the failure, instead do: `git commit --amend` is insufficient; use `git reset --hard <some earlier ancestor of A>` if available, or more directly: perform an interactive rebase that drops the "danger" commit, or `git filter-branch`/`git commit --amend` after `git reset --soft` losing `dangerous.py`'s content) such that `_is_ancestor(A, current_head)` is False or the resulting commit no longer contains `dangerous.py` in the ancestor-diff term while `dangerous.py` still exists changed in the tree relative to `A`.
4. Call `compute_v2_review_set(cwd, baseline_sha=A, head_at_capture=A)`.
5. Assert: `dangerous.py`'s absolute path IS present in the returned `review_paths` (expected/desired), and demonstrate the current code returns it ABSENT — proving the review set silently drops a file that differs from the true pre-turn baseline (`changed_since` would include it) solely because `dirty_now`'s ancestor-gated term missed it. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L163-204)
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
        # Check if HEAD exists (i.e., repo has at least one commit)
        head_check = subprocess.run(
            [*GIT_CMD, "rev-parse", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        if head_check.returncode != 0:
            # No commits yet — skip review rather than creating commits in the user's repo
            debug_log("No commits in repo, skipping baseline capture")
            return None

        result = subprocess.run(
            [*GIT_CMD, "stash", "create"],
            cwd=cwd, capture_output=True, text=True, timeout=15
        )
        sha = result.stdout.strip()
        if sha:
            return sha

        # Working tree is clean — stash create returns empty. Use HEAD.
        result = subprocess.run(
            [*GIT_CMD, "rev-parse", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        sha = result.stdout.strip()
        return sha if sha else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        debug_log(f"Failed to capture git baseline: {e}")
        return None
```

**File:** plugins/security-guidance/hooks/diffstate.py (L386-438)
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
    untracked_in_review = sorted(new_untracked & review_set)
    metrics = {
        "dirty_now_count": len(dirty_now),
        "changed_since_count": len(changed_since) if changed_since is not None else -1,
        "review_set_count": len(review_set),
    }
    # Only emit when nonzero to stay under the 10-key telemetry cap.
    if preexisting_unchanged:
        metrics["preexisting_untracked_excluded"] = len(preexisting_unchanged)
    return review_paths, diff_base, repo, untracked_in_review, metrics
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L446-515)
```python
def handle_user_prompt_submit(input_data):
    """
    Handle UserPromptSubmit — capture git baseline SHA.
    Called on every user prompt. Updates the baseline so the stop hook
    only reviews changes made since the last prompt.

    Does NOT reset touched_paths/fire_count/previous_findings — those are
    consumed by Stop (consume_stop_state) and time-expired respectively.
    UPS racing the asyncRewake Stop hook caused a meaningful share of reviews
    to be lost when the wipe landed before Stop's state read.

    """
    cwd = input_data.get("cwd", "")
    if not cwd:
        debug_log("UPS: no cwd, skipping baseline capture")
        sys.exit(0)

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

    if preserved["value"]:
        debug_log(
            "UPS: preserving prior baseline — previous Stop hook never "
            "consumed touched_paths (likely user interrupt / aborted turn)"
        )
    elif sha:
        debug_log(f"Captured git baseline: {sha[:12]}")
    else:
        debug_log("Failed to capture git baseline (not a git repo?)")

    sys.exit(0)
```
