### Title
Stop-hook `compute_v2_review_set` silently drops committed changes when HEAD moves sideways, letting malicious diffs bypass LLM security review - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` only picks up *committed* changes when the turn's `head_at_capture` is a strict ancestor of the current `HEAD` (a linear advance). If a Claude session (already able to run arbitrary `git` via the Bash tool, e.g. under prompt injection) commits a malicious change and then performs an ordinary sideways git operation such as `git checkout <other-ref>` or `git reset --hard <unrelated-commit>`, the working tree becomes clean against the *new* HEAD, `_is_ancestor(head_at_capture, current_head)` is false, and the malicious commit's files never enter `dirty_now`. Because `review_set = dirty_now ∩ changed_since`, the malicious content is excluded even though `changed_since` (diff vs. the turn's stash baseline) would still show it, so the Stop hook's LLM vulnerability scan never sees it.

### Finding Description
`compute_v2_review_set(cwd, baseline_sha, head_at_capture, untracked_at_baseline)` in `plugins/security-guidance/hooks/diffstate.py` computes: [1](#0-0) 

- `tracked_dirty`/`untracked` come from `git status --porcelain` — i.e. only what's dirty *relative to the current HEAD/index right now*.
- Committed-but-not-currently-dirty changes are only added via `_git_name_only(repo, f"{head_at_capture}..HEAD")`, and only when `_is_ancestor(repo, head_at_capture, current_head)` is true — a strictly linear HEAD advance.
- The final `review_set` is `dirty_now & changed_since` (intersection with the diff against `baseline_sha`), explicitly to suppress "checkout/pull ballooning."

If, within one turn, the agent (a) commits a malicious change on top of `head_at_capture`, then (b) performs a normal git operation that moves HEAD sideways/backwards (`git checkout <other-branch>`, `git reset --hard <ref>`, switching worktrees, etc.), then:
- The working tree is clean vs. the new HEAD → `tracked_dirty` is empty.
- `head_at_capture` is no longer an ancestor of the new `current_head` → the `head_at_capture..HEAD` diff is never unioned in.
- `dirty_now` ends up empty (or missing the malicious file), so intersecting with `changed_since` drops the malicious file from `review_set` regardless of what `changed_since` contains.

`handle_stop_hook` then hits the empty-review-set skip path and exits without ever calling the LLM reviewer: [2](#0-1) 

The malicious commit remains in the repository's object graph (reachable by its branch ref or reflog) but is never diffed to the security reviewer. On the next `UserPromptSubmit`, `capture_git_baseline` re-stashes the *current* (now-clean) working tree state as the new baseline: [3](#0-2) 

so from that point on the plugin has no memory that the malicious content was ever introduced — if it is later merged/checked-out back in without further edits, no new diff appears against the fresh baseline and it still won't be reviewed by the Stop hook. Nothing in `handle_stop_hook` or `compute_v2_review_set` re-derives the review set from `head_at_capture` reachability via non-linear history (e.g. `git rev-list --all --since=<capture>` or reflog scanning); the ancestor-only check is the sole mechanism for surfacing committed work, and it is bypassable by ordinary, unprivileged git commands.

### Impact Explanation
This breaks the stated invariant that "the review set must stay bound to the right repo, baseline, and touched paths": a dangerous change that was genuinely made during the reviewed turn is excluded from the diff sent to the LLM reviewer. Because the security-guidance Stop hook is the mechanism intended to catch hardcoded secrets, exfiltration code, SSRF, command injection, etc. introduced during a session, silently excluding a malicious commit means such a change can ship without ever being flagged or forcing a fix — enabling the underlying malicious code (e.g., code that reads local files/tokens and sends them to an attacker-controlled endpoint) to execute unreviewed, i.e. sensitive code/diff/token/local-file disclosure to an unintended sink.

### Likelihood Explanation
The only precondition is that the agent session can run ordinary git commands (commit, checkout, reset) — capabilities Claude Code already has via its Bash tool in normal automation flows, and exactly the capability a prompt-injection attack over repository content would try to leverage. No maintainer/admin privilege, leaked credentials, or social engineering is required; the sequence is a few standard git operations. The bug is deterministic given the described ancestor-check logic, so it is fully repeatable.

### Recommendation
Do not rely solely on `_is_ancestor(head_at_capture, current_head)` to decide whether to include the turn's committed work. Additionally scan for commits made during the turn regardless of where HEAD currently points, e.g. by recording every commit SHA created during the turn (via `git rev-list --walk-reflogs` from `head_at_capture` and current HEAD, or a reflog scan bounded by turn start time similar to `_git_reflog_recent_commits`) and union their changed files into `dirty_now` even when the branch pointer has since moved elsewhere. Alternatively, fall back to reviewing the full `changed_since` set (dropping the `dirty_now` intersection) whenever a non-ancestor HEAD change is detected during the turn, rather than silently treating it as "clean."

### Proof of Concept
Unit/integration test in the existing Stop-hook test suite (extends the `compute_v2_review_set` tests):
1. Init a repo, commit `A` (`head_at_capture = A`), capture baseline via `capture_git_baseline` (stash create at `A`, clean tree).
2. Write a dangerous file `evil.py` with an obvious "vulnerable" marker, `git commit` → `B` (parent `A`).
3. `git checkout -b sibling A` (or `git reset --hard A`) to move HEAD sideways so `head_at_capture (A)` is no longer an ancestor of `current_head`.
4. Call `compute_v2_review_set(cwd, baseline_sha, head_at_capture=A, untracked_at_baseline={})`.
5. Assert the review set is non-empty and still contains `evil.py` (expected per the stated invariant). Current behavior: `review_paths == []`, demonstrating the malicious file is dropped and `handle_stop_hook` would `_skip(9)` without invoking `analyze_code_security`.

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1792-1797)
```python
    review_paths, diff_base, repo_root, untracked, v2_metrics = compute_v2_review_set(
        cwd, baseline_sha, head_at_capture, untracked_at_baseline
    )
    if not review_paths:
        debug_log("Stop hook: empty review set")
        _skip(9, touched_paths_count=len(touched_paths))
```
