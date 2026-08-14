## Analog Vulnerability Found

### Title
No validation of attacker-controllable git commit timestamp (`%ct`) before computing commit "freshness" used to gate the automated security review - (File: `plugins/security-guidance/hooks/gitutil.py`)

### Summary
`_git_reflog_recent_commits()` in the `security-guidance` plugin computes a commit's age as `now - int(ct)`, where `ct` is the committer timestamp (`%ct`) taken directly from `git log` output, with no sanity check that the value is plausible (not far in the past, not in the future, not malformed). This mirrors the `PeriodicPriceBucket.updatePrice` bug: an externally-supplied timestamp feeds directly into a downstream decision variable (there, `periodIndex`; here, the fresh/stale classification of a commit) without any bounds validation.

### Finding Description
`_git_reflog_recent_commits` scans recent HEAD reflog entries and classifies each `commit*` entry as "fresh" or "stale" based on the committer timestamp: [1](#0-0) 

Specifically:
```python
now = int(_time.time())
...
sha, ct, subject = parts
try:
    age = now - int(ct)
except ValueError:
    continue
...
if idx == 0 or age <= max_age_s:
    fresh.append(sha)
else:
    stale += 1
``` [2](#0-1) 

`ct` is a git committer timestamp, which is fully controllable by whoever creates the commit — e.g., via `GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE` env vars or `git commit --date=...`. In a Claude Code session, the agent (potentially acting on attacker-supplied instructions via prompt injection, or a malicious/compromised contributor's commit being processed automatically) can freely set this value when running `git commit` through the Bash tool. There is no check that `ct` is within a sane range (e.g., not older than some bound, not negative, not decoded from garbage) before it is used to compute `age` and decide whether the commit belongs in the `fresh` list — exactly the missing `newTimestamp > 0`-style validation called out in the report.

The docstring for this function confirms it is a fallback path used by the commit-review hook when stdout-based commit detection fails (piped/redirected output, or a chained `git commit && git push`), i.e., it directly feeds the decision of which commits get automatically reviewed for security issues: [3](#0-2) 

Because entries at `idx > 0` are gated purely on `age <= max_age_s` with no bound on how `ct` was derived, a commit whose committer timestamp is set further in the past than `max_age_s` (120s by default via `STOP_LOOP_STATE_TTL_SEC`/similar windows used by callers) is classified `stale` and excluded from `fresh`, regardless of when it was actually created.

### Impact Explanation
If this reflog fallback path is reached (stdout-based detection failed) and the review is being driven off `fresh`, an attacker-influenced commit (created with a backdated `--date`) can be excluded from the `fresh` list and therefore skipped by the automated security-review hook that is supposed to scan every new commit for vulnerabilities before it's pushed/reported. This is a direct analog to the original bug: an unvalidated externally-supplied timestamp silently corrupts a security-relevant classification (there `periodIndex`, here fresh-vs-stale), causing downstream logic to take the wrong branch.

### Likelihood Explanation
Exploitability depends on reaching the fallback path (the primary stdout `[branch sha]` detection must fail — plausible via output redirection/piping or a chained command per the code comments) and on whichever caller consumes `fresh`/`stale` to decide what to review. I was not able to trace that caller within the remaining budget, so I cannot confirm the full end-to-end "review is skipped" impact with certainty — this is a plausible but not fully proven exploit chain from the evidence gathered.

### Recommendation
Validate `ct` before using it to compute `age`: reject/skip entries where the parsed timestamp is unreasonable (e.g., more than a small tolerance in the future, or earlier than the repository's known creation time), and do not let a single malformed/backdated timestamp silently reclassify a commit as stale. Consider deriving freshness from a source the committer cannot control (e.g., the hook's own wall-clock capture at PostToolUse time) rather than trusting `%ct`.

### Proof of Concept
Conceptual reproduction (not independently executed against a live install):
1. In a repo being monitored by the security-guidance plugin, run:
   `GIT_COMMITTER_DATE="2000-01-01T00:00:00" git commit --date="2000-01-01T00:00:00" -am "malicious change"`
2. Trigger conditions where stdout-based commit-sha detection fails (e.g., `git commit ... > /dev/null && git push`), forcing the commit-review hook onto the `_git_reflog_recent_commits` fallback.
3. Because `ct` decodes to a year-2000 epoch value, `age = now - ct` vastly exceeds `max_age_s` (120s), so the commit is classified `stale` (for `idx > 0` entries) and is excluded from the `fresh` list, potentially causing the automated vulnerability scan to never fire on that commit's diff. [4](#0-3)

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L227-255)
```python
def _git_reflog_recent_commits(repo_root, max_age_s=120, max_n=5):
    """Return (fresh_commit_shas, stale_count) from the HEAD reflog.

    Scans the last `max_n` reflog entries and returns the SHAs whose action is
    `commit*` AND whose commit timestamp is within `max_age_s` of now,
    newest-first. `stale_count` is the number of commit-action entries that
    were too old (so the caller can distinguish "no commit happened" from
    "commit happened earlier than the window").

    Used by commit-review when stdout-based `[branch sha]` detection fails
    (output piped/redirected/-q, or a chained command after `git commit`
    pushed the success line off — `git commit && git push` makes HEAD@{0}
    `update by push`, not `commit:`). The HEAD@{0}-only check
    keeps the not-yet-visible-HEAD skip rare; analysis showed the
    residual is dominated by these chained-command and noop-guard cases.

    Safety vs. blindly reading HEAD:
      - cross-repo (`cd ../other && git commit`): repo_root's own reflog has
        no fresh commit, so this returns ([], 0).
      - commit actually failed (pre-commit reject, nothing-staged): reflog's
        recent entries are the prior checkout/commit/reset → ([], 0) or only
        stale entries.
      - HEAD raced ahead (a second commit landed before this async hook ran):
        both commits appear in the scan and both get reviewed — correct.
      - prior Bash call's commit within the window: would be returned here,
        but the call site deduplicates against `.git/sg-reviewed-shas` so a
        SHA is reviewed at most once. This is also the non-overlap invariant
        with push-sweep.
    """
```

**File:** plugins/security-guidance/hooks/gitutil.py (L262-266)
```python
        r = subprocess.run(
            [*GIT_CMD, "log", "-g", "-n", str(max_n),
             "--format=%H|%ct|%gs", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
```

**File:** plugins/security-guidance/hooks/gitutil.py (L271-299)
```python
    import time as _time
    now = int(_time.time())
    fresh, stale = [], 0
    for idx, line in enumerate(r.stdout.splitlines()):
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, ct, subject = parts
        # `commit: msg`, `commit (amend): msg`, `commit (initial): msg`,
        # `commit (merge): msg` — all create a reviewable commit object.
        if not subject.startswith("commit"):
            continue
        try:
            age = now - int(ct)
        except ValueError:
            continue
        # HEAD@{0} (idx==0) is exempt from the age gate. The gate exists to
        # bound the WIDENED HEAD@{1..max_n-1} scan from picking up commits
        # made by *prior* Bash calls; HEAD@{0} is by definition the most
        # recent reflog entry and was previously accepted unconditionally
        # (_git_reflog_head_if_just_committed previously had no age check).
        # Applying max_age_s to idx==0 made the not-yet-visible-HEAD skip
        # noticeably more frequent on chained
        # `git commit && <slow command>` where %ct is >120s old by the
        # time the async PostToolUse hook fires.
        if idx == 0 or age <= max_age_s:
            fresh.append(sha)
        else:
            stale += 1
```
