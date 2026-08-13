## Finding: Race condition in security-guidance hook state locking disables atomicity on Windows

The reported class of bug — concurrent operations racing against a shared, unsynchronized data structure, where the "happy path" atomicity guarantee silently degrades under concurrency — has a direct analog in this repo's `plugins/security-guidance/hooks/session_state.py`.

### Root cause

`with_locked_state()` is the single synchronization primitive used by every "atomic" security-gating check in the plugin (`atomic_check_and_mark_warning`, `atomic_check_counter`, `atomic_check_rate_limit`, `consume_stop_state`, `_dedup_against_state`, `_record_fire`, etc.). On platforms where `fcntl` is unavailable (Windows), the function explicitly skips locking entirely: [1](#0-0) 

```python
if fcntl is None:
    # No file locking available (Windows) — run without locking
    state = load_state(session_id)
    result = callback(state)
    save_state(session_id, state)
    return result
```

This is a read-modify-write with no exclusion at all when `fcntl is None`, i.e., on Windows every caller of this helper.

### Why this matches the reported bug class

The report describes concurrent `Publish`/`Broadcast` vs. shutdown racing on an unprotected map, producing unsound outcomes beyond the "acceptable" error case (lost delivery, corrupted state, bypass of intended guarantees). The comments throughout `security_reminder_hook.py` and `diffstate.py` establish that this exact kind of concurrency is expected and intentional in this codebase: the `Stop` hook is documented as `asyncRewake` — it runs in the background while the next `UserPromptSubmit`/`PostToolUse` hook can fire concurrently in a separate subprocess: [2](#0-1) 

The `commit-review` PostToolUse handler and the `Stop` handler are also documented as racing each other against the same state file (`previous_findings`, `pending_warnings`, rate-limit buckets, fire counters): [3](#0-2) 

All of this careful "read fresh state, compute race delta, dedupe" logic assumes `with_locked_state` provides real mutual exclusion. On Windows it provides none — the entire correctness argument built on top of `with_locked_state` (rate limiting, dedup, fire-count capping, findings bookkeeping) collapses into an unprotected read-modify-write race, exactly analogous to the unprotected map/channel race in the reported broker bug.

### Impact

On Windows, concurrent hook subprocesses (`Stop` running in the background while a new turn's `UserPromptSubmit`/`PostToolUse` fires, or parallel tool calls triggering concurrent `PostToolUse` invocations) can interleave read-modify-write cycles on the shared JSON state file:
- `atomic_check_rate_limit` / `atomic_check_counter` can lose increments, letting call counts exceed configured caps (rate-limit/cost-control bypass).
- `_record_fire` / `_record_findings` writes to `previous_findings` can be clobbered by a concurrent writer, causing findings to be silently lost — meaning the security-guidance mechanism that would otherwise force `exit(2)` and block a vulnerable diff from proceeding can instead see stale/incomplete state and skip the block.
- `stop_hook_fire_count` bookkeeping used to cap the `asyncRewake` loop can be corrupted, defeating the intended bound.

This is a "hook bypass" style trust-boundary issue: the plugin's core enforcement primitive (force-continue via `exit(2)` on unresolved findings) relies on state that is not actually race-safe on a documented, in-scope platform.

### Recommendation

Provide a real cross-platform lock in `with_locked_state` (e.g., `msvcrt.locking` on Windows, or a lock-file-based advisory lock implemented consistently across platforms) instead of silently degrading to no synchronization when `fcntl` is unavailable. Alternatively, document and test that Windows callers must serialize state access some other way, and add regression tests that simulate concurrent `with_locked_state` callers to verify no lost updates.

### Citations

**File:** plugins/security-guidance/hooks/session_state.py (L118-138)
```python
def with_locked_state(session_id, callback):
    """
    Execute callback with exclusive access to the state file.
    The callback receives the state dict and can modify it in place.
    State is saved after the callback returns.
    Returns the callback's return value.
    """
    lock_file = get_lock_file(session_id)
    state_dir = os.path.dirname(lock_file)

    try:
        os.makedirs(state_dir, exist_ok=True)
    except OSError:
        pass

    if fcntl is None:
        # No file locking available (Windows) — run without locking
        state = load_state(session_id)
        result = callback(state)
        save_state(session_id, state)
        return result
```

**File:** plugins/security-guidance/hooks/diffstate.py (L74-85)
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
```

**File:** plugins/security-guidance/hooks/llm.py (L685-697)
```python
def _dedup_against_state(session_id: str, vulns: List[Dict[str, Any]],
                         prompted: set) -> Tuple[List[Dict[str, Any]], int]:
    """Drop vulns that a CONCURRENT asyncRewake hook wrote to
    previous_findings while this hook's LLM was running.

    `prompted` is the (filePath, category) set the LLM was already told about
    via the prev_section prompt block. The LLM is instructed to only re-flag
    those if the attempted fix is incomplete, so a re-flag of a `prompted`
    entry is an intentional "fix didn't work" verdict and MUST pass through.
    We therefore re-read state now and only filter the race delta —
    (seen_now − prompted) — i.e. findings the LLM was never told about
    because they were written mid-review by the other hook.
    Returns (surviving_vulns, n_dropped).
```
