### Title
Committed-then-amended dangerous change silently dropped from Stop-hook review set - ([File: plugins/security-guidance/hooks/diffstate.py])

### Finding Description
`capture_git_baseline` (called from `handle_user_prompt_submit`) snapshots `baseline_sha` (a `git stash create`/`HEAD` SHA) and `head_at_capture` at every `UserPromptSubmit`. [1](#0-0) [2](#0-1)  At Stop time, `compute_v2_review_set` builds the reviewed file set as an **intersection** of two independently-computed sets:

- `dirty_now`: files dirty vs current `HEAD`, plus (only if `head_at_capture` is a git-ancestor of `current_head`) files touched by commits `head_at_capture..HEAD`. [3](#0-2) 
- `changed_since`: `git diff --name-only <baseline_sha>` — a tree-level diff, unaffected by history shape. [4](#0-3) 

```python
review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
``` [5](#0-4) 

If a commit `H1` already existed at the moment `head_at_capture`/`baseline_sha` were captured (e.g. it was made in a prior turn, or by prior tool use before the hook re-armed), and the current turn amends that exact commit (`git commit --amend`) to introduce or keep the dangerous change, the new commit `H2` has the *same parent* as `H1` — `H1` and `H2` are siblings, so `H1` is no longer an ancestor of `H2`. `_is_ancestor(head_at_capture, current_head)` returns `False`, so the `head_at_capture..HEAD` name-only diff is never unioned into `dirty_now`. Because the amended file is now fully committed and matches the working tree, `git status --porcelain` reports nothing dirty either, so `dirty_now` ends up empty for that file. Meanwhile `changed_since` (diff against `baseline_sha`, i.e. `H1`) correctly still contains the dangerous file, since the tree differs from the stale baseline tree. The intersection of an empty `dirty_now` with a non-empty `changed_since` yields an empty `review_set` for that file — it is silently excluded from the Stop-hook diff sent to the LLM reviewer, even though it is a genuine content change introduced since the baseline. This directly breaks the stated invariant that the review set stay bound to the right baseline and touched paths, since amending a pre-baseline commit desynchronizes the "linear advance" ancestry check from the tree-level baseline diff.

### Impact Explanation
The Stop-hook LLM security review (`analyze_code_security`) is the plugin's only automated backstop against dangerous edits (secrets, injection, path traversal, etc.) making it into the reviewed diff. [6](#0-5)  By causing an ordinary `git commit` + `git commit --amend` sequence — entirely normal, unprivileged git usage inside a cloned repo — an attacker (or attacker-influenced Claude behavior, e.g. via prompt injection in repo content instructing "amend the last commit") can make a dangerous file permanently invisible to the review set for that turn. Combined with `save_baseline_sha`/`with_locked_state` advancing the baseline forward after each Stop, the dangerous content is then baked into history as "already reviewed," and subsequent turns' diffs (against the new baseline) won't see it either — effectively a permanent bypass of the review gate for that change, i.e. unauthorized file content escaping the approved review/workspace-scope boundary.

### Likelihood Explanation
No special privilege is required beyond ordinary git commands (`commit`, `commit --amend`) which are common, expected operations in a coding session (e.g. "fix the commit message," "squash this into the previous commit"). The only precondition is that `head_at_capture` correspond to a commit that later gets amended rather than only ever fast-forwarded — a routine occurrence, not a contrived edge case. This is fully deterministic and reproducible with a small local git repo; no timing races or external services needed.

### Recommendation
Don't require strict git-ancestry between `head_at_capture` and `current_head` to fold in committed changes. Instead, always compute the committed-content delta via a tree-level diff (`git diff --name-only head_at_capture...HEAD` or better, always union `changed_since` results directly into the review set for files whose current content differs from `head_at_capture`'s tree, regardless of ancestry) so that non-linear history rewrites (amend, rebase, filter-branch) can't zero out `dirty_now` while `changed_since` still shows real changes. At minimum, when `_is_ancestor` returns `False`, fall back to using `changed_since` alone (or `dirty_now | changed_since`) rather than intersecting an ancestry-dependent empty set with a correct tree-diff set.

### Proof of Concept
Integration test in a temp git repo:
1. `git init`, initial commit `H0` with a benign file.
2. Simulate UPS: call `capture_git_baseline(repo)` and `_git_rev_parse_head(repo)` → `baseline_sha = head_at_capture = H0`.
3. Add `dangerous.py` with obviously vulnerable code, `git add && git commit -m "add feature"` → `H1`.
4. Simulate a second UPS mid-session is skipped (session still open) — but simulate the scenario where `head_at_capture` was actually recorded as `H1` (i.e., re-run capture to set `head_at_capture=H1`, `baseline_sha=H1`, mimicking a prior turn's baseline already having advanced to include `H1`).
5. Now amend: edit `dangerous.py` further (or just `git commit --amend --no-edit`) → `H2` (parent = `H0`, sibling of `H1`).
6. Call `compute_v2_review_set(repo, baseline_sha=H1, head_at_capture=H1)`.
7. Assert: `dangerous.py` is present in the returned `review_paths` (expected/desired), but the current implementation returns it **absent**, demonstrating the bypass — `review_set` is empty despite `changed_since` (git diff H1) containing `dangerous.py`.

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

**File:** plugins/security-guidance/hooks/diffstate.py (L401-409)
```python
    dirty_now = tracked_dirty | new_untracked

    diff_base = "HEAD"
    current_head = _git_rev_parse_head(repo)
    if (head_at_capture and current_head and head_at_capture != current_head
            and _is_ancestor(repo, head_at_capture, current_head)):
        dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
        diff_base = head_at_capture

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L17-22)
```python
2. **Stop hook (final review)**: When Claude finishes, uses `git diff` against a
   baseline SHA (captured at UserPromptSubmit) to get only the code changed during the
   session. Runs two Haiku analyses on the diff:
   a) Concrete vulnerability scan with severity ratings
   b) Areas-of-concern analysis identifying categories to investigate
   Exits with code 2 to force Claude to continue and address findings.
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L466-503)
```python
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
