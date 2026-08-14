### Title
Lock file creation follows symlinks due to missing `O_NOFOLLOW` in `os.open()` - (File: plugins/security-guidance/hooks/session_state.py)

### Summary
`get_lock_file()` derives a fully predictable lock-file path from `_state_key(session_id)`, and `with_locked_state()` opens that path with `os.open(lock_file, os.O_RDWR | os.O_CREAT)` with no `O_NOFOLLOW`, no `O_EXCL`, and no pre-check for an existing symlink. An attacker who can write into the state directory (shared/predictable `HOME`, or a shared `SECURITY_WARNINGS_STATE_DIR` in a multi-tenant environment) can pre-plant a symlink at the computed lock path pointing to an arbitrary file, causing the victim's Claude Code process to open and `flock()` that target file instead of a legitimate lock file.

### Finding Description
`_state_key()` sanitizes `session_id`/`CLAUDE_CODE_REMOTE_SESSION_ID` into a filename component but performs no defense against the *path itself* being replaced by a symlink [1](#0-0) . `get_lock_file()` builds the lock path deterministically from that key under `SECURITY_WARNINGS_STATE_DIR` (default `~/.claude/security`) [2](#0-1) .

`with_locked_state()` then does:
```python
lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
``` [3](#0-2) 

`os.open` with only `O_RDWR | O_CREAT` follows an existing symlink at `lock_file` transparently — there is no `O_NOFOLLOW`, `O_EXCL`, or `os.path.islink()` guard beforehand. If the attacker has already created a symlink at the predictable/computed path (feasible when `session_id`/remote session id is known or guessable, or when the state dir is shared), the victim's `os.open` call opens the symlink target with `O_RDWR`, and `fcntl.flock` locks that target file's inode.

No write actually occurs through `lock_fd` (only `flock`/`close` are performed on it — data is written to a separate `state_file` via `save_state()`), and `O_CREAT` without `O_TRUNC` means no truncation of the symlink target on open. So the concrete effect is opening and exclusively `flock()`-ing an attacker-chosen file, not content disclosure or truncation.

### Impact Explanation
Scoped impact is limited to unintended `open()`/`flock()` of an attacker-chosen file path outside the intended state directory (workspace confinement violation). Because the code never reads from or writes to `lock_fd` beyond locking, there is no direct data exfiltration or truncation from this path. Possible consequences are: unwanted exclusive locking of a victim-critical file (denial-of-service against another process's use of `flock` on that inode), or an unexpected blocking/side-effecting open if the symlink target is a special file (FIFO/device). This does not constitute privilege escalation since `os.open` still enforces normal filesystem permission checks for the process's own UID.

### Likelihood Explanation
Requires the attacker to already have write access to the plugin's state directory (`SECURITY_WARNINGS_STATE_DIR` or `~/.claude/security`) and to predict/know the `session_id` (or `CLAUDE_CODE_REMOTE_SESSION_ID`) used to derive the filename via `_state_key()`. This is a narrow precondition — typically only realistic in shared/multi-tenant hosts with a common or predictable `HOME`, or where the state dir is group/world-writable. In the common single-user desktop deployment this precondition does not hold.

### Recommendation
Open the lock file with `O_NOFOLLOW` (and ideally verify with `os.lstat`/`os.path.islink` before opening) to reject symlinks, e.g. `os.open(lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)`, and handle the resulting `OSError` (`ELOOP`) by failing closed. Additionally, create the state directory with restrictive permissions (`0o700`) so unrelated users cannot plant symlinks there.

### Proof of Concept
Integration test:
1. Set `SECURITY_WARNINGS_STATE_DIR` to a temp dir.
2. Compute `lock_path = get_lock_file("attacker-session")`.
3. Create a canary file `canary.txt` with known content/mode outside the state dir.
4. `os.symlink(canary_path, lock_path)` before calling `with_locked_state("attacker-session", lambda s: s)`.
5. Assert: `canary.txt` contents and mode are unchanged, and that `os.open` raised/rejected due to `O_NOFOLLOW` (post-fix) rather than silently succeeding by following the symlink (pre-fix, `os.readlink`-detectable target being opened).

### Citations

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

**File:** plugins/security-guidance/hooks/session_state.py (L43-46)
```python
def get_lock_file(session_id):
    """Get session-specific lock file path."""
    state_dir = os.environ.get("SECURITY_WARNINGS_STATE_DIR", os.path.expanduser("~/.claude/security"))
    return os.path.join(state_dir, f"security_warnings_state_{_state_key(session_id)}.lock")
```

**File:** plugins/security-guidance/hooks/session_state.py (L140-148)
```python
    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        state = load_state(session_id)
        result = callback(state)
        save_state(session_id, state)
        return result
```
