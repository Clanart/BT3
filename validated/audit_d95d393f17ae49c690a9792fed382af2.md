### Title
`compute_v2_review_set` skips review of committed changes after a sideways `git checkout`/`reset` because `dirty_now` is derived only from status-vs-current-HEAD - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` gates the Stop-hook review set on the intersection of `dirty_now` (git status relative to the *current* HEAD) and `changed_since` (diff against the turn's stash baseline). When HEAD moves sideways during a turn — e.g. via `git checkout <branch>` or `git reset --hard <sha>` to a ref that is not a fast-forward descendant of `head_at_capture` — `dirty_now` becomes empty because the working tree now exactly matches the new HEAD, even though the content differs completely from the turn's baseline. Since `review_set = dirty_now & changed_since`, an empty `dirty_now` silently zeroes the review set regardless of what `changed_since` contains.

### Finding Description
`compute_v2_review_set` (`plugins/security-guidance/hooks/diffstate.py:353-438`) computes:
- `dirty_now`: tracked/untracked files dirty relative to the working tree's current HEAD, plus committed files only when `head_at_capture` is a git ancestor of `current_head` [1](#0-0) .
- `changed_since`: files differing from the pre-turn stash baseline (`baseline_sha`) [2](#0-1) .
- `review_set = dirty_now & changed_since` (falling back to `dirty_now` only when `changed_since` is `None`, i.e. a git error) [3](#0-2) .

The linear-ancestry check `_is_ancestor(repo, head_at_capture, current_head)` in `gitutil.py` only returns true when `head_at_capture` is reachable from `current_head` via `merge-base --is-ancestor` [4](#0-3) . Ordinary git operations that are fully available to the agent driving the Bash tool — `git checkout <other-branch>`, `git switch`, or `git reset --hard <unrelated-sha>` — move HEAD to a commit that is *not* a descendant of `head_at_capture`. After such an operation the working tree exactly matches the new HEAD, so `git status --porcelain` reports nothing dirty, and the ancestor check fails so the committed-diff union term is skipped. `dirty_now` is therefore the empty set, and `review_set = ∅ ∩ changed_since = ∅` no matter how different the checked-out content is from the turn's stash baseline (`changed_since` is not even consulted meaningfully because the intersection with an empty set is always empty).

This breaks the stated invariant ("review set must stay bound to the right repo, baseline, and touched paths") because the final working-tree state the user ends up with after Stop can be entirely different from what was baselined, yet zero files get flagged for review. The docstring itself acknowledges the general class of gap ("Known limitation: a Bash-only turn that's interrupted before Stop fires leaves touched_paths empty…") but the sideways-checkout/reset case is a stronger variant: it silently zeroes the intersection even when `changed_since` is non-empty, not merely when `touched_paths`/PostToolUse events are missing.

### Impact Explanation
An agent turn that is redirected (e.g. through repo content instructing normal git commands, or a scripted multi-step Bash sequence) to commit a dangerous change and then move HEAD sideways — to a pre-existing malicious branch, a stashed/dangling commit, or a prior commit via `reset --hard` — ends the turn with `review_set == []`. `security_reminder_hook.py`'s Stop path (which calls `compute_v2_review_set` per the call sites in that file and in `security_reminder_hook.py`) treats this as "nothing to review," so no diff is sent to the LLM reviewer and no finding is ever surfaced. This is exactly the "dangerous change treated as old and skipped" behavior the question describes, and can result in unreviewed sensitive code/config persisting in the working tree, matching the Immunefi impact category of sensitive code/diff disclosure or unintended-sink risk being missed by the review gate meant to catch it.

### Likelihood Explanation
The precondition is that the agent (whether self-directed, prompt-injected via repository content, or scripted) executes ordinary git commands (`checkout`, `switch`, `reset --hard`) during a single turn — no elevated privileges, no leaked credentials, and no exploitation of git internals are required. This is squarely within "normal git operations in a cloned repo" as specified in the target. The bug is deterministic given the sequence (not a race), so it is reliably reproducible.

### Recommendation
Do not gate `dirty_now` purely on status-vs-current-HEAD. When `head_at_capture` and `current_head` differ (regardless of ancestry direction), union in `_git_name_only(repo, head_at_capture, current_head)` (a symmetric diff between the two HEADs) rather than skipping the term whenever `_is_ancestor` is false. Alternatively, make `review_set` a union rather than a pure intersection when `changed_since` is non-empty but `dirty_now` is empty and HEAD has moved since capture, or explicitly flag the case (`head_at_capture != current_head` and not an ancestor) as a "history rewritten during turn" condition and force full re-review from `baseline_sha` in that case.

### Proof of Concept
Unit/integration test targeting `compute_v2_review_set` directly (bypassing subprocess mocking by using a real temp git repo):

1. Init a repo, commit an initial file, capture `head_at_capture = H0` and `baseline_sha` via `capture_git_baseline`.
2. Create a second branch `evil` from an unrelated point, or simply create a second commit history where a "dangerous" file exists with different content.
3. On the working branch, commit a change introducing `dangerous_file.py` containing an obviously vulnerable pattern (`H1`, descendant of `H0`).
4. Instead of leaving `H1` as HEAD, run `git checkout evil` (or `git reset --hard <some-other-sha>` that is not a descendant of `H0`) so HEAD no longer descends from `head_at_capture`, and the working tree is clean relative to the new HEAD.
5. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, {})`.
6. Assert (expected-but-failing): `dangerous_file.py`'s absolute path is present in the returned `review_paths`. Current behavior: `review_paths == []`, demonstrating the dangerous content is fully excluded from review despite differing from `baseline_sha`.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L401-408)
```python
    dirty_now = tracked_dirty | new_untracked

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

**File:** plugins/security-guidance/hooks/diffstate.py (L423-426)
```python
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
