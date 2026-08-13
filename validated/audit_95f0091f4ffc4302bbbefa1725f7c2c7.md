### Title
Security-guidance commit review runs only *after* `git commit`/`git push` complete, so the irreversible action can never actually be blocked — analogous to `Auction.sol` checking `minIbRatio` after the bond is already locked - ([File: plugins/security-guidance/hooks/security_reminder_hook.py])

### Summary
The `Auction.sol` bug pattern is: a validation check (`minIbRatio`) is evaluated only at *settlement* time, after the user has already committed an irreversible action (bonding tokens), so a failing check leaves the user's committed state stuck with no way to undo it. The `security-guidance` plugin exhibits the same "check-after-commit" structural flaw for `git commit`/`git push`: the security review hook fires as a `PostToolUse[Bash]` hook — i.e., only *after* the Bash tool has already executed `git commit` or `git push` — with the review itself running asynchronously (`asyncRewake`) in the background while the irreversible action (the commit is created / the push already left the local machine) has already occurred.

### Finding Description
`plugins/security-guidance/hooks/hooks.json` registers the review hook against `PostToolUse` matched on `Bash`, gated with `"if": "Bash(git commit:*)"` and `"if": "Bash(git push:*)"`, both marked `"asyncRewake": true` [1](#0-0) . Because this is a `PostToolUse` hook rather than `PreToolUse`, by the time the review code runs, the underlying Bash command has already completed — the commit object already exists in git's object database (or, in the `git push` case, the objects have already left the machine). The Python handler explicitly documents this ordering: it pins the review "to the exact SHA the Bash command produced" and reads it out of the command's own stdout/reflog after the fact [2](#0-1) , and for `git push` the review runs as an asynchronous "sweep" of already-pushed commits [3](#0-2) .

The review outcome, no matter how severe, can only surface findings to be "addressed or acknowledged" in a follow-up turn (`rewakeMessage`/`rewakeSummary`) — there is no mechanism wired into this hook path to revert the commit, strip secrets from history, or prevent the push that already happened [4](#0-3) . This mirrors the `Auction.sol` root cause exactly: the state-changing action (`bondForRebalance()` / `git commit`) is allowed to complete unconditionally, and the safety check (`minIbRatio` / security review) is deferred to a point where the only possible outcomes are "it passed" or "we now know it's bad but can't undo it."

### Impact Explanation
If Claude commits code containing a hardcoded secret, credential, or other critical vulnerability, and then (per the user's or CLAUDE.md's git instructions) immediately `git push`es it, the async, post-hoc review can only report the problem after the secret is already committed to local history and potentially already pushed to a shared/remote repository. Unlike a `PreToolUse` gate that could deny the tool call before execution, this design cannot prevent the disclosure — it can only prompt Claude to "address or acknowledge" a leak that has already occurred, which for secret material is generally irreversible (the credential must be treated as compromised and rotated, and git history may need rewriting on the remote). This is a direct analog to the C4 finding: the safety invariant is enforced too late to stop the harmful, hard-to-reverse action instead of gating it.

### Likelihood Explanation
This is not a hypothetical/malicious-actor scenario — it is the documented, by-design control-flow of the shipped `security-guidance` plugin, which is enabled by default in the marketplace [5](#0-4) . Any ordinary Claude Code session where the agent writes a file containing a secret/vulnerability and then commits/pushes it (a common, unprivileged, everyday workflow) triggers this exact sequence — no adversarial node, peer, or operator is required, matching the report's Medium-severity assumption that a benign actor can trigger the loss window purely through normal usage combined with timing (here: commit before async review completes, or push before its sweep runs).

### Recommendation
Move the security-critical checks to `PreToolUse` on `Bash(git commit:*)` / `Bash(git push:*)` so obviously dangerous content (hardcoded secrets, critical vulnerabilities) can actually deny the underlying tool call before the commit/push executes, analogous to the report's fix of moving the `minIbRatio` check into `bondForRebalance()` instead of `settleAuction()`. Where a fully synchronous pre-check is too slow for interactive UX, at minimum perform a fast, synchronous pattern-based secret/credential scan pre-commit (layer 1, which already exists for `Edit`/`Write`) and wire it to actually block `git commit`/`git push` when a high-confidence secret match is found, rather than relying solely on the asynchronous, after-the-fact LLM/agentic review.

### Proof of Concept
1. Ask Claude Code (with the `security-guidance` plugin's default configuration) to add a file containing a hardcoded API key and then run `git commit -am "add config" && git push`.
2. The `Bash` tool executes the compound command; `PreToolUse` performs no security-specific gate on the commit/push itself.
3. `PostToolUse[Bash]` fires only after the command returns, matching `Bash(git commit:*)` / `Bash(git push:*)` per `hooks.json` [1](#0-0) , and launches the asynchronous commit review (`asyncRewake`), which resolves the SHA from the already-produced commit output [6](#0-5) .
4. By the time the review completes and reports the hardcoded secret, the commit already exists locally and the push has already delivered it to the remote — there is no code path in this hook that reverts the commit or blocks/retracts the push; it only rewakes the conversation with a message to "address or acknowledge" the finding [7](#0-6) .

Note: I was unable to fully inspect `handle_commit_review_posttooluse`'s complete body (the read tool call for that function's full source did not return before the session ended), so I cannot confirm with certainty whether any additional remediation/rollback branch exists later in that function beyond what's cited above. This should be verified directly in a live session before treating the PoC as fully confirmed.

### Citations

**File:** plugins/security-guidance/hooks/hooks.json (L35-55)
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/sg-python.sh\" \"${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py\"",
            "if": "Bash(git commit:*)",
            "asyncRewake": true,
            "rewakeMessage": "Background security review of commit — address or acknowledge the findings below, then continue with the user's original request or continue waiting for their reply:",
            "rewakeSummary": "Commit security review found issues"
          },
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/sg-python.sh\" \"${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py\"",
            "if": "Bash(git push:*)",
            "asyncRewake": true,
            "rewakeMessage": "Background security review of pushed commits not yet reviewed — address or acknowledge the findings below, then continue with the user's original request or continue waiting for their reply:",
            "rewakeSummary": "Push security review found issues"
          }
        ],
        "matcher": "Bash"
      }
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1020-1051)
```python
    if not repo_root:
        debug_log("Commit review: not in a git repo")
        emit_metrics({"skipped": True, "skip_reason": 26, **_base})
        sys.exit(0)

    # Pin the review to the exact SHA the Bash command produced, parsed from
    # its stdout. Reviewing HEAD instead is wrong when the commit was made in
    # a different repo than the hook's cwd (`cd ../other && git commit && cd -`,
    # subshells), or when a second commit lands before this async hook reaches
    # `git show` — both would review an unrelated commit. The reflog-action
    # fallback above is the narrow exception: it only fires when output gave
    # us nothing AND the cwd repo's own reflog confirms a `commit:` just
    # happened there, which rules out the cross-repo case.
    #
    # Take only the LAST match: pre-commit/husky hooks can print bracketed
    # labels like `[pre-commit abc1234]` that precede the real `[branch sha]`
    # line; chained commands like `git commit && git commit` produce multiple
    # real SHAs and we want the most recent. The real commit line is always
    # last in git's own output — the earlier matches are either decoys or
    # superseded commits.
    if _reflog_shas:
        # Output-based detection already failed above; the reflog SHAs are the
        # authoritative ones. Don't re-parse bash_output here — any bracketed
        # token it contains is by construction NOT the `[branch sha]` line
        # (or commit_succeeded would have been True via the fast path). The
        # list is newest-first and may contain >1 entry when a single Bash
        # call made multiple commits (`git commit -m a && git commit -m b`);
        # all are reviewed.
        shas = _reflog_shas
    else:
        all_shas = _COMMIT_SHA_RE.findall(bash_output)
        shas = [all_shas[-1]] if all_shas else []
```

**File:** plugins/security-guidance/README.md (L13-17)
```markdown
```
/plugin install security-guidance@claude-plugins-official
```

Marketplace ships enabled by default in Claude Code — no setup beyond having the CLI itself.
```
