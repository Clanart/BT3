### Title
Stop-hook security review permanently drops low-priority files from vulnerability scanning after unconditionally consuming their tracked state - ([File: plugins/security-guidance/hooks/security_reminder_hook.py])

### Summary
The `security-guidance` plugin's Stop hook tracks per-turn edited files in a `touched_paths` list and unconditionally clears ("consumes") that tracked state at the start of every Stop-hook run, before it is known whether the subsequent LLM security review will actually cover the *entire* set of touched files. When the file set exceeds `MAX_DIFF_FILES`, the hook silently drops the lower-priority files from the review (`_prioritize_diff_files`) — but the tracking state for those dropped files has already been erased and the git baseline is advanced past them, permanently removing them from future security-review coverage. This mirrors the Ajna `moveLiquidity` bug class: a tracking structure (`positionIndexes` / here `touched_paths` + `baseline_sha`) is removed/advanced unconditionally even though the underlying operation (`moveQuoteToken` / here the LLM diff review) only partially completed, permanently orphaning the remainder.

### Finding Description
`consume_stop_state` snapshots and unconditionally clears `touched_paths` under a single lock at the very start of `handle_stop_hook`: [1](#0-0) 

Only a small number of early-exit paths in `handle_stop_hook` call `restore_unreviewed_stop_state` to put the consumed paths back (e.g., when `api.anthropic.com` is unreachable): [2](#0-1) 

However, once the diff is computed and the file count exceeds `MAX_DIFF_FILES`, the hook prioritizes and truncates the file list, dropping the rest ("lower-risk") from review — without restoring their tracking state: [3](#0-2) 

If the (partial) review that runs on the retained files finds no issues, the code falls into the "no security issues found" branch, which does **not** restore state for the dropped files and does **not** advance the baseline either — but because `touched_paths` was already cleared by `consume_stop_state` and never repopulated for the dropped files, the *next* `UserPromptSubmit` hook's "preserve old baseline" guard (`if state.get("touched_paths") and state.get("baseline_sha")`) sees an empty `touched_paths` and advances `baseline_sha` to a fresh capture: [4](#0-3) 

That baseline advance closes the diff window: any subsequent Stop-hook diff is computed relative to the new baseline, so code the model wrote in the dropped/unreviewed files during the prior turn falls before the new baseline and is never included in a future diff-based review again (unless the same files are dirtied a second time, and even then only the incremental change on top of the already-unreviewed content is diffed).

### Impact Explanation
This is a security-control bypass in the automated code-security-review hook (`security-guidance`), which the plugin itself treats as a trust boundary where "the model is the attacker, the user is the victim" (explicitly called out in the plugin's own adversarial-verification prompt). Because the attacker here is the Claude Code agent/model being reviewed (or a user steering it), an unprivileged or adversarial workflow that touches more than `MAX_DIFF_FILES` files in a single turn can guarantee that some files it modifies are silently excluded from the security review, with no error, warning, or record that those files were skipped — and the exclusion becomes *permanent* once the baseline rolls forward. This lets vulnerable or malicious code changes (e.g., a backdoor introduced in a low-priority file) permanently evade the Stop-hook's automatic vulnerability-detection safety net.

### Likelihood Explanation
Requires a turn touching more than `MAX_DIFF_FILES` distinct files (a bulk refactor, generated code, or a large multi-file change is a realistic and easily attacker-steerable trigger), and the retained/reviewed subset must return no findings (or the vulnerability must be specifically placed in one of the deprioritized files). Given the prioritization heuristic is deterministic (`_prioritize_diff_files` ranks by risk-path tokens), an attacker who understands the ranking can deliberately place malicious content in files that will be deprioritized (e.g., non-"risk-token" paths), making exploitation reasonably likely for a knowledgeable actor.

### Recommendation
Do not advance `baseline_sha` or let `touched_paths` clearance become final for files that were dropped due to the `MAX_DIFF_FILES` cap. Specifically:
1. In the `len(diff_files) > MAX_DIFF_FILES` branch, re-insert the dropped files' paths back into `touched_paths` (mirroring `restore_unreviewed_stop_state`) so they remain queued for the next Stop-hook fire instead of being silently discarded from tracking.
2. Ensure the "no security issues found" path and any other early return past the diff-computation stage also restores tracking for any files that were part of the original touched set but not actually included in `diff_files` sent to the LLM.
3. Consider logging/emitting a metric whenever files are dropped due to the cap, so operators have visibility into coverage gaps instead of silent, unbounded exclusion.

### Proof of Concept
Conceptual sequence (cannot be dynamically executed via static code search, but derivable directly from the control flow):
1. In a single turn, an agent/user edits/creates more than `MAX_DIFF_FILES` files, one of which (`evil.py`) contains an intentionally obfuscated vulnerability and is placed under a path that `_prioritize_diff_files`'s risk-token heuristic ranks low (e.g., a generic `utils/` or `data/` file rather than `auth/`, `admin/`, etc.).
2. `PostToolUse` calls `record_touched_path` for every edited file, including `evil.py`. [5](#0-4) 
3. On `Stop`, `consume_stop_state` clears `touched_paths` for ALL files (including `evil.py`) up front.
4. `handle_stop_hook` computes `diff_files`, finds it exceeds `MAX_DIFF_FILES`, and calls `_prioritize_diff_files`, which drops `evil.py` from the set sent to `analyze_code_security`. [6](#0-5) 
5. The LLM reviews only the retained files and (assume) finds nothing; the "no issues found" branch is taken without restoring `evil.py`'s tracking state.
6. On the next `UserPromptSubmit`, since `touched_paths` is empty, the baseline advances past `evil.py`'s vulnerable commit. [7](#0-6) 
7. `evil.py`'s vulnerability is now permanently outside any future Stop-hook diff window and is never flagged by the automated security review, despite having been "tracked" at step 2.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L57-71)
```python
def record_touched_path(session_id, file_path):
    """Append a file path to the touched_paths list (deduped, capped at 200).

    Stop is the consumer and clears under the same lock it reads with; UPS
    no longer wipes. The cap is a defensive bound for sessions where Stop
    never fires (disabled mid-session, abort) — git diff naturally filters
    stale paths so over-retention is harmless, just wasteful.
    """
    def _record(state):
        paths = state.setdefault("touched_paths", [])
        if file_path not in paths:
            paths.append(file_path)
            if len(paths) > 200:
                del paths[:len(paths) - 200]
    with_locked_state(session_id, _record)
```

**File:** plugins/security-guidance/hooks/diffstate.py (L74-107)
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
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L479-503)
```python
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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1748-1790)
```python
    v2_metrics = {}

    def _skip(reason, restore=False, **extra):
        if restore:
            restore_unreviewed_stop_state(session_id, touched_paths, snap_baseline)
        # CC truncates metrics to 10 keys by
        # insertion order. v2_metrics (3) must precede sweep (3) so the v2
        # diagnostics survive when extra adds touched_paths_count + ip_* keys.
        emit_metrics({
            "skipped": True, "skip_reason": reason, "fire_index": fire_count + 1,
            "diff_strategy_v2": True,
            **v2_metrics, **extra, **sweep,
        })
        sys.exit(0)

    # Limit stop hook firings per asyncRewake loop to prevent infinite loops.
    # fire_count auto-expires after STOP_LOOP_STATE_TTL_SEC so a stale count
    # from a prior turn doesn't block this one.
    if MAX_STOP_HOOK_FIRINGS > 0 and fire_count >= MAX_STOP_HOOK_FIRINGS:
        debug_log(f"Stop hook: already fired {fire_count} times (max {MAX_STOP_HOOK_FIRINGS}), skipping")
        _skip(2)

    if not ENABLE_CODE_SECURITY_REVIEW or not HAS_API_CREDENTIALS:
        debug_log("Stop hook: LLM review disabled or no API credentials")
        _skip(3)

    # Stop-hook-only kill switch — placed after consume_stop_state so
    # touched_paths is still cleared each turn (a disabled Stop hook that
    # never consumed state would accumulate stale paths) and after the sweep
    # so pattern-warning efficacy metrics still emit. The commit/push reviews
    # have their own gates (ENABLE_COMMIT_REVIEW / ENABLE_CODE_SECURITY_REVIEW).
    if not ENABLE_STOP_REVIEW:
        debug_log("Stop hook: ENABLE_STOP_REVIEW=0")
        # 50+ for opt-out skips that aren't push-sweep (which owns 40-49).
        _skip(50)

    if not ensure_anthropic_reachable():
        debug_log("Stop hook: api.anthropic.com unreachable")
        _skip(10, restore=True)

    if not cwd:
        debug_log("Stop hook: no cwd")
        _skip(4)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1829-1844)
```python
    # Mirror commit-review: hard-bail only on pathological diffs (>300 files,
    # usually a bad baseline), otherwise prioritize by security-risk path
    # tokens and review the top MAX_DIFF_FILES. Stop is the only surface for
    # uncommitted edits; the old hard-skip at >30 files dropped the 31-300
    # bucket entirely, which is where cross-file source→sink vulns hide.
    # _cap_files_for_prompt already bounds bytes downstream.
    _stop_dropped = 0
    if len(diff_files) > 10 * MAX_DIFF_FILES:
        debug_log(f"Stop hook: pathological diff ({len(diff_files)} files > "
                  f"{10 * MAX_DIFF_FILES}), skipping")
        _skip(8, diff_files_count=len(diff_files))
    if len(diff_files) > MAX_DIFF_FILES:
        diff_files, _stop_dropped = _prioritize_diff_files(
            diff_files, MAX_DIFF_FILES)
        debug_log(f"Stop hook: prioritized to {len(diff_files)} files "
                  f"(dropped {_stop_dropped} lower-risk)")
```
