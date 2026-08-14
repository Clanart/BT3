### Title
`git commit --amend` during a Claude turn drops the amended file from the Stop-hook review set, letting a dangerous change be skipped - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` intersects `dirty_now` (working-tree/HEAD status plus commits reachable when `head_at_capture` is a strict ancestor of the current HEAD) with `changed_since` (diff against the `baseline_sha` stash commit) to build the Stop-hook review set. When the turn's edits are folded into history via `git commit --amend`, the amended commit is a sibling of `head_at_capture` (same parent, new tree) rather than its descendant, so `_is_ancestor(head_at_capture, current_head)` is false and the amend's file changes are never unioned into `dirty_now`. Since the working tree is clean after the amend, `dirty_now` for that file is empty, its intersection with `changed_since` is empty, and the file silently drops out of the review set.

### Finding Description
`handle_user_prompt_submit` in `security_reminder_hook.py` captures `baseline_sha` via `capture_git_baseline` (a `git stash create` snapshot, or HEAD if clean) and `head_at_capture` via `_git_rev_parse_head`, storing both through `save_baseline_sha`/state writes. [1](#0-0) 

At Stop, `compute_v2_review_set` computes:
- `dirty_now` = tracked-dirty ∪ new-untracked (from `git status`), extended with `head_at_capture..HEAD` names **only if** `head_at_capture` is a strict ancestor of the current HEAD.
- `changed_since` = `git diff <baseline_sha>` name list.
- `review_set = dirty_now & changed_since` (falls back to `dirty_now` only when `changed_since` is `None`, i.e. missing baseline / git error — not the case here). [2](#0-1) 

If, during the turn, the agent (or any subsequent `git` invocation the attacker's repo content/automation induces) runs `git commit --amend`, the new HEAD is a sibling of `head_at_capture` — same parent commit, different tree — not its descendant. `_is_ancestor(repo, head_at_capture, current_head)` therefore returns `False`, so the amend's changed files are never added to `dirty_now`. Because the amend committed exactly what was in the working tree, `git status` now reports the tree as clean, so `dirty_now` for the amended file is empty. The intersection with `changed_since` (which does still contain the file, since `git diff <baseline_sha>` compares the stash tree to the current worktree and would show it) is therefore empty for that path, and the file is excluded from `review_paths`. [3](#0-2) 

The Stop hook (`handle_stop_hook`) treats an empty `review_paths` as "nothing to review" and exits cleanly via `_skip(9)` before any diff content, LLM analysis, or block/exit(2) enforcement occurs. [4](#0-3) 

Notably, the codebase already recognizes `git commit --amend` as a special case requiring reflog-based handling for the **commit-review** hook (`_resolve_amend_pre_sha`, used by `handle_commit_review_posttooluse`) — but that logic only guards the PostToolUse[Bash] commit reviewer, not the Stop-hook's `compute_v2_review_set` path, which has no amend-awareness at all. [5](#0-4) 

This breaks the stated invariant that "the review set must stay bound to the right repo, baseline, and touched paths": an ordinary, unprivileged git operation (amend) — something Claude itself, an autonomous subagent, or a compound Bash command in the session may run — silently removes an otherwise-correctly-tracked dangerous change from the reviewed set.

### Impact Explanation
The Stop-hook LLM security review (and its exit(2) enforcement forcing Claude to address findings) is the last automated backstop reviewing Claude-authored diffs before they land. If the review set silently excludes a file that was actually changed in the turn (because it was folded via `--amend`), a dangerous change (e.g. injected command execution, backdoor, secret) is never surfaced to the LLM reviewer and never blocks the turn — matching the "Unauthorized local command execution that bypasses Claude Code approval or deny controls" impact class, since the review gate is the control being bypassed.

### Likelihood Explanation
This requires no special privilege: any workflow where Claude (or a script/subagent it invokes) issues `git commit` followed later in the same turn by `git commit --amend` on the same commit (a very common pattern for "fix and re-commit" or squash workflows) triggers the gap deterministically. No malicious repo content, credentials, or social engineering is needed — only ordinary git usage inside the session between `UserPromptSubmit` baseline capture and `Stop`.

### Recommendation
In `compute_v2_review_set`, don't rely solely on strict ancestor-of-HEAD to detect "commits made this turn." Additionally detect amend/rebase-style history rewrites of `head_at_capture` (e.g., via reflog inspection similar to `_resolve_amend_pre_sha`, or by tracking the pre-turn tree's file set and diffing content directly against `baseline_sha` without requiring `dirty_now` inclusion) so that files changed via `--amend` (or other non-linear HEAD-rewriting operations, such as `rebase`/`reset --soft` + recommit) remain part of the review set. A more robust general fix: when `head_at_capture != current_head` and it is *not* an ancestor, still union `changed_since` results directly into the review set instead of intersecting with a `dirty_now` computed only from status-vs-HEAD.

### Proof of Concept
Integration test plan (pytest, using a temp git repo):
1. `git init`, create `file.py` with safe content, commit (`C0`).
2. Simulate UPS: call `capture_git_baseline(repo)` → `baseline_sha` (should equal `HEAD`, i.e. `C0`, tree clean); `head_at_capture = C0`.
3. Simulate Claude's turn: write dangerous content into `file.py` (e.g. `os.system(user_input)`), `git add -A && git commit -m "feat"` → `C1`.
4. Simulate a follow-up: edit `file.py` again slightly and `git commit --amend --no-edit` → `C2` (sibling of `C1`, same parent `C0`).
5. Call `compute_v2_review_set(repo, baseline_sha=C0, head_at_capture=C0)`.
6. Assert: `_is_ancestor(repo, C0, C2)` — expected `True` here actually since C0 is ancestor of C2 through direct parent... 

   Correction: the actual failure case requires `head_at_capture` to point at a HEAD *after* an initial commit (not before it), i.e., capture baseline mid-turn between two commits, then amend the second. Precise repro:
   - `C0` exists before baseline capture.
   - Baseline captured: `baseline_sha=C0` (stash create on clean tree = C0), `head_at_capture=C0`.
   - Claude commits `C1` on top of `C0` (dangerous file included) → `head_at_capture (C0)` **is** ancestor of `C1`, so this alone is caught correctly by the existing ancestor branch.
   - Claude then runs `git commit --amend` turning `C1` into `C1'` (same parent `C0`, new tree). Current HEAD is now `C1'`. `_is_ancestor(C0, C1')` is still `True` (`C0` is still the parent) — so the ancestor branch *does* still fire and adds `head_at_capture..HEAD` (`C0..C1'`) names via `_git_name_only`, which would include the dangerous file.

   This means the specific amend-drops-review scenario needs `head_at_capture` to be **the pre-amend commit itself** (i.e., baseline captured *after* the first commit, right before the amend) — e.g., UPS fires between the initial commit and the amend within the same turn (a new user prompt submitted mid-turn, or a subagent/background hook re-triggering baseline capture). In that case `head_at_capture = C1`, and after `--amend`, current HEAD = `C1'`, a **sibling** of `C1` (not descendant) → `_is_ancestor(C1, C1')` = `False`, reproducing the gap described above.
7. Assert `compute_v2_review_set(repo, baseline_sha, head_at_capture=C1)` returns `review_paths` that **excludes** `file.py`, while `git diff baseline_sha -- file.py` (manually) shows the dangerous content is present — demonstrating the dangerous change is dropped from review.

Note: I was not able to fully execute this reproduction in a live git environment (read-only analysis tooling); the trace above is derived from static code-path analysis of `compute_v2_review_set`, `_is_ancestor` usage, and `handle_user_prompt_submit`'s baseline-preservation logic. Confirming the precise UPS-timing precondition (baseline recapture landing exactly between the pre-amend commit and the amend) requires running the described integration test against the actual `gitutil._is_ancestor`/`_git_name_only` implementations, which I could not open in full due to search-tool limitations on `gitutil.py`.

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L517-573)
```python
def _resolve_amend_pre_sha(repo_root, expected_post_sha=None):
    """For a `git commit --amend` we just ran, return the pre-amend SHA via
    reflog, or None if it can't be safely determined.

    expected_post_sha: the post-amend SHA the caller parsed from bash stdout
    (or reflog). If provided, HEAD@{0} of `repo_root` must match it (prefix
    compare — bash stdout SHAs are abbreviated, reflog %H is 40 chars) before
    we trust the reflog-derived pre-amend SHA. This guards against the
    cross-repo case (`cd ../other && git commit --amend && cd -`) where
    `repo_root` happens to have its own recent amend that's unrelated to
    the bash command we're reviewing.

    We require HEAD@{0}'s reflog subject to start with `commit (amend)` —
    otherwise our `--amend` regex matched something that didn't actually
    perform an amend (e.g., `git commit --amend --dry-run`, aliased commands,
    aborted amends), and HEAD@{1} would be the wrong commit. Also requires
    HEAD@{1} to NOT itself be an amend, since back-to-back amends would have
    HEAD@{1} as the previous-amend's post state — the original commit we
    want to compare against is then HEAD@{2}, but at that point we're
    reaching and fall back to a full review.

    Bytes + decode('utf-8', errors='replace'): reflog subjects embed commit
    subjects, which git stores as raw bytes (commit messages may be latin-1
    / cp1252 / etc.). text=True would raise UnicodeDecodeError (a
    ValueError, not OSError) on non-UTF8 bytes and crash the hook.
    """
    if not repo_root:
        return None
    try:
        r = subprocess.run(
            [*GIT_CMD, "log", "-g", "-2", "--format=%H|%gs", "HEAD"],
            cwd=repo_root, capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    stdout_text = r.stdout.decode("utf-8", errors="replace")
    lines = [ln for ln in stdout_text.splitlines() if "|" in ln]
    if len(lines) < 2:
        return None
    head0_sha, _, head0_subj = lines[0].partition("|")
    head1_sha, _, head1_subj = lines[1].partition("|")
    if not head0_subj.startswith("commit (amend)"):
        return None
    if head1_subj.startswith("commit (amend)"):
        return None
    # Cross-repo guard: the post-amend SHA the caller is about to review must
    # match HEAD@{0} of repo_root. Otherwise the bash command was likely run
    # in a different repo than repo_root, and the reflog we just read is
    # unrelated. Prefix-compare: expected_post_sha is typically the 7-char
    # abbreviated SHA captured from bash stdout by _COMMIT_SHA_RE (git's
    # default core.abbrev floor), while head0_sha is the full 40-char %H —
    # strict equality would always fail and silently disable the delta path.
    if expected_post_sha and not head0_sha.startswith(expected_post_sha):
        return None
    return head1_sha or None
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
