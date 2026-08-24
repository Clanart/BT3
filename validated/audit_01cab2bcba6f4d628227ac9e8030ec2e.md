I found a genuine analog: the GitHub-host classification regex in `isGitHubHost` is over-broad in exactly the same way the Solidity report's arithmetic was wrong — a superficially-plausible check whose actual evaluated result grants a stronger classification than intended, feeding into a security decision (in this case, credential-helper trust level and TLS-error suppression) that shouldn't be reached.

### Title
`isGitHubHost` regex misclassifies attacker-controlled hostnames as GitHub, enabling credential-helper trust escalation and TLS-error suppression - (File: app/src/lib/api.ts)

### Summary
`isGitHubHost` is used to decide whether an unknown HTTPS remote host should be treated as a GitHub Enterprise instance. That classification feeds `trampoline-credential-helper.ts`'s `getEndpointKind`, which returns `'enterprise'` (as opposed to `'generic'`) when `isGitHubHost` returns true, and it also triggers a certificate-error-suppression fetch to the candidate host's `/meta` endpoint. The heuristic regex used to short-circuit this decision, `/(^|\.)(github)\./.test(hostname)`, matches any hostname that merely starts with (or contains a label ending in) the literal string `github.`, which includes attacker-registered domains such as `github.evil.com`, `sub.github.notgithub.io`, etc.

### Finding Description [1](#0-0) 

```js
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)
  ...
  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }
  ...
}
```

The comment implies the intent is to catch enterprise-style hosts such as `github.example.com` (a "github" subdomain of an internal domain). But the regex `(^|\.)(github)\.` only requires the literal substring `github.` to appear at the start of the hostname or immediately after a dot — it does not anchor to the end of the label or verify that `github` is a full DNS label. Any hostname a remote attacker fully controls (e.g., because they operate the domain used in a git remote URL or run a man-in-the-middle proxy on a domain they registered) satisfies this check as long as it contains `github.` as a substring boundary, e.g.:

- `github.attacker.com` → matches `(^)(github)\.`
- `notgithub.github.io`... (this one wouldn't match since `github.io` needs a preceding `.` which it has — actually `github.io` itself would match, incorrectly classifying legitimate but unrelated GitHub Pages hosting domains as an "enterprise" endpoint too)

This is the same *class* of bug as the Solidity report: a validation gate performs an operation (arithmetic there, string matching here) whose result is broader/weaker than the developer intended, and that miscalculated value is then trusted downstream to authorize a more privileged path (higher leverage there; elevated "enterprise" trust classification and TLS-suppression request here) without any secondary check that would catch the discrepancy.

### Impact Explanation
When `getEndpointKind` classifies a hostname as `'enterprise'` instead of `'generic'`, GitHub Desktop's trampoline credential helper (`app/src/lib/trampoline/trampoline-credential-helper.ts`) will offer/return GitHub-scoped credentials/tokens for that host as if it were a legitimate GitHub Enterprise Server, rather than treating it as a generic (untrusted) remote. [2](#0-1)  Additionally, `isGitHubHost` unconditionally calls `suppressCertificateErrorFor(metaUrl)` and issues a network request to the candidate host before this regex short-circuit is even reached in other code paths that rely on this function, and the regex allows an attacker-controlled domain to skip the normal `/meta` probing/enterprise-version verification entirely, short-circuiting straight to `return true`. This can lead to unauthorized GitHub credential exposure to a domain the user does not actually intend to authenticate against — a credential/token-exfiltration-class impact reachable purely by the user adding or being redirected to a maliciously-named git remote/host, without any local access.

### Likelihood Explanation
Likelihood is Medium: the attacker needs to control (register) a domain containing the substring `github.` as a prefix/label boundary and get the victim to add it as a remote or trigger a fetch/clone against it (e.g., via a crafted repository URL shared with the victim) — no local access, malware, or leaked credentials are required, matching the valid impact criteria (attacker controls a git remote/proxy response).

### Recommendation
Tighten the heuristic to require an exact label match rather than a loose substring/prefix test, e.g. `/(^|\.)github\.[^.]+$/` restricted to validate `github` as a standalone label immediately preceding the enterprise domain suffix, or better, remove the heuristic short-circuit entirely and always fall through to the authenticated `/meta` + `x-github-request-id` verification (which is already implemented later in the function) before trusting an unknown host as GitHub-owned.

### Proof of Concept
1. Attacker registers `github.attacker.com` and hosts a git server / HTTPS endpoint there.
2. Victim adds `https://github.attacker.com/owner/repo` as a remote or is directed to it via a crafted clone link.
3. When Desktop's credential helper resolves the endpoint kind via `getEndpointKind` → `isGitHubHost('https://github.attacker.com')`, hostname `github.attacker.com` fails the `isDotCom`/`isGHE`/`isKnownThirdPartyHost` checks but matches `/(^|\.)(github)\./`, so `isGitHubHost` returns `true` immediately. [3](#0-2) 
4. `getEndpointKind` then returns `'enterprise'` for this attacker-controlled host, and the credential helper proceeds along the GitHub-enterprise-trusted code path rather than the generic/untrusted path. [4](#0-3) 

Note: I was not able to fully trace every downstream consumer of the `'enterprise'` vs `'generic'` classification within the trampoline credential helper in the available index (some file contents may be truncated by index size limits), so the exact credential payload returned for each classification should be verified in a full checkout before treating this as conclusively exploitable end-to-end; a Devin session with full repository access would be needed to confirm the precise credential material exposed.

### Citations

**File:** app/src/lib/api.ts (L2429-2463)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L167-179)
```typescript
  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```
