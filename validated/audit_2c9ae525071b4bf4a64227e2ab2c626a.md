### Title
`compute_v2_review_set` silently drops committed changes when `git commit --amend` breaks the HEAD-ancestor check, letting a dangerous change bypass Stop-hook security review - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` only adds a turn's committed diff to the review set when the pre-turn `head_at_capture` is a git ancestor of the new `HEAD` [1](#0-0) . A normal `git commit --amend` (or any rewrite that replaces `head_at_capture` with a sibling commit sharing the same parent) makes this ancestor check false, and because the working tree is clean after the amend, `dirty_now` ends up empty, so intersecting it with `changed_since` yields an empty review set even though the amended commit's content genuinely differs from the stashed baseline.

### Finding Description
`compute_v2_review_set` computes the Stop-hook's review set as `dirty_now ∩ changed_since` (falling back to `dirty_now` alone if `changed_since` is unavailable) [2](#0-1) .

- `dirty_now` is built from `git status --porcelain` (`tracked_dirty | new_untracked`), plus—only when `head_at_capture` is an ancestor of the current `HEAD`—the name-only diff of `head_at_capture..HEAD` [3](#0-2) .
- `changed_since` is the diff of the working tree against the pre-turn `baseline_sha` (a `git stash create` snapshot) [4](#0-3) .

When the agent runs `git commit --amend` during the turn, the new `HEAD` is a *sibling* of the old `head_at_capture` (same parent, rewritten tip) rather than its descendant, so `_is_ancestor(repo, head_at_capture, current_head)` returns `False` [5](#0-4) . Consequently the `head_at_capture..HEAD` diff is never folded into `dirty_now`. Since the amend leaves the working tree clean, `tracked_dirty` and `new_untracked` are both empty, making `dirty_now = ∅`. Meanwhile `changed_since` (diff against the original `baseline_sha`, captured before the turn) correctly still contains the dangerous file, because its content differs from that pre-turn snapshot. But `review_set = dirty_now ∩ changed_since = ∅ ∩ {dangerous_file} = ∅` — the file is dropped from the review set entirely, even though it was genuinely authored/modified in this turn and never previously reviewed.

The docstring explicitly acknowledges the design intent only covers "HEAD advanced linearly" commits [6](#0-5) , but does not account for the fact that a non-linear rewrite (amend/rebase/reword) combined with a clean working tree zeroes out `dirty_now` and thus the whole intersection, rather than degrading gracefully to `changed_since` alone. This is a genuine gap in "the review set must stay bound to the right repo, baseline, and touched paths" — the baseline correctly still reflects the old (pre-turn) state, but the ancestor-gated `dirty_now` term silently loses the amended content, so the intersection incorrectly reports "nothing to review."

### Impact Explanation
Any dangerous code (e.g., a file containing an attacker-influenced shell command, backdoor, or exfiltration logic) that Claude commits and then amends in the same turn is never surfaced to the Stop-hook's LLM security reviewer. Because Claude Code's security-guidance plugin relies on this review set to flag/gate risky diffs before they are treated as "old"/already-reviewed, this allows dangerous local file/command changes to slip past the plugin's approval/deny gate — matching "Unauthorized local command execution that bypasses Claude Code approval or deny controls."

### Likelihood Explanation
This requires no special privilege beyond what an ordinary Claude Code session already has in a cloned repo: staging a change, committing it, and amending the commit are all standard, frequently-used git operations (e.g., "fix a typo in my last commit," "squash this into the previous commit," triggered by ordinary repo content or agent behavior). No admin rights, no maintainer access, no bypass of git plumbing is needed — it is fully reachable through normal automation flows, making it highly feasible and repeatable.

### Recommendation
Do not gate the committed-diff term strictly on `_is_ancestor(head_at_capture, current_head)`. Instead, whenever `current_head != head_at_capture`, always union in the diff between `head_at_capture` and `HEAD` (or, more robustly, always fall back to using `changed_since` alone as the review set whenever it is non-empty but `dirty_now` is empty due to a non-linear HEAD move), so amended/rebased/reworded commits' content changes are not silently excluded. At minimum, treat "`HEAD` changed at all this turn" (not just fast-forward) as a case requiring the `head_at_capture..HEAD` diff to be folded into `dirty_now`.

### Proof of Concept
Unit/integration test in the style of existing diffstate tests:

1. Init a git repo, commit an initial safe file, capture `baseline_sha = capture_git_baseline(cwd)` and `head_at_capture = _git_rev_parse_head(cwd)`.
2. Write a dangerous file (e.g. `evil.sh` with a hazardous command), `git add` + `git commit -m "add feature"`.
3. Run `git commit --amend -m "add feature (typo fix)"` with no content change to the working tree (clean tree after amend).
4. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture, {})`.
5. Assert `evil.sh` is present in the returned `review_paths`.

Expected (buggy) result: `review_paths` is empty (or omits `evil.sh`) because `_is_ancestor(head_at_capture, new_HEAD)` is `False` and `dirty_now` is empty, producing `review_set = dirty_now ∩ changed_since = ∅`. This violates the invariant that the review set must remain bound to files genuinely touched since baseline, and demonstrates the dangerous file is skipped from Stop-hook review.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L372-374)
```python
    Returns (absolute paths sorted, diff_base, repo_root, metrics).
    diff_base is "HEAD" unless HEAD advanced linearly this turn (commits),
    in which case it's head_at_capture so committed files produce a diff.
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
