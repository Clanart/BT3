### Title
Sideways HEAD move (checkout/reset to unrelated ref) during a turn causes committed dangerous changes to be silently excluded from the Stop-hook review set - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` only adds commits made "this turn" to the reviewable set when the current `HEAD` is a linear descendant of `head_at_capture` (via `_is_ancestor`). If a git operation moves `HEAD` sideways (e.g. `git checkout <other-ref>` followed by a commit) rather than advancing it as a descendant of the turn's start point, that ancestor check fails, the commit's files never get unioned into `dirty_now`, and because the working tree is clean after a commit, `dirty_now` ends up empty — zeroing the final `review_set` via the `dirty_now & changed_since` intersection even though the dangerous file genuinely differs from `baseline_sha`.

### Finding Description
`load_baseline_sha`/`save_baseline_sha` in `plugins/security-guidance/hooks/diffstate.py` (lines 43-54) are simple session-state accessors with no validation tying them to a specific repo state beyond the raw SHA string captured once per `UserPromptSubmit` by `capture_git_baseline` [1](#0-0) .

The actual review-set computation lives in `compute_v2_review_set`: [2](#0-1) 

The logic is:
1. `tracked_dirty`/`untracked` come from `git status --porcelain` against the *current* `HEAD` — i.e., anything already committed is invisible here.
2. The `head_at_capture..HEAD` diff is only unioned into `dirty_now` when `_is_ancestor(repo, head_at_capture, current_head)` is true — i.e., `HEAD` must have advanced *linearly* from the turn's starting commit.
3. `review_set = dirty_now & changed_since` — an AND, not an OR, with the baseline diff.

If, during a turn, a git command moves `HEAD` to an unrelated ref (e.g. `git checkout other-branch` or `git reset --hard <unrelated-sha>`) and a dangerous change is then committed there, `head_at_capture` is no longer an ancestor of the new `HEAD`, so step 2 never fires. Because the change was committed (not left dirty), `git status` reports a clean tree, so `dirty_now` is empty (or excludes the dangerous file). The intersection with `changed_since` (which *would* correctly show the file as different from `baseline_sha`) is therefore forced to empty/excluded — the dangerous file drops out of `review_set` entirely and is never sent to the Stop-hook LLM review or reflected in the `get_git_diff` pathspec that consumes `review_set`.

This is reachable purely through "normal git operations in a cloned repo" (checkout to another branch/ref plus a commit) — no admin privilege, no state-file tampering, and no direct mutation of `load_baseline_sha`'s storage is required. It only requires the agent (driven by attacker-controlled repository content, e.g. a malicious README/Makefile/CI instruction) to run a checkout/reset-then-commit sequence during the turn.

### Impact Explanation
This breaks the stated invariant that "the review set must stay bound to the right repo, baseline, and touched paths." A dangerous code change that lands on `HEAD` via a non-linear git operation is treated as pre-existing/untouched and is silently skipped by the Stop-hook security review, even though `git diff` against the session's own `baseline_sha` shows it as new. This is a wrong-target/target-loss mutation bypass of the security-review gate: real, session-authored malicious/dangerous code committed during the turn escapes LLM review and the `exit(2)` remediation loop entirely.

### Likelihood Explanation
Feasible and repeatable: any repository content that causes the agent to run something equivalent to `git checkout <other-ref> && <edit dangerous file> && git commit` within one turn triggers the gap deterministically. No race condition, no privileged access, no reliance on other sessions — a single-session, single-repo reproduction is sufficient.

### Recommendation
Don't gate the `head_at_capture..HEAD` union on `_is_ancestor`. Instead:
- Always include `_git_name_only(repo, f"{head_at_capture}..HEAD")` (or equivalently diff current `HEAD` against `head_at_capture` unconditionally) regardless of ancestry, since the goal is "what changed since the turn started," not "did HEAD move linearly."
- Alternatively, when `head_at_capture` is not an ancestor of current `HEAD` (a sideways/backward move occurred), treat this as a strong signal to force a full review (fall back to reviewing the union of `changed_since` alone, bypassing the `dirty_now` AND-gate) rather than silently narrowing scope to nothing.

### Proof of Concept
Unit/integration test in the style of the existing `compute_v2_review_set` tests:
1. Init a repo, commit an initial file, capture `head_at_capture = H0` and `baseline_sha = S` (via `capture_git_baseline`).
2. Create and checkout a second branch pointing at an unrelated commit (`git checkout -b other <some-other-commit-not-descended-from-H0>`), or `git reset --hard` to an unrelated existing commit/tag in the repo.
3. Add a dangerous file/content and `git commit` it, producing `H2` where `H0` is NOT an ancestor of `H2`.
4. Call `compute_v2_review_set(cwd, baseline_sha=S, head_at_capture=H0, untracked_at_baseline={})`.
5. Assert: `changed_since` (internal) would include the dangerous file, but the returned `review_paths` (and `review_set_count` in `metrics`) is empty / does not contain the dangerous file — demonstrating the bypass.
6. Contrast with a control case where `H0` *is* an ancestor of `H2` (normal linear commit) and assert the dangerous file *is* included — confirming the difference is caused specifically by the ancestry check.

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
