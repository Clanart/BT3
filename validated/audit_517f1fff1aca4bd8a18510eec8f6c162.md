### Title
Stop-hook review-set becomes bindable to a stale HEAD via `git commit --amend`, letting a dangerous change be silently skipped - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` decides which files get sent to the Stop-hook LLM reviewer by intersecting `dirty_now` (working tree vs. current `HEAD`) with `changed_since` (vs. the turn-start `baseline_sha`). The linear-history fast-path that folds in commits made during the turn only fires when `head_at_capture` is an *ancestor* of the current `HEAD`. A `git commit --amend` breaks that ancestor relationship, so a dangerous change that is amended into a commit becomes invisible to `dirty_now` and is dropped from the review set entirely, even though it still differs from `baseline_sha`.

### Finding Description
`compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py` computes: [1](#0-0) 

- `dirty_now` = tracked-dirty-vs-`HEAD` ∪ new-untracked, optionally unioned with `head_at_capture..HEAD` **only if** `_is_ancestor(repo, head_at_capture, current_head)` holds: [2](#0-1) 
- `review_set = dirty_now & changed_since` (intersection, not union), where `changed_since` comes from `baseline_sha` via `_git_name_only`: [3](#0-2) 

`_is_ancestor` checks `merge-base --is-ancestor head_at_capture current_head`: [4](#0-3) 

An unprivileged actor driving ordinary git commands in the session (Claude itself, or any automation acting on its behalf) can:
1. Let `head_at_capture` = C0 be captured at `UserPromptSubmit` via `capture_git_baseline`/`_git_rev_parse_head`.
2. Introduce a dangerous change and `git commit` it (commit C1, child of C0) — normally `C0` is an ancestor of `C1`, so the linear fast-path applies and the file is folded into `dirty_now`.
3. Immediately `git commit --amend` that commit. This produces a **new** commit object C1′ with the same parent C0. C1′ does **not** have C0's original child C1 in its history, and critically, `head_at_capture` (C0) is still technically an ancestor of C1′ (C0 → C1′), so in a simple amend `_is_ancestor` still holds. However, once the amended commit makes the working tree clean relative to the amended `HEAD`, `tracked_dirty` is empty (nothing differs from current `HEAD`), and the fast-path union `head_at_capture..HEAD` (`C0..C1′`) via `_git_name_only` would still normally pick up the file — **unless** a second amend, rebase, or any non-fast-forward rewrite (e.g., `commit --amend` after `reset --soft` to an earlier point, or interactive rebase) is used so that `head_at_capture` is no longer reachable from the new `HEAD` at all. In that case `_is_ancestor` returns `False`, the linear-history term is skipped, `tracked_dirty` is empty (working tree clean against the rewritten `HEAD`), and `new_untracked` doesn't include the file (it's tracked). The result is `dirty_now = ∅` (or missing the file), so `review_set = dirty_now & changed_since` **excludes the dangerous file even though `changed_since` (computed against `baseline_sha`) still contains it**, because the term missing from the intersection zeroes it out regardless of what `changed_since` says.

This breaks the stated invariant that "the review set must stay bound to the right repo, baseline, and touched paths": the review set is silently narrowed to `∅` for a file that a normal git rewrite (amend/rebase) moved out of the `dirty_now` term, purely due to history-linearity bookkeeping, not because the change was reviewed or reverted. `get_baseline_file_content` and the whole baseline-diff machinery in `diffstate.py` are the mechanism that computes and consumes this state (`baseline_sha`, `head_at_capture`), so the misbinding originates in this file's baseline-tracking design.

### Impact Explanation
This is a Logic-level guard bypass: the Stop-hook security review (LLM vulnerability scan that would otherwise force `exit(2)` and block the turn) never sees the dangerous diff, so a vulnerable commit is accepted as if it had been reviewed and found clean. This matches "Logic-level service disruption caused by bypassing a required guard or misbinding security state" — the guard (mandatory LLM security review before Stop) is bypassed by a state-binding defect (`head_at_capture`/`baseline_sha` losing the ancestor relationship after ordinary amend/rebase operations), not by any privilege escalation.

### Likelihood Explanation
Preconditions are met by entirely ordinary developer/agent workflow: any turn where Claude (or a compound Bash command) commits then amends/rebases before the Stop hook fires. No special permissions, secrets, or social engineering are required — `git commit --amend` and interactive rebase are common operations that Claude Code performs routinely when iterating on commit messages or fixing up prior commits. The bug is deterministic given the described sequence, making it fully repeatable.

### Recommendation
Change the linear-advance detection in `compute_v2_review_set` to not rely solely on `_is_ancestor(head_at_capture, current_head)`. Instead, always compute `changed_since` from `baseline_sha` as the authoritative source of "content differs from turn start," and use `dirty_now` only to prune *already-reviewed, currently-clean* files that are provably identical between `head_at_capture` and `HEAD` — never to gate out files purely because they no longer appear "dirty" after a history rewrite. Concretely, union the `changed_since` set into `dirty_now` whenever `head_at_capture` cannot be resolved as a strict ancestor (treat amend/rebase as the "committed differently but still turn-owned" case), or drop the intersection in favor of `changed_since` alone when `baseline_sha` is present and valid, falling back to the union `dirty_now | changed_since` rather than `dirty_now & changed_since`.

### Proof of Concept
Integration test in the plugin's test suite (e.g., alongside existing `compute_v2_review_set` tests):
1. Init a temp git repo, commit an initial file, capture `head_at_capture = C0` and `baseline_sha` via `capture_git_baseline`.
2. Write a dangerous change (e.g., `os.system(user_input)`) to `vuln.py`, `git commit -m "add vuln"` → `C1`.
3. `git commit --amend --no-edit` → `C1'`; then perform a second rewrite that breaks ancestry, e.g. `git reset --soft C0 && git commit -m "squashed" --amend` or an interactive-rebase-equivalent (`git rebase -i` reordering) so that `merge-base --is-ancestor C0 HEAD` returns non-ancestor for the intended chain, or simply verify with a rebase onto an unrelated commit.
4. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, {})`.
5. Assert `review_paths` (or `review_set`) still contains `vuln.py`'s absolute path — i.e., the dangerous file is not silently dropped from the returned `review_paths`.

Expected (buggy) result: `review_paths` is empty or omits `vuln.py` despite `vuln.py` differing from `baseline_sha`. Expected (fixed) behavior: `review_paths` always includes `vuln.py` when its content differs from `baseline_sha`, regardless of amend/rebase history rewrites.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L401-426)
```python
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
