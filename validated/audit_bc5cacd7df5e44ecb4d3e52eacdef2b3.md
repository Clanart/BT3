### Title
Automated commit/push security-review can be permanently bypassed by forging entries in the unauthenticated `sg-reviewed-shas` dedup file - (File: plugins/security-guidance/hooks/diffstate.py)

### Summary
The `security-guidance` plugin's commit-review and push-sweep hooks decide whether a given commit needs an LLM security scan by checking set-membership of its SHA in a plain-text, unsigned file, `.git/sg-reviewed-shas`. Just as the Dopex report shows a strict invariant (`collateral.balanceOf(address(this)) == _totalCollateral - loss`) being trusted without accounting for a third party mutating the underlying shared state, this plugin trusts the contents of `sg-reviewed-shas` as proof that "an LLM already reviewed this commit," without any cryptographic binding between the recorded SHA and an actual completed review. Anything with local filesystem write access to `.git/` can add an arbitrary commit SHA to this file and the plugin will silently treat that commit as already reviewed forever after.

### Finding Description
`_append_reviewed_shas` appends `<sha>\t<ts>\t<pv>\t<vulns_found>` lines to a repo-local file under `.git/` [1](#0-0) , and `_load_reviewed_shas` simply reads back the set of 40-hex SHAs from that file with no signature, hash-chain, or other integrity check [2](#0-1) .

Two consumers trust this set as ground truth that a real review happened:

1. `handle_commit_review_posttooluse`'s reflog fallback: if stdout parsing fails, it treats a commit as needing review only when its SHA is **not** already in `_load_reviewed_shas(_root)` [3](#0-2) .
2. The push-sweep dedup, which is explicitly designed to "advance its diff base past the contiguous reviewed prefix and skip entirely when everything pushed was already covered" [4](#0-3) .

Because the file lives at a well-known, predictable path (`.git/sg-reviewed-shas`), is plain-text, append-only, and has no authentication tying an entry to an actual LLM call having run, any process capable of writing to that path (e.g., a Bash command executed as a side-effect of prompt-injected instructions embedded in a file Claude reads, a malicious pre/post-commit-adjacent script, or a compromised dependency's install hook) can pre-seed it with the exact SHA of a soon-to-be-created malicious commit. Git commit SHAs are deterministic given tree, parent, author/committer identity and timestamps, so an attacker who controls the commit content (which is the entire point of the attack) can compute the SHA in advance and write it to the dedup file before or immediately after `git commit` runs — including via low-level plumbing (`git commit-tree` + `git update-ref`) that doesn't match the `_GIT_COMMIT_RE` trigger regex used to gate the `PostToolUse` hook at all [5](#0-4) , so the genuine review path never fires in the first place, and the poisoned entry ensures the push-sweep's reflog/dedup fallback never fires either.

This mirrors the Dopex root cause precisely: a security-critical decision (`subtractLoss`'s `require`, here "skip re-review of this SHA") is gated on an externally-mutable piece of shared state (`collateral.balanceOf`, here the `sg-reviewed-shas` file) that an unprivileged actor can desynchronize from the invariant the code assumes ("balance reflects only tracked flows" / "SHA present ⇒ genuinely reviewed"), with no reconciliation mechanism available to the legitimate owner.

### Impact Explanation
A successful poisoning of `sg-reviewed-shas` causes Claude Code's automated LLM-based vulnerability scan (the entire purpose of the `security-guidance` plugin) to be silently and permanently skipped for a specific malicious commit, both at commit time (via the reflog fallback) and at push time (via push-sweep). This is a concrete security-control bypass: it allows a backdoor, credential-exfiltration payload, or other vulnerability introduced by an attacker-influenced code change to be committed and pushed without ever triggering the blocking `exit(2)` guidance that would otherwise force Claude/the user to fix it. This directly maps to "hook bypass" and "direct local/project compromise impact" in the accepted trust-boundary categories.

### Likelihood Explanation
Exploitation requires local write access to the target repository's `.git/` directory and the ability to get a shell command executed in that repo's context (e.g., through prompt injection content that Claude later executes via its Bash tool, a malicious build/test script, or a compromised transitive dependency's postinstall hook) — the same capability level (unprivileged local file write) the Dopex analog assumes for the attacker transferring collateral tokens. No special permissions on the CC session itself are needed beyond what many real-world prompt-injection or supply-chain scenarios already grant. The main friction is predicting/controlling the exact commit SHA in advance, but since the attacker is the one authoring the malicious commit's tree/parent, this is fully computable offline before writing the poisoned entry.

### Recommendation
- Do not treat presence-in-file as proof of review; instead have `_append_reviewed_shas` record a review outcome that is cryptographically tied to the actual diff content reviewed (e.g., an HMAC over `sha + diff content + a process-local secret`), and verify that HMAC before trusting a "reviewed" entry to skip re-scanning.
- Restrict/verify the reflog- and push-sweep fallback paths so a SHA is only considered "already reviewed" when it was actually produced by this session's own analysis pipeline (e.g., track in in-memory/locked session state rather than a world-writable file), not by reading back an on-disk file that any local process can edit.
- At minimum, validate file permissions/ownership of `sg-reviewed-shas` and treat any externally-modified-since-last-write file as untrusted, falling back to full review.

### Proof of Concept
1. Attacker plants prompt-injected instructions in a file Claude will read (e.g., a README, issue body, or tool output) telling it to run, via Bash, low-level git commands that create a malicious commit without invoking the plain `git commit` subcommand that the hook's regex matches:
   ```
   TREE=$(git write-tree)
   SHA=$(git commit-tree $TREE -p $(git rev-parse HEAD) -m "backdoor")
   git update-ref refs/heads/$(git symbolic-ref --short HEAD) $SHA
   printf "%s\t%s\t0\t0\n" "$SHA" "$(date +%s)" >> "$(git rev-parse --git-dir)/sg-reviewed-shas"
   ```
2. `_GIT_COMMIT_RE` never matches (`git commit-tree`/`git update-ref`, not `git commit`), so `handle_commit_review_posttooluse` never triggers a review for this commit [6](#0-5) .
3. The forged SHA is now present in `.git/sg-reviewed-shas`, exactly matching the format `_load_reviewed_shas` parses [2](#0-1) .
4. When the user later runs `git push`, the push-sweep hook's reflog/dedup logic sees the SHA already recorded as reviewed and skips it, so the malicious commit is pushed with the security-guidance plugin never having inspected its content.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L207-220)
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

**File:** plugins/security-guidance/hooks/diffstate.py (L250-264)
```python
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

**File:** plugins/security-guidance/hooks/diffstate.py (L267-280)
```python
def _append_reviewed_shas(repo_root, shas, vulns_found=0):
    """Record that `shas` were reviewed. Best-effort; never raises.

    Uses fcntl.flock for the read-gc-write; appends are O_APPEND-atomic but
    GC needs the lock so concurrent CC sessions in the same clone don't race
    each other's truncation.
    """
    p = _reviewed_shas_path(repo_root)
    if not p or not shas:
        return
    import time as _time
    ts = int(_time.time())
    pv = _PV or 0
    lines = [f"{s}\t{ts}\t{pv}\t{int(vulns_found)}\n" for s in shas]
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L593-597)
```python
# Regex matching `git commit` commands. Mirrors Claude Code's own commit
# detection — it does NOT tolerate `git -c k=v commit` global options, which
# keeps this hook aligned with CC's commit attribution on what counts as a
# commit.
_GIT_COMMIT_RE = re.compile(r'\bgit\s+commit(?:\s|$)')
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L916-921)
```python
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not _GIT_COMMIT_RE.search(command):
        # Defensive only — hooks.json's `"if": "Bash(git commit:*)"` is the
        # real gate so CC never spawns python3 for ls/grep/etc. This catches
        # cases where CC's command matching fails open and spawns the hook anyway.
        sys.exit(0)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L966-985)
```python
    _skip_21_sub = 0
    if not commit_succeeded and not interrupted and cwd:
        _root = _git_toplevel(cwd)
        _fresh, _stale = _git_reflog_recent_commits(_root)
        if _fresh:
            _already = _load_reviewed_shas(_root)
            _reflog_shas = [s for s in _fresh if s not in _already]
            if _reflog_shas:
                commit_succeeded = True
                debug_log(
                    f"Commit review: stdout had no `[branch sha]`; reflog "
                    f"shows {len(_reflog_shas)} fresh unreviewed commit(s) "
                    f"({_reflog_shas[0][:12]}...)"
                )
            else:
                # Fresh commit(s) in reflog but all already in
                # sg-reviewed-shas — likely a Bash retry or the commit was
                # reviewed via a prior fire. Correct to skip; sub=2 lets telemetry
                # split this from genuine fails.
                _skip_21_sub = 2
```
