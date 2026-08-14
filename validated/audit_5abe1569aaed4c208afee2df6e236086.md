### Title
Amend-delta review trusts unverified pre-amend SHA, permanently hiding already-committed dangerous code from review - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Finding Description
`_resolve_amend_pre_sha` resolves the pre-amend commit purely from reflog shape checks (`HEAD@{0}` subject starts with `commit (amend)`, `HEAD@{1}` is not itself an amend, and `HEAD@{0}` matches the caller-supplied `expected_post_sha`) [1](#0-0) . Nowhere does it check whether the resolved `pre_amend_sha` was actually recorded in `.git/sg-reviewed-shas` (the file used elsewhere to track which commits were genuinely reviewed) [2](#0-1) .

`handle_commit_review_posttooluse` then uses this unverified `pre_amend_sha` to compute a delta-only diff (`git diff pre_amend_sha sha`) instead of a full `git show sha`, explicitly to save review cost on the assumption that the pre-amend content was "already reviewed on the original commit" [3](#0-2) [4](#0-3) . If the original (pre-amend) commit's review was skipped for any legitimate reason — hourly rate limit (`skip_reason=23`), LLM/API failure, message-only/no-reviewable-files skip, or the review feature being disabled — the dangerous code introduced by that commit was never inspected. A subsequent `git commit --amend` that only touches unrelated lines (e.g., fixing the commit message or an unrelated file) produces a delta diff that does not include the already-present dangerous lines, so the amend-delta review sees nothing new and emits no warning, while the final amended commit — the one that actually gets pushed — still contains the unreviewed dangerous code.

The guard conditions in `_resolve_amend_pre_sha` and the caller (`is_amend and not _reflog_shas and len(all_shas) <= 1 and commit_invocations <= 1`) [5](#0-4)  only protect against cross-repo/race/chained-command misattribution of the pre-amend SHA; they do nothing to verify that the pre-amend SHA was ever actually security-reviewed.

### Impact Explanation
This is a Security-control bypass: it silently defeats the commit-review dangerous-code-detection hook for a class of realistic sequences (rate-limited or API-failed commit review followed by an innocuous amend), causing dangerous code to reach the repository's final HEAD state without ever being flagged/blocked/rewoken, which is exactly the invariant the audit calls out — dangerous edits must remain reviewable across amends. This matches the "Security-control bypass that silently disables or routes around blocking/review boundaries" impact class.

### Likelihood Explanation
Requires only normal, unprivileged repository operations (a sequence of `git commit` calls sufficient to exhaust `MAX_COMMIT_REVIEWS_PER_HOUR`, or a benign API hiccup, followed by `git commit --amend`) — no admin privilege, no test/mock manipulation. It is somewhat dependent on hitting a pre-existing skip condition (rate limit, API failure, or similar) on the original commit, which is realistically triggerable by an attacker who controls their own commit cadence, making this moderately likely rather than a hard guarantee. Note: I could not fully verify within the remaining budget whether the later `git push` sweep (`handle_push_sweep_posttooluse`) independently re-diffs the full range since the last reviewed base (which could catch this at push time) — this is a genuine gap in my analysis and should be checked before finalizing severity, since if push-sweep does a true full-range diff against the last known-reviewed SHA rather than trusting `sg-reviewed-shas` membership of the amend's pre-image, it may still catch the unreviewed content at push time.

### Recommendation
In `_resolve_amend_pre_sha` (or its caller), require that `pre_amend_sha` is a member of `_load_reviewed_shas(repo_root)` before trusting the delta-only path; if it is not present, fall back to a full `git show` review of the post-amend commit so the whole commit content (including any previously-unreviewed base) is inspected.

### Proof of Concept
Integration test plan for `plugins/security-guidance/hooks/security_reminder_hook.py`:
1. In a temp git repo, monkeypatch/exhaust `MAX_COMMIT_REVIEWS_PER_HOUR` (or force `analyze_code_security` to return no findings once) so the first `git commit -m "wip"` adding a file with a clearly dangerous pattern (e.g., `eval(user_input)`) is skipped from review (assert `skip_reason` metric emitted, no findings recorded, and the sha absent from `.git/sg-reviewed-shas`).
2. Run `git commit --amend -m "wip2"` with no content change (or an unrelated one-line change to a different file).
3. Invoke `handle_commit_review_posttooluse` with the corresponding `tool_response.stdout` for the amend.
4. Assert: `pre_amend_sha` is resolved and used for a delta diff, `diff_files` excludes the dangerous line, and no vuln/warning is emitted (`emit_metrics` shows `vulns_found=0` or a skip), even though the final `HEAD` commit content (via `git show HEAD`) still contains `eval(user_input)`.
5. Expected fix behavior: after adding the reviewed-shas membership check, the same scenario should fall back to a full `git show` review of the amended HEAD, and the test should assert that the dangerous pattern is now surfaced (non-empty `vulns`/`concrete_guidance`, exit code 2).

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L558-573)
```python
    head0_sha, _, head0_subj = lines[0].partition("|")
    head1_sha, _, head1_subj = lines[1].partition("|")
    if not head0_subj.startswith("commit (amend)"):
        return None
    if head1_subj.startswith("commit (amend)"):
        return None
    # Cross-repo guard: the post-amend SHA the caller is about to review must
    # match HEAD@{0} of repo_root. Otherwise the bash command was likely run
    # in a different repo than repo_root, and the reflog we just read is
    # unrelated. Prefix-compare: expected_post_sha is typically the 7-char
    # abbreviated SHA captured from bash stdout by _COMMIT_SHA_RE (git's
    # default core.abbrev floor), while head0_sha is the full 40-char %H —
    # strict equality would always fail and silently disable the delta path.
    if expected_post_sha and not head0_sha.startswith(expected_post_sha):
        return None
    return head1_sha or None
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1064-1071)
```python
    # `git commit --amend`: review only the delta added by the amend
    # (pre-amend..post-amend) instead of the full amended commit. Without this,
    # the amend re-reviews the entire commit including code already reviewed
    # on the original commit, costing 30-60s of LLM time and re-flagging
    # findings the user may have just amended IN ORDER TO fix. Pre-amend
    # SHA comes from the reflog and is validated to be an amend (see
    # _resolve_amend_pre_sha) — otherwise we fall back to full-commit review.
    #
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1100-1105)
```python
    is_amend = bool(_GIT_AMEND_RE.search(command))
    commit_invocations = len(_GIT_COMMIT_RE.findall(command))
    pre_amend_sha = None
    if (is_amend and not _reflog_shas and len(all_shas) <= 1
            and commit_invocations <= 1):
        pre_amend_sha = _resolve_amend_pre_sha(repo_root, expected_post_sha=shas[0])
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1119-1132)
```python
    for sha in shas:
        try:
            if pre_amend_sha:
                # Delta review: pre-amend → post-amend. `git diff` (not show)
                # so the output is a pure unified diff with no commit header.
                result = subprocess.run(
                    [*GIT_CMD, "diff", "--no-color", "--no-ext-diff", pre_amend_sha, sha, "--"],
                    cwd=repo_root, capture_output=True, timeout=15
                )
            else:
                result = subprocess.run(
                    [*GIT_CMD, "show", "-p", "--no-color", "--no-ext-diff", sha, "--"],
                    cwd=repo_root, capture_output=True, timeout=15
                )
```

**File:** plugins/security-guidance/hooks/diffstate.py (L242-264)
```python
_REVIEWED_SHAS_BASENAME = "sg-reviewed-shas"
_REVIEWED_SHAS_CAP = 500

def _reviewed_shas_path(repo_root):
    gd = _git_dir(repo_root)
    return os.path.join(gd, _REVIEWED_SHAS_BASENAME) if gd else None


def _load_reviewed_shas(repo_root):
    """Set of full 40-hex shas previously reviewed in this clone."""
    p = _reviewed_shas_path(repo_root)
    if not p or not os.path.exists(p):
        return set()
    out = set()
    try:
        with open(p, "r") as f:
            for line in f:
                sha = line.split("\t", 1)[0].strip()
                if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
                    out.add(sha)
    except OSError:
        pass
    return out
```
