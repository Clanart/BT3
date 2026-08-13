### Title
Post-amend baseline desync causes `compute_v2_review_set` to zero out the review set for dangerous changes - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` derives the reviewed file set as the intersection of "currently dirty vs HEAD" (`dirty_now`) and "changed since baseline" (`changed_since`). A `git commit --amend` performed within a turn makes the working tree clean (`dirty_now` becomes empty) while rewriting HEAD to a commit that is *not* an ancestor of `head_at_capture`, so the linear-advance branch that would otherwise fold committed diffs into `dirty_now` never fires. The intersection of an empty `dirty_now` with a non-empty `changed_since` is empty, silently dropping the amended dangerous change from review.

### Finding Description
`load_baseline_sha`/`head_at_capture` are captured once per `UserPromptSubmit` turn via `capture_git_baseline` and `_git_rev_parse_head` [1](#0-0) . At `Stop`, `compute_v2_review_set` computes:

```
dirty_now = tracked_dirty | new_untracked           # from `git status` porcelain
if head_at_capture is ancestor of current HEAD:
    dirty_now |= diff(head_at_capture..HEAD)         # only for linear commits
changed_since = diff(baseline_sha)                   # full diff vs the captured baseline
review_set = dirty_now & changed_since
``` [2](#0-1) 

`git commit --amend` rewrites HEAD into a sibling commit (same parent as the pre-amend commit), not a descendant of `head_at_capture` when the amend targets a commit already existing at capture time. In that case `_is_ancestor(head_at_capture, current_head)` is `False`, so the code-committed-this-turn branch is skipped, and the working tree becomes clean after the amend, making `dirty_now` empty. `changed_since` (a direct diff between `baseline_sha` and the current tree) would still contain the dangerous file, but the intersection `dirty_now & changed_since` yields `∅`, so the dangerous file is excluded from `review_paths` even though it is genuinely different from the baseline. This is purely a consequence of "normal git operations" (an ordinary `commit --amend`) that a Claude Code turn (attacker-influenced, e.g., via prompt injection or malicious repo instructions) can trivially trigger without any special privilege, satisfying the "attacker controls normal git operations in a cloned repo" precondition.

The fallback path that would otherwise protect the review (`review_set = dirty_now` when `changed_since is None`) does not help here, because `changed_since` is not `None` — it correctly computes a diff — the bug is the intersection with an incorrectly-empty `dirty_now`, not a missing baseline.

### Impact Explanation
A dangerous change (e.g., an injected backdoor, credential exfiltration, path traversal) introduced via `git commit --amend` during a Claude Code turn is silently omitted from the Stop-hook's LLM security review, defeating the "review set must stay bound to the right repo, baseline, and touched paths" invariant. Because the omitted code is nonetheless committed to the repository, this can result in unauthorized/unreviewed writes to the workspace persisting past the review boundary — a scoped breach of the review gate's guarantee that all agent-authored changes are checked before the turn "completes," aligning with the "Unauthorized file read or write outside the user-approved workspace or target scope" impact category (here specifically, an unreviewed write escaping the intended review scope).

### Likelihood Explanation
The preconditions are ordinary: any turn where the model runs `git commit` followed (in the same or later `Bash` call, same turn) by `git commit --amend` against a commit that predates `head_at_capture`'s advance triggers the bug. No elevated privileges, secrets, or social engineering are required — just standard Bash git usage that Claude Code performs routinely for commit workflows. It's fully reproducible deterministically given the described commit sequence.

### Recommendation
Do not intersect with a `dirty_now` that can regress to empty after a commit. Instead, when HEAD has moved since `head_at_capture` at all (not just when it is a strict ancestor), always union in `diff(baseline_sha..HEAD)`/`diff(head_at_capture..HEAD, allow-non-ancestor)` file names into the candidate set, e.g. by using `git diff --name-only <baseline_sha> <current_head>` unconditionally (regardless of ancestry) alongside working-tree dirty files, rather than gating the git-log-diff term on `_is_ancestor`. Alternatively, drop the `dirty_now` intersection entirely for the git-history term and rely solely on `changed_since` (diff against `baseline_sha`) union `dirty_now`, since `changed_since` already correctly scopes to "changed since this turn started."

### Proof of Concept
Unit/integration test plan for `compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py`:
1. Init a temp git repo, commit an initial file, capture `baseline_sha = HEAD` and `head_at_capture = HEAD` (simulating UPS on a clean tree).
2. Simulate a turn: write `dangerous.py` with malicious content, `git add`, `git commit -m "wip"` (HEAD advances to C1, linear from baseline — sanity check review set includes `dangerous.py` here).
3. Simulate a second turn: at "UPS" recapture `baseline_sha = C1`, `head_at_capture = C1` (clean tree).
4. Attacker/model action: modify `dangerous.py` further, `git add`, then `git commit --amend --no-edit` (rewrites C1 into C1', sibling of C1, same parent).
5. Call `compute_v2_review_set(cwd, baseline_sha=C1, head_at_capture=C1)`.
6. Assert: **expected** `review_paths` contains `dangerous.py` (since content differs from baseline C1). **Actual** (bug): `dirty_now` is empty (clean tree post-amend) and `_is_ancestor(C1, C1')` is `False`, so `review_set` is empty and `dangerous.py` is dropped — assertion fails, confirming the vulnerability.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L468-498)
```python
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
