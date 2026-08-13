### Title
Missing `session_id` Collapses To A Shared `"default"` State-File Key, Causing Security-Reminder Hook State (Warnings/Rate-Limits/Findings) To Collide Across Unrelated Sessions - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
The C4 report's root cause is a shared/reused identifier (`totalSupply()`-derived `tokenId`) causing unrelated state (positions) to collide, which breaks an invariant the contract relies on for correctness/uniqueness. The closest analog I can substantiate in this repo is in the `security-guidance` plugin's hook state management, where the per-session state key falls back to the literal string `"default"` whenever `session_id` is absent from the hook's JSON input, causing state from unrelated Claude Code sessions to be persisted to, and read from, the same file.

### Finding Description
`main()` in `security_reminder_hook.py` reads `session_id` from the hook's stdin payload with a hardcoded fallback: [1](#0-0) 

That value is passed straight through to `_state_key()`, which is the sole thing that scopes state files to a session: [2](#0-1) 

`get_state_file()`/`get_lock_file()` build the on-disk path purely from `_state_key(session_id)`, with no additional entropy (no PID, no hook invocation nonce, no cwd/repo hash): [3](#0-2) 

This state file backs several security-relevant, atomically-guarded gates: whether a warning has already been shown (`atomic_check_and_mark_warning`), whether a per-session counter has been exhausted (`atomic_check_counter`), and a rolling-window rate limit on LLM-based commit/push reviews (`atomic_check_rate_limit`): [4](#0-3) [5](#0-4) 

If two or more Claude Code processes (e.g., concurrent sessions in different repos/worktrees, or any invocation where the hook harness fails to populate `session_id` in the JSON payload) both fall through to the `"default"` key, their state is merged into one shared file under `fcntl` locking. This is structurally identical to the XDEFI bug: an identifier that is supposed to uniquely scope a "position" (there: an NFT position keyed by `totalSupply()+1`; here: a session's warning/rate-limit/finding state keyed by `session_id`) collapses onto the same value for two logically distinct entities, so operations meant to be independent silently interact.

### Impact Explanation
Because `atomic_check_and_mark_warning` and `atomic_check_rate_limit` gate whether the security-reminder/commit-review LLM analysis actually fires (see the Stop-hook and commit-review paths that call `analyze_code_security` and then `sys.exit(2)` to force Claude to continue and fix flagged issues): [6](#0-5) 

a collision on the `"default"` key means Session B can silently "consume" Session A's warning/rate-limit budget (or vice versa), causing the security-guidance hook to skip its LLM review/warning for a session that never actually triggered it. This is a hook-bypass in the sense that the review/blocking mechanism this plugin relies on (`exit code 2` forcing Claude to fix flagged vulnerabilities) can be silently suppressed for one session because unrelated state from a different session/user already marked the same warning/rate-limit key as consumed. It does not grant arbitrary command execution by itself, but it degrades or defeats a security control (commit/push code-review gating and warning de-duplication) across a trust boundary between concurrently running, logically independent Claude Code sessions.

### Likelihood Explanation
This requires the hook harness to omit `session_id` from the JSON payload it feeds the hook via stdin — normal Claude Code hook invocations are documented to always include `session_id`: [7](#0-6) 

I could not find, within this index, any code path in the (out-of-scope, closed-source) core CLI that would omit `session_id`, nor could I confirm this has ever occurred in practice — the fallback in `security_reminder_hook.py` line 2044 is written defensively ("`input_data.get("session_id", "default")`"), suggesting the author anticipated but never observed this as guaranteed-populated. Likelihood is therefore low/uncertain: it depends on an edge case in the (unindexed) hook-dispatch machinery rather than a reachable, user-triggerable code path within this plugin alone. This is a meaningfully weaker claim than the original XDEFI finding, which had a fully reachable, deterministic PoC within the audited contract itself.

### Recommendation
Remove the silent `"default"` fallback and instead fail closed (skip the hook run with a debug log) or derive a locally-unique fallback key (e.g., combining PID, start timestamp, and a random nonce) so that two processes never share a state file when `session_id` is genuinely absent. Additionally, consider adding a secondary uniqueness check (e.g., embedding the `session_id` value inside the state JSON itself and verifying it matches on load) so a `_state_key` collision is detected and treated as "no prior state" rather than merged.

### Proof of Concept
Not directly executable/provable from the indexed code alone — the collision requires an external precondition (hook input missing `session_id`) that I could not confirm is reachable from within this repository's indexed contents. Conceptually:
1. Start two Claude Code sessions concurrently (Session A in repo X, Session B in repo Y), both without `CLAUDE_CODE_REMOTE_SESSION_ID` set.
2. If the hook harness ever invokes `security_reminder_hook.py` for either session with a payload lacking `session_id`, both fall to `_state_key("default")`, i.e., the same file `security_warnings_state_default.json`.
3. Session A's `git commit` triggers `atomic_check_rate_limit(session_id="default", key="commit-review", ...)`, consuming one slot of the shared rolling window.
4. Session B's subsequent `git commit`, also resolving to `"default"`, sees the shared counter already elevated and can be rate-limited/skipped even though Session B individually never approached `MAX_COMMIT_REVIEWS_PER_HOUR`.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L237-269)
```python
def atomic_check_and_mark_warning(session_id, warning_key):
    """
    Atomically check if a warning has been shown and mark it as shown if not.
    Returns True if this is the first time seeing this warning (should show it),
    False if it was already shown (should skip it).
    """
    def _check(state):
        warnings = state["shown_warnings"]
        if warning_key in warnings:
            return False
        warnings.append(warning_key)
        return True

    result = with_locked_state(session_id, _check)
    return result if result is not None else True

def atomic_check_counter(session_id, counter_key, max_count):
    """
    Atomically check if a counter has reached its limit and increment if not.
    Returns True if the counter is below max_count (should proceed),
    False if it has reached or exceeded max_count (should skip).
    """
    def _check(state):
        counters = state.get("counters", {})
        current = counters.get(counter_key, 0)
        if current >= max_count:
            return False
        counters[counter_key] = current + 1
        state["counters"] = counters
        return True

    result = with_locked_state(session_id, _check)
    return result if result is not None else True
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L271-304)
```python
def atomic_check_rate_limit(session_id, key, max_per_window, window_s):
    """Rolling-window rate limit: allow at most `max_per_window` calls per
    `window_s` seconds, per (session_id, key).

    Returns (allowed: bool, count_in_window: int). count_in_window is the
    post-decision count (i.e., includes this call if allowed) so callers can
    emit it directly as a telemetry gauge.

    Replaces session-lifetime `atomic_check_counter` for commit-review and
    push-sweep. Telemetry showed a small but persistent share of sessions hit
    the lifetime cap, and those were multi-day persistent sessions that then
    lost coverage for many subsequent commits — not burst abusers. A rolling
    hour keeps the same cost ceiling for any 1h window while letting long
    sessions regain coverage.

    State key: rate_limits: {"<key>": [ts, ts, ...]}. Timestamps are pruned
    on every call so the list is bounded by max_per_window; no migration
    needed from the old `counters` dict — different key.
    """
    import time as _time
    now = _time.time()
    cutoff = now - window_s

    def _check(state):
        buckets = state.setdefault("rate_limits", {})
        ts_list = buckets.get(key, [])
        # Prune; tolerate non-numeric junk from a corrupted state file.
        ts_list = [t for t in ts_list if isinstance(t, (int, float)) and t > cutoff]
        if len(ts_list) >= max_per_window:
            buckets[key] = ts_list
            return False, len(ts_list)
        ts_list.append(now)
        buckets[key] = ts_list
        return True, len(ts_list)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1854-1947)
```python
    # Stop hook is single-shot only. Agentic review is wired into
    # handle_commit_review_posttooluse (PostToolUse on `git commit`) — commits
    # are slower-OK and benefit from the deeper context-reading loop.
    concrete_guidance, vulns = analyze_code_security(
        diff_files, is_diff=True, previous_findings=previous_findings
    )
    # NOTE: analyze_security_concerns disabled — it produces too many false positives
    # on pre-existing patterns in starter code. The concrete vulnerability analysis
    # is more precise and has severity filtering (high/critical only).

    stop_review_elapsed = _time.time() - stop_review_start
    debug_log(f"Stop hook: LLM reviews took {stop_review_elapsed:.1f}s total")

    review_ms = int(stop_review_elapsed * 1000)
    fire_index = fire_count + 1

    # Late dedup: drop only what a concurrent commit-review wrote while our
    # LLM ran. Anything already in `previous_findings` (the consume_stop_state
    # snapshot) that the LLM re-flagged is an intentional "fix incomplete"
    # verdict and passes through.
    if vulns:
        vulns, n_deduped = _dedup_against_state(
            session_id, vulns, prompted=_finding_keys(previous_findings)
        )
        if n_deduped and not vulns:
            debug_log("Stop hook: all findings already delivered by commit-review")
            _skip(35, deduped=n_deduped, review_ms=review_ms)
        concrete_guidance = _format_vulns_guidance(vulns)

    if concrete_guidance:
        finding_snapshots = [
            {
                "filePath": v.get("filePath", ""),
                "category": v.get("category", "Unknown"),
                "vulnerableCode": v.get("vulnerableCode", ""),
            }
            for v in vulns
        ]
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

        if new_sha:
            debug_log(f"Updated git baseline after stop hook: {new_sha[:12]}")

        sev = {"critical": 0, "high": 0, "medium": 0}
        for v in vulns:
            s = v.get("severity", "medium")
            if s in sev:
                sev[s] += 1
        # 8 base keys + at most 2 sweep keys = 10 (cap). Drop the mask here.
        # untracked_baseline_n is the signal for whether the UPS-time
        # untracked-snapshot capture actually ran.
        sweep_trimmed = {k: v for k, v in sweep.items() if k != "warn_unresolved_mask"}
        emit_metrics({
            "vulns_found": len(vulns),
            "untracked_baseline_n": len(untracked_at_baseline),
            "diff_strategy_v2": True,
            "critical_count": sev["critical"],
            "high_count": sev["high"],
            "files_reviewed": len(diff_files),
            "touched_paths_count": len(touched_paths),
            "review_ms": review_ms,
            "fire_index": fire_index,
            **({"diff_truncated": llm._last_review_truncated_bytes}
               if llm._last_review_truncated_bytes else {}),
            **sweep_trimmed,
        }, rewake_summary=_format_vulns_summary(vulns))

        # Exit code 2 with stderr forces Claude to continue and fix
        sys.stderr.write(PROVENANCE_BANNER + "\n\n" + concrete_guidance + CONTINUATION_SUFFIX + "\n")
        sys.exit(2)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2044-2044)
```python
    session_id = input_data.get("session_id", "default")
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

**File:** plugins/security-guidance/hooks/session_state.py (L37-46)
```python
def get_state_file(session_id):
    """Get session-specific state file path."""
    state_dir = os.environ.get("SECURITY_WARNINGS_STATE_DIR", os.path.expanduser("~/.claude/security"))
    return os.path.join(state_dir, f"security_warnings_state_{_state_key(session_id)}.json")


def get_lock_file(session_id):
    """Get session-specific lock file path."""
    state_dir = os.environ.get("SECURITY_WARNINGS_STATE_DIR", os.path.expanduser("~/.claude/security"))
    return os.path.join(state_dir, f"security_warnings_state_{_state_key(session_id)}.lock")
```

**File:** plugins/plugin-dev/skills/hook-development/SKILL.md (L300-312)
```markdown
## Hook Input Format

All hooks receive JSON via stdin with common fields:

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.txt",
  "cwd": "/current/working/dir",
  "permission_mode": "ask|allow",
  "hook_event_name": "PreToolUse"
}
```
```
