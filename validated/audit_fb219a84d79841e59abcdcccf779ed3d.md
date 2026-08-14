### Title
Amend delta-review skips unreviewed pre-amend commit content and permanently marks it as "reviewed" - ([File: plugins/security-guidance/hooks/security_reminder_hook.py])

### Summary
`_resolve_amend_pre_sha` and its caller in `handle_commit_review_posttooluse` implement a delta-review optimization for `git commit --amend`: instead of re-scanning the full amended commit, only the diff between the pre-amend SHA and the post-amend SHA is sent to the LLM. This optimization silently assumes the pre-amend commit's own content was already reviewed, but that assumption is never verified against the persisted `sg-reviewed-shas` log, so a commit whose content was never actually scanned can be amended and get the *entire* final commit marked "reviewed" while only the (attacker-shaped) small delta was analyzed.

### Finding Description
`_resolve_amend_pre_sha` (lines 517–573) purely validates that the *local* reflog shows an amend just happened (`commit (amend)` subject, HEAD@{1} not itself an amend, post-amend SHA prefix match); it says nothing about whether HEAD@{1}'s content was ever reviewed by `handle_commit_review_posttooluse`.

The caller decides to take the cheaper delta path based only on three *local Bash-invocation* signals: `is_amend`, `not _reflog_shas`, `len(all_shas) <= 1`, `commit_invocations <= 1` (lines 1100–1105) [1](#0-0) . None of these check whether `pre_amend_sha` is a member of `_load_reviewed_shas(repo_root)` — the exact set that exists specifically to track which commits the hook has actually scanned [2](#0-1) .

If the *original* commit (the one being amended) was made in a way that never triggered a full review — e.g. its `[branch sha]` line was hidden from stdout and it fell outside the 120-second reflog-freshness window used by the fallback detector, or it was produced by a tool path that doesn't route through this PostToolUse Bash matcher — then `pre_amend_sha` was never scanned by `analyze_code_security`. When the user/agent later runs `git commit --amend`, the hook computes `git diff pre_amend_sha sha` and reviews only that small delta (lines 1121–1132) [3](#0-2) . Any dangerous content that was already present in the pre-amend commit and untouched by the amend simply never appears in the reviewed diff.

Worse, after this partial review, `_append_reviewed_shas` records the **full post-amend SHA** as reviewed regardless of whether the delta or full-commit path was used [4](#0-3) . This same reviewed-shas log is read by push-sweep to decide which commits to skip re-scanning on push [5](#0-4) . So once the amended commit is stamped "reviewed," the dangerous content it carries is now permanently exempted from both future commit-review re-checks and the push-sweep, even though it was never actually analyzed by any LLM pass.

### Impact Explanation
This breaks the stated invariant that dangerous edits/commands must remain reviewable and blockable across retries, amends, and pushes. Attacker-influenced dangerous code (e.g., inserted via a normal edit that lands in a commit whose review was suppressed/missed) can ride along in a commit that is later amended with a trivial, security-irrelevant change; the delta-only review never sees the dangerous code, and the reviewed-shas bookkeeping then blocks it from ever being flagged, including at push time. This matches "unauthorized local command execution / dangerous change that bypasses Claude Code's review-and-warn controls," since the code that should have surfaced a security warning (and thus prompted human/approval scrutiny) is committed and pushed with no rewake/warning.

### Likelihood Explanation
Requires: (1) a first commit whose review is missed by the hook (plausible given the code's own comments about stdout suppression/piping/-q flags, and the 120-second reflog-freshness cutoff used for the fallback detector), and (2) a subsequent `git commit --amend` in a *separate* Bash invocation touching only unrelated content. Both are normal, easily reachable git workflows (common when a user squashes a trivial fixup into a prior commit) and require no privilege beyond ordinary edit/commit access that Claude Code already grants. The hook's own inline comments acknowledge similar suppressed-stdout cases are common enough to warrant a fallback path, indicating the missed-first-review precondition is realistic, not a corner case.

### Recommendation
Before taking the delta-review path, verify `pre_amend_sha` (or its full 40-hex resolution) is present in `_load_reviewed_shas(repo_root)`. If it is not, fall back to full `git show -p` review of the post-amend commit instead of the diff-only delta, so unreviewed pre-existing content is never skipped purely because it happens to precede an amend.

### Proof of Concept
Integration test plan for `handle_commit_review_posttooluse`:
1. In a temp git repo, make a commit whose stdout is fully suppressed (e.g. `git commit -q -m wip > /dev/null 2>&1`) and whose bash `tool_response.stdout/stderr` is empty, and simulate that this commit is >120s old at review time (monkeypatch time or `_git_reflog_recent_commits` to report it as stale) so the commit-review PostToolUse handler exits without ever calling `analyze_code_security` on it — confirm `sg-reviewed-shas` does NOT contain its SHA.
2. Add a clearly dangerous pattern to a file in that same commit (e.g. `eval(input())` or `os.system(user_input)`).
3. Run `git commit --amend -m "unrelated typo fix"` touching only a different, benign file, with normal (non-suppressed) stdout.
4. Invoke `handle_commit_review_posttooluse` with the amend Bash command/tool_response.
5. Assert: `_resolve_amend_pre_sha` returns the pre-amend SHA and `pre_amend_sha` truthy path is taken; the diff sent to `analyze_code_security` (`diff_files`) does NOT include the dangerous line from step 2; `emit_metrics`/exit path reports no vulnerabilities found; and `sg-reviewed-shas` now contains the post-amend SHA (full commit, including the dangerous code) — proving the dangerous code is marked reviewed without ever being scanned, and that a subsequent simulated `git push` sweep would skip it as already covered.

### Citations

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1254-1265)
```python
    try:
        full_shas = []
        for s in shas:
            r = subprocess.run(
                [*GIT_CMD, "rev-parse", "--verify", "-q", s],
                cwd=repo_root, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                full_shas.append(r.stdout.strip())
        _append_reviewed_shas(repo_root, full_shas, vulns_found=len(vulns or []))
    except Exception:
        pass
```

**File:** plugins/security-guidance/hooks/diffstate.py (L207-221)
```python
# ─── push-sweep reviewed-commit tracking ────────────────────────────────────
#
# Repo-local (not session-local) record of which commits the commit-review
# hook has already reviewed, so the push-sweep can advance its diff base past
# the contiguous reviewed prefix and skip entirely when everything pushed was
# already covered. Lives under `.git/` (same precedent as CC's
# `.git/claude-trailers`) so it survives across sessions and is per-clone.
#
# Format: one line per reviewed sha, append-only:
#   <40-hex-sha>\t<unix-ts>\t<pv>\t<vulns_found>
#
# The trailing columns are observability only — load reads just the sha set.
# GC keeps the last _REVIEWED_SHAS_CAP entries; the file is small (~64 bytes
# per line) so even at the cap it's ~32KB.

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
