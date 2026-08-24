### Title
Hardcoded trust anchor for GitHub's SSH host key bypasses host-authenticity prompts without a live/verifiable source - (File: `app/src/lib/trampoline/trampoline-askpass-handler.ts`)

### Summary
GitHub Desktop auto-accepts the SSH host-authenticity prompt for `github.com` by comparing the host, key type, and fingerprint reported by `ssh`/git against a single hardcoded string constant, rather than deferring to git/ssh's own `known_hosts` mechanism or a value that Desktop can verify/refresh against an authoritative source.

### Finding Description
`handleSSHHostAuthenticity` in `app/src/lib/trampoline/trampoline-askpass-handler.ts` parses the "add SSH host" prompt via `parseAddSSHHostPrompt` and automatically answers "yes" — silently trusting the remote host — whenever: [1](#0-0) 
matches `info.host === 'github.com'`, `info.keyType === 'RSA'`, and `info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'`.

This mirrors the reported bug class exactly: a security-relevant constant (`EPOCH_SECONDS` in the Move contract) is hardcoded from a single point-in-time reference rather than derived from, or continuously validated against, an authoritative/official source, and the code has no mechanism to detect or react if that value becomes outdated. Here, the fingerprint constant is the trust anchor for auto-accepting a host key on behalf of the user — if GitHub ever rotates its RSA host key again (as it did in March 2023 after a private key exposure incident), this hardcoded value goes stale. Only if the code is also updated in lockstep will the comparison keep working correctly; a comment in the code even instructs future contributors to update it manually, which is the same "example-value-not-guaranteed-to-remain-the-same" invariant flagged in the report: [2](#0-1) 

### Impact Explanation
The direct failure mode of a stale constant here is fail-safe (the auto-accept condition no longer matches, so the user is prompted normally via `trampolineUIHelper.promptAddingSSHHost`), which is why this is assessed as low/moderate rather than critical. However, it does represent a broken invariant worth flagging under the same bug class as the report: Desktop is silently making a trust decision on the user's behalf based on a single hardcoded example value with no verification path, no expiry/staleness detection, and no fallback to check against GitHub's officially published SSH key fingerprints page. Any latent bug in how `parseAddSSHHostPrompt` extracts `info.host`/`info.keyType`/`info.fingerprint` from the raw prompt text (comparison here is purely on parsed string fields, not on the actual SSH transcript) would let the hardcoded auto-accept condition be satisfied for a connection that isn't actually to genuine `github.com`, since correctness is entirely dependent on the parser and the constant being exactly right.

### Likelihood Explanation
Reachability requires only a normal git SSH operation against `github.com`; the auto-accept path is always active. Exploitation of the invariant, however, requires either (a) GitHub rotating its host key again without Desktop's hardcoded constant being updated (an operational/maintenance risk, not attacker-triggered), or (b) a flaw in `parseAddSSHHostPrompt`'s extraction of the host/keyType/fingerprint fields that an attacker-controlled prompt string could influence. I was not able to fully inspect `app/src/lib/ssh/ssh.ts` (`parseAddSSHHostPrompt`) or `app/test/unit/ssh-test.ts` before running out of tool iterations, so I cannot confirm whether the parsing is strict enough to prevent field confusion from crafted prompt text. This is the key open question for further investigation.

### Recommendation
- Avoid hardcoding a single trust-anchor fingerprint indefinitely; document its provenance (link to GitHub's official SSH key fingerprints page) and add a mechanism (e.g., a build-time or update-time check) to detect when it's stale.
- Audit `parseAddSSHHostPrompt` in `app/src/lib/ssh/ssh.ts` to ensure `host`, `keyType`, and `fingerprint` are parsed unambiguously from the raw `ssh`/git prompt and cannot be influenced by attacker-controlled repository/remote configuration or proxy responses.
- Consider removing the auto-accept fast path entirely and always deferring to the user or to git/ssh's standard `known_hosts` trust model, consistent with the report's general recommendation to not silently rely on values that "have no certainty of remaining the same."

### Proof of Concept
Not applicable as a directly exploitable PoC given fail-safe behavior on mismatch; this is reported as an analog of the "unverified/undocumented constant" invariant-break class per the task's method, pending confirmation of `parseAddSSHHostPrompt`'s parsing strictness (file content not fully retrievable within available tool budget — a Devin session with full repo access would be needed to inspect `app/src/lib/ssh/ssh.ts` and `app/test/unit/ssh-test.ts` in full to confirm or rule out the parsing-confusion sub-issue).

### Citations

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L18-37)
```typescript
async function handleSSHHostAuthenticity(
  operationGUID: string,
  prompt: string
): Promise<'yes' | 'no' | undefined> {
  const info = parseAddSSHHostPrompt(prompt)

  if (info === null) {
    return undefined
  }

  // We'll accept github.com as valid host automatically. GitHub's public key
  // fingerprint can be obtained from
  // https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
  if (
    info.host === 'github.com' &&
    info.keyType === 'RSA' &&
    info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'
  ) {
    return 'yes'
  }
```
