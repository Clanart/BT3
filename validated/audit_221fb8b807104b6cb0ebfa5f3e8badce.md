### Title
Bash-only edits with no interrupt-safe touch tracking let `load_baseline_sha`/UserPromptSubmit re-baseline past an unreviewed dangerous change, causing it to be silently skipped by the Stop-hook diff - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` derives the Stop-hook's review set as `dirty_now ∩ changed_since(baseline_sha)`, where `baseline_sha` is refreshed on every `UserPromptSubmit` unless the "preserve" guard fires. That guard only fires when `touched_paths` is non-empty, but `touched_paths` is populated exclusively via `record_touched_path` from Edit/Write PostToolUse hooks, never from raw `Bash` commands. A dangerous change made purely through `Bash` (e.g. `sed`, `cat >>`, a build/lint script, or a `git commit` of attacker-influenced content) followed by the turn ending without Stop actually completing (interrupt, follow-up prompt, tool-reject, model crash, `maxTurns`) leaves `touched_paths` empty, so the next `UserPromptSubmit` overwrites `baseline_sha` past the unreviewed commit/edit, permanently removing it from `changed_since` and thus from the review set.

### Finding Description
- `handle_user_prompt_submit` calls `capture_git_baseline(cwd)` and unconditionally overwrites `state["baseline_sha"]` unless `state.get("touched_paths") and state.get("baseline_sha")` are both already set: [1](#0-0) 
- `touched_paths` is only appended to by `record_touched_path`, which is wired to Edit/Write PostToolUse handling, not Bash: [2](#0-1) 
- The Stop-hook review set is `dirty_now ∩ changed_since(baseline_sha)`; once `baseline_sha` is advanced past a commit, that commit's content is no longer "changed since baseline," so it drops out of `review_set` even though it was never actually reviewed: [3](#0-2) 
- The code's own docstring for `compute_v2_review_set` explicitly acknowledges this exact gap: *"a Bash-only turn that's interrupted before Stop fires leaves touched_paths empty, so the next UPS re-baselines past those edits."* [4](#0-3) 
- `restore_unreviewed_stop_state` exists to re-arm the preservation guard when Stop itself exits early for a transient reason, but it is only invoked from within the Stop handler's own early-exit paths — it cannot protect against a turn that never reaches Stop at all (user interrupt, follow-up prompt before Stop starts, process kill), which is exactly the gap the docstring calls out: [5](#0-4) 

Exploit flow: content in a repository (build script, Makefile target, CI helper, or attacker-crafted instructions that Claude follows via `Bash`) causes a dangerous edit/commit to land purely through `Bash` tool calls in a turn that is interrupted (new user message arrives, tool-reject, crash) before the Stop hook fires and calls `consume_stop_state`. Because `touched_paths` stayed empty, the very next `UserPromptSubmit` recomputes `baseline_sha` from the now-dirty/committed tree, and the dangerous change is baked into the new baseline. All subsequent Stop-hook diffs are computed against this shifted baseline, so `changed_since` no longer contains the dangerous file, and it is never surfaced to the LLM review or the user — breaking the stated invariant that "the review set must stay bound to the right repo, baseline, and touched paths."

### Impact Explanation
A dangerous code change (backdoor, hardcoded secret, injected exfiltration logic) introduced via Bash-only tool use can be made permanently invisible to the security-guidance Stop-hook LLM review by simply interrupting/aborting the turn that introduced it. This defeats the sole automated safety net the plugin provides for "changed code" review, allowing sensitive code/diff content to pass through unreviewed and reach whatever local or remote sink the attacker-influenced repo directs it to (e.g., a later `git push`, a build artifact, or an exfiltration channel embedded in the change) — matching "Sensitive code, prompt, token, diff, or local file disclosure to an unintended local or remote sink."

### Likelihood Explanation
This requires no privilege beyond normal repository content and ordinary Bash usage that Claude Code performs routinely (many agent workflows edit files via shell commands, not just the Edit/Write tools, especially when following instructions embedded in README/Makefile/CI content in the cloned repo). Turn interruption before Stop fires (new prompt, tool-reject, timeout, crash) is a common, easily-triggerable event, and the code's own comments confirm this is a known, reproducible gap rather than a theoretical one.

### Recommendation
Track "dirty since baseline" state independently of tool type — e.g., record touched paths (or a git-status snapshot) from any Bash command that mutates the working tree/HEAD (via a PostToolUse[Bash] hook analogous to the existing commit-review hook), not only Edit/Write. Alternatively, make the UPS preservation check compare `git status`/`HEAD` at prompt-submit time against the last-reviewed baseline rather than relying on the `touched_paths` proxy, so any uncommitted-and-unreviewed delta (regardless of how it was produced) blocks re-baselining until Stop actually consumes it.

### Proof of Concept
Integration test plan (extends existing diffstate/session_state test suite):
1. Simulate UPS: call `handle_user_prompt_submit`/`capture_git_baseline` to set `baseline_sha = B0`, `touched_paths = []`.
2. Simulate a Bash-only dangerous edit: write a file containing a marker vulnerability and `git commit` it directly via subprocess (not through `record_touched_path`), producing `HEAD = H1`. Do NOT call `record_touched_path` (mirrors Bash-only tool use).
3. Simulate turn interruption: do not call `consume_stop_state`/Stop hook at all.
4. Call `handle_user_prompt_submit` again (new turn) — assert `preserved["value"]` is `False` and `state["baseline_sha"]` becomes a new SHA derived from `H1` (i.e., baseline advanced past the dangerous commit).
5. Call `compute_v2_review_set(cwd, new_baseline_sha, head_at_capture=H1)` and assert the dangerous file path is **absent** from `review_set` — demonstrating the file is now permanently excluded from Stop-hook review despite never having been reviewed.
6. Compare against a control run where the same edit is made via a simulated Edit/Write call (`record_touched_path` invoked), confirming `touched_paths` non-empty preserves the baseline and keeps the dangerous file in `review_set`.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L486-503)
```python
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

**File:** plugins/security-guidance/hooks/diffstate.py (L116-137)
```python
def restore_unreviewed_stop_state(session_id, paths, baseline_sha):
    """Put consumed touched_paths back so the next Stop reviews them.

    consume_stop_state cleared touched_paths on disk; if Stop then exits
    early for a transient reason (CCR API unreachable, Haiku HTTP error)
    the next UPS would see an empty list, fall through the preservation
    guard, and re-baseline past the unreviewed edits. Restoring keeps the
    guard armed. Prepend+dedupe so any concurrent next-turn PostToolUse
    appends survive.
    """
    if not paths:
        return

    def _restore(state):
        existing = state.get("touched_paths", [])
        merged = list(dict.fromkeys(list(paths) + list(existing)))
        if len(merged) > 200:
            merged = merged[:200]
        state["touched_paths"] = merged
        if baseline_sha and not state.get("baseline_sha"):
            state["baseline_sha"] = baseline_sha
    with_locked_state(session_id, _restore)
```

**File:** plugins/security-guidance/hooks/diffstate.py (L368-370)
```python
    Known limitation: a Bash-only turn that's interrupted before Stop fires
    leaves touched_paths empty, so the next UPS re-baselines past those edits.
    v1 never reviews Bash-only turns at all, so v2 is no worse there.
```

**File:** plugins/security-guidance/hooks/diffstate.py (L403-426)
```python
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
