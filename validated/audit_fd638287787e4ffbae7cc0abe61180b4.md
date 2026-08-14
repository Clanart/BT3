### Title
Security-guidance plugin accepts forged "already-reviewed" commit markers, letting a compromised/prompt-injected agent bypass commit and push security review - (File: `plugins/security-guidance/hooks/diffstate.py`)

### Summary
The `security-guidance` plugin tracks which commits have already been security-reviewed in a plain, unsigned append-only log at `.git/sg-reviewed-shas`. Both the commit-review hook's reflog fallback and the push-sweep hook trust this file's contents as proof that a SHA was genuinely reviewed, with no cryptographic binding between a log entry and an actual completed review. Any process with ordinary filesystem write access to the repo — including the Claude Code agent itself if compromised via prompt injection — can append a forged entry for an arbitrary commit SHA and cause the plugin's own security hooks to silently skip reviewing that commit, exactly analogous to a Merkle-proof verifier accepting a fabricated proof because it never binds the proof to a canonical, trust-rooted commitment.

### Finding Description
`_load_reviewed_shas()` reads `.git/sg-reviewed-shas` and returns the set of SHAs it contains, validating only that each token is 40 hex characters — it performs no check that the entry actually corresponds to a review that ran: [1](#0-0) 

`_append_reviewed_shas()` writes new lines to that same file with no signature, MAC, or any value derived from the actual LLM review output — anyone who can write to the working tree can append arbitrary lines in the exact accepted format (`<sha>\t<ts>\t<pv>\t<vulns_found>`): [2](#0-1) 

This "reviewed" set is then trusted as an authorization gate in two places:

1. In the commit-review reflog fallback, SHAs found in `_load_reviewed_shas()` are filtered out of the set to be reviewed, treating them as already covered: [3](#0-2) 

2. In push-sweep, `_compute_push_sweep_base()` advances the diff base past the contiguous prefix of SHAs present in the reviewed set, and any commit in that prefix is never diffed or sent to the LLM reviewer at all: [4](#0-3) [5](#0-4) 

The verifier pattern here mirrors the reported bug class exactly: `ok := <SHA present in file>; if ok { skip_review() }` — the "proof" (an entry in the reviewed-shas log) is accepted without being bound to any canonical, tamper-evident commitment (e.g., a hash of the actual diff content, or a signature produced only by the real review pipeline). Because the file lives under `.git/` in the working tree and is written with plain `open(...).write()`/append calls guarded only by `fcntl.flock` (for concurrency, not authenticity), it is fully writable by the same agent process that also has Bash tool access.

### Impact Explanation
`security-guidance` is the trust boundary meant to catch a compromised or prompt-injected Claude Code agent trying to introduce vulnerable/malicious code into a commit before it reaches the user or gets pushed (the documented "model is the attacker, the user is the victim" boundary for these hooks). By forging entries in `sg-reviewed-shas`, an attacker-controlled agent can:
- Mark the SHA of a commit it is about to create (or one already made outside review) as reviewed, causing both the per-commit hook and the push-sweep hook to skip analysis and never surface findings/never exit with code 2 to force remediation.
- Cause an entire prefix of a push range to be silently skipped from review, letting malicious code ship to the remote with no LLM security check at all.

This is a full, silent bypass of the project's own automated security-review control for git automation — the exact class of "trust the proof blindly" failure described in the report, adapted to this repo's git-automation hook-bypass surface.

### Likelihood Explanation
No privileged access is required beyond what the agent already has (Bash + file write in the working tree, which every Claude Code session running this plugin has by design). The forgery only requires knowing the fixed line format (`sha\tts\tpv\tvulns_found`), which is documented in comments in the same file. No cryptographic material needs to be broken — the log has no cryptographic binding to a real review at all.

### Recommendation
- Do not treat a bare presence-in-file entry as proof of review. Bind each entry to a value derived from the actual reviewed content (e.g., a hash of the diff that was sent to the LLM plus a token minted only by the review pipeline itself, verified before trusting the entry) so a forged entry cannot be crafted without also reproducing the real review.
- Consider moving the reviewed-SHA state out of a plaintext, agent-writable path, or protect it with a MAC keyed by a secret not accessible to the agent process, so a compromised/prompt-injected model cannot self-attest that its own malicious commit was reviewed.
- Add negative tests that append a hand-crafted line to `sg-reviewed-shas` for a SHA containing an injected vulnerability and confirm the commit-review/push-sweep hooks still catch it.

### Proof of Concept
1. Start a Claude Code session with the `security-guidance` plugin enabled and commit-review/push-sweep on (default).
2. Have the agent (simulating prompt-injected/compromised behavior) run, before making its malicious change:
   ```bash
   MALICIOUS_SHA=$(git rev-parse HEAD)   # or a SHA it is about to create
   printf "%s\t%d\t1\t0\n" "$MALICIOUS_SHA" "$(date +%s)" >> .git/sg-reviewed-shas
   ```
3. Introduce an obviously vulnerable change (e.g., `subprocess.run(cmd, shell=True)` with attacker-controlled `cmd`), commit it with that SHA (or amend to it), and push.
4. Observe that `_load_reviewed_shas()` returns the forged SHA as reviewed; `handle_commit_review_posttooluse`'s reflog-fallback filtering and `handle_push_sweep_posttooluse`'s `_compute_push_sweep_base` both treat the commit as already covered, so no diff is ever sent to `analyze_code_security`, no findings are emitted, and exit code 2 (which would force the agent to fix the issue) never fires — the vulnerable commit is pushed with no security-guidance review at all.

### Citations

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

**File:** plugins/security-guidance/hooks/diffstate.py (L267-284)
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
    try:
        import fcntl
        with open(p, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L717-736)
```python
def _compute_push_sweep_base(prev_upstream, push_range, reviewed):
    """Advance the diff base past the contiguous reviewed prefix.

    Spec: review `git diff B..HEAD` where `B` is the newest commit such that
    `prev_upstream..B` is entirely in `reviewed`. Returns (B, unreviewed_tail).
    `B == None` means the whole range is reviewed (caller should skip).
    `push_range` must be oldest→newest.

    Examples (✓=reviewed, ✗=not):
      [✓1, ✗2, ✓3]  → B=1, tail=[2,3]   (cannot trim suffix; Read is at HEAD)
      [✓1, ✓2, ✓3]  → B=None            (all reviewed → skip)
      [✗1, ✓2, ✗3]  → B=prev_upstream, tail=[1,2,3]
      []            → B=None
    """
    i = 0
    while i < len(push_range) and push_range[i] in reviewed:
        i += 1
    if i == len(push_range):
        return None, []
    base = push_range[i - 1] if i > 0 else prev_upstream
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L965-985)
```python
    _reflog_shas: List[str] = []
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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1537-1544)
```python
    reviewed = _load_reviewed_shas(repo_root)
    base, tail = _compute_push_sweep_base(prev_upstream, push_range, reviewed)
    prefix_advanced = len(push_range) - len(tail)
    if base is None:
        debug_log("Push sweep: every pushed commit already reviewed")
        emit_metrics({**_base, "pushed": len(push_range), "unreviewed": 0,
                      "prefix_advanced": prefix_advanced})
        sys.exit(0)
```
