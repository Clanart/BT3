### Title
Stop-hook baseline auto-advance in `load_baseline_sha`/`compute_v2_review_set` lets an unresolved dangerous edit permanently drop out of the review set - ([File: plugins/security-guidance/hooks/diffstate.py])

### Finding Description
The Stop hook computes its review set as `dirty_now ∩ changed_since(baseline_sha)` in `compute_v2_review_set` [1](#0-0) . `baseline_sha` is whatever `load_baseline_sha`/`consume_stop_state` returns from the on-disk session state [2](#0-1) , and that state is keyed **only by `session_id`** — `_state_key` never incorporates the repo path, `cwd`, or worktree, so nothing binds a stored `baseline_sha` to the repo/worktree it was captured in [3](#0-2) .

After a Stop fire successfully flags a vulnerability, `handle_stop_hook` immediately re-captures the baseline from the *current, still-vulnerable* working tree and writes it back as the new `baseline_sha`/`untracked_at_baseline` [4](#0-3) . `capture_git_baseline` uses `git stash create`, which snapshots HEAD plus all current uncommitted changes (including the still-unfixed dangerous file) [5](#0-4) .

On the very next Stop fire (same asyncRewake loop, or a later user turn once `UserPromptSubmit` re-captures a fresh baseline the same way [6](#0-5) ), the still-dirty dangerous file is dirty relative to `HEAD` (`tracked_dirty`) but *identical* to the new baseline commit, so `_git_name_only(repo, baseline_sha)` returns an empty (but valid, non-`None`) set for `changed_since` [7](#0-6) . The code explicitly treats empty-but-valid as "trust it" (only `None`/error falls back to `dirty_now` alone) [8](#0-7) , so `review_set = dirty_now & changed_since = ∅`. The dangerous file silently disappears from every subsequent review set — with zero further edits, zero commits, and zero privilege escalation, purely via the normal "write dangerous code → let Stop fire once → do nothing else" flow, or across turns via the same baseline-recapture in `UserPromptSubmit`.

Compounding this, because state is not scoped to repo/cwd, `Stop-hook diff selection` will happily diff whatever `cwd` the current hook invocation reports against a `baseline_sha` captured under a completely different `cwd`/worktree earlier in the session (e.g. the agent `cd`s into a different clone, worktree, or submodule mid-session) — nothing in `handle_stop_hook`, `compute_v2_review_set`, or `load_baseline_sha` validates that the stored baseline belongs to the repo currently being diffed.

### Impact Explanation
This is a Security-control bypass: the LLM-backed review/blocking mechanism (`analyze_code_security` + `exit(2)` continuation forcing) is the only gate on uncommitted, non-committed dangerous edits (commit-review is a separate, SHA-anchored surface and does not cover Write/Edit-only changes) [9](#0-8) . Once the baseline auto-advances past an unresolved dangerous file, that file is treated as "old" and is never surfaced to the reviewer again for the rest of the session (and across turns, since UPS re-baselines the same way), silently disabling the security check for that change without any attacker privilege beyond normal edit/turn behavior.

### Likelihood Explanation
Fully reachable with standard inputs: no admin rights, no key leakage, no parser bypass — just (1) a dangerous edit landing in the working tree, (2) one Stop fire that flags it (advances baseline), and (3) no further edit to that file. This is a routine, easily-triggered sequence (a non-compliant or prompt-injected agent turn, or simply an interrupted/ignored fix instruction) and is fully deterministic/repeatable given the code path shown above.

### Recommendation
Do not fold an already-flagged-but-unfixed file back into the "reviewed" baseline. Options: (a) only advance `baseline_sha` for files that were NOT part of `vulns`/`finding_snapshots` this fire (partial baseline advance), or (b) key the persisted findings/baseline on file content hash and keep re-including a file in `changed_since` until its content actually changes from the hash recorded at flag-time, or (c) at minimum, bind session state (`_state_key`) to the resolved `_git_toplevel(cwd)` so baseline/touched_paths can never be interpreted against a different repo/worktree than the one that produced them.

### Proof of Concept
Unit test around `compute_v2_review_set`/`capture_git_baseline` (both re-exported from `diffstate.py`):
```python
def test_unresolved_vuln_drops_out_of_review_set(tmp_git_repo):
    repo = tmp_git_repo  # has one commit
    baseline0 = capture_git_baseline(repo)          # clean baseline

    write(repo, "vulnerable.py", "os.system(user_input)")  # dangerous, uncommitted

    review1, *_ = compute_v2_review_set(repo, baseline0, head_at_capture=head(repo))
    assert any("vulnerable.py" in p for p in review1)      # correctly flagged first time

    # Simulate handle_stop_hook's post-flag baseline advance (no fix applied)
    baseline1 = capture_git_baseline(repo)

    review2, *_ = compute_v2_review_set(repo, baseline1, head_at_capture=head(repo))
    # Bug: vulnerable.py is still present, unfixed, and dirty vs HEAD,
    # but it disappears from the review set.
    assert not any("vulnerable.py" in p for p in review2)
```
Expected (post-fix) assertion: `review2` should still contain `vulnerable.py` until the finding is actually resolved (content changes or is explicitly cleared), demonstrating that the invariant "the review set must stay bound to the right repo, baseline, and touched paths" is currently violated.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L50-54)
```python
def load_baseline_sha(session_id):
    """Load the git baseline SHA from state."""
    def _load(state):
        return state.get("baseline_sha")
    return with_locked_state(session_id, _load)
```

**File:** plugins/security-guidance/hooks/diffstate.py (L163-201)
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

**File:** plugins/security-guidance/hooks/session_state.py (L25-34)
```python
def _state_key(session_id):
    # In CCR each user turn is a new CC process with a fresh session_id; the
    # remote session ID is stable across those restarts. Prefer it so the
    # pending-warnings sweep and any unprocessed touched_paths survive.
    key = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or session_id
    # The key becomes a filename component under the state dir. CC session ids
    # are UUIDs (sanitization is a no-op for them), but nothing in the hook
    # protocol guarantees that, so strip path separators and anything else
    # that could escape the state dir, and bound the length.
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key))[:128]
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L154-161)
```python
# Stop-hook git-diff review only — does NOT gate the commit/push reviews.
# Lets multi-agent / shared-worktree deployments keep the commit reviewer
# (anchored to a fixed SHA from the worker's own `git commit` stdout) while
# turning off the Stop-hook diff (anchored on baseline_sha…HEAD, which a
# sibling agent in the same worktree can move under us). The pre-existing
# ENABLE_CODE_SECURITY_REVIEW gate is shared between Stop and commit/push
# and stays for backwards compat as the all-LLM-review master switch.
ENABLE_STOP_REVIEW = os.environ.get("ENABLE_STOP_REVIEW", "1") != "0"
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L488-502)
```python
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
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1892-1916)
```python
        # Update baseline so next stop hook iteration only sees new changes
        new_sha = capture_git_baseline(cwd)
        new_untracked_baseline = _list_untracked(cwd) if new_sha else None

        def _record_fire(state):
            state["stop_hook_fire_count"] = fire_index
            state["stop_hook_fire_count_ts"] = _time.time()
            # Re-read under lock — the commit-review PostToolUse hook may have
            # appended findings since consume_stop_state snapshotted.
            # Dedupe on (filePath, category) — vulnerableCode includes diff
            # context lines that drift between fires, so byte-identical
            # matching let the same finding accumulate as "new" each fire.
            existing = [f for f in state.get("previous_findings", []) if isinstance(f, dict)]
            seen = {(f.get("filePath", ""), f.get("category", "")) for f in existing}
            for f in finding_snapshots:
                key = (f["filePath"], f["category"])
                if key not in seen:
                    seen.add(key)
                    existing.append(f)
            state["previous_findings"] = existing
            state["previous_findings_ts"] = _time.time()
            if new_sha:
                state["baseline_sha"] = new_sha
                state["untracked_at_baseline"] = new_untracked_baseline
        with_locked_state(session_id, _record_fire)
```

**File:** plugins/security-guidance/hooks/gitutil.py (L303-318)
```python
def _git_name_only(cwd, base, include_untracked=False):
    """Return the set of repo-root-relative paths that differ from `base`,
    or None if git failed (unresolvable ref, not a repo, timeout). Callers
    must distinguish None (error → don't trust as a filter) from set()
    (genuinely nothing changed). `-c core.quotePath=false -z` keeps non-ASCII
    and space-containing paths intact."""
    def _run(env):
        result = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "diff", "--name-only", "-z", base],
            cwd=cwd, capture_output=True, text=True, timeout=30,
            env=env,
        )
        if result.returncode != 0:
            debug_log(f"_git_name_only({base!r}) rc={result.returncode}: {result.stderr[:200]}")
            return None
        return {p for p in result.stdout.split("\0") if p}
```
