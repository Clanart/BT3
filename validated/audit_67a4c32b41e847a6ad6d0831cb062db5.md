### Title
Baseline drift via `git commit --amend` causes reviewed-worthy diff to be silently excluded from the v2 review set - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
`compute_v2_review_set` intersects `dirty_now` (working-tree/porcelain state relative to the *current* HEAD) with `changed_since` (diff against the stashed pre-turn `baseline_sha`) to decide which files get reviewed. The "committed this turn" carve-in only fires when `head_at_capture` is an ancestor of the new HEAD via `_is_ancestor`. An ordinary `git commit --amend` (or any history-rewriting op such as rebase) produces a new HEAD that is *not* a descendant of `head_at_capture`, so that carve-in never runs, while the amended file is also clean relative to the new HEAD (so it's absent from `dirty_now` too). The result is `dirty_now` empty for that file even though `changed_since` (which still diffs against the untouched stash SHA) correctly shows it as modified — and because `review_set = dirty_now & changed_since`, the file drops out of the review set entirely.

### Finding Description
`record_touched_path` (`diffstate.py:57`) and the sibling state functions (`save_baseline_sha`, `consume_stop_state`) maintain `baseline_sha` / `head_at_capture` in per-session state that `compute_v2_review_set` (`diffstate.py:353`) later uses to compute what Claude's Stop hook reviews: [1](#0-0) 

`_is_ancestor` (`gitutil.py:377`) checks `git merge-base --is-ancestor head_at_capture current_head`, which is only true when HEAD moved *forward* via a normal commit — not when history was rewritten: [2](#0-1) 

Exploit flow, achievable purely with normal git commands in the cloned repo (no privilege needed beyond what the agent/session already has):
1. UPS fires, `capture_git_baseline` stashes the pre-turn state as `baseline_sha`, and `head_at_capture` is recorded as the then-current HEAD.
2. Claude (or a script driven by attacker-supplied instructions/content) edits a dangerous file and runs `git commit` — `head_at_capture` is now an ancestor of the new HEAD, so at this point the file would still be reviewable via the linear-advance branch.
3. A subsequent `git commit --amend` (e.g. "fix typo" as a legitimate-looking follow-up, or driven by tool/agent automation, or by prompt-injected repo content instructing an amend) creates a new commit object that shares the *same parent* as the original — it is a sibling, not a descendant, of `head_at_capture`'s child. `_is_ancestor(head_at_capture, new_HEAD)` now returns `False`.
4. `tracked_dirty`/`git status --porcelain` against the new HEAD shows the file as clean (it's fully committed), so it's absent from `dirty_now`.
5. `changed_since = _git_name_only(repo, baseline_sha)` still correctly reports the file as different from the original stash baseline.
6. `review_set = dirty_now & changed_since` intersects an empty `dirty_now` term for that file with a non-empty `changed_since` term → the file is excluded from `review_paths`, even though it genuinely differs from the session's pre-turn baseline and was written during the session.
7. `handle_stop_hook` builds `diff_files` from this shrunken `review_paths` (via `get_git_diff` restricted to those paths), so the dangerous change is never sent to the LLM reviewer and the Stop hook exits without findings.

This breaks the stated invariant ("the review set must stay bound to the right repo, baseline, and touched paths") because the touched/committed file silently falls out of the review set purely due to a benign-looking amend, with no code path re-including it.

### Impact Explanation
This is a Security-control bypass: the security-guidance plugin's entire enforcement mechanism (Stop-hook LLM review that blocks/nags on vulnerable diffs via `sys.exit(2)`) can be silently routed around. Any dangerous code introduced in a commit that is later amended (a very ordinary git workflow step — fixing a commit message, squashing a follow-up fix, etc.) will never reach the reviewer, and the session proceeds as if the code were clean. No explicit malicious intent is even required — normal amend/rebase hygiene defeats the review.

### Likelihood Explanation
High feasibility and no special privileges: the attacker only needs the ability to run ordinary git commands inside the working tree that Claude Code is already operating in (which is the normal operating mode for this agent). `git commit --amend` after an initial commit is an extremely common and unremarkable operation, making this both easy to trigger accidentally and trivial to trigger deliberately (e.g. via prompt-injected instructions telling the agent to "clean up the commit message" right after introducing a vulnerability).

### Recommendation
In `compute_v2_review_set`, don't gate the "committed this turn" carve-in solely on `_is_ancestor`. When `head_at_capture != current_head` and ancestry fails (indicating amend/rebase/reset), fall back to diffing `changed_since`-only files that are also reachable from `head_at_capture` in either direction (e.g. via `git diff head_at_capture...HEAD` or by unioning `changed_since` directly into `dirty_now` whenever HEAD has changed at all, not only when it advanced linearly), so review coverage can't regress below the pre-turn baseline snapshot regardless of how HEAD was rewritten.

### Proof of Concept
Integration test plan for `diffstate.compute_v2_review_set`:
1. Init a temp git repo, commit an initial file, capture `baseline_sha = capture_git_baseline(repo)` and `head_at_capture = _git_rev_parse_head(repo)`.
2. Write a "dangerous" file (`app.py` with e.g. `os.system(user_input)`), `git add . && git commit -m "add feature"`.
3. Run `git commit --amend -m "add feature (typo fix)"` with no content change re-staged (or trivial unrelated change), producing a new HEAD sibling to the prior commit.
4. Call `compute_v2_review_set(repo, baseline_sha, head_at_capture, {})`.
5. Assert `app.py`'s absolute path IS present in the returned `review_paths` — expected to FAIL under current logic (file is dropped), demonstrating the bypass.
6. As a control, repeat without the amend step (plain second commit) and assert the file **is** included, confirming the amend specifically is what breaks inclusion.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L403-409)
```python
    diff_base = "HEAD"
    current_head = _git_rev_parse_head(repo)
    if (head_at_capture and current_head and head_at_capture != current_head
            and _is_ancestor(repo, head_at_capture, current_head)):
        dirty_now |= _git_name_only(repo, f"{head_at_capture}..HEAD") or set()
        diff_base = head_at_capture

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
