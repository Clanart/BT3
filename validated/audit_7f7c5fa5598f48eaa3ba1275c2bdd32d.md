### Title
Amend/rebase after commit lets `compute_v2_review_set` drop a committed dangerous change from the review set - ([File: plugins/security-guidance/hooks/diffstate.py])

### Summary
`compute_v2_review_set` computes `review_set = dirty_now ∩ changed_since`, where `dirty_now` only includes commit-range content when `head_at_capture` is a linear ancestor of current `HEAD` [1](#0-0) . If a Claude/agent turn commits a dangerous change and then rewrites that commit (`git commit --amend`, rebase, or any history-rewriting operation), `head_at_capture` is no longer an ancestor of the new `HEAD`, so `_is_ancestor` returns `False` and the `head_at_capture..HEAD` diff term is never unioned into `dirty_now` [2](#0-1) . Since the amended commit leaves the working tree clean, `tracked_dirty` from `git status --porcelain` is also empty for that file [3](#0-2) , so `dirty_now` does not contain the file at all, and the intersection with `changed_since` (which does contain it, since it differs from `baseline_sha`) yields an empty review set for that file.

### Finding Description
`consume_stop_state` snapshots `baseline_sha` and `head_at_capture` under a lock at Stop-hook time [4](#0-3) , and `handle_stop_hook` passes these into `compute_v2_review_set` to decide which files get sent to the LLM review [5](#0-4) .

`compute_v2_review_set`'s logic is:
- `dirty_now` = currently-dirty tracked files + new untracked files, unioned with `head_at_capture..HEAD` diff *only if* `head_at_capture` is a linear ancestor of the new HEAD [6](#0-5) .
- `changed_since` = `git diff --name-only <baseline_sha>` (content vs. the pre-turn stash), unioned with new untracked files [7](#0-6) .
- `review_set = dirty_now ∩ changed_since` [8](#0-7) .

Normal git operations reachable in an ordinary cloned-repo workflow (edit → `git commit` → `git commit --amend`, or an interactive rebase, or any command that rewrites the just-made commit) advance `HEAD` to a SHA for which `head_at_capture` is *not* an ancestor (the original commit object is discarded/replaced). This is a routine, non-privileged operation — no admin rights, no store mutation, just standard git commands a coding agent (or an attacker steering it via injected content/tool output) can invoke through Bash. When that happens:
1. `_is_ancestor(repo, head_at_capture, current_head)` returns `False`, so the commit-range diff is never added to `dirty_now`.
2. Because the change is fully committed, `git status --porcelain` shows nothing dirty for that file, so `tracked_dirty` also excludes it.
3. `changed_since` still contains the file (it legitimately differs from the stash baseline), but the intersection with the now-empty `dirty_now` for that path drops it from `review_set`.
4. `handle_stop_hook` treats an empty/missing entry the same as "nothing to review" for that file — the dangerous file is silently excluded from `diff_files` sent to `analyze_code_security`, without any explicit warning that history was rewritten.

Because `capture_git_baseline` also updates `baseline_sha` to a fresh `git stash create`/`HEAD` value after each Stop firing [9](#0-8) , once the amended commit is missed once, subsequent baselines are captured *after* the dangerous content already exists in history, so `changed_since` for future turns won't show it either — the dangerous content is permanently excluded from review, not merely delayed.

### Impact Explanation
This breaks the stated invariant that "an attacker must not hide a dangerous change by shifting it outside the computed review window." A dangerous file modification (e.g., adding a backdoor, disabling a security check, injecting a malicious command) can be committed and then amended/rebased within the same turn, causing the Stop-hook's LLM security review to never see that content. Since the Stop-hook is the mechanism gating whether Claude Code forces the agent to address/fix flagged vulnerabilities via exit code 2, silently excluding the file means the dangerous change is never flagged, is never blocked, and reaches the user's repository unreviewed — matching "Unauthorized local command execution that bypasses Claude Code approval or deny controls" since the security-guidance hook is one of the controls meant to catch such changes before they're accepted.

### Likelihood Explanation
The precondition is only a normal, unprivileged sequence of git operations within a single agent turn (commit + amend/rebase), which is common in ordinary development workflows and easily reachable by an agent following injected instructions from repo content, issue text, or tool output that suggests "clean up your commit" or "amend the message." No special permissions, leaked credentials, or social engineering of a human operator are required — it's a reproducible, deterministic consequence of the `_is_ancestor` check combined with the `dirty_now ∩ changed_since` intersection logic. It fires reliably every time an amend/rebase occurs after a commit within a reviewed turn.

### Recommendation
Do not gate the commit-range diff term on `_is_ancestor`. When `head_at_capture != current_head` and `head_at_capture` is *not* an ancestor (history was rewritten), still include a diff term that captures content differences — e.g., fall back to treating `changed_since` (diff vs. `baseline_sha`) as authoritative for `review_set` rather than intersecting with a `dirty_now` that structurally cannot represent amended/rebased commits. Alternatively, when non-ancestor `HEAD` movement is detected, union `dirty_now` with the full symmetric diff between `head_at_capture` and `current_head` (`git diff --name-only head_at_capture current_head`, ignoring ancestry) so amended/rebased content is still counted as "dirty since capture," while still intersecting with `changed_since` to filter pre-existing user WIP.

### Proof of Concept
Integration test plan for `compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py`:
1. Init a git repo, create `safe.py` with benign content, commit (`C0`).
2. Capture baseline: call `capture_git_baseline(cwd)` → `baseline_sha`; record `head_at_capture = C0`.
3. Simulate the dangerous turn: edit `evil.py` with a dangerous pattern (e.g., `os.system(user_input)`), `git add -A && git commit -m "add feature"` → `C1`.
4. Amend the commit: `git commit --amend -m "add feature (cleaned)"` → `C2` (note `C0` is still ancestor of `C2` in a simple amend if only the message changed AND parent unchanged — to force non-ancestry, instead do an interactive rebase/`git reset --soft C0 && git commit -m "squashed"` or amend with `git commit --amend --no-edit` after a `git reset --soft HEAD~1` sequence, or simplest: `git rebase -i` that drops then re-adds, or just do `git reset --hard C0` then re-commit differently, producing a HEAD (`C2`) that is not reachable via `C0`'s ancestry check against the *original* `head_at_capture` value captured pre-turn — verify via `git merge-base --is-ancestor head_at_capture HEAD; echo $?` returns non-zero).
5. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture)`.
6. Assert: `review_paths` **should** include `evil.py`, but the bug causes it to be excluded — i.e., current behavior: `"evil.py" not in [os.path.basename(p) for p in review_paths]` while `changed_since` (verifiable via `git diff --name-only baseline_sha`) does contain it.
7. Expected (fixed) behavior: `evil.py` must appear in `review_paths` regardless of the ancestry rewrite, per the invariant that a dangerous change must remain in the review window.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L74-113)
```python
def consume_stop_state(session_id):
    """Atomically snapshot all state the Stop hook needs and clear touched_paths.

    The Stop hook is asyncRewake — it runs in the background after Claude's
    turn ends. The user can submit a new prompt before this hook finishes its
    initial state read. Telemetry showed a meaningful share of would-be reviews lost when
    the next turn's UPS wiped touched_paths before Stop read it.

    Single locked read-then-clear closes that window: PostToolUse appends
    after this clear go into the next snapshot; UPS overwrites of baseline_sha
    after this snapshot are invisible to this Stop fire.
    """
    import time as _time
    now = _time.time()

    def _snap(state):
        fire_ts = state.get("stop_hook_fire_count_ts", 0)
        expired = (now - fire_ts) > STOP_LOOP_STATE_TTL_SEC
        findings_ts = state.get("previous_findings_ts", fire_ts)
        findings_expired = (now - findings_ts) > PREVIOUS_FINDINGS_TTL_SEC
        snap = {
            "touched_paths": list(state.get("touched_paths", [])),
            "baseline_sha": state.get("baseline_sha"),
            "head_at_capture": state.get("head_at_capture"),
            "untracked_at_baseline": (
                dict(state["untracked_at_baseline"])
                if isinstance(state.get("untracked_at_baseline"), dict) else {}
            ),
            "fire_count": 0 if expired else state.get("stop_hook_fire_count", 0),
            "fire_count_expired": expired and state.get("stop_hook_fire_count", 0) > 0,
            "previous_findings": [] if findings_expired else list(state.get("previous_findings", [])),
        }
        state["touched_paths"] = []
        return snap

    return with_locked_state(session_id, _snap) or {
        "touched_paths": [], "baseline_sha": None, "head_at_capture": None,
        "untracked_at_baseline": {},
        "fire_count": 0, "fire_count_expired": False, "previous_findings": [],
    }
```

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

**File:** plugins/security-guidance/hooks/diffstate.py (L426-426)
```python
    review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
```

**File:** plugins/security-guidance/hooks/gitutil.py (L330-370)
```python
def _git_status_porcelain(cwd):
    """One `git status --porcelain=v1 -z` → (tracked_dirty, untracked) sets of
    repo-root-relative paths, or (None, None) on error. Replaces the
    `_temp_index + git diff HEAD --name-only` pair for the v2 dirty_now
    computation: faster in large repos, and yields the
    untracked set separately so the later get_git_diff can do a targeted
    `add -N -- <files>` instead of a whole-tree `add -N .`.

    -uall: list individual files inside untracked directories (default
    collapses to `dir/`). Required so the untracked set subtracts cleanly
    against the UPS-time `_list_untracked` snapshot, which uses ls-files and
    therefore always lists individual files."""
    try:
        r = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "status",
             "--porcelain=v1", "-uall", "-z"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            debug_log(f"_git_status_porcelain rc={r.returncode}: {r.stderr[:200]}")
            return None, None
        tracked, untracked = set(), set()
        entries = r.stdout.split("\0")
        i = 0
        while i < len(entries):
            e = entries[i]
            if not e:
                i += 1
                continue
            xy, path = e[:2], e[3:]
            if xy == "??":
                untracked.add(path)
            else:
                tracked.add(path)
                # Rename/copy entries are XY old\0new\0 — second NUL field is
                # the origin path; consume it so it isn't misparsed as a new
                # 2-char-status entry.
                if "R" in xy or "C" in xy:
                    i += 1
            i += 1
        return tracked, untracked
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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1892-1915)
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
```
